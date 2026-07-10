# Design — 真实会话 checkpoint 只读回看

## 模块边界

**新建独立模块，不复用 `graph_debug.py` 的 router**（避免其 `debug:` 前缀语义与安全隔离逻辑被污染）：

| 层 | 新文件 | 说明 |
|---|---|---|
| 后端路由 | `src/sales_agent/api/routes/conversation_history.py` | 新 router，prefix `/agents/{agent_id}/history` |
| 后端共享 | `src/sales_agent/api/routes/graph_debug.py` 内的 `_safe_serialize` / `_checkpoint_node_label` | 直接 import 复用（或抽到 `_graph_debug_helpers.py`，见实现取舍） |
| 前端 API | `console/src/api/conversationHistory.ts` | 3 个 GET |
| 前端页面 | `console/src/pages/History/ConversationHistoryPage.tsx` | 新页 |
| 前端组件 | `CheckpointDAG` / 时间轴 / `JsonNode` | 从 GraphDebugPage 复用（已存在） |

接入点（仅这 3 处改现有文件，纯增量）：
- `src/sales_agent/main.py` — `app.include_router(conversation_history.router)`
- `console/src/App.tsx` — 加路由 `/history`
- `console/src/layout/Sidebar.tsx` — 顶层菜单加「会话历史」

## 数据流

```
[ConversationHistoryPage]
  ├─ agent 选择器（复用现有 agent 列表 API）
  ├─ GET /agents/{aid}/history/conversations            → conversations 表 (updated_at desc)
  ├─ 手动输入 conversation_id 框（兜底）
  │
  ├─ 选中会话 ─→ GET .../conversations/{cid}/checkpoints  → graph.aget_state_history(thread_id=cid)
  │                                                          → checkpoint 链（时间轴）
  └─ 点某步 ───→ GET .../conversations/{cid}/checkpoints/{cp}/state → 匹配 checkpoint_id → values (JSON viewer)
```

## 后端端点契约

三个端点全部 GET，response 模型与 `graph_debug.py` 的 `CheckpointSummary` / `CheckpointStateResponse` 同构（便于前端复用类型）。

| Method | Path | 实现 |
|---|---|---|
| GET | `/agents/{agent_id}/history/conversations` | `select(Conversation).where(agent_id==aid).order_by(updated_at desc).limit/offset`。返回 `ConversationListItem[]`（`conversation_id=id, message, channel, task_type, status, updated_at`）。 |
| GET | `/agents/{agent_id}/history/conversations/{cid}/checkpoints` | `get_checkpointer()` + `build_online_graph().compile(checkpointer=…)`（或任意已注册 graph，关键是用同一 checkpointer 单例）→ `aget_state_history({configurable:{thread_id:cid}})` → 按 step asc 排序。**不调 `_ensure_debug_thread`**。 |
| GET | `/agents/{agent_id}/history/conversations/{cid}/checkpoints/{cp}/state` | 同上 history 遍历，匹配 `configurable.checkpoint_id == cp` → `_safe_serialize(values)`。 |

### 关键技术点（probe 已知，避免踩坑）

1. **读 history 不依赖 builder**：checkpoint 数据是纯 dict state，存在 checkpointer 里，按 `thread_id` 索引。compile 任意 graph（用同一 `get_checkpointer()` 单例）即可读任意 thread 的 history。`graph_debug.py` 的 `_compile_with_checkpointer` 即此模式（compile `chat`，读 `debug:` thread）。
2. **`aget_state_history` 返回 newest-first**：需按 `metadata.step` asc 重排（`graph_debug.py:608` 已有同款逻辑）。
3. **config 必须含 `checkpoint_ns`**：`graph_debug.py` 经 probe 确认 `aupdate_state` / replay 需 `checkpoint_ns=""`。本任务只读 `aget_state_history`，单 `thread_id` 即可，但仍保持 config 形态一致以防 LangGraph 版本差异。
4. **`_safe_serialize`**：state values 含 LangGraph 内部对象，必须过它才能 JSON 序列化（`graph_debug.py:281`）。

## 前端组件复用策略

- 直接从 `GraphDebugPage.tsx` import 已有子组件：`CheckpointDAG`、时间轴 stepper 部分、`JsonNode`。
- 若这些组件当前耦合了 debug-run 专用 props（如 fork/replay 回调），则传入只读模式 props（隐藏 edit/replay 按钮）——实现时确认，必要时给组件加 `readOnly` 开关。
- 会话列表用 antd `Table`；agent 选择器复用现有 agent list API。
- 三 GET 用 react-query 的 `useQuery`，`conversation_id` 作为 query key。

## 安全（已知风险，明确标注）

- 端点无强鉴权，与 `graph_debug` 一致（`main.py` 直接 `include_router`，`deps.py` 无 auth）。
- 真实会话 state 含完整对话、检索内容、用户标识 → **依赖 console 内网部署**。
- README「产品文档对照」+ changelog 标注：后续如需对外开放，必须先加鉴权（RBAC / token）。

## 兼容性 / 回滚

- 纯新增：新 router + 新页 + 新 API client，仅 3 处接入点（main.py / App.tsx / Sidebar.tsx）。
- **无 DB migration、无生产路径改动**。
- 回滚：删新文件 + 撤 3 处接入即可。
