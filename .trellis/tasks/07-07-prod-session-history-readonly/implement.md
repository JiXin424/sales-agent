# Implement — 真实会话 checkpoint 只读回看

## 执行 checklist（按顺序）

### 后端
- [ ] 1. 新建 `src/sales_agent/api/routes/conversation_history.py`
  - [ ] 1.1 `router = APIRouter(prefix="/agents/{agent_id}/history", tags=["conversation-history"])`
  - [ ] 1.2 `GET /conversations`：`select(Conversation).where(Conversation.agent_id == agent_id).order_by(Conversation.updated_at.desc())`，`limit`/`offset` query（默认 50/0）。Pydantic `ConversationListItem` + `ConversationListResponse`。
  - [ ] 1.3 `GET /conversations/{conversation_id}/checkpoints`：复用 `get_checkpointer()` → compile online graph（或任一注册 graph）→ `aget_state_history({configurable:{thread_id:conversation_id}})` → 按 step asc 排序。**不调 `_ensure_debug_thread`**。
  - [ ] 1.4 `GET /conversations/{conversation_id}/checkpoints/{checkpoint_id}/state`：遍历 history 匹配 `checkpoint_id` → `_safe_serialize(values)`。
  - [ ] 1.5 复用 `graph_debug` 的 `_safe_serialize` / `_checkpoint_node_label`（import；必要时抽到共享 helper）。
- [ ] 2. `src/sales_agent/main.py` 加 `app.include_router(conversation_history.router)`（import + 挂载，参照 `main.py:208`）。
- [ ] 3. **只读自检**：`grep -nE "POST|PUT|PATCH|DELETE|aupdate_state|astream" src/sales_agent/api/routes/conversation_history.py` → 必须为空。

### 前端
- [ ] 4. 新建 `console/src/api/conversationHistory.ts`：`listConversations` / `getConversationCheckpoints` / `getConversationCheckpointState` 三个 GET（走统一 `utils/api.js`）。
- [ ] 5. 新建 `console/src/pages/History/ConversationHistoryPage.tsx`
  - [ ] 5.1 顶部：agent 选择器 + 手动输入 `conversation_id` 框（兜底）。
  - [ ] 5.2 会话列表 `Table`（点行选中 conversation_id）。
  - [ ] 5.3 右侧：选中会话的 checkpoint 时间轴（复用 `CheckpointDAG` / 时间轴），点某步 → `JsonNode` 展示 state。
  - [ ] 5.4 复用组件用**只读模式**（隐藏 GraphDebugPage 的 edit/replay 按钮，必要时加 `readOnly` prop）。
- [ ] 6. `console/src/App.tsx` 加路由 `<Route path="history" element={<ConversationHistoryPage />} />`（顶层 AppLayout 域，import + 注册）。
- [ ] 7. `console/src/layout/Sidebar.tsx` 顶层 `menuItems` 加 `{ key: '/history', icon: <HistoryOutlined />, label: '会话历史' }`（import `HistoryOutlined`）。

### 收尾（CLAUDE.md 规则）
- [ ] 8. 更新 `README.md`「产品文档对照」+「更新日志」节（状态/说明/日期）。
- [ ] 9. 新建/追加 `changelog/2026-07-07.md`：每条含 改动对象 / 类型 / 影响范围 / 改动明细 / 原因；**标注「敏感数据 + 无强鉴权」已知风险**。
- [ ] 10. 更新 `tasks/lessons.md`（若实现中有新教训）。

## 验证命令

```bash
# 后端 import 自检
python -c "from sales_agent.api.routes import conversation_history; print(conversation_history.router.prefix)"

# 只读自检（必须为空输出）
grep -nE "POST|PUT|PATCH|DELETE|aupdate_state|astream\(" src/sales_agent/api/routes/conversation_history.py

# router 挂载
grep -n "conversation_history" src/sales_agent/main.py

# 前端类型检查 / 构建
cd console && npx tsc --noEmit   # 或 npm run build
```

## 生产验证（按 CLAUDE.md 规则 4）

- **必须走生产入口验证**：本项目生产入口是钉钉 Stream（非 HTTP `/agent/chat`，两条路径不同）。本任务不改正生产代码，验证重点：
  1. 用一条真实钉钉 `conversation_id` 调 `GET /agents/{agent_id}/history/conversations/{cid}/checkpoints`，确认返回非空 checkpoint 链（证明 PG checkpoint 可读回）。
  2. `docker logs <tenant>-stream` 确认 stream 容器连上且无 crash、无行为变化（证明未影响生产）。
- 不能只 curl HTTP 200 就判健康。

## 回滚点

- 任一步出问题：删 `conversation_history.py` + 前端新文件，撤 `main.py` / `App.tsx` / `Sidebar.tsx` 三处接入。无 DB 变更、无生产路径改动，回滚零风险。
