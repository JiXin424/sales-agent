# Ontology 入库 Pipeline + 前端知识库页改造 设计

> 日期：2026-06-25
> 状态：设计稿（待 review）
> 关联：`docs/superpowers/plans/2026-06-25-ontology-neo4j-knowledge-engine.md`（已完成的本体引擎内核）、`docs/ontology-neo4j-ops.md`

## 1. 目标

把已有的 ontology 入库内核（`OntologyIngestionService.ingest_paths`，目前**只在测试里被调用**）接通成一个**完整、可从前端一键触发的入库流程**，并把前端知识库页（`AgentKnowledgePage`）改造成：

**上传文件（多文件 .md/.txt）→ SSE 实时进度（6 阶段步骤条）→ 完成提示（含统计）→ 查看图谱（外链 Neo4j Browser）**

## 2. 范围

**做：**
- 后端：多文件上传 → 落盘 → 每文件一个后台入库任务 → SSE 推送阶段进度；接入真实 LLM（DeepSeek + dashscope）做实体/事实抽取。
- 前端：整页替换 `AgentKnowledgePage` 为「上传区 + 多文件进度列表」；失败隔离 + 重试；外链 Neo4j Browser。

**不做（non-goals）：**
- 不做 custom 图谱可视化 UI（复用 Neo4j Browser）。
- 不做 PDF/Word 解析（仅 `.md` / `.txt` 纯文本）。
- 不引入独立 worker 进程（用 `asyncio.create_task` 后台执行，单进程内异步）。

## 3. 关键决策（已与用户确认）

| 决策 | 选择 |
|---|---|
| 文件格式 | `.md` / `.txt` 纯文本（extractor 直接 `read_text`，不加解析依赖） |
| 页面改造 | 整个知识库页替换为 ontology 上传流程（移除 legacy 文档表格） |
| 进度反馈 | **SSE 实时推送**（后端推阶段变化，前端 EventSource 订阅，不轮询） |
| 批量执行 | **方案 2：每文件一个 job**，单文件隔离（一个失败不影响其它） |
| 页面布局 | **A：上传区置顶 + 下方进度列表**，6 阶段 Steps 进度条 |
| 查看图谱 | **外链 Neo4j Browser**（`visual_url`，零后端改动） |
| 真实 LLM | chat = DeepSeek `deepseek-chat`；embedding = dashscope `text-embedding-v3`（1024 维，与向量索引一致） |

## 4. 架构（方案 2：每文件一个 job）

### 4.1 后端

**(a) 上传触发** — `POST /agents/{agent_id}/ontology/ingest`
- 改为 `multipart/form-data`，接 `files: list[UploadFile]`。
- 流程：
  1. 校验扩展名（仅 `.md`/`.txt`），非法文件 400。
  2. 落盘到 `{DATA_DIR}/agents/{agent_id}/ontology/{uuid}_{原文件名}`。
  3. **每个文件**创建一个 `IngestionJob`（`engine=ontology_neo4j`, `status=running`, `stage=uploaded`, `agent_id`, `tenant_id`, `documents_seen=1`）。
  4. **每个文件**起一个 `asyncio.create_task` 跑 `ingest_paths(paths=[该文件], progress_callback=…)`，注入真实 `ChatModel` + `EmbeddingModel` + `Neo4jClient`。
  5. 立即 `202` 返回 `[{job_id, filename}, …]`。
- **隔离**：单文件失败只把它自己的 job 标 `failed`，其它文件照常。

**(b) 进度回调** — `ingest_paths` 增强
- `ingest_paths(..., progress_callback: Callable[[str, dict], Awaitable[None]] | None = None)`。
- 在每次 `stage` 切换（`parsed` / `extracting_entities` / `extracting_facts` / `writing_neo4j` / `completed`）以及出错时，回调 `progress_callback(stage, partial_stats)`。
- `progress_callback` 默认 `None`（不影响现有测试与 live 测试）。

**(c) 进度总线** — 新增 `src/sales_agent/ontology/progress.py`
- `JobProgressBus`：内存 `dict[str, list[asyncio.Queue]]`。
- `subscribe(job_id) -> asyncio.Queue`：每个订阅者一个 queue。
- `async publish(job_id, event: dict)`：向该 job 所有 queue put 事件。
- `remove(job_id)`：订阅者全断开时清理。
- 进程级单例（模块全局）。

**(d) SSE endpoint** — `GET /agents/{agent_id}/ontology/jobs/{job_id}/events`
- `media_type="text/event-stream"`，用 FastAPI `StreamingResponse`（**不引入 sse-starlette 新依赖**）。
- 订阅 `bus.subscribe(job_id)`，循环 `yield f"data: {json}\n\n"`：
  - 先推一条当前快照（从 DB 读 job 当前 stage/status/stats，避免订阅晚到时漏进度）。
  - 后续推 bus 事件。
  - 收到终态（`completed` / `completed_with_errors` / `failed`）推一条 final 后 `break` 关闭流。
- 订阅者断开（客户端关闭 EventSource）时清理 queue。

**(e) 真实 LLM 注入**
- 复用 `TenantResolver`（与 `ChatPipeline` 同款）：`resolver.resolve(tenant_id)` → `resolver.get_model_provider(tenant_info)` → `provider.chat` / `provider.embedding`。
- 抽到 `src/sales_agent/ontology/runner.py`（新）：`build_ingestion_service(db, settings, tenant_id) -> OntologyIngestionService`，封装 Neo4j client + repository + 真实 LLM provider 注入。route 调它。
- 密钥只走环境变量（`secrets/*.env`），**绝不硬编码**。

**(f) 引擎一致性**
- 前端进页查 `getOntologyStatus`：若 `knowledge_engine != "ontology_neo4j"` 或 `neo4j_ready=false` → 页面顶部警示 + 禁用上传（避免点了没反应）。

### 4.2 前端（`AgentKnowledgePage.tsx` 整页替换）

布局 A：
- **顶部状态条**：`ontology_status` / `neo4j_ready` Tag；`visual_url` 存在时显示「Neo4j Browser →」外链。未就绪 → Alert 警示 + 禁用上传。
- **上传区**：Ant Design `Upload.Dragger`（多选 `.md`/`.txt`）→ 自定义 `customRequest` 调 `startOntologyIngest(agentId, files)`（multipart）→ 拿 `[{job_id, filename}]`。
- **进度列表**：每文件一行（`filename` 为 key）：
  - **进行中**：6 阶段 Steps（上传 / 解析 / 抽实体 / 抽事实 / 写图谱 / 完成），`EventSource` 订阅 `/jobs/{job_id}/events`，按 `stage` 更新当前步骤（`status=process`），已完成步骤 `finish`。
  - **完成**：绿色行 + 统计（`entities_created` / `facts_created` / `facts_pending_review` / `conflicts_created`）+ 「查看图谱 →」外链 `visual_url`。
  - **失败**：红色行 + 错误信息（`error_summary` / `errors`）+ 「重试」按钮（重新上传该文件）。
- **全部完成**：顶部 `message.success` 汇总（N 个文件入库完成，共 X 实体 Y 事实）。

### 4.3 数据流

```
浏览器 Upload → POST multipart /ontology/ingest
  → 后端落盘 + 每文件建 job + asyncio.create_task(ingest_paths)
  → 202 返回 [{job_id, filename}]
浏览器对每个 job_id 开 EventSource → /jobs/{job_id}/events
后台 ingest_paths 执行 → 每阶段 progress_callback → bus.publish(job_id, {stage,stats})
SSE endpoint 从 bus 读 → yield data: → 浏览器更新 Steps
ingest_paths 终态 → bus.publish(done/error) → SSE 推 final → 关闭 → 浏览器显示完成/失败
```

## 5. SSE 事件协议

```
event: snapshot
data: {"stage":"extracting_entities","status":"running","stats":{...}}

event: progress
data: {"stage":"writing_neo4j","stats":{"entities_created":3,"facts_created":2}}

event: done
data: {"status":"completed","stats":{"entities_created":5,"facts_created":4,"facts_pending_review":1,"conflicts_created":0}}

（或）
event: error
data: {"status":"failed","error_summary":"抽取超时","errors":[...]}
```

## 6. 错误处理

- **单文件失败**：该 job `status=failed`（或 `completed_with_errors`），`error_summary` 记录；前端该行红色 + 错误 + 「重试」（重新上传）。其它文件不受影响。
- **后台任务异常**：`asyncio.create_task` 内 try/except，把 job 标 `failed` 并 `bus.publish(error)`，不让异常静默。
- **LLM 抽取 JSON 不合规**：`extractor._extract_json` 已有 fenced/裸 JSON fallback；仍失败则该 fact/entity 跳过并记 warning（现有行为）。
- **进程重启**：运行中的 `asyncio.create_task` 丢失（不持久）。job 行留在 DB 为 `running`（陈旧）。**接受**（小规模）；后续可加启动时把 `running` 标 `failed`（陈旧任务清理），列为后续。
- **SSE 连接清理**：客户端断开 → generator 退出 → `bus` 该订阅者 queue 移除，无泄漏。

## 7. 测试

**后端：**
- `tests/unit/ontology/test_progress_bus.py`：publish/subscribe、多订阅者、remove。
- `tests/unit/ontology/test_ingestion_service.py` 扩展：`progress_callback` 在每个 stage 被调用（断言调用序列）。
- `tests/integration/test_ontology_api.py` 扩展：`POST /ingest`（multipart 多文件）→ 返回多 job_id + 后台任务把 job 推到 completed（用 fake LLM provider）；`GET /jobs/{id}/events` SSE 订阅收到 snapshot+progress+done（用 `httpx.AsyncClient` 流式读）。
- `tests/integration/test_ontology_neo4j_live.py` 扩展（gated）：**真实 DeepSeek 抽取 + dashscope embedding**，验证 JSON 抽取稳定性、embedding 维度=1024、端到端写图谱。gated by `ONTOLOGY_LIVE_LLM=1`（避免 CI 跑真实 LLM）。

**前端：**
- `console/src/tests/api/knowledge.test.ts` 扩展：`startOntologyIngest` 发 multipart（断言 method=POST、url、form-data 含文件）。
- 进度组件单元测试（可选）：mock EventSource，断言 stage → Steps 状态映射。

## 8. 风险与验证点

1. **embedding 维度**：dashscope `text-embedding-v3` 默认 1024 维，需与 `entity_embedding_vector`（1024）一致。live LLM 测试断言 `len(embedding)==1024`。
2. **DeepSeek JSON 抽取稳定性**：真实文档跑一遍，看 `parse_entities_json`/`parse_facts_json` 成功率；必要时调 prompt。
3. **`asyncio.create_task` 生命周期**：进程重启丢失运行中任务（接受）。
4. **SSE 背压/断开**：bus 用 asyncio.Queue（默认无界），订阅者断开靠 generator 退出清理；大量并发 job 时监控内存（小规模可接受）。
5. **multipart 上传大小**：.md/.txt 通常小；超大文件后续再加限制。

## 9. 文件改动清单

**后端：**
- `src/sales_agent/ontology/ingestion_service.py` — `ingest_paths` 加 `progress_callback`。
- `src/sales_agent/ontology/progress.py` — 新，`JobProgressBus`。
- `src/sales_agent/ontology/runner.py` — 新，`build_ingestion_service`（真实 LLM 注入）。
- `src/sales_agent/api/routes/ontology.py` — `start_ontology_ingest` 改 multipart 多文件+多 job+后台任务；新增 SSE endpoint。
- 测试：`test_progress_bus.py`（新）、`test_ingestion_service.py`（扩展）、`test_ontology_api.py`（扩展）、`test_ontology_neo4j_live.py`（扩展，gated 真实 LLM）。

**前端：**
- `console/src/pages/Agents/AgentKnowledgePage.tsx` — 整页替换。
- `console/src/api/knowledge.ts` — `startOntologyIngest` 改 multipart 多文件上传；新增 `subscribeJobEvents`（EventSource 封装）。
- `console/src/api/types.ts` — 新增 `JobEvent` / `IngestStartResponse` 类型。
- `console/src/tests/api/knowledge.test.ts` — 更新上传 wrapper 测试。

**文档：**
- `docs/ontology-neo4j-ops.md` — 补「上传入库」用法。
- `changelog/2026-06-25.md` — 追加本次改动记录。
- `README.md` — 更新知识库页说明（如需）。

## 10. 部署 / 启用前提

- `KNOWLEDGE_ENGINE=ontology_neo4j` + `NEO4J_*` 已配置、Neo4j 就绪（前端会检测并提示）。
- `MODEL_API_KEY`（DeepSeek）+ `EMBEDDING_API_KEY`（dashscope）已在 `secrets/*.env` 配置。
- 无新 DB migration（`IngestionJob` 字段已具备；新增的只是 SSE 总线为内存态）。
