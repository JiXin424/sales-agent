# 真实会话 checkpoint 只读回看（会话历史页）

## Goal

在 console 新增独立「会话历史」页，让开发者/运营**只读查看任一真实会话（钉钉生产）在每个 graph 节点边界的 checkpoint 快照**，用于事后定位 routing 走偏、retrieval 拉错、回答异常等问题。

**严格只读：只看历史快照，绝不 fork / replay / 改 state / 重跑生产会话。** 不改变生产执行路径的任何行为（用户约束：“先保持现版本”）。

## Background / Problem

LangGraph 原生 time-travel 在本项目的现状（经代码调研）：

- **数据已具备**：钉钉生产路径 `integrations/dingtalk/graph_stream.py:79-89` 用 `AsyncPostgresSaver`（PG 持久），`thread_id = conversation_id`，而 `conversation_id` 由 `DingTalkConversationMapper.generate_conversation_id` 产出，并作为 `Conversation.id` 主键落库。每个 graph 节点边界都写 checkpoint，多 worker 共享、重启不丢。
- **回看入口被拦**：现有只读 history 端点在 `api/routes/graph_debug.py`，强制 `thread_id` 以 `debug:` 开头（`_ensure_debug_thread`，`graph_debug.py:525`），真实会话 `conversation_id` 一律 403。这是当初有意的安全隔离（已归档任务 `07-02-graph-debug-time-travel` 的 Out-of-Scope）。
- **结果**：PG 里躺着所有历史会话的 checkpoint，但无 HTTP 端点能读出。

本任务把「只读时间轴」能力从 debug run 扩展到真实会话，并配独立前端页。

## Requirements

- **R1 后端·会话列表**：`GET /agents/{agent_id}/history/conversations`，从 `conversations` 表按 `agent_id` 过滤、`updated_at desc` 返回最近会话（含 `conversation_id / message 摘要 / channel / task_type / status / updated_at`），支持分页（`limit`/`offset`，默认 50）。
- **R2 后端·checkpoint 时间轴**：`GET /agents/{agent_id}/history/conversations/{conversation_id}/checkpoints`，调 `graph.aget_state_history(thread_id=conversation_id)` 返回 checkpoint 链（`checkpoint_id / step / node / ts / next / parent_checkpoint_id`）。**不校验 `debug:` 前缀**。
- **R3 后端·单 checkpoint state**：`GET .../checkpoints/{checkpoint_id}/state`，返回该 checkpoint 完整 `values`（过 `_safe_serialize` 保证 JSON 可序列化）。
- **R4 只读保证**：本任务**只新增 GET 端点**，不得新增任何 POST/PUT/PATCH/DELETE；不得调用 `aupdate_state` / `astream(None, …)` 等 fork/replay API（grep 可证）。
- **R5 前端·独立菜单页**：Sidebar 顶层新增「会话历史」菜单（`/history`）。页面含：agent 选择器 + 最近会话列表（R1）+ 手动输入 `conversation_id` 框（兜底）。选中会话 → 展示其 checkpoint 时间轴（R2）+ 点某步展示该步 state（R3），复用 GraphDebugPage 的 `CheckpointDAG` / 时间轴 / `JsonNode` 组件。
- **R6 不影响生产路径**：不改 `graph_stream.py` / `online_graph.py` / 钉钉路径任何执行逻辑；checkpoint 读取复用 `get_checkpointer()` 单例。
- **R7 鉴权**：保持与现有 `graph_debug` 端点一致（无强 auth，靠 `agent_id` 软隔离）。在 README / changelog 标注「敏感数据 + 无强鉴权」为已知风险。

## Constraints

- **不新增 DB schema / migration**：只读 `conversations` 表 + 读 LangGraph checkpoint（表由 `AsyncPostgresSaver.setup()` 自动建）。无 Alembic migration。
- **复用 checkpointer 单例**：`get_checkpointer()`（生产 `AsyncPostgresSaver`，未配 `DATABASE_URL` 时 `InMemorySaver`）。读 history 只需 `checkpointer + thread_id`，与 compile 哪个 graph 无关（checkpoint 是纯 dict state 存在 checkpointer 里）。
- **回溯粒度**：生产路径 `online_graph` 是父图持 checkpointer，`chat` / `guided_flow` 子图编译时**不带** checkpointer（`online_graph.py:68,76`）。所以时间轴粒度是 **online graph 节点边界**（`normalize_turn / context_resolution / evidence_routing / chat / guided_flow / log_flow_output / duplicate / clarification / ...`），不是 chat 子图内部逐步。chat 节点的输入/输出（含 `answer_dict`）会出现在父图 checkpoint。
- **生产入口**：本项目生产入口是钉钉 Stream（非 HTTP `/agent/chat`）。HTTP `/agent/chat` 走老 `ChatPipeline`，不写 checkpoint，因此本功能覆盖钉钉会话；HTTP 会话无 checkpoint 可回看。
- 前端栈：React + Ant Design + react-query（现有）。

## Out of Scope（明确不做）

- ❌ 真实会话的 state 编辑（`aupdate_state`）/ fork / replay（即“真回溯”——重跑或改写历史）。
- ❌ 改变生产执行路径任何行为。
- ❌ HTTP `/agent/chat` 老路径的回看（无 checkpoint）。
- ❌ 强鉴权 / RBAC（保持现状，仅标注风险）。
- ❌ checkpoint 数据清理任务（已知风险，后续）。
- ❌ chat 子图内部逐步 checkpoint 粒度。

## Acceptance Criteria

- [ ] `GET /agents/{agent_id}/history/conversations` 返回按 updated_at desc 的会话列表，分页可用；无数据时返回空列表不报错。
- [ ] 用任一钉钉真实 `conversation_id` 调 R2 端点，返回非空 checkpoint 链（每个 online graph 节点一条）。
- [ ] R2 链中任一 `checkpoint_id` 调 R3，返回该步完整 state values。
- [ ] 新增代码中**没有任何** POST/PUT/PATCH/DELETE 端点，没有 `aupdate_state` / replay 调用（grep 可证）。
- [ ] console「会话历史」页能选 agent → 看到会话列表 → 点开某会话看到时间轴 → 点某步看到 state。
- [ ] 手动输入 conversation_id 也能回看（兜底路径）。
- [ ] 生产钉钉路径行为零变化（`graph_stream.py` / `online_graph.py` 无逻辑改动）。
- [ ] task.py start 前已有 prd.md + design.md + implement.md。
