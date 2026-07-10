# Todo — eval 迁移到 graph（独立后续工作）

> **已完成** — 见 `.trellis/tasks/07-08-eval-dingtalk-graph-migrate/`。
> eval 已迁移到 `invoke_online_turn`，`chat_pipeline.py` 及辅助模块已删除。
> 本文件保留作为历史记录。
>
> 由 `07-07-prompt-cleanup-register` 任务分出。原计划作为
> `services/chat_pipeline.py` 删除的前置条件。

## 背景

`services/chat_pipeline.py`（ChatPipeline）在生产零调用：
- HTTP `/agent/chat` 走 `_execute_via_graph`（graph 路径）。
- 钉钉 Stream 走 `services/online_conversation.py:invoke_online_turn`（graph 路径）。

但 `eval/deepeval_*.py` 仍依赖 ChatPipeline（lessons #31 指出的「eval 老路径」），
所以本类**不能删**。本任务负责把 eval 调用方迁移到 `invoke_online_turn`，
迁移完成后即可整文件删除 ChatPipeline（含 `services/path_router.py` /
`services/agent_executor.py` / `PipelineResult` / `_ProcessingNoticeGuard` 等
仅 chat_pipeline 使用的辅助）。

## 工作清单

- [ ] 盘点 `eval/deepeval_*.py` 中所有 `ChatPipeline(...)` / `pipeline.execute(...)`
      调用点，记录每个测试的具体期望（PipelineResult 字段、副作用、日志等）。
- [ ] 评估 `invoke_online_turn` 当前返回值是否覆盖 eval 期望的字段；缺口列出补齐方案
      （如 RunTracer、PipelineResult 兼容包装）。
- [ ] 设计 eval 友好的 entrypoint（例如 `run_eval_turn(tenant_id, agent_id, message, ...)`
      → 内部调 `invoke_online_turn` + 拼装成 eval 期望的 result 对象）。
- [ ] 逐个迁移 `eval/deepeval_*.py` 到新 entrypoint，跑 deepeval 对比基线一致。
- [ ] 全部迁移完成后：删除 `services/chat_pipeline.py` + 仅被它用的辅助模块（grep 确认零残留）。
- [ ] README「产品文档对照」同步移除 ChatPipeline 条目。

## 关键约束

- **不可降低 eval 指标的可比性**：迁移前后同一批 fixture 跑出来的 deepeval 指标应一致
  （允许细微浮动，但不可系统性偏离）。
- **保留 lessons #31 的警示**：迁移过程中如发现 eval 老路径行为与 graph 不一致，
  以 graph 为准，并在 `lessons.md` 追加新教训。
- 优先走生产入口（CLAUDE.md #4）—— `invoke_online_turn` 就是当前钉钉 Stream 用的入口。

## 风险

- ChatPipeline 走的 PathRouter（fast/standard/slow）在 graph 中已由 conditional edges 取代，
  eval 若依赖 path label 需在新 entrypoint 中重新映射。
- ChatPipeline 内有 `conversation_logger` / `RunTracer` 副作用，迁移时需确认 graph 路径
  也有等价记录（否则 eval 评估会缺数据）。
