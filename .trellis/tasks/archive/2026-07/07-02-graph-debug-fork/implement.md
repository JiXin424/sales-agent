# Implement — Graph Debug fork（A2）

## Phase A — 后端（`graph_debug.py`）

- [ ] A0. **先 probe** `aupdate_state` 的 `as_node` 行为（进程内 InMemorySaver）：改某 checkpoint 的 message 后,看 state 是否按预期落到 next 节点入口、新 checkpoint 的 parents 是否指向 fork 点。据此决定 `as_node` 传不传。
- [ ] A1. `POST .../threads/{tid}/checkpoints/{cid}/state`：前缀校验 + `aupdate_state({configurable:{tid,cid}}, values, as_node=...)` + 返回新 checkpoint_id。
- [ ] A2. `POST .../threads/{tid}/checkpoints/{cid}/replay`：前缀校验 + `astream(None, {configurable:{tid,cid}}, stream_mode=[...])` + SSE 事件。
- [ ] A3. 抽 `_run_graph_sse(graph, config, graph_id)` 让 `/run` 与 replay 共用流式逻辑（DRY）。

验证：进程内 probe（共享 InMemorySaver）—— update 改 state → replay 重跑 → 新 checkpoint `metadata.parents` 指向 fork 点 → 403。

## Phase B — 前端

- [ ] B1. `JsonNode.tsx` 加 `editable` + `onChange`（编辑模式，叶节点可输入）。
- [ ] B2. `graphDebug.ts` 加 `updateCheckpointState` + `replayCheckpoint`（后者 SSE）。
- [ ] B3. `GraphDebugPage.tsx`：fork 流程（编辑→update→replay→刷新）+ `Modal.confirm` + trace 显示。

验证：`cd console && npm run build`。

## Review Gate

- [ ] as_node 行为已 probe 确认并记 lessons；A1 只读行为不回归；前缀校验无绕过；无新依赖；SSE 抽公用无重复。

## Phase C — 验证

- [ ] 进程内：update + replay + parents 分支血缘 + 403 + A1 不回归。
- [ ] 待目标环境：HTTP run + 浏览器 fork 流程 + 跨重启持久。

## 收尾

- [ ] README / `changelog/2026-07-02.md`（追加 A2 段）/ lessons（as_node 发现）/ design 同步。
- [ ] `task.py finish` + archive；之后建 A3（分支树）task。
