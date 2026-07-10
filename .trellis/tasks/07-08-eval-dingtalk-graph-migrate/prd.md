# DeepEval 迁移到钉钉生产链路并删除 ChatPipeline

## Goal

让 DeepEval 评估完全走生产钉钉链路（`invoke_online_turn` / Online Graph），不再依赖已废弃的 `ChatPipeline`，迁移完成后删除 `chat_pipeline.py` 及仅被它使用的辅助代码。不受 `tasks/todo_eval_migrate_to_graph.md` 的约束（指标可比性等让位于"链路正确性"）。

## 已确认事实（代码勘察）

### 生产链路
- HTTP `/agent/chat` → `agent.py` → `invoke_online_turn`（graph 路径）
- 钉钉 Stream → `processor.py:131` → `invoke_online_turn`（graph 路径）
- `invoke_online_turn` (`services/online_conversation.py:123`) 返回 Online Graph 末态 dict

### eval 当前依赖
- **唯一真 import**：`eval/deepeval_test_cases.py:463` → `from ...chat_pipeline import ChatPipeline`
- 其他 eval 文件（`deepeval_eval.py`, `deepeval_optimize.py`）都经 `call_agent_pipeline()` 间接调
- optimizer graph 的 `run_evaluate` 是 subprocess 调 `deepeval_eval.py`，不直接 import → 自动受益

### 字段缺口
`invoke_online_turn` 返回的 Online State 末态缺 eval 需要的字段：
- `chat_node` 只 `return {answer_dict, response_kind, last_event_id}` 给 online state
- ChatGraphState 里有但没透传：`sources`, `risk_result`, `usage`, `route_confidence`, `path`
- graph 路径无 `PipelineTimings`（逐阶段耗时）和 `RunTracer`

### ontology sources 缺文本
- graph retrieve_node 的 ontology 路径返回 sources 只有 `title`，无 `text`/`content`
- eval 需要检索原文做 retrieval_context（Faithfulness/AnswerRecall 指标用）
- 当前 eval 用 `_fetch_ontology_sources` 补丁绕过 pipeline 直接查 Neo4j
- graph 已有 `ontology_context_text`（完整实体+事实文本），只是没进 sources

### timings 用途
- `latency_ms`/`ttft_ms` 只用于报告展示（显示延迟/TTFT 列），不影响 DeepEval 指标评分

### 测试依赖（3 个文件）
- `test_processing_notice.py`：快速命令部分已在 graph 侧 `test_validation_node.py` 覆盖；`_ProcessingNoticeGuard` 在 graph 用 stream_writer 取代
- `test_coach_pipeline_integration.py`：测 coach 报告拦截，graph 侧未接入（独立遗留问题）
- `test_realtime_guidance.py`：测实时引导，同上

### 删除边界（代码勘察确认）
- **可删**：`chat_pipeline.py`、`latency_tracker.py`、`run_tracer.py`、`path_router.py`(PathRouter类)、对应 5 个测试
- **保留**：`agent_executor.py`(graph在用)、`task_router.py`(内部函数自用)、`PathRouterConfig`(config+routing node在用)、`_HELP/_RESET_COMMANDS`(graph有自己副本)

## 已决策

1. **字段缺口** → 改 `chat_node` 透传 sources/risk_result/usage/route_confidence/path 回 OnlineConversationState
2. **timings** → wall-clock(latency_ms) + ttft_ms 弃用(置0，报告显示"—")
3. **测试** → 全部删除（已被 graph 测试覆盖 或 功能已不存在）
4. **ontology sources** → 改 retrieve_node 让 ontology sources 带 text 字段(从 ontology_context_text)
5. **删除范围** → 完整清理 chat_pipeline + 仅被它用的辅助模块 + 对应测试

## Requirements

- [ ] `call_agent_pipeline` 改为调 `invoke_online_turn`，不再 import ChatPipeline
- [ ] `chat_node` 透传 sources/risk_result/usage/route_confidence/path 到 OnlineConversationState
- [ ] OnlineConversationState 新增 sources/risk_result/usage 等字段定义
- [ ] retrieve_node ontology 路径 sources 补 text 字段
- [ ] latency_ms 用 wall-clock，ttft_ms 置 0
- [ ] 删除 `_fetch_ontology_sources` / `_is_ontology_engine` 补丁（graph sources 已有 text）
- [ ] 删除 `chat_pipeline.py` + `latency_tracker.py` + `run_tracer.py` + `path_router.py`
- [ ] 删除 5 个测试文件
- [ ] 保留 `agent_executor.py` / `task_router.py` / `PathRouterConfig`
- [ ] README / docs 中 ChatPipeline 相关表述同步更新

## Acceptance Criteria

- [ ] `git grep -i ChatPipeline` 在 src/ eval/ scripts/ 下零结果
- [ ] `git grep chat_pipeline` 在 src/ eval/ scripts/ tests/ 下零结果
- [ ] `python eval/deepeval_eval.py --tenant-id taishan --limit 3` 成功跑通
- [ ] `pytest tests/unit/graph/ tests/unit/test_validation_node.py` 全绿
- [ ] `pytest tests/` 无 import error（迁移后的测试集）
- [ ] 无遗留死代码（latency_tracker/run_tracer/path_router 引用清零）
