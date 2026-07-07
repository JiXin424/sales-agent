# Research: Knowledge Prompt Resolve Callers（8 个内部 prompt 补注册 + 层次 2 全接 PromptRegistry）

- **Query**: 把 8 个内部硬编码 prompt 常量注册到 `PromptRegistry`（新 `category="knowledge"`）并让所有调用方走运行时三级回退（用户已选定「层次 2 全部」），需摸清调用方全貌、db 可达性、占位符情况、`_ENTITY_EXTRACTION_PROMPT` 两份是否可合并。
- **Scope**: internal
- **Date**: 2026-07-07

---

## TL;DR（关键约束）

1. **`PromptRegistry.resolve_prompt("knowledge", key, tenant_id, agent_id)` 三级回退的第三级（内置常量）目前空缺** — `prompt_defaults.BUILTIN_PROMPTS` 没有 `category="knowledge"` 任何条目（`prompt_defaults.py:111-116`，仅 task/system/router/risk/coach/web）。**必须新增**一个 `_knowledge_entries()` 并把 7 条（去重后）`BuiltinPrompt` 加进去，否则 tenant 未配 DB 版本时直接抛 `ValueError`（`prompt_registry.py:164`）。
2. **8 个原始常量去重后是 7 个注册 key**：`ontology_graph.py:23` 与 `retrieval_service.py:16` 的 `_ENTITY_EXTRACTION_PROMPT` 经 `diff` **字节级完全相同**（diff exit=0）→ 可共用一个 key（建议 `("knowledge","ontology_term_extractor")`），但**两个模块的引用都要改成走 registry**。
3. **db 可达性分三类**，全部可达成，无需新透传：
   - (a) **API 入库链路（OntologyIngestionService → LLMExtractor → extract_entities/facts）** 和 **API 探索器链路（OntologyRetrievalService → _extract_search_terms / OntologyAnswerService.generate_answer → ONTOLOGY_RESPONSE_PROMPT）** 都已持有 `AsyncSession`（`ontology.py:88` 的 `db: DbSession`；`_do_ingest:182` 的 `bg_db`；`_build_explorer_services:302` 的 `db`）。只需把 db/tenant_id/agent_id 透传到 service 构造或方法调用。
   - (b) **ChatPipeline._run_ontology**（`chat_pipeline.py:540-549`）调 `OntologyRetrievalService.retrieve` → 触发 `_extract_search_terms` → 用 `_ENTITY_EXTRACTION_PROMPT`。`self.db` 已就位（`chat_pipeline.py` 的 `__init__` 注入），但 `OntologyRetrievalService` 当前构造时不收 db（`chat_pipeline.py:92`、`ontology.py:327`），需补。
   - (c) **graph 节点 `extract_terms_node`**（`ontology_graph.py:45`，由 `retrieve_node` 在 ontology 路径调用，`retrieval.py:114`）→ `runtime.context.get("db")` **已可用**（`dingtalk/graph_stream.py:122` 与 `online_conversation.py:228-233` 都注入了 `db`）。`extract_terms_node` 当前只取 `chat_model`（:51），加一行 `db = runtime.context.get("db")` + 从 `state` 取 `tenant_id`/`agent_id` 即可（参照 `risk_check_node`/`generate_node` 范式）。
4. **MD_OPTIMIZE 链路也全有 db**：`KnowledgeIngestor.db`（`knowledge_ingestor.py:28`）持 session，所有 4 个入口（`cli.py:56`、`uploads.py:127/276/344`、`documents.py:45`）都把 `db` 传进 `KnowledgeIngestor`。`_optimize_md`（`knowledge_ingestor.py:238-256`）构造 `MDOptimizer(self.chat_model)` 时 **没有传 db**，需要在 `MDOptimizer` 构造或 `optimize()` 调用处补 db/tenant_id/agent_id。
5. **占位符情况复杂**：8 个模板里 **5 个含 `.format` 占位符**（ENTITY/FACT/MD_OPTIMIZE_USER/ONTOLOGY_RESPONSE/两份 _ENTITY_EXTRACTION），**3 个不含**（IMAGE_INTERPRET、MD_OPTIMIZE_SYSTEM）。原代码全部用裸 `.format(...)` 调用 — 注册到 DB 后**运营若把字面 `{{...}}` 改成 `{...}` 会触发 lessons #30 字面花括号陷阱**。改法须用 `format_map(SafeDict)` 或保持调用方就地 `.format`（见第 4 节）。
6. **CLI（cli.py）入库只走 `KnowledgeIngestor`（MD_OPTIMIZE 路径），不走 `OntologyIngestionService`** → CLI 不触发 ENTITY/FACT/IMAGE_INTERPRET/ONTOLOGY_RESPONSE 这 4 个 prompt。CLI 唯一相关的是 MD_OPTIMIZE（通过 `cli.py:56` → `KnowledgeIngestor` → `_optimize_md`）。
7. **`image_to_text`（img_parser.py:73）零调用方**，但 `IMAGE_INTERPRET_PROMPT` **被 `ingestion_service.py` 直接 import**（:148、:201）并在 `_pdf_page_to_vision`/`_image_to_text_via_vision` 中通过 httpx 直发 LLM（不经过 `image_to_text()`）。注册到 registry 后**两处 import** 都要替换。

---

## Findings

### 0. 8 个 prompt 的注册 key 建议（去重后 7 个）

| # | 原始常量（文件:行） | 建议 category/key | 是否有 `.format` 占位符 | required_placeholders |
|---|---|---|---|---|
| 1 | `ENTITY_EXTRACTION_PROMPT`（`extractor.py:93`） | `("knowledge","ontology_entity_extractor")` | 是 `{content}` | `("content",)` |
| 2 | `FACT_EXTRACTION_PROMPT`（`extractor.py:101`） | `("knowledge","ontology_fact_extractor")` | 是 `{entities_json}` `{content}` | `("content","entities_json")` |
| 3 | `IMAGE_INTERPRET_PROMPT`（`img_parser.py:44`） | `("knowledge","ontology_image_interpret")` | 否 | `()` |
| 4 | `MD_OPTIMIZE_SYSTEM_PROMPT`（`md_optimizer.py:24`） | `("knowledge","md_optimize_system")` | 否 | `()` |
| 5 | `MD_OPTIMIZE_USER_TEMPLATE`（`md_optimizer.py:59`） | `("knowledge","md_optimize_user")` | 是 `{content}` | `("content",)` |
| 6 | `_ENTITY_EXTRACTION_PROMPT`（**两份完全相同**：`ontology_graph.py:23` + `retrieval_service.py:16`） | `("knowledge","ontology_term_extractor")` （**共用一个 key**） | 是 `{question}` | `("question",)` |
| 7 | `ONTOLOGY_RESPONSE_PROMPT`（`answer_service.py:81`） | `("knowledge","ontology_response")` | 是 `{graph_json}` `{question}` `{task_type}` | `("graph_json","question","task_type")` |

**验证 `_ENTITY_EXTRACTION_PROMPT` 两份相同**（`diff <(sed -n '23,28p' ontology_graph.py) <(sed -n '16,21p' retrieval_service.py)` → exit=0 无输出）。两份的 22-行 header 注释略不同，但 prompt 正文逐字节相同。

---

### 1. 每个 prompt 的所有调用方（区分链路）

#### 1.1 `ENTITY_EXTRACTION_PROMPT`（`extractor.py:93`）

| 调用方函数 | 调用方文件:行 | 链路类型 | db 可达性 |
|---|---|---|---|
| `extract_entities(chat_model, content)` | `extractor.py:148-170`（在 :160 用 `ENTITY_EXTRACTION_PROMPT.format(content=chunk)`） | 被下方 LLMExtractor 适配器调用 | 见下方链路 |
| `LLMExtractor.extract_entities` | `ontology/runner.py:20-21`（包装 `extract_entities(self._chat, content)`） | 适配器 | 透传至下方 |
| `OntologyIngestionService._ingest_one` | `ontology/ingestion_service.py:350`（`await self.extractor.extract_entities(content)`） | 入库 API 服务层 | **`self.db`**（构造时注入，`ingestion_service.py:268`） |
| `OntologyIngestionService.ingest_paths` | `ingestion_service.py:273-315`（循环调 `_ingest_one`） | 入库 API 主入口 | `self.db` |
| `build_ingestion_service(db, settings, model_provider)` | `ontology/runner.py:29-43`（构造 `OntologyIngestionService(db=db, ...)`） | 入库 API 组装工厂 | **`db: AsyncSession`** 是参数 |
| `_do_ingest(job_id, tenant_id, agent_id, path)` | `api/routes/ontology.py:171-206`（:191 `service = build_ingestion_service(bg_db, settings, model_provider)`） | 入库 API 后台任务 | **`bg_db`**（:182 `async with session_factory() as bg_db`） |
| `_run_ingest_background` | `api/routes/ontology.py:136-168`（调 `_do_ingest`） | 入库 API task wrapper | 同上 |
| `POST /agents/{agent_id}/ontology/ingest` | `api/routes/ontology.py:83-133`（:121 `asyncio.create_task(_run_ingest_background(job_id=..., tenant_id=agent.tenant_id, agent_id=agent.id, path=...))`） | HTTP 入库端点 | **`db: DbSession`**（:87），但实际后台任务用新建的 `bg_db`；`tenant_id`/`agent_id` 已透传 |

**`extract_entities` 的外部直接调用方**：仅 `LLMExtractor.extract_entities`（runner.py:21）一处。`grep -rn "extract_entities\b" src --include="*.py"` 仅命中 `extractor.py:148`（定义）、`ingestion_service.py:251`（Protocol 接口）、`ingestion_service.py:350`（service 调用）、`runner.py:20-21`（适配器）。

#### 1.2 `FACT_EXTRACTION_PROMPT`（`extractor.py:101`）

调用方结构与 1.1 完全对称：

| 调用方函数 | 调用方文件:行 | 链路 |
|---|---|---|
| `extract_facts(chat_model, content, entities)` | `extractor.py:198-239`（:230 `FACT_EXTRACTION_PROMPT.format(content=chunk, entities_json=entities_json)`） | 函数本体 |
| `LLMExtractor.extract_facts` | `runner.py:23-26` | 适配器 |
| `OntologyIngestionService._ingest_one` | `ingestion_service.py:389`（`await self.extractor.extract_facts(content, entities)`） | 入库服务 |
| 其外层链路同 1.1（`ingest_paths` → `build_ingestion_service` → `_do_ingest` → `_run_ingest_background` → `POST /ontology/ingest`） | 同上 | 入库 API |

#### 1.3 `IMAGE_INTERPRET_PROMPT`（`img_parser.py:44`）

**关键**：`image_to_text`（img_parser.py:73）**无任何调用方**（`grep -rn "image_to_text\b" src --include="*.py"` 仅命中定义和 docstring）。但 `IMAGE_INTERPRET_PROMPT` 被 `ingestion_service.py` 直接 import 使用。

| 调用方函数 | 调用方文件:行 | 链路 | db 可达性 |
|---|---|---|---|
| `_pdf_page_to_vision(page, filename)` | `ingestion_service.py:129-185`（:148 `from sales_agent.ontology.img_parser import IMAGE_INTERPRET_PROMPT`；:171 `{"type":"text","text": IMAGE_INTERPRET_PROMPT}`） | 入库 PDF 扫描页解读（同步 httpx） | **无 db**（函数本身是同步 helper，被 `_read_content` 调用，链路上方有 db） |
| `_image_to_text_via_vision(path)` | `ingestion_service.py:188-240`（:200-203 import；:227 `IMAGE_INTERPRET_PROMPT` 直发 LLM） | 入库图片文件解读（同步 httpx） | **无 db**（同步 helper） |
| `_read_content(path)` | `ingestion_service.py:56-126`（:89 调 `_pdf_page_to_vision`；:123 调 `_image_to_text_via_vision`） | 入库文件读取入口 | 同上 |
| `OntologyIngestionService._ingest_one` | `ingestion_service.py:336`（`content = _read_content(path)`） | 入库服务 | **`self.db`** |
| 外层链路同 1.1（`ingest_paths` → `build_ingestion_service` → `_do_ingest` → 入库端点） | 同上 | 入库 API |

**改法约束**：`_pdf_page_to_vision` / `_image_to_text_via_vision` 是同步函数，无法直接 `await reg.resolve_prompt`。需在调用方（`_ingest_one` 或 `_read_content`）层先解析好 prompt 字符串再透传，或把这两个 helper 改为 async。

#### 1.4 `MD_OPTIMIZE_SYSTEM_PROMPT`（`md_optimizer.py:24`）+ 1.5 `MD_OPTIMIZE_USER_TEMPLATE`（`md_optimizer.py:59`）

两个常量在同一个调用点使用：

| 调用方函数 | 调用方文件:行 | 链路 | db 可达性 |
|---|---|---|---|
| `MDOptimizer.optimize(raw_content, source_type_hint)` | `md_optimizer.py:86-143`（:100 `MD_OPTIMIZE_USER_TEMPLATE.format(content=raw_content)`；:109 `{"role":"system","content": MD_OPTIMIZE_SYSTEM_PROMPT}`） | MD 优化器本体 | **无 db**（构造时只收 `chat_model`，:78-84） |
| `MDOptimizer.optimize_batch` | `md_optimizer.py:145-169`（内部循环调 `self.optimize`） | 批量入口 | 同上 |
| `KnowledgeIngestor._optimize_md(raw_content, file_path)` | `knowledge_ingestor.py:238-256`（:249 `optimizer = MDOptimizer(self.chat_model)`；:251 `optimized = await optimizer.optimize(raw_content, source_type_hint=hint)`） | 知识库入库 MD 优化 | **`self.db`**（KnowledgeIngestor 构造时注入，:28），但 **构造 MDOptimizer 时没传 db** |
| `KnowledgeIngestor._ingest_file` | `knowledge_ingestor.py:124-236`（:144 `raw_content = await self._optimize_md(raw_content, file_path)`） | 入库单文件入口 | `self.db` |
| `KnowledgeIngestor.ingest_directory` | `knowledge_ingestor.py:37-118` | 入库目录主入口 | `self.db` |

**4 个外层入口（都把 db 传给 KnowledgeIngestor）**：

| 入口 | 文件:行 | 怎么拿 db |
|---|---|---|
| CLI `ingest` 命令 | `cli.py:40-77`（:48 `async with factory() as db`；:56 `KnowledgeIngestor(db=db, embedding_model=provider.embedding, chat_model=provider.chat)`） | `db` ✓（**注意**：CLI 入库**不走** OntologyIngestionService，只走 KnowledgeIngestor，所以 CLI 只触发 MD_OPTIMIZE，不触发 ENTITY/FACT/IMAGE_INTERPRET/ONTOLOGY_RESPONSE） |
| `POST /tenants/{tenant_id}/knowledge/files` 类端点 | `uploads.py:127-128`（`KnowledgeIngestor(db, model_provider.embedding)` — **没传 chat_model**！）、:276-277、:344-345 | `db: DbSession` ✓，但 **chat_model 缺失** → MD 优化根本不会触发（`knowledge_ingestor.py:142 if optimize_md and self.chat_model is not None`） |
| `documents.py` 重建索引 | `documents.py:45`（`KnowledgeIngestor(db=db, embedding_model=embedding_model)` — **也没传 chat_model**） | 同上 |

**改法约束**：要在 `MDOptimizer` 内部调 `reg.resolve_prompt`，必须把 db/tenant_id/agent_id 透传。最简方案是改 `MDOptimizer.__init__` 多收一个 `db`，或在 `optimize()` 多收 db/tenant_id/agent_id 三个 kwargs。`KnowledgeIngestor._optimize_md` 调用处（:249-251）需相应改动。**uploads.py / documents.py 的三个入口当前没传 chat_model**，MD 优化路径根本不走，因此不影响这些入口 — 改 `MDOptimizer` 签名不会破坏它们。

#### 1.6 `_ENTITY_EXTRACTION_PROMPT`（两份，内容相同）

##### 1.6a 图节点版（`ontology_graph.py:23`）

| 调用方函数 | 调用方文件:行 | 链路 | db 可达性 |
|---|---|---|---|
| `extract_terms_node(state, runtime)` | `ontology_graph.py:45-88`（:51 `chat_model = runtime.context.get("chat_model")`；:62 `_ENTITY_EXTRACTION_PROMPT.format(question=question)`） | 对话图 ontology 路径节点 | **`runtime.context.get("db")`** ✓（生产入口 `dingtalk/graph_stream.py:122` 注入；`online_conversation.py:228-233` 也注入），当前节点没取 db，需新增一行 |
| `retrieve_node`（在 ontology 路径分支） | `graph/nodes/retrieval.py:114`（`local.update(await extract_terms_node(local, runtime))`） | 对话图检索节点 | `runtime.context` 透传 |
| `graph_query_node`、`vector_fallback_node`、`compact_evidence_node` | `ontology_graph.py:94/134/171` | 同一 ontology 子流程的后续节点 | 同上 |
| `node_metadata.py:134` | `graph/node_metadata.py:134` 元数据登记（`{"name":"_ENTITY_EXTRACTION_PROMPT","source":"graph/retrieval/ontology_graph.py","note":"仅 ontology 路径的 extract_terms 调用"}`） | 仅文档/可视化元数据 | — |

**state 里有哪些字段可用**（`retrieval.py:100-110`）：`question`、`tenant_id`、`agent_id`、`task_type`、`search_terms`、`graph_rows`、`vector_fallback_used`、`compacted_evidence`。所以 `tenant_id = state["tenant_id"]`、`agent_id = state.get("agent_id")` 都已就位，**无需新透传**任何字段。

##### 1.6b retrieval_service 版（`retrieval_service.py:16`）

| 调用方函数 | 调用方文件:行 | 链路 | db 可达性 |
|---|---|---|---|
| `OntologyRetrievalService._extract_search_terms(self, question)` | `retrieval_service.py:42-80`（:52 `_ENTITY_EXTRACTION_PROMPT.format(question=question)`） | 内部方法 | **`self` 当前没有 db** |
| `OntologyRetrievalService.retrieve(tenant_id, agent_id, question)` | `retrieval_service.py:82-158`（:84 `search_terms = await self._extract_search_terms(question)`） | 公开检索入口 | tenant_id/agent_id 已是 kwargs |
| `OntologyAnswerService.answer_for_task` | `answer_service.py:208-227`（:217 `graph_evidence = await self.retrieval.retrieve(tenant_id=tenant_id, agent_id=agent_id, question=message)`） | 答案服务（带检索） | `self.retrieval`（即 OntologyRetrievalService 实例） |
| `POST /agents/{agent_id}/ontology/query`（探索器同步） | `api/routes/ontology.py:375-395`（:381 `evidence = await retrieval.retrieve(tenant_id=agent.tenant_id, agent_id=agent.id, question=req.query)`） | 探索器 HTTP | **`db: DbSession`** ✓（:379 经 `_build_explorer_services(agent, db)` 构造 `OntologyRetrievalService(repository, provider.embedding)`，**当前没传 db**） |
| `POST /agents/{agent_id}/ontology/query/stream`（探索器 SSE） | `api/routes/ontology.py:398-438`（:417 `evidence = await retrieval.retrieve(...)`） | 探索器流式 | 同上 |
| `ChatPipeline._run_ontology` | `chat_pipeline.py:534-552`（:540 `_build_ontology_answer_service`；:543 `evidence = await ontology_service.retrieval.retrieve(tenant_id=..., agent_id=resolved_agent_id, question=message)`） | ChatPipeline hybrid/ontology_neo4j 引擎分支 | **`self.db`** ✓（ChatPipeline 构造时注入），但 `_build_ontology_answer_service`（:84-93）构造 `OntologyRetrievalService(repository, embedding, chat)` **没传 db** |

**改法约束**：`OntologyRetrievalService.__init__`（`retrieval_service.py:30-40`）需多收一个 `db`，并在 `_extract_search_terms` 内部 `try: reg.resolve_prompt("knowledge","ontology_term_extractor", tenant_id, agent_id) except: fallback to constant`。但 `_extract_search_terms(question)` 当前签名只收 question，需要改为收 `tenant_id`/`agent_id` 或从 `self` 读 — 由于 `retrieve()` 已有 tenant_id/agent_id，建议改 `_extract_search_terms(question, tenant_id, agent_id)`，或在 `__init__` 存 db 后从 `retrieve()` 把 ids 透传给 `_extract_search_terms`。

**3 处构造 `OntologyRetrievalService`**：`ontology.py:327`（探索器）、`chat_pipeline.py:92`（ChatPipeline）、（无第三处；graph 节点走 `extract_terms_node` 不用此 service）。改 `__init__` 加 `db` 参数须同步更新这两处构造点。

#### 1.7 `ONTOLOGY_RESPONSE_PROMPT`（`answer_service.py:81`）

| 调用方函数 | 调用方文件:行 | 链路 | db 可达性 |
|---|---|---|---|
| `render_response_prompt(graph_evidence, message, task_type, prompt_text=None)` | `answer_service.py:183-200`（:194 `template = prompt_text or ONTOLOGY_RESPONSE_PROMPT`；:196 `template.format(graph_json=..., question=message, task_type=task_type)`） | 渲染函数 | **函数本身无 db**（纯函数） |
| `OntologyAnswerService.generate_answer(graph_evidence, message, task_type, prompt_text=None)` | `answer_service.py:229-276`（:246 `rendered_prompt = render_response_prompt(graph_evidence, message, task_type, prompt_text=prompt_text)`） | 答案生成 | **`self` 当前没有 db** |
| `OntologyAnswerService.answer_for_task(...)` | `answer_service.py:208-227`（:222 `return await self.generate_answer(graph_evidence=..., message=..., task_type=..., prompt_text=prompt_text)`） | 答案 + 检索一体 | 同上（**且该方法当前无外部调用方**） |
| `POST /agents/{agent_id}/ontology/query` | `api/routes/ontology.py:375-395`（:384 `result = await answer_service.generate_answer(graph_evidence=evidence, message=req.query, task_type=EXPLORER_TASK_TYPE)`） | 探索器同步 | **`db: DbSession`** ✓（:379），但 `_build_explorer_services` 构造 `OntologyAnswerService(retrieval, provider.chat)`（:328）**没传 db** |
| `POST /agents/{agent_id}/ontology/query/stream` | `api/routes/ontology.py:398-438`（:424 `result = await answer_service.generate_answer(...)`） | 探索器流式 | 同上 |
| `_build_full_context(evidence, query)` | `api/routes/ontology.py:332-338`（:335 `"system_prompt": render_response_prompt(evidence, query, EXPLORER_TASK_TYPE)`） | 探索器右栏「完整上下文」展示 | **`db: DbSession`** ✓（外层 `_build_explorer_services` 拿到），但当前 `_build_full_context` 签名不收 db |

**改法约束**：
- `OntologyAnswerService.__init__`（`answer_service.py:203-206`）需加 `db` 字段。
- `render_response_prompt` 是纯函数（无 self），需在 `generate_answer`/`_build_full_context` 调用方层先 `await reg.resolve_prompt(...)` 拿到模板字符串，再透传给 `render_response_prompt(..., prompt_text=template)`（已有的 `prompt_text` 参数就是为这种场景预留的，见 :187-188、:215、:243-244 注释）。
- **ChatPipeline 不调 `generate_answer`**（grep 命中 :543 只调 `retrieve`，不调 `generate_answer`），所以 `ONTOLOGY_RESPONSE_PROMPT` 在 ChatPipeline 路径**不触发**。

---

### 2. db 可达性总览（按链路分类）

| 链路 | 触发的 prompt | db 来源 | 是否需新透传 |
|---|---|---|---|
| **HTTP 入库 `POST /ontology/ingest`** | ENTITY、FACT、IMAGE_INTERPRET | `_do_ingest:182 bg_db`（async with session_factory）；`OntologyIngestionService.db` | 已有 db，只需透传 tenant_id/agent_id（已在 :193-194 调 `service.ingest_paths(tenant_id=..., agent_id=...)`） |
| **HTTP 探索器 `POST /ontology/query` / `/query/stream`** | _ENTITY_EXTRACTION（service 版）、ONTOLOGY_RESPONSE | `ontology.py:379 db: DbSession` 经 `_build_explorer_services` 构造服务 | 需改 `OntologyRetrievalService.__init__` / `OntologyAnswerService.__init__` 收 db；并把 tenant_id/agent_id 从 `ontology.py:381-385` 透传到 `generate_answer` |
| **对话图 ontology 路径** | _ENTITY_EXTRACTION（graph 版） | `runtime.context.get("db")`（生产入口 `dingtalk/graph_stream.py:122`、`online_conversation.py:228-233`） | 已可读，需在 `extract_terms_node` 新增 `db = runtime.context.get("db")`；tenant_id/agent_id 从 `state`（`retrieval.py:101-102`） |
| **ChatPipeline._run_ontology（hybrid/ontology_neo4j 引擎）** | _ENTITY_EXTRACTION（service 版） | `chat_pipeline.py:540-549 self.db` | 需改 `_build_ontology_answer_service:84-93` 构造 `OntologyRetrievalService` 时传 db |
| **CLI `sales-agent ingest`** | MD_OPTIMIZE（仅当 `chat_model` 给入） | `cli.py:48 db` → `KnowledgeIngestor(db=db)` | 已有 db，需改 `MDOptimizer.__init__` 或 `optimize()` 收 db/tenant_id/agent_id |
| **HTTP 知识库 `POST /knowledge/files` 类** | MD_OPTIMIZE（**当前路径死代码**，因为 :127/:276/:344 构造 KnowledgeIngestor 时**没传 chat_model**） | `db: DbSession` ✓ | 当前路径死，改 MDOptimizer 签名不破坏 |
| **`documents.py` 重建索引** | MD_OPTIMIZE（**当前路径死代码**，:45 同样没传 chat_model） | `db` ✓ | 同上 |

**CLI 的特殊情况**：CLI 入库**不传 tenant_id** 到 `KnowledgeIngestor`（`cli.py:56` 只传 `db/embedding_model/chat_model`，:62 `ingestor.ingest_directory(tenant_id=tenant, ...)`）。tenant_id 在 `ingest_directory` 调用时给，但 `KnowledgeIngestor._optimize_md` 当前签名 `(raw_content, file_path)` 不收 tenant_id/agent_id。若要让 MD_OPTIMIZE 走 registry，需把 tenant_id/agent_id 从 `ingest_directory` → `_ingest_file` → `_optimize_md` → `MDOptimizer.optimize` 一路透传。

---

### 3. 占位符分析（决定 resolve_prompt 后是否要再 `.format`）

| Prompt | 模板正文的占位符 | 字面花括号（已用 `{{ }}` 转义） | 当前调用点 | 改法提示 |
|---|---|---|---|---|
| `ENTITY_EXTRACTION_PROMPT` | `{content}` | 是（:94 `{{"entities":[{{"type":"Product",...}}]}}`） | `extractor.py:160 .format(content=chunk)` | resolve 后仍需 `.format(content=chunk)`；DB 版需保持 `{{ }}` 转义 |
| `FACT_EXTRACTION_PROMPT` | `{content}` `{entities_json}` | 是（:102 `{{"facts":[{{...}}]}}`） | `extractor.py:230 .format(content=chunk, entities_json=entities_json)` | resolve 后仍需 `.format`；两个 placeholder 都要给 |
| `IMAGE_INTERPRET_PROMPT` | 无 | 无 | 直接当 text 发 | resolve 后直接用，无需 `.format`；**最安全** |
| `MD_OPTIMIZE_SYSTEM_PROMPT` | 无 | 有 YAML frontmatter `---`（不是花括号）+ markdown 代码块 | `md_optimizer.py:109` 直接当 system content | resolve 后直接用 |
| `MD_OPTIMIZE_USER_TEMPLATE` | `{content}` | 无字面花括号 | `md_optimizer.py:100 .format(content=raw_content)` | resolve 后仍需 `.format(content=raw_content)` |
| `_ENTITY_EXTRACTION_PROMPT`（两份） | `{question}` | 是（:27 输出示例 `["福多多", "零风险承诺"]` 是方括号，不是花括号，安全） | `ontology_graph.py:62 / retrieval_service.py:52 .format(question=question)` | resolve 后仍需 `.format(question=question)` |
| `ONTOLOGY_RESPONSE_PROMPT` | `{graph_json}` `{question}` `{task_type}` | 是（:111 `{{"answer":"...","evidence":[...],"confidence":0.8}}`） | `answer_service.py:196 .format(graph_json=..., question=..., task_type=...)` | resolve 后仍需 `.format`；三个 placeholder 都要给 |

**lessons #30 陷阱提示**：上述 5 个含 `.format` 占位符的模板，**所有字面 JSON 花括号都已用 `{{ }}` 双写转义**（在源代码里就这么写）。注册到 DB 后：
- 优点：DB 版模板若照抄源代码的 `{{ }}`，`.format` 调用照常工作。
- 风险：运营在后台编辑器里**看见的是 `{{ }}` 双花括号**，若以为是笔误改成单 `{`，调用 `.format` 立刻抛 `KeyError`（lessons #30 描述的场景）。
- 缓解：调用方包一层 `format_map(SafeDict)` 兜底未知占位符；或在 `PromptRegistry` 编辑器侧做校验提示。**当前代码全部用裸 `.format`，未做兜底**。

---

### 4. `_ENTITY_EXTRACTION_PROMPT` 两份内容对比（diff 结果）

执行 `diff <(sed -n '23,28p' ontology_graph.py) <(sed -n '16,21p' retrieval_service.py)` → **exit=0，无输出**。两份 prompt 正文（不含模块顶部 22 / 13-15 行的注释 header）字节级完全相同：

```
_ENTITY_EXTRACTION_PROMPT = """从用户问题中提取用于知识图谱搜索的实体名称和关键词。
只返回 JSON 数组，不要其他内容。

用户问题：{question}

输出示例：["福多多", "零风险承诺"]"""
```

**结论**：可共用一个注册 key（建议 `("knowledge","ontology_term_extractor")`）。两个模块（`ontology_graph.py` / `retrieval_service.py`）的引用都要替换为 `await reg.resolve_prompt("knowledge","ontology_term_extractor", tenant_id, agent_id)`。

**改后是否还需要保留两份模块常量作 fallback？** 需要 — 因为 `extract_terms_node`（graph 路径）和 `OntologyRetrievalService._extract_search_terms`（service 路径）都必须有「db/tenant_id 缺失或 registry 抛 ValueError 时」的兜底（参照 `risk_check_node:96-110` 的 try/except 范式）。建议把常量集中到一个地方（例如 `prompts/ontology_term_extractor_prompt.py`）再两处 import，避免双份维护。

---

### 5. `PromptRegistry.resolve_prompt` 三级回退机制 & 未注册行为

**位置**：`src/sales_agent/services/prompt_registry.py:124-164`

**签名**：
```python
async def resolve_prompt(
    self, category: str, key: str, tenant_id: str, agent_id: str | None = None,
) -> str:
```

**三级回退**（:135-164）：
1. **Agent prompt_set 映射**（:135-142，仅当 `agent_id` 非空）：查 `Agent.prompt_set_id` → `AgentPromptSet.task_prompt_versions_json`（兼容新旧 schema，:91-106 的 `_extract_version_id`）→ `PromptVersion`。
2. **Tenant active 版本**（:144-157）：`SELECT PromptVersion WHERE tenant_id=? AND prompt_category=? AND COALESCE(prompt_key, task_type)=? AND status='active' LIMIT 1`。
3. **内置默认**（:159-162）：`get_builtin(category, key)` → 返回 `BuiltinPrompt.template`。

**未注册 (category,key) 行为**（:159-164）：
```python
builtin = get_builtin(category, key)
if builtin is not None:
    return builtin.template
raise ValueError(f"No prompt found for {category}/{key}")
```
→ **抛 `ValueError`**（不回退、不返回 None）。

**对当前 7 个 key 的含义**：
- 当前 `BUILTIN_PROMPTS`（`prompt_defaults.py:111-116`）**没有任何 `category="knowledge"` 条目**。`get_builtin("knowledge", ...)` 在第三级回退时永远返回 None。
- 因此**改造前必须**在 `prompt_defaults.py` 新增 `_knowledge_entries()` 并把 7 条 `BuiltinPrompt` 追加到 `BUILTIN_PROMPTS`，否则 tenant 未配 DB 版本时直接抛 `ValueError`。
- 调用方应同时包 try/except 兜底（参照 `prompt_resolver_helper.resolve_quick_session_prompts:65-69` 与 `risk_check_node:96-110` 的范式），db 缺失或 registry 抛错时回退到模块常量。

**`required_placeholders_for(category, key)` 行为**（`prompt_defaults.py:127-132`）：
- 若新 key 注册到 `BUILTIN_PROMPTS`，返回 `BuiltinPrompt.required_placeholders`。
- 若未注册，默认返回 `["message"]`。
- `_validate_placeholders`（`prompt_registry.py:56-71`）会在 `create_version` / `update_draft` 时校验模板含必须占位符；新建 DB 版本时若漏了 placeholder 会抛 `ValueError`。
- 因此 7 条 `BuiltinPrompt` 的 `required_placeholders` 字段必须按第 0 节表格精确填写，否则 tenant 通过 API 创建/编辑 DB 版本会被校验挡下。

**`create_version` 的 `task_type` 字段**（`prompt_registry.py:208-244`）：非 task 类时 `task_type=None`（:235），仅 task 类才填 task_type。`category="knowledge"` 属于非 task，应 `task_type=None`。

---

## 改造路径建议（仅描述事实存在的范式，非推荐）

仓库里已有的两套可照搬范式：

1. **「helper 函数集中解析」范式**（`services/prompt_resolver_helper.py`）：新增 `resolve_knowledge_prompts(db, tenant_id, agent_id) -> dict`，集中调 `PromptRegistry` 把 7 个 key 解析成 dict 返回，失败返回 `{}`。调用方拿到 None/缺失时回退常量。
2. **「调用点就地 try/except」范式**（`graph/nodes/risk_check.py:96-110`、`graph/nodes/generation.py:85-95`、`graph/retrieval/web_fallback.py:52-67`、`coach/daily_evaluator.py:591-595`）：节点内 `if db is not None and tenant_id: try: prompt = await PromptRegistry(db).resolve_prompt(...) except: pass`。

无论哪种，前置工作：
- 在 `prompt_defaults.py` 新增 `_knowledge_entries()` 返回 7 条 `BuiltinPrompt`，并追加到 `BUILTIN_PROMPTS`（:111-116）。
- 8 个原模块常量**保留**（作 fallback 兜底用），但调用点改为「先试 registry → 失败回退常量」。

---

## Caveats / Not Found

- **`OntologyAnswerService.answer_for_task`（`answer_service.py:208-227`）当前无外部调用方**（grep 仅命中定义和 :222 的内部转发）。该方法已预留 `prompt_text` 参数，是 registry 化的最自然 hook，但因为没有调用方，改它不影响生产行为。
- **`image_to_text`（`img_parser.py:73`）零调用方**：函数本身已废弃（生产中用 `ingestion_service._image_to_text_via_vision` 替代，走同步 httpx）。改 `IMAGE_INTERPRET_PROMPT` 调用方只需改 `ingestion_service.py:148,171,201,227` 这 4 处，不需要动 `image_to_text` 函数体（但其内部 :131 也用了 `IMAGE_INTERPRET_PROMPT`，若一并改造需同步）。
- **uploads.py / documents.py 的 KnowledgeIngestor 入口当前没传 chat_model** → MD 优化路径在这些端点根本不触发。改 `MDOptimizer.__init__` 加 db 参数**不会破坏这些入口**（它们构造的 KnowledgeIngestor 无 chat_model，永远走不到 `_optimize_md`）。但若未来给这些入口补 chat_model，需确保 db 也同步传入。
- **未追踪的潜在入口**：guided_flow / coach 子系统经 grep 不调用上述任何 service（仅 `chat_pipeline.py:540-549` 一处间接调 `OntologyRetrievalService.retrieve`），但若未来 coach/guided_flow 自建 ontology 调用路径需另查。
- **CLI 入库链路不传 agent_id**：`cli.py:62 ingestor.ingest_directory(tenant_id=tenant, ...)` 只给 tenant_id，没有 agent_id。若 MD_OPTIMIZE 要走 registry，`agent_id=None` 是可接受的（registry 第二级 tenant active 仍能命中），但调用方签名要允许 `agent_id=None`。
- **`graph/node_metadata.py:134` 仅是元数据登记**（用于 graph_debug 可视化），改 prompt 调用方式后建议同步更新该处的 source/note 字段（可选，不影响运行）。
- **未验证 `OntologyIngestionService` 是否会通过 e2e 测试 fixture 触发**：本次只 grep 了 `src/`，未深查 `tests/integration/` 是否有 fixture 直接构造 service 并绕过 db（若有，加 db 必填参数会破坏 e2e；建议改造前补一次 `grep -rn "OntologyIngestionService\|LLMExtractor\|OntologyRetrievalService\|OntologyAnswerService\|MDOptimizer" tests/`）。
