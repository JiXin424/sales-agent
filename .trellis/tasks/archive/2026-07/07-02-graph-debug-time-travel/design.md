# Design — Graph Debug checkpoint time-travel（只读）

## 决策快照（grilling 结论）

| 维度 | 决策 |
|---|---|
| 场景 | 开发者调试(A) |
| 范围 | 只读 checkpoint 链 + 节点 state(A1) |
| 持久化 | `AsyncPostgresSaver` + `debug:` 前缀(C1) |
| run 找回 | `/run` 回传 thread_id + 前端 localStorage(D1) |

## 关键事实（调研,含文件:行）

- `thread_id` === `conversation_id`,1:1。
- 生产 checkpointer = `AsyncPostgresSaver`(`src/sales_agent/graph/checkpoints.py:35`),模块级单例 `_async_pg_saver`,`setup()` 自建 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 表。
- graph-debug 当前 `builder.compile()` 无 checkpointer(`src/sales_agent/api/routes/graph_debug.py:118`),thread_id 每次 fresh uuid(`:145`)。
- GraphDebugPage 现状:`console/src/pages/Agents/GraphDebugPage.tsx`(282 行),SSE 解析 `node_start/node_output/node_end/done/error`;**无 thread_id、无历史**、trace 每次 reset。API 在 `console/src/api/graphDebug.ts`。
- LangGraph checkpointer **无"列出所有 thread_id"原生 API** → 必须由前端持有 thread_id(D1)。
- `ChatGraphState`(`src/sales_agent/graph/state.py`)30+ 字段,含嵌套 dict(`retrieval_info` / `answer_dict` / `risk_result`)→ state 用 JSON viewer 展示。

## 数据流

```
[GraphDebugPage]
  ├─ POST /graph-debug/run ──(SSE: started{thread_id} → node_* → done)──▶ 实时 trace
  │      backend: await get_checkpointer() → compile(checkpointer=cp)
  │             thread_id = f"debug:{uuid4()}";  写 checkpoints 到 PG
  ├─ run 完 → GET /graph-debug/threads/{thread_id}/checkpoints
  │      → [{checkpoint_id, step, node, ts, next}, ...]
  ├─ 点某步 → GET .../checkpoints/{checkpoint_id}/state
  │      → {values: {...full ChatGraphState...}}
  └─ localStorage: runs[] = {thread_id, graph_id, message, ts}
```

## 后端契约

### 改动:`POST /agents/{agent_id}/graph-debug/run`（`graph_debug.py`）

- `await get_checkpointer()` 取单例 → `builder.compile(checkpointer=cp)`。
- `thread_id = f"debug:{uuid4()}"`;input_state 的 `conversation_id` 用同一值(保持 state 内一致)。
- 首个 SSE `started` 事件**新增** `thread_id` 字段。

### 新增:`GET /agents/{agent_id}/graph-debug/threads/{thread_id}/checkpoints`

- 校验 `thread_id.startswith("debug:")` 否则 **403**。
- `graph = builder.compile(checkpointer=cp)`(graph_id 默认走 query 参数 `?graph_id=chat`,或用默认 chat 图编译——见"待定"注);`history = [s async for s in graph.aget_state_history({"configurable":{"thread_id": tid}})]`。
- 返回(按 step 升序):
  ```json
  [{"checkpoint_id":"...","step":<int>,"node":"<last writer>","ts":"<iso>","next":["<node>",...]}, ...]
  ```
- `node` 推断:读 `snapshot.tasks[*].name`(回退 `snapshot.next[0]`)。**实测(langgraph>=1.2)`metadata.writes` 不存在**(metadata 仅 `source`/`step`/`parents`),原 writes 方案弃用——见风险2。`step` 取 `metadata["step"]`;`ts` 取 `created_at`;`next` 取 `snapshot.next`。字段 `.get()` 兜底,缺失返回 `null`。`node` 语义为"该 checkpoint 之后将执行的节点"(= interrupt 点)。

### 新增:`GET .../threads/{thread_id}/checkpoints/{checkpoint_id}/state`

- 校验 `debug:` 前缀。
- 在 history 中匹配 `checkpoint_id`(`snapshot.config["configurable"]["checkpoint_id"] == checkpoint_id`),返回 `{"values": <snapshot.values>, "next": <snapshot.next>}`。
- values 经 `_safe_serialize`(`graph_debug.py:200`,已存在)保证 JSON 可序列化。

### checkpointer 接线注意

- `get_checkpointer()` 是 async(内部 `await setup()`)。`list_graphs` 里同步 compile 只为画 mermaid,**不需要 checkpointer,保持不变**。仅 `/run` 与两个新端点 `await` 取 cp。
- 同一 thread 多次查询复用单例 cp 即可,**无需缓存 compiled graph**(state 归属在 cp,不在 graph 实例)。

### 待定(实现时定,不阻塞)

history 端点要知道用哪个 `graph_id` 编译——checkpoint 数据按 thread_id 存,与具体 graph 拓扑无关,`aget_state_history` 只需 checkpointer。但 `builder.compile(checkpointer=cp)` 需要一个 builder。方案:端点用 **chat 主图** builder 编译即可读取任意 debug thread 的 history(state 是 plain dict,不依赖图拓扑)。若前端"最近 run"列表已存 `graph_id`,可优先用对应 builder,否则回退 chat。实现时选简单方案。

## 前端契约（`GraphDebugPage.tsx` + `api/graphDebug.ts`）

### 新增 API（`console/src/api/graphDebug.ts`）

- `getCheckpoints(agentId, threadId)` → GET 列表
- `getCheckpointState(agentId, threadId, checkpointId)` → GET 详情
- 注意:现有 `runGraphDebug` 是原生 `fetch`(SSE),保持不动。

### GraphDebugPage 改动

- 解析 `started` 事件 → 存 `currentThreadId`。
- `done` 后调 `getCheckpoints` → `checkpoints` state → 渲染时间轴(Ant Design `Steps` 或自定义 stepper;每项显示 step + node + ts)。
- 点某步 → `getCheckpointState` → JSON viewer(`react-json-view` **优先用项目已有依赖**,无则自写轻量 collapse,避免引包)。
- `loadRunsFromStorage()` / `saveRun()`:`localStorage["graph-debug:runs"]` 存最近 **N=20** 条;顶部 `Select` 切换 → 重新 `getCheckpoints`。
- 现有 SSE trace 面板保留,与新时间轴并列(Tabs:"实时 trace" / "Checkpoint 时间轴")。

## 隔离与安全

- `debug:` 前缀强制校验 → debug 页面**无法读取生产会话 state**(含 tenant 数据)。
- debug run 用 `tenant_id="debug"`、`user_id="debug-user"`(现有),retrieve 连真实知识库、generate 调真实 LLM(调试本就需真跑)——已知,接受。
- graph-debug **不传 store** → 不污染 cross-session memory。

## 风险与回滚

- **风险1**:`debug:` 数据累积于 checkpoints 表。MVP 不清理,记风险;后续加按前缀定期清理。
- **风险2(已实证)**:`metadata.writes` 在 `langgraph>=1.2` **不存在**(进程内验证:`aget_state_history` 的 snapshot.metadata 仅 `source`/`step`/`parents`)。原 writes 推断失败,改用 `snapshot.tasks[*].name`(回退 `next[0]`)。`step`/`checkpoint_id`/`next`/`created_at` 字段名均验证正确。`.get()` 兜底仍保留以防 tasks 为空。
- **风险3**:JSON viewer 新依赖 → 先查 `console/package.json` 已有;无则自写轻量 collapse。
- **回滚**:改动集中在 `graph_debug.py` + 2 个前端文件 + 1 个 api 文件,`git revert` 即可;**不动 DB schema**(checkpoint 表由 LangGraph 自管),无需 migration 回滚。

## 兼容性

- 不改现有 `/graphs`、`/run` 响应形状(`run` 的 `started` 事件**仅新增** `thread_id` 字段,前端向后兼容)。
- 不改生产路径。
