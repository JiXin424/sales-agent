# Graph Debug checkpoint time-travel（只读时间轴）

## Goal

在 GraphDebugPage 上,为每次调试 run 提供**只读 checkpoint 时间轴**:开发者发起一次 run 后,能查看该 run 在每个节点执行完毕时的完整 state 快照,用于定位 routing 走偏、retrieval 拉错、risk 误杀等问题。**只看,不改、不重跑。**

## Background / Problem（现状,经代码调研）

"时间旅行"在整个项目里目前**完全不存在**。三条执行路径的持久化现状各不相同:

| 路径 | 执行器 | checkpointer | 可回溯? |
|---|---|---|---|
| 钉钉流式 | LangGraph 图 | `AsyncPostgresSaver`(PG,持久) | checkpoint 一直在写,**但无代码读回 history** |
| HTTP `/agent/chat` | 老的 `ChatPipeline`(**非图**) | ❌(`_execute_via_graph` 是未启用死代码,局部 InMemorySaver) | 无 |
| GraphDebugPage 调试 | LangGraph 图 | ❌ `graph_debug.py:118` `builder.compile()` 未传 checkpointer | 跑完即忘 |

LangGraph 原生支持 `aget_state_history(thread_id)` 等回溯 API,基础设施(`AsyncPostgresSaver`)就绪,缺的是**接线 + 前端可视化**。

## Requirements

- **R1** graph-debug 路径接上 checkpointer(复用 `get_checkpointer()` 单例 `AsyncPostgresSaver`),使每次 run 的每个节点 boundary 都写 checkpoint。
- **R2** thread_id 统一用 `debug:{uuid4}` 前缀,与生产会话隔离。
- **R3** `/run` 端点在首个 SSE `started` 事件里回传 thread_id。
- **R4** 新增只读 history 端点(按 thread_id + checkpoint_id):
  - 列表:返回 checkpoint 链元信息(`checkpoint_id / step / node / ts / next`),**不带 full state**。
  - 详情:返回单个 checkpoint 的完整 state `values`。
- **R5** history 端点强制校验 `thread_id` 以 `debug:` 开头,否则 **403**。
- **R6** 前端 GraphDebugPage:
  - run 完后用 thread_id 拉 checkpoint 链,渲染为可点击时间轴(stepper)。
  - 点某步 → 拉该 checkpoint 完整 state → JSON viewer(可折叠)展示。
  - localStorage 维护"最近调试 run"列表(`thread_id / graph_id / message 摘要 / 时间`),顶部下拉切换。
  - SSE 实时 trace 保留作 run 进行中的反馈。

## Constraints

- 不引入新 DB 依赖(复用现有 Postgres + `AsyncPostgresSaver`)。
- **多 worker 兼容**(`uvicorn --workers N`)——这正是选 Postgres 而非内存的核心原因。
- `langgraph>=1.2.0` API:`aget_state_history`、`StateSnapshot`(`values / next / metadata.writes / created_at`)。
- 前端栈:React + Ant Design + mermaid(现有)。
- 不修改生产 HTTP / 钉钉路径代码。

## Out of Scope（A1 边界,留待后续 A2）

- `update_state` / state 编辑器
- fork(改 state 重跑)/ replay 重跑
- 分支树(多次 fork 的 DAG 可视化)
- checkpoint 间 diff
- 真实会话 thread(非 `debug:` 前缀)的回看
- checkpoint 数据清理任务(记为已知风险,后续加)

## Acceptance Criteria

- [ ] graph-debug run 后,Postgres `checkpoints` 表出现该 `debug:` thread 的多条 checkpoint(每节点一条)。
- [ ] GET history 列表端点返回按 step 排序的 checkpoint 链,每项含 `checkpoint_id / step / node / ts / next`。
- [ ] GET state 端点返回指定 checkpoint 的完整 `ChatGraphState` values。
- [ ] 非 `debug:` 前缀 thread_id 请求 history 端点返回 403。
- [ ] 前端 run 完后展示时间轴;点任一步展示该步完整 state(JSON viewer)。
- [ ] 刷新页面后,localStorage 的"最近 run"列表仍在,可重新加载某 run 的时间轴(跨重启可查,因 PG 持久)。
- [ ] 多 worker 下,run 与后续 history 查询落在不同 worker 仍能查到(PG 共享)。
- [ ] 现有 SSE 实时 trace 行为不回归。
