# Sales Agent v0 — ToB 销售陪跑 Agent 本地原型

## 项目简介

Sales Agent 是一个面向 ToB 企业销售团队的 AI 陪跑助手。它通过自然语言对话为销售人员提供情绪支持、知识查询、话术生成、异议处理和沟通复盘等能力，帮助销售团队提升效率和标准化销售质量。

v0 是本地可运行原型，支持 **HTTP API** 和 **钉钉单聊** 两种接入方式，用于验证 Agent 的核心逻辑：任务路由、延迟优化路径选择、RAG 检索、话术生成、风险检查和多租户隔离。采用 **dedicated mode** 部署（一租户一实例），确保密钥和数据隔离。

**Phase D** 增加了完整的 Pilot 验证与质量循环系统：Pilot 成功指标仪表盘、质量审查队列、反馈根因分类（9 种类别）、知识缺口生命周期追踪、评估回归测试、Prompt/RAG 变更对比、运维告警、周报自动生成和 Pilot 退出决策框架（expand/continue/remediation/stop）。管理控制台提供 6 个新增页面覆盖所有 Pilot 运营工作流。

## 核心功能

### 6 类销售任务

| 任务类型 | 说明 | 示例输入 |
|----------|------|----------|
| 情绪支持 `emotional_support` | 客户不回复、焦虑、想放弃时给行动建议 | "客户三天没回我了，这单没戏了" |
| 知识问答 `knowledge_qa` | 基于企业知识库回答产品、案例、FAQ | "我们产品的核心优势是什么？" |
| 话术生成 `script_generation` | 按场景生成温和版、推进版、简短版话术 | "帮我写一段催客户确认演示的话术" |
| 异议处理 `objection_handling` | 识别异议类型，给策略和回应话术 | "客户说太贵了，另一家便宜很多" |
| 销售复盘 `conversation_review` | 分析沟通记录，指出问题和替代表达 | "帮我复盘：客户问多少钱，我说最低8万" |
| 通用教练 `general_sales_coaching` | 通用销售建议，无法归类时兜底 | "做 ToB 销售最重要的是什么？" |

### 延迟优化 — 三级执行路径

所有请求根据复杂度自动选择 fast / standard / slow 路径，简单请求不调 LLM、不调 RAG，复杂请求完整执行并可在钉钉中发送"处理中"提示：

| 路径 | 适用场景 | 目标延迟 | 特征 |
|------|----------|----------|------|
| **Fast** | 帮助、重置、简单情绪支持、简单话术 | <1s ~ 4s | 不调 LLM router、不调 RAG、不调 LLM risk check |
| **Standard** | 异议处理、普通话术、简单复盘、通用教练 | 3s ~ 6s | 规则路由（LLM 兜底）、条件 RAG、规则风险检查 |
| **Slow** | 知识问答、长对话复盘、高风险请求 | 6s ~ 15s | 完整管道 + RAG + LLM 风险检查 + 处理中提示 |

关键优化策略：
- **规则优先，LLM 兜底**：规则匹配置信度 >= 0.75 时不调 LLM router
- **RAG 条件触发**：仅 `knowledge_qa` 必须检索，其他任务按需
- **风险检查分级**：规则检查必执行，LLM 风险检查仅在 medium/high 风险或命中风险关键词时触发
- **Provider 缓存**：同一租户复用 ModelProvider 实例
- **完整可观测性**：每请求记录各阶段耗时、LLM 调用率、RAG 跳过原因、p50/p90/p95 统计

### 知识库（Neo4j 本体引擎，生产默认）

生产默认 `KNOWLEDGE_ENGINE=ontology_neo4j`：用 Neo4j 知识图谱（Entity / Fact / Evidence / SourceDocument）
替代传统 chunk 检索。首版采用图检索 + 保守向量回退（`ONTOLOGY_VECTOR_FALLBACK=conservative`），
保持 chat 输出与现有 `summary` / `sections` 响应格式兼容。

- Neo4j 存储图谱节点，PostgreSQL 仍存储入库任务与聊天日志。
- 高风险事实进入人工复核（pending review），不进入用户可见回答。
- Agents 知识页新增入库面板（状态 / 任务 / 冲突可视化 + 启动入库）。
- 支持上传 `.md` / `.txt` / `.doc` / `.docx` / `.pdf` / `.pptx` / `.xlsx` / `.jpg` / `.png` / `.webp` / `.bmp` / `.gif`；旧版 `.doc` 经 LibreOffice 无头转 `.docx` 后解析，`.xlsx` 用 openpyxl 按 sheet 抽取，图片用视觉 LLM 解读（需 `ontology.vision_enabled: true`）。
- 「本体探索」调试页（`/agents/:id/ontology`）：三栏实时可视化检索过程 / 问答 / 喂给大模型的完整上下文（SSE 流式，复用图谱检索+回答引擎）。
- **图谱+RAG 混合并行（`KNOWLEDGE_ENGINE=hybrid` 或 `HYBRID_RETRIEVAL=true`）**：graph 路径（钉钉 Stream 生产入口）下，LangGraph `Send` fan-out 同时执行 ontology 图谱检索与 RAG 检索，结果经 state reducer（`sources: add`）合并后统一进 `evidence_gate → generate`。与「混合检索 Hybrid RRF」（向量+关键词，`retrieval.mode: hybrid`）是两个不同维度的混合，可叠加。

详见 [`docs/ontology-neo4j-ops.md`](docs/ontology-neo4j-ops.md)。

### legacy_rag（传统 Markdown chunk 检索，回退模式）

未配置 Neo4j 时（`KNOWLEDGE_ENGINE=legacy_rag`，代码默认值）走传统 chunk RAG——**生产未使用**，
保留作回退与 CLI ingest/chat/eval 路径：Markdown 标准入库（含 YAML front matter）、
按标题层级 / FAQ Q&A 对切分、pgvector 向量检索（强制 `tenant_id` 过滤）、跨租户泄漏拦截、
条件检索（按任务类型决定是否调用，跳过时记录原因）。

### 混合检索（Hybrid RRF，→ 默认 `retrieval.mode: hybrid`）

**2026-06-26 新增**：默认启用向量 + 关键词双通道 RRF 融合检索，解决纯向量召回对中文口语化/模糊查询不稳定的问题。

- **向量通道**：pgvector cosine similarity（已有，不变）
- **关键词通道**（新增）：中文 n-gram tokenize + 同义词扩展（`data/synonyms.json`，340+ 条销售领域口语→标准名映射）+ 倒排索引（text/section_title/search_keywords 三字段）+ IDF 加权打分
- **RRF 融合**：`score = vector_weight/(k+rank_v) + keyword_weight/(k+rank_k)`，参数可配（`keyword_weight=0.5, rrf_k=60`）
- **同义词覆盖**：方法论(冰山模型/破框五步/GROW...)、场景(嫌贵/拖延/竞品应对...)、痛点(培训无效/CRM替代...)、竞品(黑谷/亿量/云客...)、产品(AI教练/标准包...) 等 8 大类
- 检索模式可通过 `retrieval.mode: vector|keyword|hybrid` 切换

### 检索召回率评测（→ `scripts/run_retrieval_eval.py`）

**2026-06-26 新增**：对标 Ontology-Taishan 的 trace-driven 评测方法论。30 题明确版评测集覆盖 6 类销售场景（客户开拓/异议处理/案例复盘/竞品对比/产品定位/销售管理），计算 recall@k + MRR + 按类别汇总，支持 trace 落盘（`eval/rounds/traces/round_XX/`）+ Markdown round 报告。

```bash
python scripts/run_retrieval_eval.py --tenant taishan --mode hybrid --round 01
```

### MD 优化预处理（→ `retrieval.md_optimization_enabled: true`）

**2026-06-26 新增**：入库前用 LLM 自动增强 Markdown 文档，注入完善的 YAML frontmatter、`search_keywords`（5-15 个检索关键词/口语化表达）、FAQ `## Q:` 标准化标记、正文首段检索锚点摘要。默认关闭，开启后向量+关键词双通道均受益于增强后的元数据。

### 图片/扫描件 AI 视觉解读（→ `ontology.vision_enabled: true`）

**2026-06-26 新增**：支持图片格式直接上传入库（jpg/png/webp/bmp/gif），用视觉 LLM（默认 qwen-vl-plus）将图片转为结构化文本描述后进入实体/事实抽取 → Neo4j 入库链路。同时 PDF 解析自动检测扫描件空白页并走视觉解读。默认关闭。

### 风险检查

三阶段检查，9 种风险类型，4 种处置动作，支持规则和 LLM 两级检查：

```
用户输入预检 → 检索来源检查 → 模型输出后检
                     ↓（条件化）
              LLM 风险复核（仅 medium/high 风险或命中风险关键词时触发）
```

| 风险类型 | 默认动作 | 示例 |
|----------|----------|------|
| 价格承诺 `price_commitment` | block | "保证最低价" |
| 交付承诺 `delivery_commitment` | block | "保证一周上线" |
| 夸大承诺 `unsupported_claim` | rewrite | "保证业绩翻倍" |
| 竞品攻击 `competitor_attack` | rewrite | "竞品都是骗人的" |
| 跨租户泄漏 `cross_tenant_leakage` | block | 输出了其他企业资料 |
| 折扣承诺 `discount_commitment` | warn | "一定可以给折扣" |

### 多租户隔离

- **Dedicated mode**（默认）：一租户一容器，密钥完全隔离
- 所有数据表按 `tenant_id` 强过滤
- 向量检索 SQL 层 + 应用层双重 tenant 校验
- 请求 `tenant_id` 必须匹配实例 `TENANT_ID`

### API Key 安全

- 只存 `api_key_ref: "env:MODEL_API_KEY"`，不存明文
- 日志、错误响应、debug 响应只展示 `api_key_fingerprint`（sha256 前 8 位）
- Agent 执行层通过 `TenantRuntime` 抽象访问模型，不直接读环境变量
- `.env` 文件不入 git，`secrets/` 目录不入 git

### 多轮对话

- 三层上下文：短期消息窗口（默认 4 轮）+ 会话摘要记忆 + 当前请求 context
- 8 小时无消息自动过期
- 支持重置命令："新话题" / "清空上下文" / "/reset"
- 连续指令检测："继续优化上一版" / "基于刚才那版改"
- 摘要按阈值更新：仅在消息轮数或历史字符数超阈值时刷新

### 钉钉单聊集成

- 支持 **Stream（WebSocket 常驻连接）** 和 **HTTP 回调** 两种模式
- **流式互动卡片**（Stream 模式，`dingtalk.streaming_enabled: true`）：**文字 / 语音 / 图片**统一走打字机流式卡片；语音/图片先显示「正在识别…」过渡卡片（ASR 转写 / 视觉解读，需 `dingtalk.media_enabled: true`），转写完成后无缝接到回答流式。识别失败则在原卡片 finalize 友好提示。
- 慢任务处理中提示：超过 5 秒自动发送"我正在结合资料整理，稍等一下。"
- 消息速率限制（每用户每分钟 / 每天 / 每租户）
- Markdown 格式回复

### 统一引导流程（Guided Flows）

**2026-07-06 新增**：访前准备、访后复盘、小赢欣赏、卡点破框四大引导流程通过统一
LangGraph `online_graph` 路由，取代遗留的 `quick_session_graph` 和 `daily_eval_graph`
占位图。

| 流程 | 触发词 | 说明 |
|------|--------|------|
| 访前准备 `visit_preparation` | "访前准备" | 三步问答生成客户作战卡 |
| 访后复盘 `post_visit_review` | "访后复盘" | 三步问答输出拜访复盘卡 |
| 小赢欣赏 `small_win_appreciation` | "小赢欣赏" | 三步问答记录小赢并给激励 |
| 卡点破框 `breakthrough` | "卡点破框" | 三步问答诊断并推荐破框方案 |

**关键设计：**
- 所有流程共享同一个图结构（`graph/guided_flow/graph.py`），通过 state 中的
  `active_flow` 字段区分流程类型
- 每次用户回答推进一个阶段（flow_stage: question_n → question_n+1 → card），
  完成后回到普通 Chat
- 新触发词中途打断当前流程，旧状态丢弃
- `退出` 关键词随时返回普通 Chat
- 钉钉 Stream 路径通过 `graph_stream.py` 中的 `online_graph` 路由，HTTP 路径通过
  `agent.py` 中的 `resolve_graph` 分派
- **图注册表**：`graph/registry.py` 统一 `GRAPH_REGISTRY`，
  含 `online`（统一在线会话）、`guided-flow`（引导流程）、`chat`（Chat 子图）。ontology 检索不再是独立子图，
  其步骤（extract_terms → graph_query → [vector_fallback] → compact_evidence）由
  `retrieve_node` 直接内联调用，证据回流主 `generate_node` 统一生成（保证 PromptRegistry
  的 system/task prompt 对 ontology 路径同样生效）

**配置与回退：**
```
GUIDED_FLOWS_ENABLED=true    # 启用统一引导流程（默认）
GUIDED_FLOWS_ENABLED=false   # 路由所有文本到普通 Chat 管道
```
设置 `GUIDED_FLOWS_ENABLED=false` 并重启实例后，所有消息走普通 Chat Graph。
进行中的引导状态保存在进程内存中，重启或自然日切换后丢弃。

**遗留行为说明：**
- `quick_sessions` 表保留但不再写入（入口已全部转向 Guided Flow handlers）
- Daily Evaluation 继续通过 `DailyEvaluationService` 和 scheduler 运行，
  与引导流程无关

### 有界意图路由（Bounded Intent Routing / Topic Management）

**2026-07-06 新增**：话题生命周期管理、上下文解析与证据路由，让 Online Graph
能够跟踪用户当前话题、处理指代消解、区分"继续当前话题"与"开始新话题"，
并根据意图决定是否需要知识检索。

#### 核心概念

| 概念 | 说明 |
|------|------|
| **ConversationTopic** | 表示用户当前关注的话题，包含 summary、current_goal、key_entities |
| **Topic 生命周期** | 活跃话题 30 分钟无消息自动关闭，关闭后 24 小时内可显式恢复（见 [Memory Runbook](docs/runbooks/short-term-memory.md)）|
| **PostgreSQL 持久化** | Online Graph 执行状态通过 PostgreSQL LangGraph checkpointer 持久化，跨进程重启/Worker 变更不丢状态 |
| **Stable Thread ID** | `online:<tenant>:<agent>:<channel>:<user>`（无日期组件），同用户始终映射到同线程 |
| **Turn Lock** | PostgreSQL advisory lock（`pg_advisory_xact_lock`）序列化相同线程轮次 |
| **Context Resolver** | LLM 判定本轮消息与当前话题的关系：continue / revise / switch / new / ambiguous |
| **Evidence Router** | 根据解析后的查询，决定意图类型和知识检索策略（required / optional / none） |
| **Clarification** | 歧义消息时触发澄清循环，最多 2 次尝试，超 2 次默认新建话题（安全阀） |

#### Router 模式

**ContextDecision**（上下文解析输出）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `turn_relation` | str | continue / revise / switch / new / ambiguous |
| `standalone_query` | str | 重写的独立查询（指代消解后） |
| `retained_entities` | list[str] | 从当前话题继承的实体 |
| `retracted_goals` | list[str] | 当前话题中撤回的目标 |
| `confidence` | float | 解析置信度 |
| `reason_code` | str | 解析原因代码 |

**EvidenceDecision**（证据路由输出）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `intent` | str | 任务类型（knowledge_qa / objection_handling / 等） |
| `knowledge_policy` | str | required / optional / none |
| `knowledge_scope` | list[str] | 知识范围限定 |
| `retrieval_query` | str | 可选，用于检索的查询重写 |
| `confidence` | float | 意图分类置信度 |
| `reason_code` | str | 路由原因代码 |

**知识策略说明：**

| policy | 含义 | 行为 |
|--------|------|------|
| `none` | 不需要知识检索 | 直接生成回答 |
| `optional` | 尝试检索，无结果不影响回答 | 并行检索后合并 |
| `required` | 必须检索知识，否则返回失败提示 | 阻塞直到检索完成 |

#### 话题生命周期

```
创建 → [30分钟无活动] → 自动关闭 → [24小时内] → 显式恢复
  │                              │
  └── continue / revise / switch ┘         ── 超过24小时 → 不可恢复，创建新话题
```

- **活跃超时**：30 分钟（`topic_routing.idle_minutes`）
- **恢复窗口**：24 小时（`topic_routing.restore_hours`）
- **澄清尝试**：最多 2 次（`topic_routing.max_clarification_attempts`），超限默认新建话题

#### 澄清解析器

当 Context Resolver 无法判断用户意图时（`turn_relation="ambiguous"`），
触发澄清流程，使用无 LLM 的命令映射（前缀匹配）：

| 用户输入 | 行为 |
|----------|------|
| "继续" / "接着刚才" / "然后" / "接着说" | 继续当前话题 |
| "新问题" / "换个话题" / "reset" | 新建话题 |
| "取消" / "算了" / "忘了" | 取消当前话题 |

超出精确命令时使用 LLM 兜底（`CLARIFICATION_RESOLVER_PROMPT`）。

#### 配置与回退

```yaml
topic_routing:
  enabled: false             # 默认关闭
  idle_minutes: 30           # 话题活跃超时（分钟）
  restore_hours: 24          # 关闭话题恢复窗口（小时）
  max_clarification_attempts: 2  # 最大模糊回答澄清尝试次数
```

```
TOPIC_ROUTING_ENABLED=false  # 默认，跳过上下文解析和证据路由，直连 Chat 管道
TOPIC_ROUTING_ENABLED=true   # 启用话题管理、上下文解析、证据路由
```

设置 `TOPIC_ROUTING_ENABLED=false`（默认）并重启实例后，所有消息绕过
话题层直接进入 Chat 管道，`ConversationTopic` 表无写入。

**已知限制：**
- 长期用户 Memory（跨会话、跨天用户画像和行为偏好）超出本设计范围，
  需要单独的设计文档覆盖授权、溯源、TTL、用户检查/删除、矛盾消解和选择性检索。
- 话题状态保存在 `conversation_topics` 表中，重启容器后仍需从 DB 恢复。
- 当前版本无显式管理层 UI 查看/管理话题。

### Governed long-term memory

Spec 2 adds explicit DingTalk remember/correct/forget and policy-gated inferred memory candidates. It is disabled by default via `long_term_memory.enabled=false`; enable in staging before production rollout. Run `scripts/run_long_term_memory_gate.sh` against an isolated test database before deployment.

### User profile memory

Spec 3 projects active atomic memories into evidence-backed user profiles and recalls only task-relevant profile items into a bounded `USER_MEMORY_CONTEXT` block. It is disabled by default via `user_profile_memory.enabled=false`. Run `scripts/run_user_profile_memory_gate.sh` against an isolated test database before deployment.

## 快速开始

### 1. 启动数据库

```bash
docker compose up -d postgres
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入：
#   TENANT_ID=taishan
#   MODEL_API_KEY=sk-your-key
#   MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
#   MODEL_CHAT_MODEL=qwen-plus
#   MODEL_EMBEDDING_MODEL=text-embedding-v3
```

### 3. 安装依赖并启动

```bash
pip install -e . -i https://mirrors.aliyun.com/pypi/simple/
PYTHONPATH=src uvicorn sales_agent.main:app --port 8000
```

### 4. 创建租户并导入知识库

```bash
# 创建租户
curl -X POST http://localhost:8000/tenants \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "taishan", "name": "泰山兄弟"}'

# 导入知识库
PYTHONPATH=src python3 -m sales_agent.cli ingest \
  --tenant taishan \
  --path data/sales-agent/tenants/taishan/documents
```

### 5. 开始使用

**CLI 方式：**

```bash
PYTHONPATH=src python3 -m sales_agent.cli chat --tenant taishan
```

**HTTP API 方式：**

```bash
curl -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "taishan",
    "user_id": "user_001",
    "message": "客户说太贵了怎么回？"
  }'
```

**响应示例：**

```json
{
  "conversation_id": "a1b2c3d4",
  "tenant_id": "taishan",
  "task_type": "objection_handling",
  "answer": {
    "summary": "这是价格异议，建议先澄清比较口径，再回到价值和风险。",
    "sections": [
      {"title": "异议类型", "content": "价格异议"},
      {"title": "客户可能顾虑", "content": "..."},
      {"title": "回应策略", "content": "..."},
      {"title": "推荐话术", "content": "「...」"},
      {"title": "风险提醒", "content": "不要直接承诺折扣"}
    ]
  },
  "sources": [
    {"document_id": "doc_001", "title": "产品价值说明", "score": 0.82}
  ],
  "risk": {"level": "medium", "flags": ["price_related"], "action": "warn"},
  "debug": {
    "task_type": "objection_handling",
    "route_confidence": 0.92,
    "latency_ms": 1800,
    "provider": "openai_compatible",
    "api_key_ref": "env:MODEL_API_KEY",
    "api_key_fingerprint": "a1b2c3d4",
    "path": "standard",
    "path_reason": "default_standard_path",
    "stage_latency_ms": {"validation": 5, "routing": 35, "generation": 1500, "risk_check": 120},
    "llm_calls": {"router": false, "main": true, "risk": false, "summary": false},
    "retrieval_info": {"called": false, "reason": "path_does_not_need_retrieval"}
  }
}
```

## CLI 命令

```bash
# 导入知识库
sales-agent ingest --tenant taishan --path data/.../documents

# 交互式聊天
sales-agent chat --tenant taishan --user user_001

# 运行评估集
sales-agent eval --tenant taishan --file eval/smoke_test.jsonl
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 进程存活检查（不依赖模型） |
| GET | `/ready` | 就绪检查（模型配置、数据目录） |
| GET | `/diagnostics/model` | 模型连通性检查（管理员手动触发） |
| GET | `/health/latency-stats` | 延迟统计（p50/p90/p95，按路径分组） |
| POST | `/tenants` | 创建/更新租户 |
| GET | `/tenants/{tenant_id}` | 获取租户信息 |
| POST | `/tenants/{tenant_id}/documents/ingest` | 导入知识库文档 |
| POST | `/agent/chat` | Agent 聊天（核心端点） |
| GET | `/conversations/{conversation_id}` | 查看会话详情 |
| POST | `/feedback` | 提交点赞/点踩反馈 |

### 管理后台 API

所有管理后台端点位于 `/tenants/{tenant_id}/admin/` 前缀下：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/conversations` | 列出租户会话 |
| GET | `/admin/runs/{run_id}` | Agent 运行追踪 |
| GET | `/admin/runs/{run_id}/steps` | 运行步骤详情 |
| GET | `/admin/documents` | 文档列表 |
| GET | `/admin/feedback/summary` | 反馈汇总 |
| GET | `/admin/latency-stats` | 延迟统计 |
| GET | `/admin/model-calls` | 模型调用日志 |
| GET | `/admin/workflow-metrics` | 工作流质量指标 |
| GET | `/admin/pilot-metrics` | 获取 Pilot 成功指标 |
| GET | `/admin/pilot/status` | 获取 Pilot 退出决策状态 |

### Pilot 验证与质量循环 API（Phase D）

所有 Pilot 端点位于 `/tenants/{tenant_id}/admin/pilot/` 前缀下，共 36 个端点：

| 功能域 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| **Pilot 指标** | GET | `/pilot-metrics` | DAU/WAU、任务分布、反馈比、延迟、错误率 |
| **质量审查** | POST | `/review-queue/scan` | 扫描负反馈、高风险、模型错误等自动入队 |
| | GET/POST/PATCH | `/review-queue[/{id}]` | 审查队列 CRUD + 状态流转 |
| **反馈分类** | PATCH | `/feedback/{id}/classify` | 根因分类（9 种类别） |
| | GET | `/feedback-categories/summary` | 按分类聚合统计 |
| **知识缺口** | GET/POST/PATCH | `/knowledge-gaps[/{id}]` | 缺口生命周期：open→document_needed→uploaded→verified |
| | POST | `/knowledge-gaps/{id}/link-document` | 关联文档 |
| **评估回归** | GET/POST | `/eval-suites` | 评估套件管理 |
| | POST | `/eval-suites/{id}/run` | 运行评估 |
| | GET | `/eval-runs[/{id}/results]` | 运行结果查看 |
| **变更对比** | POST | `/compare` | 前后评估结果对比 |
| **运维告警** | GET/POST/PATCH | `/alert-rules[/{id}]` | 告警规则管理 |
| | POST | `/alert-rules/seed-defaults` | 一键创建默认规则 |
| | GET | `/alerts` | 活跃告警列表 |
| | POST | `/alerts/{id}/acknowledge` | 确认告警 |
| | POST | `/alerts/{id}/resolve` | 解决告警 |
| | POST | `/alerts/evaluate` | 立即评估所有规则 |
| **Pilot 报告** | POST | `/reports/generate` | 生成周报/月报（JSON + Markdown） |
| | GET | `/reports[/{id}]` | 报告列表和详情 |
| **Pilot 决策** | GET | `/status` | 自动分类：expand/continue/remediation/stop |

## 配置

### 环境变量（`.env` 文件）

| 变量 | 说明 | 必填 |
|------|------|------|
| `DEPLOYMENT_MODE` | 部署模式（dedicated） | 是 |
| `TENANT_ID` | 当前实例绑定的租户 ID | 是 |
| `TENANT_NAME` | 租户名称 | 否 |
| `MODEL_PROVIDER` | 模型供应商（openai_compatible） | 是 |
| `MODEL_API_KEY` | 模型 API 密钥 | 是 |
| `MODEL_BASE_URL` | 模型 API 地址 | 是 |
| `MODEL_CHAT_MODEL` | Chat 模型名称 | 是 |
| `MODEL_EMBEDDING_MODEL` | Embedding 模型名称 | 是 |
| `VECTOR_COLLECTION` | 向量集合名 | 否 |
| `DATA_DIR` | 数据目录 | 否 |
| `LOG_DIR` | 日志目录 | 否 |

### YAML 配置（`config/default.yaml`）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `model.api_key_ref` | API key 引用格式 | `env:MODEL_API_KEY` |
| `model.base_url` | 模型 API 地址 | - |
| `model.chat_model` | Chat 模型 | `qwen-plus` |
| `model.embedding_model` | Embedding 模型 | `text-embedding-v3` |
| `retrieval.top_k` | 检索返回数量 | 5 |
| `retrieval.min_score` | 最低相似度阈值 | 0.35 |
| `retrieval.chunk_size` | 文本块大小（字） | 700 |
| `retrieval.chunk_overlap` | 块重叠（字） | 120 |
| `retrieval.mode` | 检索模式（vector/keyword/hybrid） | `hybrid` |
| `retrieval.keyword_weight` | 关键词在 RRF 中的权重 | 0.5 |
| `retrieval.rrf_k` | RRF 常数 k | 60 |
| `retrieval.md_optimization_enabled` | MD 优化预处理开关 | `false` |
| `ontology.vision_enabled` | 图片视觉解读开关 | `false` |
| `ontology.vision_model` | 视觉模型 | `qwen-vl-plus` |
| `conversation.history_turns` | 多轮历史轮数 | 4 |
| `conversation.expire_after_hours` | 会话过期时间 | 8 |
| `source_display.max_visible_sources` | 销售可见来源数 | 3 |
| `latency.enabled` | 延迟优化开关 | `true` |
| `latency.default_path` | 默认执行路径 | `standard` |
| `latency.long_message_chars` | 长消息阈值（字） | 3000 |
| `latency.long_history_chars` | 长历史阈值（字） | 5000 |
| `latency.processing_notice_after_seconds` | 慢任务提示阈值（秒） | 5.0 |
| `path_router.enable_fast_path` | 启用 fast path | `true` |
| `path_router.enable_slow_path_notice` | 启用慢任务提示 | `true` |
| `path_router.llm_router_confidence_threshold` | LLM router 调用阈值 | 0.75 |
| `path_router.clarify_confidence_threshold` | 追问/澄清阈值 | 0.45 |
| `guided_flows.enabled` | 统一引导流程开关 | `true` |
| `guided_flows.timezone` | 引导流程时区（自然日重置） | `Asia/Shanghai` |
| `topic_routing.enabled` | 有界意图路由（话题管理/上下文解析/证据路由） | `false` |
| `topic_routing.idle_minutes` | 话题活跃超时（分钟） | `30` |
| `topic_routing.restore_hours` | 关闭话题恢复窗口（小时） | `24` |
| `topic_routing.max_clarification_attempts` | 最大模糊回答澄清尝试次数 | `2` |
| `retrieval.parallel_enabled` | 并行 Ontology + RAG 检索（Send fan-out） | `true` |

## Docker 部署

### 开发模式（只启动数据库）

```bash
docker compose up -d postgres
```

### 生产部署 — Dedicated Mode（多租户，每租户独立容器）

仓库内置一份**已纳入 git 追踪的模板** `secrets/example.env`（仅此一个文件在 `secrets/` 下被追踪，
真实租户 env 仍被 `.gitignore` 忽略）。该文件与根目录 `.env.example` 均为**软链**，
共同指向唯一权威真源 `deploy/tenant.env.example`——改模板只需改这一处，且会随 CI/CD
自动发布到无源码目标机（每次部署由 `deploy-remote.sh` 落盘到目标机 `secrets/example.env`）。

```bash
# 1. 从模板为每个租户创建 .env 文件（example.env 本身永远不会被部署）
cp secrets/example.env secrets/taishan.env
# 编辑 secrets/taishan.env 填入密钥和配置（替换所有 <...> 占位符）

chmod 600 secrets/*.env

# 2. 运行部署脚本：自动发现 secrets/*.env，箭头键选择租户（或 --env / --yes）
scripts/deploy-release.sh
#    - 交互式菜单自动排除 example.env 模板
#    - 新租户自动登记到 deploy/tenants.json（确认后），分配空闲端口
#    - 校验占位符 / 端口冲突 → 渲染 compose → 起服务 → 健康检查

# 单租户免确认：   scripts/deploy-release.sh --env taishan.env
# CI 全量部署：    scripts/deploy-release.sh --yes

# 3. 检查状态
scripts/check-all-tenants.sh deploy/tenants.json
```

### Key 轮换

```bash
# 1. 更新 secrets/taishan.env 中的 MODEL_API_KEY
# 2. 重启该租户容器
docker compose --profile taishan-split restart
# 3. 验证
curl http://localhost:8101/diagnostics/model
```

### 无源码目标机：CI 自动同步运维脚本

`deploy/deploy-targets.json` 里 `method=image-deploy` 的目标机（如 test）走无源码部署：CI 只推
Docker 镜像、不推源码。为让这些机器也能停服务/查健康，每次 `git push origin main` 触发的 CI
会在部署前自动把两个**自包含**脚本同步到目标机 `${dir}/scripts/`（由 `scripts/ci-fanout.sh`
的 image-deploy 分支用 `tar | ssh` 完成）：

- `scripts/stop-tenant.sh` — 停止/移除该机某租户容器（`--tenant <id>`，或交互式菜单）
- `scripts/check-tenant.sh <tenant_id> <api_port>` — 检查容器 + `/health` `/ready`

容器名规则 `sales-agent-<id>-<role>` 与本机一致，目标机上 `bash scripts/stop-tenant.sh` 开箱即用。
详见 `deploy/DEPLOY_USAGE.md`。

### CI/CD 分支部署策略（main vs dev）

| push 分支 | 部署目标 | 重建方式 |
|---|---|---|
| `main` | prod3 + prod2 + test **全部三台** | 仅应用容器（api/stream/worker/frontend）force-recreate；postgres/neo4j 不动，DB migration 由应用启动时 alembic 自动跑 |
| `dev` | **仅本机 prod2**（taishan + taishankaifa2） | 应用容器 force-recreate；**test/prod3 不动** |

- `main` 由 `.gitea/workflows/deploy.yml` 触发（构建 `:SHA`+`:latest`，fan-out `deploy/deploy-targets.json` 全部目标）。
- `dev` 由 `.gitea/workflows/deploy-dev.yml` 触发（构建 `:dev`，fan-out `deploy/deploy-targets-dev.json`，仅 prod2）。
- `FORCE_RECREATE_APP=1` 让 CI 部署强制重建应用容器（即使镜像 digest 未变）；手动 `scripts/deploy-release.sh` 不设该变量则只重建镜像变化的容器。
- ⚠️ test/prod3 只跟 main。dev 分支领先 main 时，push main 会把 dev 部署过的 prod2 回退到 main 版本——发布前先把 dev merge 进 main。

## 项目结构

```
sales-agent/
├── docker-compose.yml              # Docker Compose（PostgreSQL + 多租户 Agent）
├── pyproject.toml                  # Python 项目配置
├── .env.example                    # 环境变量模板（软链 → deploy/tenant.env.example）
├── .gitignore                      # Git 忽略（含 .env 和 secrets/）
├── config/
│   └── default.yaml                # 全局默认配置（含延迟优化配置）
├── scripts/
│   └── init-db.sql                 # 数据库初始化（pgvector 扩展）
├── secrets/                        # 租户密钥（不入 git）
│   └── example.env                 #   租户 env 模板（软链 → ../deploy/tenant.env.example，唯一权威真源）
├── data/sales-agent/tenants/       # 租户知识库
│   └── taishan/documents/    #   产品介绍、FAQ、案例
├── eval/
│   └── smoke_test.jsonl            # 冒烟评估集（12 条）
├── src/sales_agent/
│   ├── main.py                     # FastAPI 入口（启动校验、中间件）
│   ├── cli.py                      # CLI 入口（ingest/chat/eval）
│   ├── core/
│   │   ├── config.py               # 配置加载（YAML + 环境变量 + 延迟优化配置）
│   │   ├── database.py             # 异步数据库引擎（SQLAlchemy）
│   │   ├── exceptions.py           # 错误码和异常类（11 种错误码）
│   │   ├── secret_resolver.py      # 密钥解析（api_key_ref）+ 脱敏工具
│   │   └── tenant_runtime.py       # 租户运行时抽象（TenantRuntime）
│   ├── models/                     # 数据库模型（19 张表）
│   │   ├── tenant.py               #   tenants
│   │   ├── document.py             #   documents, source_files, document_chunks (+pgvector)
│   │   ├── conversation.py         #   conversations, messages, summaries, retrieval_logs
│   │   ├── tenant_model_config.py  #   tenant_model_configs（只存 key ref）
│   │   ├── model_call_log.py       #   model_call_logs（脱敏日志）
│   │   ├── feedback.py             #   feedbacks（含 categories_json 根因分类）
│   │   ├── review_item.py          #   review_items（质量审查队列）
│   │   ├── knowledge_gap.py        #   knowledge_gaps（知识缺口追踪）
│   │   ├── eval.py                 #   eval_suites, eval_cases, eval_runs, eval_run_results
│   │   ├── alert.py                #   alert_rules, alerts（运维告警）
│   │   └── pilot_report.py         #   pilot_reports（Pilot 报告）
│   ├── api/
│   │   ├── schemas.py              # Pydantic 请求/响应模型（含 Phase D schemas）
│   │   ├── deps.py                 # 依赖注入
│   │   └── routes/
│   │       ├── health.py           #   /health, /ready, /diagnostics/model, /health/latency-stats
│   │       ├── tenants.py          #   /tenants
│   │       ├── documents.py        #   /tenants/{id}/documents/ingest
│   │       ├── agent.py            #   /agent/chat（委托 Online Graph）
│   │       ├── conversations.py    #   /conversations/{id}
│   │       ├── feedback.py         #   /feedback
│   │       ├── admin.py            #   /tenants/{id}/admin（监控、指标、工作流）
│   │       ├── prompts.py          #   /tenants/{id}/prompts（Prompt 版本管理）
│   │       ├── uploads.py          #   /tenants/{id}/knowledge（知识上传）
│   │       └── pilot.py            #   /tenants/{id}/admin/pilot（36 端点，Phase D）
│   ├── services/                   # 核心业务逻辑（22 个模块）
│   │   ├── online_conversation.py  #   生产入口：invoke_online_turn（Online Graph 封装）
│   │   ├── latency_stats.py        #   p50/p90/p95 百分位统计收集器
│   │   ├── request_validator.py    #   请求校验
│   │   ├── tenant_resolver.py      #   租户配置解析 + 模型 provider 构建（含缓存）
│   │   ├── task_router.py          #   任务路由（规则优先 + LLM 兜底 + 调用率日志）
│   │   ├── retriever.py            #   RAG 检索（条件触发 + skip reason 日志）
│   │   ├── agent_executor.py       #   Agent 执行（prompt 选择 + LLM 调用）
│   │   ├── risk_checker.py         #   风险检查（规则 + LLM 两级检查）
│   │   ├── response_formatter.py   #   响应格式化 + 销售文本输出
│   │   ├── conversation_logger.py  #   会话日志记录（含延迟和 LLM 调用率日志）
│   │   ├── context_loader.py       #   多轮上下文管理（阈值摘要更新）
│   │   ├── knowledge_ingestor.py   #   知识导入编排
│   │   ├── prompt_registry.py      #   Prompt 版本管理
│   │   ├── feedback_service.py     #   反馈持久化
│   │   ├── run_tracer.py           #   Agent 运行追踪
│   │   ├── pilot_metrics_service.py       #   [Phase D] Pilot 成功指标
│   │   ├── review_queue_service.py        #   [Phase D] 质量审查队列
│   │   ├── feedback_classification_service.py  #   [Phase D] 反馈根因分类
│   │   ├── knowledge_gap_service.py       #   [Phase D] 知识缺口追踪
│   │   ├── eval_runner_service.py         #   [Phase D] 评估回归测试
│   │   ├── change_comparison_service.py   #   [Phase D] Prompt/RAG 变更对比
│   │   ├── alert_service.py               #   [Phase D] 运维告警
│   │   ├── pilot_report_service.py        #   [Phase D] Pilot 报告生成
│   │   └── pilot_status_service.py        #   [Phase D] Pilot 退出决策
│   ├── llm/                        # 模型 Provider 抽象
│   │   ├── base.py                 #   ChatModel / EmbeddingModel 接口
│   │   └── openai_compatible.py    #   OpenAI 兼容实现（重试 + 批量 embedding）
│   ├── rag/                        # RAG 管道
│   │   ├── markdown_parser.py      #   Markdown + YAML front matter 解析
│   │   ├── chunker.py              #   按标题/FAQ/段落切分
│   │   └── vector_store.py         #   pgvector 写入和余弦相似度检索
│   ├── prompts/                    # Prompt 模板
│   │   ├── system.py               #   通用系统约束
│   │   ├── emotional_support.py    #   情绪支持
│   │   ├── knowledge_qa.py         #   知识问答
│   │   ├── script_generation.py    #   话术生成（固定三版）
│   │   ├── objection_handling.py   #   异议处理
│   │   ├── conversation_review.py  #   沟通复盘
│   │   └── general_coaching.py     #   通用教练
│   └── integrations/               # 外部集成
│       └── dingtalk/               #   钉钉单聊集成
│           ├── processor.py        #     事件处理（委托 invoke_online_turn + 卡片进度）
│           ├── stream_client.py    #     WebSocket 常驻连接
│           ├── message_sender.py   #     消息发送（重试 + token 缓存）
│           ├── message_renderer.py #     Markdown 渲染
│           ├── config.py           #     钉钉配置
│           └── ...                 #     签名验证、用户映射、限流等
└── tests/
    ├── conftest.py                 # 测试配置
    └── unit/                       # 160+ 单元测试
        ├── graph/                  #   Chat/Online/Guided Flow 图测试（170 个）
        ├── test_validation_node.py #   快速命令（help/reset）
        ├── test_latency_tracker.py #   耗时追踪
        ├── test_latency_stats.py   #   延迟统计
        ├── test_processing_notice.py # 处理中提示生命周期
        ├── test_chunker.py         #   Markdown 解析 + 切分
        ├── test_task_router.py     #   任务路由（13 个场景）
        ├── test_risk_checker.py    #   风险检查（13 个场景）
        ├── test_request_validator.py # 请求校验
        ├── test_response_formatter.py # 响应格式化
        ├── test_secret_resolver.py #   密钥解析 + 脱敏 + 租户匹配
        └── dingtalk/               #   钉钉集成测试
            ├── test_command_parser.py
            ├── test_event_receiver.py
            ├── test_message_renderer.py
            ├── test_rate_limiter.py
            ├── test_signature.py
            ├── test_tenant_guard.py
            └── test_user_mapper.py
```

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.10+ |
| HTTP 框架 | FastAPI + Uvicorn |
| CLI | Typer |
| 数据库 | PostgreSQL 16 (Docker) + pgvector 扩展 |
| ORM | SQLAlchemy 2.0 (async) |
| 向量检索 | pgvector 余弦相似度 |
| LLM SDK | OpenAI Python SDK（兼容国内模型服务） |
| 钉钉 SDK | dingtalk-stream |
| 包管理 | pip + pyproject.toml |
| 容器编排 | Docker Compose（profiles 支持多租户） |
| 管理控制台 | React 18 + TypeScript + Ant Design 5 + Vite |
| 状态管理 | TanStack React Query |
| 路由 | React Router v6 |

## 运行测试

```bash
# 单元测试
PYTHONPATH=src pytest tests/unit/ -v

# 集成测试（Phase D）
PYTHONPATH=src pytest tests/integration/test_pilot_api.py -v
```

当前 280+ 测试（含 unit/graph/guided-flow/topic-routing/dingtalk/pipeline-parity）全部通过，覆盖：
- Markdown 解析和切分（8 个）
- 任务路由 6 种类型 + 优先级 + 置信度 + 调用率日志（13 个）
- 路径路由 fast/standard/slow + 配置开关 + 风险关键词（22 个）
- 风险检查输入/来源/输出 + LLM 风险 + 组合（13 个）
- Chat Graph 快速命令（8 个）
- 延迟统计（百分位收集器）
- 请求校验（6 个）
- 响应格式化（7 个）
- 密钥解析 + 指纹 + 脱敏 + 租户匹配（17 个）
- 钉钉集成（命令解析 + 事件 + 渲染 + 限流 + 签名 + 用户映射）（34 个）
- **统一引导流程**（图结构 + 触发器 + 四种流程 handler + online 图 + 图对比 + 配置开关）（39 个）
- **Online Conversation 图**（路由节点 + 图构建 + checkpoint + registry + 检索节点 + 快速命令 + 风险）（28 个）
- **引导流程在线路由**（HTTP + 钉钉 Stream 路由 + 多轮 + 退出 + 打断 + 重复事件保护）（33 个）
- **话题管理**（生命周期、过期/恢复、分支/修正、澄清循环、幂等性）（25 个）
- **上下文路由节点**（context_resolution、evidence_routing、图级 resolved/clarify/guided-bypass 路径）（14 个）

## Demo 数据

项目包含演示租户：

| 租户 | 知识库文档 | 场景 |
|------|-----------|------|
| `taishan` | 产品介绍、FAQ（10 个 Q/A）、客户案例 | AI 销售助手产品 |

冒烟评估集（`eval/smoke_test.jsonl`）包含 12 条测试用例，覆盖 5 类任务 + 风险拦截场景。

## 数据库表（19 张）

| 表 | 说明 | Phase |
|----|------|-------|
| `tenants` | 租户配置 | A |
| `documents` | 文档元数据 | A |
| `source_files` | 原始文件记录 | A |
| `document_chunks` | 文本块 + pgvector embedding | A |
| `conversations` | 会话记录 | A |
| `conversation_messages` | 消息明细 | A |
| `conversation_summaries` | 会话摘要记忆 | A |
| `retrieval_logs` | 检索日志 | A |
| `tenant_model_configs` | 租户模型配置（只存 api_key_ref） | A |
| `model_call_logs` | 模型调用脱敏日志 | A |
| `prompt_versions` | Prompt 版本管理 | B |
| `ingestion_jobs` | 知识导入任务 | B |
| `agent_runs` | Agent 运行追踪 | B |
| `agent_run_steps` | Agent 运行步骤 | B |
| `feedbacks` | 用户反馈（含根因分类 categories_json） | B/D |
| `review_items` | 质量审查队列 | D |
| `knowledge_gaps` | 知识缺口追踪 | D |
| `eval_suites` + `eval_cases` | 评估套件和用例 | D |
| `eval_runs` + `eval_run_results` | 评估运行和结果 | D |
| `alert_rules` | 告警规则 | D |
| `alerts` | 告警记录 | D |
| `pilot_reports` | Pilot 报告 | D |

## 文档

| 文件 | 说明 |
|------|------|
| `docs/PILOT_ONBOARDING_GUIDE.md` | Pilot 新租户上线指南 |
| `docs/PILOT_OPERATING_PLAYBOOK.md` | Pilot 日常运营手册（5 个工作流） |

## 管理控制台

基于 React + TypeScript + Ant Design 的管理控制台，提供完整的运营和 Pilot 管理界面：

| 页面 | 路径 | 说明 |
|------|------|------|
| 仪表盘 | `/dashboard` | 系统运营指标总览 |
| **Pilot 指标** | `/pilot` | DAU/WAU、任务分布、反馈比、Pilot 退出决策 |
| 对话记录 | `/conversations` | 会话列表和详情 |
| 知识库 | `/knowledge` | 文档上传和导入管理 |
| Prompt 管理 | `/prompts` | Prompt 版本管理和预览 |
| 工作流质量 | `/workflow` | 工作流任务质量指标 |
| 反馈管理 | `/feedback` | 反馈列表和汇总 |
| **质量审查** | `/review` | 审查队列、自动扫描、状态流转、反馈分类 |
| **知识缺口** | `/gaps` | 缺口生命周期管理、文档关联 |
| **Eval 回归** | `/eval` | 评估套件管理、运行触发、结果对比 |
| **运维告警** | `/alerts` | 告警规则 CRUD、活跃告警管理 |
| **Pilot 报告** | `/reports` | 周报/月报生成、Markdown 下载 |
| Pilot 就绪 | `/readiness` | 上线就绪检查 |

> **粗体** 标注为 Phase D 新增页面。

### Prompt 全层解耦（2026-06-22）

所有层 prompt（task / system / router / risk / coach / web / knowledge）已统一纳入 DB 版本管理（**33 个内置槽位**；2026-07-07 补注册 `router/context_resolver` + `router/clarification_resolver` + 7 个 `knowledge/*`（入库实体/事实/图片抽取、MD 优化、检索提词、图谱回答），并让 router 与 knowledge 两类 prompt 运行时都走 PromptRegistry（db 可选默认 None，空则回退常量，单测零改动），后台编辑即时生效；前端 Prompt 管理页补全 `web`/`knowledge` 分类（修复 web 漏显 bug）；2026-07-06 迁入福多多旧 prompt 并新增 `web/web_analysis`）：

- 运营在后台改任意层 prompt **即时生效**（主链路 + 钉钉 + CLI），无需改代码重部署。此前仅主 Web 链路接 DB，钉钉（主要生产渠道）和 CLI 直接用代码常量。
- **Agent 级 Prompt 绑定 UI**（`/agents/:id/prompts`）从只读改为可编辑：为每个 Agent 切换/解绑各层 prompt 版本（后端 `PUT /agents/{id}/prompts/bindings/{category}/{key}`）。
- `PromptEditPage` 支持创建 system/router/risk/coach 类 prompt + 占位符提示。
- 内置 prompt 清单经 `GET /tenants/{id}/prompts/builtin` 暴露。
- 引入 Alembic 管理 DB schema 变更（`alembic upgrade head`）。
- 详见 [`changelog/2026-06-22.md`](changelog/2026-06-22.md)。

## 更新日志

本项目的功能升级记录在 `changelog/` 目录（按日期）：

| 日期 | 摘要 |
|------|------|
| [2026-07-10](changelog/2026-07-10.md) | **LLM 调用参数配置化（temperature/max_tokens 收进 YAML）+ md_optimizer bug 修复**：24 个 LLM 调用点的硬编码 `temperature`/`max_tokens` 收进 `config/llm_call_defaults.yaml`（新增 `src/sales_agent/llm/call_params.py` loader，启动加载+校验+内存缓存，运行时 `get_call_params(call_site)` 零 IO）。**值不变纯结构重构**（YAML 填原字面量，`test_load_real_defaults_file_all_keys` 48 断言核对），无 DB/migration/后台 API/前端，仅开发者改、git 管版本。间接 helper（extractor `_generate_with_retry`、coach_flows `_llm_generate`）加 `call_site` 参数；`response_format` 等 3 处留代码；health 温度改显式；media 走原生 SDK create。顺带修 `md_optimizer.py:146` 的 `.chat()` latent crash（ChatModel 无此方法→`await .generate()`+删 response 解析）、6 个漏网 prompt 进 BUILTIN_PROMPTS。全量 1214 passed/7 pre-existing failed（asyncpg 不同 loop + FakeExtractor 签名漂移，merge-base 复跑确认零新增失败），最终全分支 review merge-ready。spec/plan 见 `docs/superpowers/`。 |
| [2026-07-10](changelog/2026-07-10.md) | **Scenario Coach 钉钉渲染缺分组标题「二、再应对」**：scenario_coach 命中（非流式）后钉钉端渲染缺「二、再应对：判断完，对应着打」这行分组标题（其余正文完整），影响所有「需判断型」场景（Q01/Q02/Q03/Q04…）。根因：手册里该父标题下面直接接 `####` 子标题、自身无独立正文，被 `loader._split_sections` 解析成 `content=""` 的 section 后，被 `[s for s in sections if s.content]` 一刀切丢弃，永不进入 `answer_dict`；`format_text_output` 的 `if title and content` 是第二层同样过滤。修复：loader 改只按 title 过滤保留分组父标题；formatter 对空 content 的 section 作纯标题行输出。新增 2 个先红后绿回归测试，scenario 全套 43 单测通过；Q04 端到端输出含该标题且四个子节正文齐全。一度怀疑流式截断——实为 loader 数据层 bug。 |
| [2026-07-10](changelog/2026-07-10.md) | **DeepEval HTML 报告渲染 markdown**：HTML 报告是客户端 JS 渲染，「完整回答」/「参考答案」原先只做尖括号转义、无 markdown→html，导致 `##`/列表/表格/加粗显示成源码。修复：内嵌 marked v12.0.2（`eval/vendor/marked.min.js`，报告保持自包含可离线），新增 `mdToHtml`（先转义 `&`/`<` 防 XSS、保留 `>` 识别 blockquote，再 `marked.parse` gfm+breaks），两字段改调它并补 `.md` CSS。node 实跑验证标题/列表/**表格**/代码块/blockquote 全渲染、`<script>` 被转义不可执行。仅影响 eval HTML 报告，不动生产链路、MD/CSV 报告。详见 changelog。 |
| [2026-07-09](changelog/2026-07-09.md) | **CI/CD 分支部署策略：main 重建全部、dev 仅本机**：dev 不再碰 test（`deploy-targets-dev.json` 删 test 条目 + `deploy-dev.yml` 删 build deploy image 步骤），dev push 只重建本机 prod2；main push fan-out 三台（prod3+prod2+test）仅应用容器 force-recreate（postgres/neo4j 不动，由 `FORCE_RECREATE_APP` + `config --services` 黑名单过滤 infra 实现）；修 `ci-fanout.sh` 把 dev 部署代码错拉成 main 的硬编码 bug（按 `DEPLOY_BRANCH` 拉对应分支）。⚠️ main 落后 dev 50 commit，push main 前建议先 merge dev→main。详见 changelog。 |
| [2026-07-09](changelog/2026-07-09.md) | **钉钉语音/图片支持流式互动卡片**：此前仅文字走流式卡片，语音/图片被硬门禁（`stream_client.py` `message_type == "text"`）挡在非流式路径。修复：① 抽出 `_should_stream` 门禁，受支持媒体类型在 `media_enabled` 时也进流式；② `_handle_streaming` 内接 `DingTalkMediaAdapter.to_agent_text`，先开「正在识别…」过渡卡片再 ASR/视觉转写，转写文本喂给同一流式图；③ `handle_dingtalk_stream_via_graph` 加可选 `card_id` 复用过渡卡（单卡 UX，文字路径零回归）；④ 修 CardSender 不可用兜底透传真实 `message_type`+媒体字段；⑤ 识别失败在原卡 finalize 友好提示。新增 12 个单测（门禁/转写/失败兜底/复用卡），dingtalk 111 + 相关 41 单测全过。遗留独立隐患：生成节点 `generate_node` 走非流式 `generate()`，token 级打字机可能未真正逐字（`graph_stream.py` 满屏调试日志印证），见 changelog。 |
| [2026-07-09](changelog/2026-07-09.md) | **记忆评估与生产运维套件（Spec 4 整体交付）**：统一多轮场景 schema（42 场景）+ 版本化报告 + 确定性指标四组（隔离/安全 fail-closed、turn/topic、long-term-memory、recall-profile）+ 确定性 model double 驱动真实 Online Graph + 七个 CLI 模式（unit-memory / graph-multiturn / model-multiturn / dingtalk-staging / compare / online-sample / promote-trace）+ fail-closed 发布门禁 + 在线采样（非阻塞）+ promote-trace 反馈闭环 + 两张新表（`memory_eval_traces` / `promoted_regressions`）。新增运维入口 `scripts/run_memory_eval_gate.sh` 与操作手册 [`docs/runbooks/memory-evaluation.md`](docs/runbooks/memory-evaluation.md)；**不改动在线 Graph 请求路径**。 |
| [2026-07-08](changelog/2026-07-08.md) | scenario-coach: E2E 修复 + Durable Short-Term Memory（持久化短期记忆）：PostgreSQL LangGraph 持久化、稳定 Thread ID、Turn-scoped reset、Advisory lock、Bounded topic restore、标准/流式路径统一、钉钉重复投递静默、Reset 状态机、24 场景评估门控。详见 [`docs/runbooks/short-term-memory.md`](docs/runbooks/short-term-memory.md)。**+ 统一租户 env 模板**（同日）：三份漂移模板收敛为唯一权威真源 `deploy/tenant.env.example`（按最全的 `taishan.env` 补齐 `DINGTALK_MEDIA_*`/`EMBEDDING_*`/`BOCHA_API_KEY` 等），`secrets/example.env`+`.env.example` 改软链；`deploy/Dockerfile` COPY + `deploy-remote.sh` 每次部署幂等落盘到无源码机 `secrets/example.env`——根治「无源码机看不到新增 env 变量」（fuduoduo scenarios 不生效的深层因）。 |
| [2026-07-07](changelog/2026-07-07.md) | **新增「会话历史」页：真实钉钉会话 checkpoint 只读回看（不 fork / 不 replay / 不动生产）**: LangGraph time-travel 此前只覆盖图调试 `debug:` 测试 run（真实会话被 `graph_debug._ensure_debug_thread` 403）。新增独立 router `conversation_history.py`（prefix `/agents/{agent_id}/history`），**仅 3 个 GET**（会话列表 / checkpoint 时间轴 / 单点 state），用 `conversation_id` 作 thread_id 调 `aget_state_history` 读回钉钉生产写入的 PG checkpoint；复用 graph_debug helper（DRY）。前端新增 `ConversationHistoryPage`（会话列表 + 手动输入兜底 + CheckpointDAG 时间轴 + JsonNode state viewer，均只读复用），挂 AgentLayout 子路由 `/agents/:agentId/history` + 侧边栏菜单。严格只读：AST/grep 证零 POST/aupdate_state/astream，不改 graph_stream/online_graph，无 DB migration。粒度=online graph 节点边界。后端 import + 前端 tsc 通过 + 接入 grep 持久化验证；真实会话端到端待部署。风险：state 含敏感数据，端点无强鉴权（同 graph_debug），依赖内网 |
| [2026-07-07](changelog/2026-07-07.md) | **钉钉端置信度引用改造（删正文自报 + 文末编号来源列表）**: ① 删 `prompts/system.py:88`「库类别→固定百分比」自报规则（原竞品库=80% 等硬编码），换为系统文末统一附来源——正文不再出现 `(置信度NN%)`。② 新增 `integrations/dingtalk/citation.py` 的 `format_citation_block`（ontology→知识图谱 / web→网络搜索 / 其余→知识库，≤3 条按 title 去重），`graph_stream` finalize 时拼到正文末尾（方案 A 代码层拼接，LLM 不参与，标题原样精确）。③ `generate_node` 透传 `state.sources` 进 `answer_dict`；web 兜底 sources 补 `source_type=web`。ontology confidence=0.8 硬编码保留在 metadata 不显示。dingtalk 93 + graph 49 单测通过；端到端 stream 验证待部署 |
| [2026-07-07](changelog/2026-07-07.md) | **清理 prompt 死代码 + 补注册 2 个 resolver + 三个 router prompt 运行时走 PromptRegistry + web 兜底默认启用**: ① 删零调用死代码 `services/context_loader.py::maybe_update_summary`（含内联 `summary_prompt`，全仓 grep 零残留）+ `ontology/img_parser.py::image_to_text`（保留 `IMAGE_INTERPRET_PROMPT`/`is_image_file`/`get_image_mime_type`，`ingestion_service` 仍走自有 `_image_to_text_via_vision`）。② `WebSearchConfig.enabled` 默认 False→True；无 `BOCHA_API_KEY` 时 `web_fallback_and_analyze` 仍安全 return None（`retrieve_node` 微开销可接受）。③ `BUILTIN_PROMPTS` 补注册 `router/context_resolver` + `router/clarification_resolver`（24→26），`/builtin`/`/effective` API 自动列出无需前端改；新增 `prompt_resolver_helper.resolve_router_prompt(db,key,tenant_id,agent_id,default)` helper（db 空或 resolve 抛错回退常量），三个 router service（`resolve_context`/`resolve_clarification`/`route_intent_evidence`）加可选 `db/tenant_id/agent_id` 参数、运行时走三级回退让后台编辑生效；`context_resolution_node` + `evidence_routing_node` 从 state 取 ids、ctx 取 db 传入；30 个单测不传 db 走旧常量路径零改动全过。④ `ChatPipeline` 强化 deprecation docstring（生产零调用，仅 `eval/deepeval_*.py` 依赖，待迁移后整文件删除），新建 `tasks/todo_eval_migrate_to_graph.md` 记录迁移工作。topic_routing 默认 OFF → 生产零行为变化。29 个 router service 单测通过 |
| [2026-07-06](changelog/2026-07-06.md) | **福多多旧 Prompt 迁移到新 24 槽位 + 生产路径联网兜底**: 旧项目 8 条 prompt 等价迁入——`system_constraint` 整体替换为福多多旧 `system_prompt`（剥钉钉单聊不适用的「群聊信息边界」段）；`knowledge_qa`/`ONTOLOGY_RESPONSE_PROMPT` 规则区写入旧 qdrant_llm 共享 13 原则（保 schema）；`task_router`/`evidence_router` 借旧 `intent_router` 的 follow_up 短追问/情绪识别/「宁可多搜不可漏搜」+触发词表（改造适配新 12 类输出）；**新增 `web/web_analysis` prompt（23→24）**，迁移旧 `web_llm`（置信度 high/med/low + 来源 URL + 主语/日期敏感）。**生产 graph 路径补联网兜底**：`retrieve_node` 在 ontology+rag 都空时调 Bocha → 独立 LLM（`WEB_ANALYSIS_PROMPT`）分析 → `analysis` 拼进 `ontology_context_text`，`generate` 节点零改动；受 `web_search.enabled` 守卫，无 `BOCHA_API_KEY` 不触发。`_ENTITY_EXTRACTION_PROMPT` 及其余 task/risk/coach/入库 prompt 不动（旧无等价）。275 相关单测通过；生产 stream 验证待部署 |
| [2026-07-06](changelog/2026-07-06.md) | **图调试页保留侧边栏 + 图最大化放大**: 回退上一轮「脱离侧边栏+Header」方案（用户反馈要保留侧边栏），保留侧边栏+Header 导航不变，graph-debug 路由仅去 Content margin/padding 让图在主区最大化；mermaid svg `width/height:100%!important` 撑满容器按 `preserveAspectRatio(xMidYMid meet)` 比例最大化。图区=侧边栏右侧全部宽 × `100vh-64px`。前端 build exit 0 |
| [2026-07-06](changelog/2026-07-06.md) | **图调试页真正占满整个屏幕（脱离侧边栏+Header）**: graph-debug 路由 early return 全屏 Layout（无 Sider 无 Header），图区=100vw×100vh；顶部轻量工具栏带「← 返回控制台」按钮保留导航；mermaid svg 用 `width/height:100%!important` 撑满容器按比例最大化（覆盖 mermaid 内联 max-width）。修上一轮只去 Content margin/padding 但侧边栏 208px+Header 64px 仍占空间、svg 不主动放大致图居中偏小的不足。前端 build exit 0 |
| [2026-07-06](changelog/2026-07-06.md) | **图调试页 Mermaid 图占满屏幕 + 执行轨迹可折叠**: graph-debug 路由突破 AgentLayout `<Content>` 的 margin/padding（白卡片），让 Mermaid 图占满 header 以下全部空间；执行轨迹 36vh→32vh 并支持一键折叠（折叠后图占满 100%）；修 `.gd-container` 高度漏算 Content padding(48px) 致溢出 + tabpane 非 flex 容器致 `gd-mermaid-wrap` 被图例/对照表挤出裁切的 bug。节点中文小字功能说明（`graph/node_metadata.py` 单一事实源，20 节点）经 mmdc 真渲染验证：SVG 用 `<foreignObject>`+HTML `<font color='#888'>` 渲染灰色小字，三图全命中。`node_metadata.py` 补 `git add` 纳入跟踪（原未跟踪，防 CI 镜像缺失 import 崩溃）。前端 tsc+vite build 通过，graph_debug 33 单测通过 |
| [2026-07-06](changelog/2026-07-06.md) | **Ontology 检索内联化（修复竞品问答绕过主生成 prompt）**: 删除 `ontology-retrieval` 子图，检索步骤（extract_terms→graph_query→[fallback]→compact）由 `retrieve_node` 直接内联调用，**永不 `skip_generation`**，ontology 证据回流主 `generate_node`，PromptRegistry 的 system/task prompt（markdown 格式/销售话术/RAG 综合）对所有检索路径生效——修复"问竞品直接端出图谱原始结果"的 bug。`GRAPH_REGISTRY` 收敛为 `online`+`guided-flow`。**+ Graph Registry & Ontology Cache**（前序）: 统一 `GRAPH_REGISTRY`，`_get_ontology_subgraph` 进程级 LRU 缓存，删除遗留 coach 图（`quick_session_graph`, `daily_eval_graph`）和 `coach/quick_session.py`（DB 会话服务，入口已全部转向 Guided Flow handlers）**+ Bounded Intent Routing**: 话题生命周期管理（30min 过期/24h 恢复）、上下文解析 ContextDecision、证据路由 EvidenceDecision、澄清循环（2 次上限）、`TOPIC_ROUTING_ENABLED=false` 回退开关。**+ 图调试缺 chat 修复 & release_resolution 死代码清理**: `GRAPH_REGISTRY` 补注册 `chat`（被 online 当子图用但迁移时漏注册，致前端图调试缺图）；删孤儿节点 `release_resolution.py` + 5 个无人读写的 state 字段（两头没接、与 chat_pipeline 内联版重复）。**+ 图调试节点计数修复 & 子图节点视觉区分**: 修 `list_graphs` 节点数恒为 2 的 bug（mermaid 文本解析启发式不适用，改读 `len(g.nodes)`/`len(g.edges)` 真实值）；`online` 图的 `chat`/`guided_flow` 子图入口节点加橙色加粗边框（双信号识别：`CompiledStateGraph` 实例 + id 命中 `GRAPH_REGISTRY`），区别于普通节点。**+ 图调试节点中文注解 & 页面布局上下重构**: 普通节点（非 start/end/子图）label 下方加中文小字功能说明（`_NODE_DESCRIPTIONS` 收录 20 节点，从 docstring 提炼）；图调试页从左右分栏改上下布局，Mermaid 图占满主区、执行轨迹移到下方 36vh |
| [2026-07-06](changelog/2026-07-06.md) | **🚨 DB schema 漂移修复（prod3 钉钉 stream crash 根因）**: prod3 stream 容器虽 Up 但每条消息都报 `InFailedSQLTransactionError`，同镜像在 prod2 正常。根因：`init_db` 原顺序 `create_all→alembic upgrade`，当 migration 同时含 `create_table`+`add_column`（如 0011 新建 `conversation_topics` + 给 `conversation_messages` 加 `topic_id`）时，`create_all` 抢先建好新表 → upgrade 撞 `DuplicateTableError` → 触发 `stamp head` 兜底 → **跳过同 migration 的 `add_column`**，形成「版本号=head 但列缺失」的幽灵漂移。**修复①** 新增 `0012_backfill_skipped_columns` 幂等 migration（`ADD/CREATE INDEX IF NOT EXISTS`）补齐 prod3 缺失的 53 列 + 6 索引（topic_id 等），列定义对齐 prod2 权威库，完整环境跑为 no-op；**修复②** `init_db` 调换为 `upgrade→create_all` 顺序修根因（前向），`_run_auto_migrations` 兜底日志改为打印完整异常 + 「add_column 被跳过须 backfill」警告。临时 pgvector 库验证新库路径不回归（71 表齐全）。**+ CI/CD DB 变更流程建议**：部署后做 schema 一致性校验（对比 alembic_version 与实际列）、migration 预演、api 容器先 upgrade 再起 stream。**✅ 已落地（schema 一致性校验）**：新增 `scripts/check_schema_consistency.py`（以 ORM `Base.metadata` 为基准对比 DB 实际列，缺即 exit 1——不依赖 `alembic_version`，stamp 撒谎也抓得住）+ `post-deploy-schema-check.sh`（等 `/health` 200 = `init_db` 完成 → `docker exec <tenant>-api` 跑校验），嵌入 image-deploy（`deploy-remote.sh` inline）与 deploy-release（step 11b）两条链路，漂移即部署失败。详见 changelog「CI 部署后 DB schema 一致性校验（落地）」 |
| [2026-07-06](changelog/2026-07-06.md) | **route_task / check_risk 节点接入 LLM + 图调试区分 LLM 节点 & 标注 prompt**: 两个图节点原是纯规则，对应 prompt（`TASK_ROUTER_PROMPT`/`RISK_CHECK_PROMPT`）只在 service 层 LLM 兜底路径用、未接进图节点。**接入**：节点改 `async def + runtime: Runtime`，按 `enable_llm_router`/`enable_llm_risk_check` 两个 feature flag（默认 False）灰度调 service 层 LLM；LLM 路由失败回退规则，LLM 风控失败回退规则结果（绝不静默放行）；LLM 路径补 `apply_evidence_policy_guard` 对齐规则路径。**前置修复**：合并 `task_router.py` 未提交的 LLM 路由修复（`_KeepMissingDict` 修 DB prompt 花括号崩 + `_extract_first_json` 修嵌套 JSON 截断——原 LLM 路由实际是坏的）；`risk_checker.check_llm_risk` 同步修同款 JSON bug；`_merge_risk_results` 从 chat_pipeline 搬到 risk_checker 公开复用。**图调试**：新增 `graph/node_metadata.py` 集中声明 22 节点类型/是否 LLM/对应 prompt（单一事实源，LangGraph tags 不从 `get_graph().nodes` 暴露故用映射表）；`/graphs` 端点加 `nodes`/`prompt_map` 结构化字段 + mermaid `llmNode` 蓝色高亮；前端图调试加图例（纯函数灰/LLM 蓝/子图橙）+ 可折叠「节点↔Prompt 对照表」，删 `_NODE_DESCRIPTIONS` 重复字典改用 node_metadata。**验证**：107+ 单测通过，flag 默认 False 时 graph 路径行为不变（24 集成测试通过），前端 tsc+vite build 通过；生产 stream 验证待部署 |
| [2026-07-02](changelog/2026-07-02.md) | **+ Knowledge Evaluation Optimization Loop**（知识评估优化迭代闭环）：20 张新表、4 个 migration、确定性归因引擎、LangGraph 优化工作流、PG-leased Worker、Sandbox 隔离构建、REST API（14 端点）、前端知识迭代工作区（6 面板）、CLI（9 命令）。+ Graph Debug 时间旅行：`/run` 接 `AsyncPostgresSaver` + `debug:` 前缀 thread_id 隔离并回传；新增 `GET .../threads/{tid}/checkpoints`（节点快照链）与 `.../checkpoints/{cid}/state`（单点完整 state）两个只读端点（强制 `debug:` 前缀，非调试 thread 403）；前端 `GraphDebugPage` 加 checkpoint 时间轴 + 自写 `JsonNode`（零新依赖）+ localStorage 历史 run 下拉。实测 langgraph>=1.2 的 `metadata.writes` 不存在，改用 `snapshot.tasks[*].name` 作节点标签。**+ A2 fork**（同日）：`POST .../state`（改 state）+ `POST .../replay`（从 checkpoint 重跑后半段）+ `JsonNode` 编辑模式 + fork 流程；**顺带修 A1 `/run` 的 SSE tuple bug**（`astream` list-mode 返回 tuple，原按 dict 解 → /run 只发 error）；实测 `aupdate_state` 不传 as_node 即可、config 须含 `checkpoint_ns=""`、`metadata.parents` 为空。**+ A3 分支树**（同日）：`list_checkpoints` 加 `parent_checkpoint_id`（实测 `snapshot.parent_config` 给精确父血缘，非启发式重建）+ 前端 `reactflow` DAG（替换线性 `Steps`、dagre 布局、自定义节点、点节点复用 viewer），新依赖 `reactflow+dagre`（bundle +68kB gzip）。 **+ eval 全机可用**（同日，[`task`](../.trellis/tasks/07-02-eval-on-all-servers/)）：新增 `scripts/run-eval.sh`（无源码机 `docker exec` 进 `<tenant>-api` 容器跑 eval，conversation 默认容器内 `--app-url http://127.0.0.1:8000`）+ `run_retrieval_eval.py` 默认 ground_truth 路径修复（`scripts/`→`eval/`）+ `ci-fanout.sh` image-deploy 分支 tar 同步 `run-eval.sh` + `deploy/eval.env.example` 模板 + README 场景 9 + `tests/unit/test_run_eval.sh` 冒烟；**实测 `.dockerignore` 的 `*.md` 只匹配根级、不递归**，`eval/questions.md` 本就在镜像，撤销原计划的 dockerignore 改动（见 lessons #23）。 **🚨 P0 Hotfix**（同日）：移除 `chat_pipeline.py`/`task_router.py`/`agent_executor.py`/`retriever.py` 的 5 处 deepeval `@observe` 装饰器——根因是 `@observe` 退出时序列化嵌套对象迭代活 `__dict__` 触发 `RuntimeError: dictionary changed size during iteration`，导致**全站 `/agent/chat` 500**（prod2/prod3/test 所有租户）；prod 未配 Confident key 时 trace 算了全丢弃、纯负债；移除后 taishan 实测 200 恢复（见 lessons #25，待正式部署到其余机器）。 |
| [2026-06-26](changelog/2026-06-26.md) | 知识库（ontology_neo4j）支持上传 `.doc`（LibreOffice 无头转 `.docx`）与 `.xlsx`（openpyxl 按 sheet 抽取）：扩展上传白名单、`_read_content` 新增两分支（抽出 `_docx_text` 复用）、前端 accept 放开、Dockerfile 装 `libreoffice-writer`、pyproject 加 `openpyxl`；移除遗留演示假数据 `tenant_demo_b`（企业云盘产品，与真实业务无关；数据库 43 表零数据、`tenants` 表未注册、eval 不依赖）+ 清理从未部署的 `tenant-b` 部署模板/文档引用（`tenants.test.json`、`deploy-release.sh`、`deployment-roles.md`）；保留跨租户安全测试 fixture（`tenant_b`）与历史日志；**清理顶层 nginx 死配置**（Traefik 已接管 SSL 终止/反代，`qiyelongxia.com.cn` 实际由共享 Traefik serve）+ 配套 `init-letsencrypt.sh`，同步清理 `.gitignore`/`.dockerignore` 死引用；前端容器内置 nginx（SPA + `/api` 代理，Traefik 路由终点）保留；**deploy/inventory 命名清理**：删 3 个死文件（`tenants.prod2/prod3/test.json` 旧版）、`tenants.hangzhou.json`→`tenants.test.json` 对齐机器身份（47.118.16.235=test）+ 修 `deploy.yml`/`ci-fanout.sh`/`deploy-targets.json`/文档引用，env 全链路动态拼接故行为等价（澄清：`deploy/tenants.json` 是 gitignored 的每机本地 inventory，非重复） |
| [2026-06-25](changelog/2026-06-25.md) | Neo4j 本体知识引擎（ontology_neo4j）：图检索 + 保守向量回退 + 高风险人工复核；双租户 dedicated 部署；前端容器化（每租户 nginx SPA）；4000 运营面板新增「环境配置」卡片（`GET /instance/config`，敏感字段点击揭示+复制）；「本体探索」三栏调试页（检索过程/问答/完整上下文，SSE 流式）；部署脚本完善：`secrets/example.env` 模板纳入 git 追踪（`.gitignore` 改 `secrets/*` + `!example.env` 反例），`deploy-release.sh` 租户发现自动排除模板、交互式箭头键选择租户；**CI/CD 接入 neo4j**：生成器渲染共享 neo4j 容器 + app `NEO4J_*` env 注入、entrypoint api 角色自动 `alembic upgrade`、`deploy-release.sh` `--env-file secrets/neo4j.env` 注入凭证、CI mirror neo4j 镜像到 registry；**钉钉快捷入口 tenant mismatch 修复**：共享域名（如 `aijiaolian.com.cn`）多租户下 Traefik 无法按 query 参数分流，快捷入口端点改为 `/integrations/dingtalk/t/{tenant_id}/...`（tenant_id 进 path 段），Traefik 按 `PathPrefix(/t/<tid>/)` 分流到各自容器，根治跨租户 403 |
| [2026-06-22](changelog/2026-06-22.md) | Prompt 全层解耦到 DB 版本管理 + 网页端「当前生效」总览直接编辑 |
