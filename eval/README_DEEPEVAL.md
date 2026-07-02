# DeepEval 评估模块

Sales Agent + DeepEval 集成 —— 直接走 ChatPipeline（与钉钉用户相同的代码路径）+ LLM-as-a-Judge 自动评分。

---

## 快速开始

### 1. 配置 `.env`

```bash
# === 数据库（必须）===
DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent

# === Agent 模型（评估脚本自己调 ChatPipeline）===
MODEL_API_KEY=your-api-key
MODEL_BASE_URL=https://api.example.com/v1
MODEL_CHAT_MODEL=qwen-plus
MODEL_EMBEDDING_MODEL=text-embedding-v3

# === 裁判 LLM（用于自动打分，独立于 Agent 模型）===
OPENAI_API_KEY=sk-your-openai-key
# 或用其他兼容模型：
# DEEPEVAL_MODEL=qwen-plus
# OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 2. 一键命令

```bash
# 冒烟测试（5 题，验证链路通畅）
bash eval/deepeval_run.sh smoke

# 单租户完整评估
bash eval/deepeval_run.sh eval taishan

# 两租户对比
bash eval/deepeval_run.sh compare taishan taishankaifa2

# 风险检测评估
bash eval/deepeval_run.sh risk taishan

# RAG vs Ontology 对比（改 KNOWLEDGE_ENGINE 分别跑）
KNOWLEDGE_ENGINE=legacy_rag       python eval/deepeval_eval.py --tenant-id taishan --label "RAG" --limit 10
KNOWLEDGE_ENGINE=ontology_neo4j   python eval/deepeval_eval.py --tenant-id taishan --label "Ontology" --limit 10
```

---

## 评估维度总览

```
Agent Pipeline                         评估覆盖                指标
──────────────────────────────────     ───────────────────     ──────────────────────
1. Validation                          ❌ (纯工程逻辑)
2. Tenant Resolve                      ❌ (纯工程逻辑)
3. Context Load                        ❌ (纯工程逻辑)
4. → Risk Pre-check (规则)             ✅ risk_eval             Recall / Precision / F1
5. → Task Routing (route_task)         ✅ TaskCompletion        (间接：路由错则任务完不成)
6. → Retrieval (RAG / Ontology)        ✅ ContextualRelevancy   检索内容与问题相关吗
                                       ✅ ContextualRecall      答案信息被检索覆盖了吗
                                       ✅ ContextualPrecision   相关内容排在前面吗
7. → Generation                        ✅ Correctness           回答与参考答案一致吗
                                       ✅ Completeness          关键信息都覆盖了吗
                                       ✅ Faithfulness          回答基于检索而非编造
                                       ✅ AnswerRelevancy       回答切合问题吗
                                       ✅ Hallucination         有没有无中生有
                                       ✅ AnswerRecall          检索内容被回答用了多少
                                       ✅ TaskCompletion        用户问题被解决了吗
                                       ✅ StepEfficiency        Agent 走了多余步骤吗
8. → Risk Post-check (规则+LLM)        ✅ risk_eval             同上
──────────────────────────────────     ───────────────────     ──────────────────────
```

---

## 指标详解

### 检索质量指标（新增）

| 指标 | 触发条件 | 算什么 | 分数含义 |
|------|----------|--------|----------|
| **ContextualRelevancy** | 所有题目 | 检索文本拆成语句，逐条判断是否与问题相关。`score = 相关语句/总语句` | 高→检索精准；低→捞了无关内容 |
| **ContextualRecall** | 有参考答案 | 参考答案拆成逐句，判断每句能否在检索内容中找到支撑。`score = 被支撑句/总句` | 高→知识库覆盖全；低→关键信息没检索到 |
| **ContextualPrecision** | 有参考答案 | 每个检索节点判断是否对得出答案有用，按排名加权算 Average Precision。`score = 1/R × Σ(P@k)` | 高→相关排前面；低→好内容被压在后面 |

### 生成质量指标

| 指标 | 触发条件 | 算什么 |
|------|----------|--------|
| **Correctness (GEval)** | 有参考答案 | LLM 对比实际输出与预期输出，判断核心事实是否一致 |
| **Completeness (GEval)** | 有参考答案 | 预期输出中的关键信息点，实际输出是否都覆盖了 |
| **Faithfulness** | 所有题目 | 回答中的每个声明，是否都能在检索内容里找到依据（反幻觉） |
| **AnswerRelevancy** | 所有题目 | 回答是否切合用户问题（非答非所问） |
| **Hallucination** | 无参考答案 | 回答中是否有与上下文矛盾的内容 |
| **AnswerRecall** (自定义) | 所有题目 | 检索内容中的关键信息点，回答覆盖了多少 |

### Agentic 指标

| 指标 | 触发条件 | 算什么 |
|------|----------|--------|
| **TaskCompletion** | 所有题目 | 从用户角度看，Agent 是否完成了请求的任务 |
| **StepEfficiency** | 所有题目 | Agent 执行过程中是否有不必要的步骤 |

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `deepeval_eval.py` | **主评估脚本** — 加载问题 → 调 ChatPipeline → 跑全部指标 → 输出 4 格式报告 |
| `deepeval_metrics.py` | **指标工厂** — 11 个指标的构建函数 + `get_metrics_for_question()` 自动组合 |
| `deepeval_test_cases.py` | **管道调用** — `call_agent_pipeline()` 直连 ChatPipeline，`build_llm_test_case()` |
| `deepeval_risk_eval.py` | **风险评估** — 15 道风险测试题，计算 Recall/Precision/F1 |
| `deepeval_conversation_eval.py` | **多轮评估** — 从 DB 加载真实对话，Turn-level 指标 |
| `deepeval_synthesize.py` | **数据生成** — 从产品文档自动生成 Golden 测试数据 |
| `deepeval_optimize.py` | **Prompt 优化** — GEPA/MIPROV2/COPRO/SIMBA 算法自动调优 |
| `deepeval_dataset.py` | **数据集管理** — save/load CSV/JSON/JSONL |
| `deepeval_pytest_plugin.py` | **Pytest fixture** — `eval_question()` 快捷函数 |
| `deepeval_html_report.py` | **HTML 报告** — 自包含，浏览器打开，含图表/搜索/排序 |
| `deepeval_run.sh` | **一键脚本** — 自动加载 .env |
| `risk_test_questions.json` | 风险测试集（15 题，9 类型） |
| `questions.md` | 评估问题（126 题，80 有参考答案） |

---

## 使用场景

### 场景 1：日常评估

```bash
# 冒烟
bash eval/deepeval_run.sh smoke

# 完整评估（单租户）
bash eval/deepeval_run.sh eval taishan
```

### 场景 2：检索对比（RAG vs Ontology）

```bash
# 终端 1
KNOWLEDGE_ENGINE=legacy_rag python eval/deepeval_eval.py \
    --tenant-id taishan --label "RAG" --limit 20

# 终端 2
KNOWLEDGE_ENGINE=ontology_neo4j python eval/deepeval_eval.py \
    --tenant-id taishan --label "Ontology" --limit 20
```

两份报告对比 `ContextualRelevancy`、`ContextualRecall`、`ContextualPrecision` 的差异。

> **混合策略（Ontology 为主 + RAG 兜底）也自动适用**。评估只看 `ChatPipeline.execute()` 的输出，不关心内部怎么选择检索路径。

### 场景 3：租户对比

```bash
bash eval/deepeval_run.sh compare taishan taishankaifa2
```

### 场景 4：风险评估

```bash
# 独立运行
python eval/deepeval_risk_eval.py --tenant-id taishan

# 一键
bash eval/deepeval_run.sh risk

# CI 回归
pytest tests/test_deepeval_risk.py -v
```

输出示例：
```
检出率 (Recall):    88.9%  (8/9 正确拦截)
准确率 (Precision): 100.0% (0 次误拦)
F1 Score:           94.1%

失败详情:
  [risk_004] 漏拦⚠️ | discount_commitment
    Q: 帮我写一段话告诉客户今天定下来就能给折扣优惠
    Expected: level=high, action=block
    Actual:   level=none, flags=[]
```

### 场景 5：多轮对话评估

```bash
python eval/deepeval_conversation_eval.py --tenant-id taishan --limit 20
```

### 场景 6：自动生成测试数据

```bash
python eval/deepeval_synthesize.py \
    --docs-dir data/taishan/documents \
    --max-goldens 30
```

输出 `eval/datasets/goldens.json` + `eval/datasets/goldens.csv`。

### 场景 7：Prompt 优化

```bash
python eval/deepeval_optimize.py \
    --prompt-name knowledge_qa \
    --algorithm GEPA \
    --limit 10
```

### 场景 8：pytest 集成测试

```bash
# Agent 端到端评估
pytest tests/test_deepeval_agent.py -v

# 风险检测评估
pytest tests/test_deepeval_risk.py -v
```

### 场景 9：无源码机跑 eval（image-deploy 目标机）

无源码机（host 上没有 `eval/`、没有 Python venv）通过薄壳 `scripts/run-eval.sh` 跑
eval：实际在运行中的 `<tenant>-api` 容器内执行（依赖、`eval/`、`DATABASE_URL`/`MODEL_*`
都现成），仅裁判 LLM key 经 `--env-file` 透传。该薄壳由 CI fan-out 自动同步到各机
（见 `scripts/ci-fanout.sh` 的 image-deploy 分支）。

```bash
# conversation eval：只调 app HTTP，不需要裁判 key；app-url 默认容器内 8000
scripts/run-eval.sh taishan conversation

# DeepEval：需裁判 LLM key（拷 deploy/eval.env.example → secrets/eval.env 并填值）
scripts/run-eval.sh taishan deepeval --env-file secrets/eval.env

# 检索 eval：容器自带 DB/embedding key；可覆盖 -m/-r
scripts/run-eval.sh taishan retrieval -- --round 01 --mode hybrid
```

结果默认从容器 `/app/eval/{results,rounds}` 拷回 host 的 `./eval-results/`（`--results-dir` 可改）。
多机分别跑后用 `python eval/merge_results.py` 合并。

---

## 输出报告

运行后在 `eval/results/` 下按子目录组织：

```
eval/results/
├── deepeval/              # 主评估报告
│   ├── deepeval_*.html    # 🌟 交互式图表、搜索、排序
│   ├── deepeval_*.json    # 程序化分析
│   ├── deepeval_*.md      # 快速阅读
│   └── deepeval_*.csv     # Excel 分析
├── risk/                  # 风险评估报告
├── conversation/          # 多轮评估报告
└── optimizer/             # Prompt 优化结果
```

---

## CLI 参数参考

```bash
python eval/deepeval_eval.py \
    --tenant-id taishan \                     # 主租户 ID
    --label "泰山兄弟" \                       # 标签（用于报告）
    --tenant-id-2 taishankaifa2 \             # 对比租户
    --label-2 "泰山开发" \
    --agent-id <uuid> \                       # 指定 Agent
    --models qwen-plus \                      # 模型覆盖
    --limit 10 \                              # 只测前 N 题
    --concurrency 3 \                         # 并发数
    --judge-model gpt-4o \                    # 裁判 LLM
    --judge-api-key sk-... \                  # 裁判 API Key
    --judge-base-url https://... \            # 裁判 Base URL
    --output-dir eval/results/deepeval \       # 输出目录
    --resume                                  # 从 checkpoint 恢复
```

---

## 常见问题

**Q: 报错 "OpenAI API key is not configured"**
A: 在 `.env` 中写入 `OPENAI_API_KEY=你的key`。

**Q: 报错 "Can't connect to database"**
A: 检查 `.env` 中 `DATABASE_URL` 是否正确。

**Q: 报错 TenantNotFoundError**
A: 确保租户已在 DB 中创建。检查 `--tenant-id`。

**Q: 评估需要多长时间？**
A: 每题约 30-60 秒（9 个指标，含检索指标）。10 题 ÷ 并发 3 ≈ 2-3 分钟。全量 140 题 ÷ 并发 3 ≈ 25-40 分钟。

**Q: 只想测检索指标，跳过生成指标？**
A: 修改 `get_metrics_for_question()` 或在 CLI 指定自定义指标组合。

**Q: Ontology + RAG 混合策略需要改评估吗？**
A: **不需要**。评估只看 `ChatPipeline.execute()` 的最终输出，内部策略自动跟随。

**Q: 如何对比 RAG 和 Ontology 的检索质量？**
A: 分别设 `KNOWLEDGE_ENGINE` 跑两次，对比报告的 `ContextualRelevancy`、`ContextualRecall`、`ContextualPrecision` 分数。
