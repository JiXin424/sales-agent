# Implement: DeepEval 迁移到钉钉生产链路并删除 ChatPipeline

## 执行顺序

按区域分 3 步，每步可独立验证。先改生产 graph（区域 A），再改 eval（区域 B），最后删死代码（区域 C）。

---

## Step 1: 生产 graph 补全返回信息（区域 A）

### 1.1 OnlineConversationState 加字段
**文件**: `src/sales_agent/graph/online/state.py`
- 在 `# ── Output ───────────` 区块新增：`sources`, `risk_result`, `usage`, `route_confidence`, `path`
- `task_type` 已存在，不重复加

### 1.2 chat_node 透传字段
**文件**: `src/sales_agent/graph/online/nodes.py` — `chat_node` 函数
- return dict 补：`sources`, `risk_result`, `usage`, `route_confidence`, `path`, `task_type`
- 这些都从 `result`（ChatGraphState 末态）取，用 `.get()` 带默认值

### 1.3 retrieve_node ontology sources 补 text
**文件**: `src/sales_agent/graph/chat/nodes/retrieval.py` — `_retrieve_via_ontology`
- sources 列表推导式里给每个 dict 加 `"text": ontology_context_text`

### 验证
```bash
pytest tests/unit/graph/test_online_graph.py tests/unit/graph/test_context_routing_nodes.py -v
pytest tests/integration/test_graph_pipeline_parity.py -v
```

---

## Step 2: eval 迁移调用入口（区域 B）

### 2.1 新增 model override 辅助函数
**文件**: `eval/deepeval_test_cases.py`
- 新增 `_resolve_chat_model_override(model: str | None) -> ChatModel | None`
- 逻辑从 chat_pipeline.py:278 提取：`ModelRegistry.load()` → `registry.get(model)` → 构建 `OpenAICompatibleChat`
- `model=None` 时返回 None（invoke_online_turn 内部自动 resolve）

### 2.2 重写 call_agent_pipeline
**文件**: `eval/deepeval_test_cases.py` — `call_agent_pipeline` 函数
- 删 `from sales_agent.services.chat_pipeline import ChatPipeline`
- 改 `from sales_agent.services.online_conversation import invoke_online_turn`
- 调 `invoke_online_turn`，参数映射见 design.md 字段映射表
- `session_user_id` 用 `f"deepeval_eval_{question.id}"` 确保 thread_id 隔离
- `channel="dingtalk"` 模拟钉钉渠道
- latency_ms 用 wall-clock，ttft_ms 置 0
- 删 reply_fn / captured_replies 逻辑
- 删 `_fetch_ontology_sources` / `_is_ontology_engine` / `_MAX_SOURCE_CHARS` 及调用块

### 2.3 更新 AgentResponse 字段提取
- `risk_level` = `risk_result.get("level", "")`（dict key 已确认：level/flags/action/notice/rewrite_summary）
- `risk_flags` = `risk_result.get("flags", [])`
- `task_type` = `result.get("task_type", "")`

### 验证
```bash
# 冒烟（需 .env + DB + Neo4j 配好）
python eval/deepeval_eval.py --tenant-id taishan --limit 3
```

---

## Step 3: 删除死代码（区域 C）

### 3.1 删除源文件
```bash
git rm src/sales_agent/services/chat_pipeline.py
git rm src/sales_agent/services/latency_tracker.py
git rm src/sales_agent/services/run_tracer.py
git rm src/sales_agent/services/path_router.py
```

### 3.2 删除测试文件
```bash
git rm tests/unit/test_processing_notice.py
git rm tests/unit/test_latency_tracker.py
git rm tests/unit/test_run_tracer.py
git rm tests/unit/test_path_router.py
git rm tests/integration/coach/test_coach_pipeline_integration.py
git rm tests/integration/coach/test_realtime_guidance.py
```

### 3.3 清理残留引用
检查并修复：
- `src/sales_agent/services/eval_runner_service.py:113,249` — 注释提到 ChatPipeline，更新措辞
- `src/sales_agent/models/agent_run.py:38` — 注释提到 ChatPipeline，更新措辞
- `src/sales_agent/services/run_tracer.py:21` — 已删，无引用
- `eval/README_DEEPEVAL.md` — ChatPipeline 相关表述改为 invoke_online_turn
- `README.md` — 如有 ChatPipeline 提及，同步更新

### 验证
```bash
# 零残留确认
git grep -i ChatPipeline -- src/ eval/ scripts/        # 应为空
git grep chat_pipeline -- src/ eval/ scripts/ tests/   # 应为空
git grep latency_tracker -- src/ eval/ tests/          # 应为空
git grep run_tracer -- src/ eval/ tests/               # 应为空
git grep "from.*path_router import PathRouter" -- src/ tests/  # 应为空(PathRouterConfig保留)

# 全量测试
pytest tests/ -v --tb=short
```

---

## 风险文件与回滚点

| 文件 | 风险 | 回滚 |
|------|------|------|
| `graph/online/nodes.py` chat_node | 改动生产 graph 返回值 | git revert，新字段 .get() 带默认值不影响旧消费者 |
| `graph/online/state.py` | 新增 state 字段 | total=False 向后兼容，旧 checkpoint 不受影响 |
| `graph/chat/nodes/retrieval.py` | 改 sources 结构 | 只加字段不删字段，renderer 仍用 title |
| `eval/deepeval_test_cases.py` | eval 核心调用链 | 最大风险点，需冒烟验证 |

## 验证命令汇总

```bash
# Step 1 后
pytest tests/unit/graph/ tests/integration/test_graph_pipeline_parity.py -v

# Step 2 后
python eval/deepeval_eval.py --tenant-id taishan --limit 3

# Step 3 后
git grep -i ChatPipeline -- src/ eval/ scripts/ tests/
pytest tests/ -v --tb=short
```
