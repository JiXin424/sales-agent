# 设计文档：graph 目录按图重组

## 结构对比

### Before（现状）
```
graph/
├── online_graph.py      (409 行: 6 节点 + 路由 + 构建)
├── chat_graph.py        (195 行: 构建逻辑)
├── state.py             (ChatGraphState)
├── online_state.py      (OnlineConversationState)
├── nodes/               (12 文件, Chat+Online 混放, __init__.py 空)
├── edges/               (2 文件, 只服务 Chat, __init__.py 空)
├── guided_flow/         ✓ 已按图分目录
├── retrieval/           (共享辅助)
├── __init__.py registry.py checkpoints.py retry_policies.py node_metadata.py
```

### After（目标）
```
graph/
├── online/              (4 文件)
│   ├── graph.py         ← online_graph.py 的 build + 辅助函数
│   ├── nodes.py         ← 6 个内联节点 + context_resolution + evidence_routing
│   ├── edges.py         ← route_online_message + route_context_resolution
│   └── state.py         ← online_state.py
├── chat/                (4+10 文件)
│   ├── graph.py         ← chat_graph.py
│   ├── nodes/           ← 旧 nodes/ 中 Chat 专属的 10 个文件（不改内部逻辑）
│   ├── edges.py         ← edges/ 中的 path_conditions + risk_conditions（合并）
│   └── state.py         ← state.py
├── guided_flow/         (不动)
├── retrieval/           (不动)
├── __init__.py registry.py checkpoints.py retry_policies.py node_metadata.py (不动)
```

## 搬迁映射表

### 源码文件搬迁

| 旧路径 | 新路径 | 说明 |
|---|---|---|
| `chat_graph.py` | `chat/graph.py` | 纯搬迁，import 路径更新 |
| `state.py` | `chat/state.py` | 纯搬迁 |
| `online_graph.py` | **拆为 3 个文件** | 见下文 |
| `online_state.py` | `online/state.py` | 纯搬迁 |
| `nodes/fast_commands.py` | `chat/nodes/fast_commands.py` | 纯搬迁 |
| `nodes/validation.py` | `chat/nodes/validation.py` | 纯搬迁 |
| `nodes/tenant_resolve.py` | `chat/nodes/tenant_resolve.py` | 纯搬迁 |
| `nodes/context_load.py` | `chat/nodes/context_load.py` | 纯搬迁 |
| `nodes/routing.py` | `chat/nodes/routing.py` | 纯搬迁 |
| `nodes/retrieval.py` | `chat/nodes/retrieval.py` | 纯搬迁 |
| `nodes/evidence_gate.py` | `chat/nodes/evidence_gate.py` | 纯搬迁 |
| `nodes/generation.py` | `chat/nodes/generation.py` | 纯搬迁 |
| `nodes/risk_check.py` | `chat/nodes/risk_check.py` | 纯搬迁 |
| `nodes/logging_node.py` | `chat/nodes/logging_node.py` | 纯搬迁 |
| `nodes/context_resolution.py` | **合并到** `online/nodes.py` | 函数体复制，原文件删除 |
| `nodes/evidence_routing.py` | **合并到** `online/nodes.py` | 函数体复制，原文件删除 |
| `edges/path_conditions.py` | **合并到** `chat/edges.py` | 内容合并 |
| `edges/risk_conditions.py` | **合并到** `chat/edges.py` | 内容合并 |

### `online_graph.py` 拆解方案（409 行 → 3 文件）

| 提取内容 | 目标文件 | 涉及函数 |
|---|---|---|
| 构建逻辑 + 子图工厂 | `online/graph.py` | `build_online_graph()` `_get_guided_flow_graph()` `_get_chat_graph()` `_unpack_context()` |
| 8 个节点函数 | `online/nodes.py` | `normalize_turn_node` `chat_node` `duplicate_node` `clarification_response_node` `log_control_response_node` `log_flow_output_node` `context_resolution_node`(从 nodes/) `evidence_routing_node`(从 nodes/) |
| 2 个条件路由 | `online/edges.py` | `route_online_message()` `route_context_resolution()` |

### `edges/` → `chat/edges.py` 合并

`path_conditions.py` (`is_fast_command` `select_retrieval_path`) + `risk_conditions.py` (`check_risk_result`) → 一个 `chat/edges.py`

## Import 路径变更清单

### `chat/graph.py`（原 `chat_graph.py`）

```
- from sales_agent.graph.state import ChatGraphState
+ from sales_agent.graph.chat.state import ChatGraphState

- from sales_agent.graph.nodes.fast_commands import fast_command_node
+ from sales_agent.graph.chat.nodes.fast_commands import fast_command_node
  ... (其余 9 个节点同模式)

- from sales_agent.graph.edges.path_conditions import is_fast_command, select_retrieval_path
- from sales_agent.graph.edges.risk_conditions import check_risk_result
+ from sales_agent.graph.chat.edges import is_fast_command, select_retrieval_path, check_risk_result
```

### `online/graph.py`（原 `online_graph.py`）

```
- from sales_agent.graph.chat_graph import build_chat_graph
+ from sales_agent.graph.chat.graph import build_chat_graph

- from sales_agent.graph.guided_flow.graph import build_guided_flow_graph
+ from sales_agent.graph.guided_flow.graph import build_guided_flow_graph  (不变)

- from sales_agent.graph.guided_flow.triggers import ...
+ from sales_agent.graph.guided_flow.triggers import ...  (不变)

- from sales_agent.graph.nodes.context_resolution import context_resolution_node
- from sales_agent.graph.nodes.evidence_routing import evidence_routing_node
+ from sales_agent.graph.online.nodes import context_resolution_node, evidence_routing_node

- from sales_agent.graph.online_state import OnlineConversationState
+ from sales_agent.graph.online.state import OnlineConversationState
```

### `__init__.py`

```
- from sales_agent.graph.chat_graph import build_chat_graph
+ from sales_agent.graph.chat.graph import build_chat_graph

- from sales_agent.graph.online_graph import build_online_graph
+ from sales_agent.graph.online.graph import build_online_graph
```

### `registry.py`

```
- from sales_agent.graph.online_graph import build_online_graph
+ from sales_agent.graph.online.graph import build_online_graph

- from sales_agent.graph.chat_graph import build_chat_graph
+ from sales_agent.graph.chat.graph import build_chat_graph
```

### `services/online_conversation.py`

```
- from sales_agent.graph.online_graph import build_online_graph
+ from sales_agent.graph.online.graph import build_online_graph
```

### `nodes/retrieval.py`（Chat 图内节点，内部 import 不变）

```
from sales_agent.graph.retrieval.ontology_graph import ...  (不变)
from sales_agent.graph.retrieval.web_fallback import ...  (不变)
```

### 测试文件 import 更新（~15 个文件）

| 旧 import | 新 import |
|---|---|
| `from sales_agent.graph.nodes.xxx import` | `from sales_agent.graph.chat.nodes.xxx import`（Chat 节点）/ `from sales_agent.graph.online.nodes import`（Online 节点） |
| `from sales_agent.graph.edges.path_conditions import` | `from sales_agent.graph.chat.edges import` |
| `from sales_agent.graph.edges.risk_conditions import` | `from sales_agent.graph.chat.edges import` |
| `from sales_agent.graph.chat_graph import` | `from sales_agent.graph.chat.graph import` |
| `from sales_agent.graph.state import` | `from sales_agent.graph.chat.state import` |
| `from sales_agent.graph.online_graph import` | `from sales_agent.graph.online.graph import` |
| `from sales_agent.graph.online_state import` | `from sales_agent.graph.online.state import` |

## 兼容性

- **公共 API 不变**：`graph/__init__.py` 的 `__all__` 保持不变，`from sales_agent.graph import build_chat_graph` 仍可用
- **不保留旧路径兼容**：`from sales_agent.graph.nodes.xxx` 不再有效。所有调用方（含测试）同步更新
- **`node_metadata.py`**：用 `{graph_id: {node_id: ...}}` 字符串映射，不受路径变更影响
- **钉钉 stream**：`graph_stream.py` 通过 `get_online_graph()` 获取 compiled graph，不直接 import 节点 → 不受影响

## 风险 & 回滚

- **风险**：搬迁是纯机械操作，风险低。主要风险在 CI 中测试 import 路径遗漏
- **回滚**：git revert 即可。所有变更是路径迁移，无逻辑改动
- **验证**：搬迁后跑全量测试 `pytest tests/unit/graph/ tests/integration/ -x`
