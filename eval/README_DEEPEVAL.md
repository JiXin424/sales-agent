# DeepEval 评估模块

Sales Agent + DeepEval 集成 —— LLM-as-a-Judge 自动评分系统。

## 快速开始

### 1. 一次性配置（写到 `.env` 文件）

编辑 **项目根目录的 `.env`** 文件，配置裁判模型的 API Key：

```
# === DeepEval 评估配置 ===
# 方式 A（推荐）：用 OpenAI GPT-4o 当裁判，评估最准
OPENAI_API_KEY=sk-你的openai-key

# 方式 B：用阿里云 Qwen 当裁判（注释掉上面，取消下面注释）
# OPENAI_API_KEY=你的阿里云-dashscope-key
# DEEPEVAL_MODEL=qwen-plus
# OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 方式 C：用 DeepSeek 当裁判
# OPENAI_API_KEY=你的deepseek-key
# DEEPEVAL_MODEL=deepseek-chat
# OPENAI_BASE_URL=https://api.deepseek.com/v1
```

`deepeval_run.sh` 启动时会自动加载 `.env`，不需要每次 export。

### 2. 快速验证（5 题试探，确认链路通畅）

```bash
# 确保 Agent 实例已启动
bash eval/deepeval_run.sh smoke http://localhost:8000 5
```

### 3. 单 KB 完整评估

```bash
bash eval/deepeval_run.sh eval http://localhost:8000 my_kb
```

### 4. 两套 KB 对比评估（核心场景）

```bash
bash eval/deepeval_run.sh compare \
    http://localhost:8001 legacy_rag \
    http://localhost:8002 ontology_neo4j
```

## 输出报告

运行后在 `eval/results/deepeval/` 下生成 **四种格式** 报告：

| 文件 | 打开方式 | 适用场景 |
|------|----------|----------|
| `deepeval_*.html` | 浏览器直接打开 | **🌟 推荐：交互式图表、搜索/排序、展开详情** |
| `deepeval_*.md` | VS Code / Typora / GitHub | 快速阅读、保留为文档 |
| `deepeval_*.json` | 任意 JSON 工具 | 程序化分析、喂给脚本 |
| `deepeval_*.csv` | Excel / WPS | 数据分析、透视表 |

### HTML 报告功能

- 📊 **汇总卡片**：总题数、各 KB 成功率、平均分数
- 📈 **KB 对比柱状图**：各指标可视化对比
- 📋 **逐题详情表**：
  - 搜索框：按问题文本搜索
  - KB 筛选：只看某个 KB 的结果
  - 状态筛选：只看通过/失败/错误
  - 点击表头排序
  - 点击问题文本展开，查看完整回答和指标评分理由

## 文件说明

| 文件 | 用途 |
|------|------|
| `deepeval_metrics.py` | 评估指标配置（正确性/忠实度/相关性/幻觉检测） |
| `deepeval_test_cases.py` | 从本地数据文件构建 LLMTestCase，调 Agent API |
| `deepeval_eval.py` | 主评估脚本，编排完整流程 |
| `deepeval_html_report.py` | HTML 报告生成器（独立自包含 HTML，无外部依赖） |
| `deepeval_run.sh` | 一键运行脚本（自动加载 `.env`） |

## 数据来源

| 文件 | 内容 | 有参考答案 |
|------|------|-----------|
| `eval/questions.md` | **126 题**（合并版：80 题产品知识 QA + 46 题销售场景） | 80 题有 ✅ |
| `eval/ground_truth_30q.json` | 30 个分类问题（有类别标注） | ❌ 仅关键词 |
| **去重后合计** | **~140 题** | **80 题** |

## 评估指标

| 指标 | 触发条件 | 依赖字段 | 说明 |
|------|----------|----------|------|
| 正确性 (GEval) | 有参考答案 | input, actual_output, expected_output | LLM 对比实际与预期，判断正确性 |
| 忠实度 (Faithfulness) | 所有题目 | input, actual_output, retrieval_context | 回答是否基于检索内容而非编造 |
| 相关性 (AnswerRelevancy) | 所有题目 | input, actual_output | 回答是否切合问题 |
| 幻觉检测 (Hallucination) | 无参考答案 | input, actual_output, context | 检测无中生有的内容 |

## CLI 参数参考

```bash
python eval/deepeval_eval.py \
    --app-url http://localhost:8000 \          # 单实例模式
    --app-url-legacy http://localhost:8001 \   # KB 对比：legacy 实例
    --label-legacy "legacy_rag" \              # KB 标签
    --app-url-ontology http://localhost:8002 \ # KB 对比：ontology 实例
    --label-ontology "ontology_neo4j" \        # KB 标签
    --models qwen-plus \                       # Agent 模型（默认用 Agent 默认）
    --limit 10 \                               # 只测前 10 题
    --concurrency 3 \                          # 并发数
    --tenant-id taishan \                      # 租户 ID
    --judge-model gpt-4o \                     # 裁判 LLM 模型
    --judge-api-key sk-... \                   # 裁判 API Key（可写 .env）
    --judge-base-url https://... \             # 裁判 Base URL
    --output-dir eval/results/deepeval         # 输出目录
```

## 常见问题

### Q: 报错 "OpenAI API key is not configured"
A: 在项目根目录 `.env` 中写入 `OPENAI_API_KEY=你的key`，脚本会自动加载。

### Q: 怎么用阿里云 Qwen 当裁判？
A: 在 `.env` 中配置：
```
OPENAI_API_KEY=<阿里云 DashScope API Key>
DEEPEVAL_MODEL=qwen-plus
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### Q: KB 对比需要两台 Agent 服务？
A: 对。`KNOWLEDGE_ENGINE` 是实例级环境变量，同一进程不能切换。需要分别启动两个实例：
```bash
# 终端 1：ontology 实例
KNOWLEDGE_ENGINE=ontology_neo4j python -m sales_agent.main

# 终端 2：legacy 实例（不同端口）
KNOWLEDGE_ENGINE=legacy_rag PORT=8001 python -m sales_agent.main
```

### Q: 评估需要多长时间？
A: 每个问题每个指标约 3-10 秒（取决于裁判 LLM 速度）。147 题 × 3 指标 ÷ 并发 3 ≈ 15-30 分钟。

### Q: 只想测试检索相关的问题（knowledge_qa），怎么过滤？
A: 目前脚本加载全部问题。如果需要过滤，可以添加 `--only-category knowledge_qa` 参数（告诉我可以加）。
