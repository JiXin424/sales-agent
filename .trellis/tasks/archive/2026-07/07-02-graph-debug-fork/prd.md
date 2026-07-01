# Graph Debug fork（A2）：改 state + 从 checkpoint 重跑

## Goal

在 A1 只读时间轴之上，支持「编辑某 checkpoint 的 state，从该点重跑后半段」，结果作为**同 thread 的分支**追加。让开发者做「如果当时换个 message / prompt / retrieval 结果，后半段会怎样」的假设实验。

## Background

A1（只读时间轴，已归档为 `07-02-graph-debug-time-travel`）铺好了 checkpointer 接线、`aget_state_history` 读取、`debug:` 隔离、时间轴 UI。A2 在其上加「写 + 重跑」。fork 结果写回同 thread（F1），LangGraph `metadata.parents` 自动记血缘，**A3 分支树白拿数据**。

## Requirements

- **R1** 后端 `POST .../threads/{tid}/checkpoints/{cid}/state`：`aupdate_state` 改 state，返回新 checkpoint_id。
- **R2** 后端 `POST .../threads/{tid}/checkpoints/{cid}/replay`：从该 checkpoint 重跑后半段，SSE 流式 trace（复用 `/run` 事件）。
- **R3** 前端 `JsonNode` 加编辑模式（可编辑 JSON 提交 values）。
- **R4** 前端 fork 流程：编辑 → update → replay → 刷新时间轴（新分支 checkpoint 出现）。
- **R5** fork 结果同 thread 分支（F1），`metadata.parents` 记血缘。
- **R6** replay 前确认提示（重跑会调 LLM / 查库）。
- **R7** 两新端点强制 `debug:` 前缀（403 否则）。

## Constraints

- 复用 A1 的 `_compile_with_checkpointer` / `_safe_serialize` / `_named_sse` / `_ensure_debug_thread`。
- 不改 A1 任何端点 / 响应形状（纯新增）。
- 不传 store；不动 DB schema；无新依赖。

## Out of Scope（A3 / 后续）

- 分支树 DAG 可视化（A3，读 `metadata.parents`）。
- A1 时间轴对分支的专门适配（A2 容忍线性展示里的重复 step）。
- 多次 fork 的分支切换 / 对比。

## Acceptance Criteria

- [ ] 改某 checkpoint 的 state（如 `message`）→ update 端点返回新 checkpoint_id。
- [ ] replay 从该 checkpoint 重跑，流式 trace 显示后半段节点重新执行。
- [ ] 重跑后 `list_checkpoints` 出现新 checkpoint，其 `metadata.parents` 指向 fork 点（分支血缘）。
- [ ] 非 `debug:` 前缀调两新端点 → 403。
- [ ] 前端：编辑 JSON → 提交 → 重跑 → 时间轴出现新 checkpoint。
- [ ] replay 前有确认提示。
- [ ] A1 只读行为不回归。
