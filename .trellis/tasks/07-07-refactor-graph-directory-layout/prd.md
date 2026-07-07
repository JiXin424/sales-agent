# 重构 graph 目录结构：按图分目录

## Goal

将 `src/sales_agent/graph/` 下散落的节点、边、状态文件按**所属图**归入独立子目录，三个图同级（online / chat / guided_flow），对齐 `guided_flow/` 已有的模式。

**用户价值**：查找/新增节点时一眼知道去哪改，新成员不必先理解演化历史就能根据图名定位到对应目录。

## 目标结构

```
graph/
├── online/              # 主图（stream 入口）
│   ├── graph.py         # build_online_graph, 仅构建逻辑
│   ├── nodes.py         # 7 个 online 独有节点
│   ├── edges.py         # route_online_message, route_context_resolution
│   └── state.py         # OnlineConversationState
├── chat/                # 独立图（HTTP 入口 + online 内嵌对象）
│   ├── graph.py         # build_chat_graph
│   ├── nodes/           # 10 个节点（保持独立文件）
│   ├── edges.py         # is_fast_command, select_retrieval_path, check_risk_result
│   └── state.py         # ChatGraphState
├── guided_flow/         # 独立子图 —— 已存在，不动
│   └── ...
├── retrieval/           # 共享辅助层 —— 不动
├── __init__.py          # 公共 API re-export
├── registry.py          # GRAPH_REGISTRY
├── checkpoints.py       # 共享
├── retry_policies.py    # 共享
└── node_metadata.py     # 共享
```

**设计原则**：三个图同级平铺，`online` 对 `chat`/`guided_flow` 的依赖通过显式 import 表达，不嵌套。`retrieval/` 保持共享辅助层不归入任何图。

## Requirements

1. Chat 图的所有节点（10 个）从 `nodes/` 移入 `chat/nodes/`（保持独立文件）
2. Chat 图的条件边从 `edges/` 移入 `chat/edges.py`
3. `chat_graph.py` → `chat/graph.py`；`state.py` → `chat/state.py`
4. Online 图的 6 个内联节点从 `online_graph.py` 拆到 `online/nodes.py`
5. Online 图的条件路由从 `online_graph.py` 拆到 `online/edges.py`
6. `online_graph.py` → `online/graph.py`（仅构建逻辑）；`online_state.py` → `online/state.py`
7. `context_resolution.py` / `evidence_routing.py` 从 `nodes/` 移入 `online/nodes.py`
8. 旧的 `nodes/` 目录删除（不做向后兼容 re-export）
9. 旧的 `edges/` 目录删除
10. 所有 import 路径更新（graph 内部 + 测试文件）
11. `__init__.py` / `registry.py` 的 import 路径更新
12. 旧的 `chat_graph.py` / `online_graph.py` / `state.py` / `online_state.py` 删除

## Acceptance Criteria

- [ ] `chat/` 目录独立自包含：`graph.py` + `nodes/`(10 文件) + `edges.py` + `state.py`
- [ ] `online/` 目录独立自包含：`graph.py` + `nodes.py` + `edges.py` + `state.py`
- [ ] `guided_flow/` `retrieval/` 保持不动
- [ ] 旧的 `nodes/` `edges/` `chat_graph.py` `online_graph.py` `state.py` `online_state.py` 删除
- [ ] `graph/__init__.py` 的公共 API（`build_chat_graph` / `build_online_graph` / `GRAPH_REGISTRY`）不变
- [ ] `graph/checkpoints.py` `graph/retry_policies.py` `graph/node_metadata.py` 不受影响
- [ ] 所有现有测试通过
- [ ] `build_chat_graph()` / `build_online_graph()` / `build_guided_flow_graph()` 三个 builder 签名和行为不变
- [ ] 升级日志已记录（`changelog/`）

## Out of Scope

- guided_flow 内部重构
- retrieval/ 目录重组
- node_metadata.py 结构变更
- 节点逻辑/行为变更（纯搬迁）
- 向后兼容 re-export（不保留旧路径别名）
