# 执行计划：graph 目录按图重组

## Phase 1: 创建目标目录

- [ ] 1.1 创建 `chat/nodes/` `online/` 目录
- [ ] 1.2 创建 `chat/__init__.py` `chat/nodes/__init__.py` `online/__init__.py`（空文件）

## Phase 2: 纯搬迁（git mv 保留历史）

- [ ] 2.1 `git mv state.py chat/state.py`
- [ ] 2.2 `git mv online_state.py online/state.py`
- [ ] 2.3 `git mv chat_graph.py chat/graph.py`
- [ ] 2.4 `git mv nodes/fast_commands.py chat/nodes/fast_commands.py`
- [ ] 2.5 `git mv nodes/validation.py chat/nodes/validation.py`
- [ ] 2.6 `git mv nodes/tenant_resolve.py chat/nodes/tenant_resolve.py`
- [ ] 2.7 `git mv nodes/context_load.py chat/nodes/context_load.py`
- [ ] 2.8 `git mv nodes/routing.py chat/nodes/routing.py`
- [ ] 2.9 `git mv nodes/retrieval.py chat/nodes/retrieval.py`
- [ ] 2.10 `git mv nodes/evidence_gate.py chat/nodes/evidence_gate.py`
- [ ] 2.11 `git mv nodes/generation.py chat/nodes/generation.py`
- [ ] 2.12 `git mv nodes/risk_check.py chat/nodes/risk_check.py`
- [ ] 2.13 `git mv nodes/logging_node.py chat/nodes/logging_node.py`

## Phase 3: 拆分 online_graph.py + 合并 context_resolution/evidence_routing

- [ ] 3.1 `git mv online_graph.py online/graph.py`
- [ ] 3.2 从 `online/graph.py` 中提取 `normalize_turn_node` `chat_node` `duplicate_node` `clarification_response_node` `log_control_response_node` `log_flow_output_node` → 写入 `online/nodes.py`
- [ ] 3.3 复制 `nodes/context_resolution.py` 的 `context_resolution_node` 函数到 `online/nodes.py`（保留所有内部 import）
- [ ] 3.4 复制 `nodes/evidence_routing.py` 的 `evidence_routing_node` 函数（含辅助函数）到 `online/nodes.py`
- [ ] 3.5 从 `online/graph.py` 中提取 `route_online_message` `route_context_resolution` → 写入 `online/edges.py`
- [ ] 3.6 `online/graph.py` 中清理，只保留 `build_online_graph` `_get_guided_flow_graph` `_get_chat_graph` `_unpack_context`，并 import nodes 和 edges

## Phase 4: 合并 edges/ 到 chat/edges.py

- [ ] 4.1 创建 `chat/edges.py`，复制 `edges/path_conditions.py` 和 `edges/risk_conditions.py` 的全部内容

## Phase 5: 更新 Import 路径

- [ ] 5.1 更新 `chat/graph.py` 的 import（10 个节点 + 2 个 edges + state）
- [ ] 5.2 更新 `online/graph.py` 的 import（chat 子图、online nodes/edges/state）
- [ ] 5.3 更新 `online/nodes.py` 的 import（context_resolution/evidence_routing 的内部依赖）
- [ ] 5.4 更新 `online/edges.py` 的 import
- [ ] 5.5 更新 `__init__.py` 的 import
- [ ] 5.6 更新 `registry.py` 的 import
- [ ] 5.7 更新 `services/online_conversation.py` 的 import
- [ ] 5.8 更新所有测试文件 import：
  - `tests/unit/graph/test_risk_node.py`
  - `tests/unit/graph/test_evidence_gate.py`
  - `tests/unit/graph/test_validation_node.py`
  - `tests/unit/graph/test_retrieval_node.py`
  - `tests/unit/graph/test_retrieval_web_fallback.py`
  - `tests/unit/graph/test_routing_node.py`
  - `tests/unit/graph/test_generation_node.py`
  - `tests/unit/graph/test_context_routing_nodes.py`
  - `tests/unit/graph/test_chat_graph.py`
  - `tests/unit/graph/test_online_graph.py`
  - `tests/integration/test_topic_memory_flow.py`
  - `tests/integration/test_graph_pipeline_parity.py`
  - `tests/unit/test_web_fallback.py`

## Phase 6: 清理旧文件 & 目录

- [ ] 6.1 删除 `nodes/context_resolution.py` `nodes/evidence_routing.py`（已合并到 online/nodes.py）
- [ ] 6.2 删除 `nodes/__init__.py`（该目录只剩它）
- [ ] 6.3 删除 `edges/path_conditions.py` `edges/risk_conditions.py` `edges/__init__.py`（已合并到 chat/edges.py）
- [ ] 6.4 `rmdir nodes/ edges/`（应为空目录）

## Phase 7: 验证

- [ ] 7.1 运行全量 graph 测试: `pytest tests/unit/graph/ -x -v`
- [ ] 7.2 运行集成测试: `pytest tests/integration/ -x -v`
- [ ] 7.3 确认无 import 错误: `python -c "from sales_agent.graph import build_chat_graph, build_online_graph, GRAPH_REGISTRY"`
- [ ] 7.4 `git status` 确认无遗漏的旧文件

## Phase 8: 收尾

- [ ] 8.1 更新 `changelog/2026-07-07.md`
- [ ] 8.2 更新 `README.md`（如有产品文档对照节涉及 graph 路径）
- [ ] 8.3 Commit: `refactor(graph): reorganize directory layout by graph`

## 回滚点

每一步 commit 都可独立 revert。建议 phase 2~4 完成后先 commit，phase 5（import 更新）完成后 commit，phase 6~7（清理+验证）完成后 commit。三阶段对应三个 commit，出问题逐级回滚。
