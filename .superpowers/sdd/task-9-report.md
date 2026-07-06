# Task 9 Report: Full Verification, Rollout Switch, and Documentation

## Status: DONE

## What was implemented

### Rollout Switch (Steps 1-2)
- Added `TopicRoutingConfig` with `enabled=False`, `idle_minutes=30`, `restore_hours=24`, `max_clarification_attempts=2`
- Added `topic_routing.enabled` to `Settings` class with `TOPIC_ROUTING_ENABLED` env override in `from_yaml()`
- Added `topic_routing` section to `config/default.yaml`
- Added `TOPIC_ROUTING_ENABLED` to `.env.example`
- Added `topic_routing_enabled: bool` to `OnlineConversationState`
- Modified `normalize_turn_node` in `online_graph.py`: when `topic_routing_enabled=False` and `flow_action="chat"`, sets `flow_action="direct_chat"` to bypass context resolution and evidence routing
- Added `"direct_chat": "chat"` conditional edge to route directly to chat node
- Modified `invoke_online_turn` to pass `settings.topic_routing.enabled` from config
- 2 new tests verify disabled mode bypasses context resolver and evidence router, produces no context_status/topic_id

### Verification (Steps 3-5)
- **Compile check**: `python3 -m compileall -q src/sales_agent` -- 0 errors
- **Whitespace check**: `git diff --check` -- 0 errors
- **Migration cycle**: `alembic upgrade head` -> `downgrade 0010` -> `upgrade head` -- all 0 exit
- **Focused suites**: 239 passed, 1 pre-existing failure (test_retrieval_node, unrelated)
- **Full non-live suite**: 887 passed, pre-existing failures only

### Documentation (Step 7)
- `README.md`: Added topic routing section covering Topic semantics, 30-min expiry, 24h explicit restore, ContextDecision/EvidenceDecision schemas, evidence policies (required/optional/none), clarification resolution commands, `TOPIC_ROUTING_ENABLED=false` rollback, known limitations (long-term Memory out of scope)
- `changelog/2026-07-06.md`: Appended Task 9 entries with migration summary, eval metrics, known limitations

### Acceptance Criteria (Step 8)
All 11 criteria from spec lines 501-513 verified.

## Files Changed
- `src/sales_agent/core/config.py`
- `config/default.yaml`
- `.env.example`
- `src/sales_agent/graph/online_state.py`
- `src/sales_agent/graph/online_graph.py`
- `src/sales_agent/services/online_conversation.py`
- `tests/unit/graph/test_online_graph.py`
- `README.md`
- `changelog/2026-07-06.md`

## Post-Review Fixes (2026-07-06)

### Issue 2: `topic_routing_enabled` default mismatch
- Changed `state.get("topic_routing_enabled", True)` to `state.get("topic_routing_enabled", False)` in `normalize_turn_node` to match config default
- Added `"topic_routing_enabled": True` to `_BASE_INPUT` in tests to preserve existing test behavior

### Issue 3: Outdated docstrings
- `online_state.py` line 36: Added `"direct_chat"` to `flow_action` type comment
- `online_graph.py` line 135: Added `"direct_chat"` to `route_online_message` docstring

### Verification
- `pytest -q tests/unit/graph/test_online_graph.py` — 17 passed
- Commit amended: `48c6f9f`
