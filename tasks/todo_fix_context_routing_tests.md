# TODO: 修复 test_context_routing_nodes 3 个失败 — context 传递机制分裂

## 根因假设
LangGraph 1.2.7 项目里 context 传递两套机制并存：
- **老机制 `__pregel_runtime`**（4 处）：context_resolution / evidence_routing / guided_flow/nodes / online_graph
- **新机制 `runtime: Runtime`**（8 处）：generation / routing / risk_check / retrieval / context_load / tenant_resolve / logging_node / ontology_graph

测试和生产入口都用 `ainvoke(context=ctx)` / `astream(context=ctx)` 新机制。
老机制节点用 `config["configurable"]["__pregel_runtime"].context` 读 ctx，
若新机制不塞 `__pregel_runtime` → `_unpack_context` 返回 None → 节点走 passthrough/真实 service → 行为偏离。

3 个失败现象吻合：
- test_graph_clarify_path：期望 clarify，实际 chat（passthrough 返回 resolved → 走 chat）
- test_graph_resolved_path：task_type 不对（evidence_routing override 失效）
- test_pending_continue_through_graph：context_status=None（pending 未处理）

## 阶段 1：探针确认根因
- [ ] 1.1 跑探针：ainvoke(context=ctx) 后，节点 config["configurable"] 里 __pregel_runtime 是否存在？runtime.context 是否存在？
- [ ] 1.2 确认老机制节点在生产里是否真的拿不到 ctx（即 topic management 是否在生产里是死的）
- [ ] 1.3 看 test_graph_resolved_path 的具体失败点（task_type？）

## 阶段 2：确定修复方向
- [ ] 2.1 若 __pregel_runtime 拿不到 → 把 3 个老机制节点统一改成 `runtime: Runtime`（与 generate/routing 等一致）
- [ ] 2.2 若 __pregel_runtime 拿得到 → 另查原因（测试 fixture / 节点签名）
- [ ] 2.3 评估修复影响：老机制节点改 runtime: Runtime 后，online_graph 里 __pregel_runtime 的引用是否还要清

## 阶段 3：实施修复
- [ ] 3.1 改 context_resolution_node：(state, config) → (state, runtime: Runtime)，删 _unpack_context
- [ ] 3.2 改 evidence_routing_node：同上
- [ ] 3.3 改 guided_flow/nodes.py：同上
- [ ] 3.4 清理 online_graph.py 里的 __pregel_runtime 引用（如果有）
- [ ] 3.5 确认测试 fixture（_build_config 的 __pregel_runtime）是否还需要

## 阶段 4：验证
- [ ] 4.1 跑 test_context_routing_nodes.py 全部通过
- [ ] 4.2 跑 tests/unit/graph/ + tests/integration/ 不破
- [ ] 4.3 生产入口验证（stream 日志）—— 若是生产 bug，修复后 topic management 应恢复

## Review

### 根因（探针确认）
**不是 context 机制分裂**——LangGraph 1.2.7 的 `ainvoke(context=ctx)` 向后兼容，ctx 正确塞进 `config["configurable"]["__pregel_runtime"].context`，老机制 `_unpack_context` 能拿到。两套机制（`__pregel_runtime` / `runtime: Runtime`）都能用，只是风格不同。

**真正根因**：commit `48c6f9f`（2026-07-06）给 `normalize_turn` 加了 `topic_routing_enabled` rollout switch（`online_graph.py:122-124`），默认 False 时把 `flow_action` 从 `chat` 改成 `direct_chat`，**绕过 context_resolution**。同天的测试 commit `15e1452` 没在 state 里设 `topic_routing_enabled=True`，导致被测节点被绕过 → `context_status=None`。

3 个失败现象全部吻合：
- test_graph_resolved_path：context_status=None（context_resolution 没执行）
- test_graph_clarify_path：response_kind="chat"（绕过 context_resolution 直连 chat，没走 clarify）
- test_pending_continue_through_graph：context_status=None（pending 没处理）

### 修复
1. **测试**：3 个失败测试 state 加 `"topic_routing_enabled": True`（test_graph_resolved_path / test_graph_clarify_path / test_pending_continue_through_graph）。test_guided_flow_bypasses 是 guided_flows_enabled=True 不受影响。
2. **生产 bug 顺手修**：钉钉 stream 入口 `graph_stream.py:91-103` 的 input_state 设了 `guided_flows_enabled` 但漏设 `topic_routing_enabled`——与测试同源（rollout switch 没全覆盖）。导致即使 `TOPIC_ROUTING_ENABLED=true`，stream 路径 topic routing 仍关闭。加一行 `"topic_routing_enabled": settings.topic_routing.enabled`（仿 online_conversation.py:221）。

### 验证
- `test_context_routing_nodes.py` 14 全过（原 11 pass + 3 fail → 14 pass）
- `tests/unit/graph/` 143 全过，零回归
- stream 模块 import OK

### 关键教训
加 rollout switch（默认关闭绕过某节点）时，必须同步：① 所有入口把 config 开关传到 state；② 所有测该节点的测试在 state 里显式开启。漏一处 = 开关失效（入口）或测试假失败（测试）。`topic_routing_enabled` 漏了 stream 入口 + 3 个测试，同源。

