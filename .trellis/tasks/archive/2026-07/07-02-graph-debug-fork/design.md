# Design — Graph Debug fork (A2): edit state + replay from checkpoint

## 决策快照（grilling 结论）

| 维度 | 决策 |
|---|---|
| 编辑入口 | **E1** — 整体 JSON 编辑器（复用 `JsonNode` + 编辑模式） |
| fork 存储 | **F1** — 写回同 thread 形成分支（LangGraph 原生，`metadata.parents` 记血缘） |

## 依赖：A1 已就绪的基础（本任务在其上增量，不重打地基）

- `graph_debug.py`：`/run` 已接 `AsyncPostgresSaver`（`_compile_with_checkpointer`）；2 个只读端点（`list_checkpoints` / `get_checkpoint_state`）；`DEBUG_THREAD_PREFIX="debug:"`；`_safe_serialize`、`_named_sse`、`_ensure_debug_thread`。
- checkpoint metadata 实测结构：`{source, step, parents}`（A1 验证）。`parents` 当前为空，**fork 后自动填充** → 这就是 A3 分支树的数据源。
- 前端 `JsonNode.tsx`（只读递归）、`GraphDebugPage.tsx`（时间轴 Steps + Tabs + localStorage）。

## 数据流

```
[checkpoint 详情面板] → 点"编辑并 fork" → JsonNode 编辑模式改 values
  → POST .../checkpoints/{cid}/state   (aupdate_state → 写新 checkpoint, 同 thread)
  → POST .../checkpoints/{cid}/replay  (astream 带 checkpoint_id → 重跑后半段, SSE trace)
  → done 后刷新 getCheckpoints         → 新分支 checkpoint 出现 (parents 指向 fork 点)
```

## 后端契约（新增 2 端点，均在 `graph_debug.py`）

### `POST .../threads/{tid}/checkpoints/{cid}/state`（改 state）

- 校验 `debug:` 前缀（复用 `_ensure_debug_thread`）。
- body：`{values: {...}, graph_id?: "chat"}`。
- `graph = await _compile_with_checkpointer(graph_id)`；`await graph.aupdate_state({"configurable":{"thread_id":tid,"checkpoint_id":cid}}, values)`。
- 返回 `{checkpoint_id: <new>}`（update 后从 `aget_state_history` 取该 thread 最新 checkpoint_id）。
- **as_node 待 probe**：`aupdate_state` 的 `as_node` 决定 values 按哪个节点的 reducer 合并。实现时用进程内 probe 确认"不传 as_node"的默认行为是否满足"直接落到 next 节点入口"。若不对，指定 `as_node=<next 节点>`。这是本任务最易错的点（类比 A1 的 writes→tasks）。

### `POST .../threads/{tid}/checkpoints/{cid}/replay`（重跑后半段）

- 校验前缀。
- `graph.astream(None, {"configurable":{"thread_id":tid,"checkpoint_id":cid}}, stream_mode=["updates","custom","debug"])` —— input=None 表示从该 checkpoint 恢复、执行其 `next` 节点链。
- 复用 `/run` 的 SSE 事件（`node_start`/`node_output`/`node_end`/`done`/`error`）。
- `done` 后前端刷新 `getCheckpoints`（新分支 checkpoint 已写入）。

### 复用 / 重构

- `_compile_with_checkpointer`、`_safe_serialize`、`_named_sse`、`_ensure_debug_thread` 全复用。
- `/run` 和 replay 的 SSE 生成高度重复 → 抽 `_run_graph_sse(graph, config, graph_id)` async generator 共用（DRY）。

## 前端契约

- `JsonNode.tsx`：加 `editable?: boolean` + `onChange?(newValue)`；编辑模式下叶节点值可输入；顶层提供「提交 / 取消」。
- `graphDebug.ts`：加 `updateCheckpointState(tid, cid, values)`（JSON fetch）+ `replayCheckpoint(tid, cid)`（SSE 原生 fetch，仿 `runGraphDebug`）。
- `GraphDebugPage.tsx`：checkpoint state 面板加「编辑并 fork」按钮 → 切 JsonNode 编辑态 → 提交 → 调 update → 调 replay（SSE trace 显示，复用「实时 trace」tab）→ 完成刷新 `getCheckpoints`。replay 前弹 `Modal.confirm`（重跑会调 LLM / 查库）。
- types.ts：加 `UpdateStateRequest`、`UpdateStateResponse`。

## 隔离与副作用

- 两新端点强制 `debug:` 前缀（否则 403）。
- 不传 store；debug tenant；replay 重跑烧 debug 自己的 LLM token（已知，UI 确认）。

## 风险与回滚

- **风险1（已 probe 落实）**：`aupdate_state` **不传 as_node** —— 默认正确合并 values、保留 fork 点 `next`（实测）。但 config **必须含 `checkpoint_ns=""`**，否则 `KeyError`。详见末尾「A2 实现结果」。
- **风险2**：fork 后 A1 的线性 `list_checkpoints` 出现重复 step（多分支混入）→ A2 容忍（按 step+ts 排序仍可用）；分支树专门展示留 A3。
- **风险3**：replay 副作用（LLM/查库）→ UI 确认 + 文档提醒。
- **回滚**：新增 2 端点 + 前端编辑模式，`git revert` 即可；不动 DB、不动 A1 端点。

## 兼容性

- 不改 A1 任何端点 / 响应形状（但顺带修复了 A1 `/run` 的 SSE tuple 解包 bug —— 见实现结果）。A3 **不能**依赖 `metadata.parents`（实测空），需从 checkpoint 链（step + `source=='update'`）重建血缘。

## A2 实现结果（probe 实证，2026-07-02）

进程内验证（共享 InMemorySaver）全过：update 改 state → 新 checkpoint、replay 重跑、403、A1 不回归。关键发现：

1. **`as_node` 不传**：`aupdate_state(config, values)` 默认正确合并 values、保留 fork 点 `next`。
2. **config 必须含 `checkpoint_ns=""`**：否则 `aupdate_state` / `astream` 抛 `KeyError: 'checkpoint_ns'`。
3. **`metadata.parents` 实测为空**：langgraph 1.2 只对 `interrupt` fork 填 parents，不对 `aupdate_state` 填。**A3 不能依赖 parents**，从 checkpoint 链（step + `source=='update'`）重建血缘。
4. **顺带修复 A1 SSE tuple bug**：`graph.astream(stream_mode=[list])` 返回 `tuple[mode, payload]`，A1 原 `/run` 按 dict 解（`chunk.get("type")`）→ 真实运行只发 error。抽 `_run_graph_sse` 时归一化 tuple/dict，`/run` 现正确发节点事件（实测 `['node_start','node_end','node_start','node_end','error']`）。A1 隐藏缺陷，A2 一并修。
