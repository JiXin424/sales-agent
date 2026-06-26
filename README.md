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
- 支持上传 `.md` / `.txt` / `.doc` / `.docx` / `.pdf` / `.pptx` / `.xlsx`；旧版 `.doc` 经 LibreOffice 无头转 `.docx` 后解析，`.xlsx` 用 openpyxl 按 sheet 抽取（含表名小标题）。
- 「本体探索」调试页（`/agents/:id/ontology`）：三栏实时可视化检索过程 / 问答 / 喂给大模型的完整上下文（SSE 流式，复用图谱检索+回答引擎）。

详见 [`docs/ontology-neo4j-ops.md`](docs/ontology-neo4j-ops.md)。

### legacy_rag（传统 Markdown chunk 检索，回退模式）

未配置 Neo4j 时（`KNOWLEDGE_ENGINE=legacy_rag`，代码默认值）走传统 chunk RAG——**生产未使用**，
保留作回退与 CLI ingest/chat/eval 路径：Markdown 标准入库（含 YAML front matter）、
按标题层级 / FAQ Q&A 对切分、pgvector 向量检索（强制 `tenant_id` 过滤）、跨租户泄漏拦截、
条件检索（按任务类型决定是否调用，跳过时记录原因）。

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
- 慢任务处理中提示：超过 5 秒自动发送"我正在结合资料整理，稍等一下。"
- 消息速率限制（每用户每分钟 / 每天 / 每租户）
- Markdown 格式回复

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

## Docker 部署

### 开发模式（只启动数据库）

```bash
docker compose up -d postgres
```

### 生产部署 — Dedicated Mode（多租户，每租户独立容器）

仓库内置一份**已纳入 git 追踪的模板** `secrets/example.env`（仅此一个文件在 `secrets/` 下被追踪，
真实租户 env 仍被 `.gitignore` 忽略）。

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

## 项目结构

```
sales-agent/
├── docker-compose.yml              # Docker Compose（PostgreSQL + 多租户 Agent）
├── pyproject.toml                  # Python 项目配置
├── .env.example                    # 环境变量模板
├── .gitignore                      # Git 忽略（含 .env 和 secrets/）
├── config/
│   └── default.yaml                # 全局默认配置（含延迟优化配置）
├── scripts/
│   └── init-db.sql                 # 数据库初始化（pgvector 扩展）
├── secrets/                        # 租户密钥（不入 git）
│   └── example.env                 #   租户 env 模板（neo4j.env.example 同级）
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
│   │       ├── agent.py            #   /agent/chat（委托 ChatPipeline）
│   │       ├── conversations.py    #   /conversations/{id}
│   │       ├── feedback.py         #   /feedback
│   │       ├── admin.py            #   /tenants/{id}/admin（监控、指标、工作流）
│   │       ├── prompts.py          #   /tenants/{id}/prompts（Prompt 版本管理）
│   │       ├── uploads.py          #   /tenants/{id}/knowledge（知识上传）
│   │       └── pilot.py            #   /tenants/{id}/admin/pilot（36 端点，Phase D）
│   ├── services/                   # 核心业务逻辑（25 个模块）
│   │   ├── chat_pipeline.py        #   共享 Chat 管道（三级路径 + 条件化步骤）
│   │   ├── path_router.py          #   路径路由（fast/standard/slow，纯规则 <1ms）
│   │   ├── latency_tracker.py      #   阶段耗时追踪（PipelineTimings）
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
│           ├── processor.py        #     事件处理（委托 ChatPipeline + 处理中提示）
│           ├── stream_client.py    #     WebSocket 常驻连接
│           ├── message_sender.py   #     消息发送（重试 + token 缓存）
│           ├── message_renderer.py #     Markdown 渲染
│           ├── config.py           #     钉钉配置
│           └── ...                 #     签名验证、用户映射、限流等
└── tests/
    ├── conftest.py                 # 测试配置
    └── unit/                       # 160+ 单元测试
        ├── test_chat_pipeline.py   #   ChatPipeline + 快速命令
        ├── test_path_router.py     #   路径路由（22 个场景）
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

当前 160+ 单元测试全部通过，覆盖：
- Markdown 解析和切分（8 个）
- 任务路由 6 种类型 + 优先级 + 置信度 + 调用率日志（13 个）
- 路径路由 fast/standard/slow + 配置开关 + 风险关键词（22 个）
- 风险检查输入/来源/输出 + LLM 风险 + 组合（13 个）
- ChatPipeline 快速命令 + 处理中提示（8 个）
- 延迟追踪 + 百分位统计（11 个）
- 请求校验（6 个）
- 响应格式化（7 个）
- 密钥解析 + 指纹 + 脱敏 + 租户匹配（17 个）
- 钉钉集成（命令解析 + 事件 + 渲染 + 限流 + 签名 + 用户映射）（34 个）

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

所有层 prompt（task / system / router / risk / coach）已统一纳入 DB 版本管理：

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
| [2026-06-26](changelog/2026-06-26.md) | 知识库（ontology_neo4j）支持上传 `.doc`（LibreOffice 无头转 `.docx`）与 `.xlsx`（openpyxl 按 sheet 抽取）：扩展上传白名单、`_read_content` 新增两分支（抽出 `_docx_text` 复用）、前端 accept 放开、Dockerfile 装 `libreoffice-writer`、pyproject 加 `openpyxl`；移除遗留演示假数据 `tenant_demo_b`（企业云盘产品，与真实业务无关；数据库 43 表零数据、`tenants` 表未注册、eval 不依赖）+ 清理从未部署的 `tenant-b` 部署模板/文档引用（`tenants.test.json`、`deploy-release.sh`、`deployment-roles.md`）；保留跨租户安全测试 fixture（`tenant_b`）与历史日志；**清理顶层 nginx 死配置**（Traefik 已接管 SSL 终止/反代，`qiyelongxia.com.cn` 实际由共享 Traefik serve）+ 配套 `init-letsencrypt.sh`，同步清理 `.gitignore`/`.dockerignore` 死引用；前端容器内置 nginx（SPA + `/api` 代理，Traefik 路由终点）保留；**deploy/inventory 命名清理**：删 3 个死文件（`tenants.prod2/prod3/test.json` 旧版）、`tenants.hangzhou.json`→`tenants.test.json` 对齐机器身份（47.118.16.235=test）+ 修 `deploy.yml`/`ci-fanout.sh`/`deploy-targets.json`/文档引用，env 全链路动态拼接故行为等价（澄清：`deploy/tenants.json` 是 gitignored 的每机本地 inventory，非重复） |
| [2026-06-25](changelog/2026-06-25.md) | Neo4j 本体知识引擎（ontology_neo4j）：图检索 + 保守向量回退 + 高风险人工复核；双租户 dedicated 部署；前端容器化（每租户 nginx SPA）；4000 运营面板新增「环境配置」卡片（`GET /instance/config`，敏感字段点击揭示+复制）；「本体探索」三栏调试页（检索过程/问答/完整上下文，SSE 流式）；部署脚本完善：`secrets/example.env` 模板纳入 git 追踪（`.gitignore` 改 `secrets/*` + `!example.env` 反例），`deploy-release.sh` 租户发现自动排除模板、交互式箭头键选择租户；**CI/CD 接入 neo4j**：生成器渲染共享 neo4j 容器 + app `NEO4J_*` env 注入、entrypoint api 角色自动 `alembic upgrade`、`deploy-release.sh` `--env-file secrets/neo4j.env` 注入凭证、CI mirror neo4j 镜像到 registry；**钉钉快捷入口 tenant mismatch 修复**：共享域名（如 `aijiaolian.com.cn`）多租户下 Traefik 无法按 query 参数分流，快捷入口端点改为 `/integrations/dingtalk/t/{tenant_id}/...`（tenant_id 进 path 段），Traefik 按 `PathPrefix(/t/<tid>/)` 分流到各自容器，根治跨租户 403 |
| [2026-06-22](changelog/2026-06-22.md) | Prompt 全层解耦到 DB 版本管理 + 网页端「当前生效」总览直接编辑 |
