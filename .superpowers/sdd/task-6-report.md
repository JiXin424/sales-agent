# Task 6 Implementation Report: Online Conversation Graph

## Status: DONE

## What was built

### Files created (4 new)

1. **`/root/code/sales-agent/src/sales_agent/graph/online_state.py`** — `OnlineConversationState` TypedDict (narrow fields only, does NOT inherit `ChatGraphState`). Contains identity/input fields, guided-flow control fields, output fields, and `last_event_id` for deduplication.

2. **`/root/code/sales-agent/src/sales_agent/graph/online_graph.py`** — The unified online conversation graph with:
   - `normalize_turn_node` — sets `requested_flow` on every turn (explicit `None` when not triggered) and chooses `flow_action` with priority: duplicate > start > cancel > advance > chat.
   - `route_online_message` — conditional edge routing based on `flow_action`.
   - `guided_flow` node — invokes cached compiled Guided Flow subgraph (no checkpointer).
   - `chat` node — invokes cached compiled Chat Graph (no checkpointer) and maps only stable response fields. Supports `chat_runner` override via runtime context for tests.
   - `duplicate_node` — sets `response_kind="duplicate"` without updating `last_event_id`.
   - `log_flow_output_node` — logs guided-flow output via `conversation_logger.log_conversation` with `task_type=active_flow_or_completed_flow`, `path="guided_flow"`.

3. **`/root/code/sales-agent/src/sales_agent/services/online_conversation.py`** — Runtime service with:
   - `build_online_thread_id` — thread ID format `online:<tenant_id>:<agent_id>:<channel>:<session_user_id>:<YYYY-MM-DD>` in Asia/Shanghai timezone.
   - `get_online_graph` — cached compiled Online Graph with process-level `InMemorySaver` singleton (production) or injectable checkpointer (tests).
   - `invoke_online_turn` — public API that resolves agent, resolves models (via `TenantResolver` when not provided), builds thread ID, and invokes the graph.

4. **`/root/code/sales-agent/tests/unit/graph/test_online_graph.py`** — 15 tests covering:
   - Duplicate event detection (`test_duplicate_event_does_not_advance`, `test_first_event_is_not_duplicate`)
   - Flow preemption (`test_new_flow_preempts_existing_flow`)
   - Cancel beats advance (`test_cancel_clears_active_flow`)
   - Guided flows disabled always routes to chat (`test_guided_disabled_routes_to_chat`, `test_guided_disabled_no_trigger_routes_to_chat`)
   - No active flow / no trigger routes to chat (`test_no_flow_trigger_routes_to_chat`)
   - State persistence (`test_fresh_saver_has_no_active_flow`, `test_same_thread_carries_active_flow`)
   - `last_event_id` tracking (`test_last_event_id_updated_on_non_duplicate`, `test_duplicate_does_not_update_last_event_id`)
   - Thread ID format and day-bounding (`test_thread_id_format`, `test_thread_id_changes_with_date`, `test_thread_id_separates_users`, `test_thread_id_same_day_same_user_is_identical`)

### Files modified (2 existing)

5. **`/root/code/sales-agent/src/sales_agent/graph/checkpoints.py`** — Added `get_online_checkpointer_sync()` returning a process-level `InMemorySaver` singleton for the Online Graph.

6. **`/root/code/sales-agent/src/sales_agent/graph/__init__.py`** — Added exports for `build_online_graph` and `get_online_checkpointer_sync`.

## Test results

```
tests/unit/graph/test_online_graph.py ...............                  15 passed
tests/unit/graph/guided_flow/ ........................                 21 passed
tests/unit/graph/test_chat_graph.py ...                                3 passed
tests/unit/graph/test_checkpoints.py ...                               3 passed
---------------------------------------------------------------------
Total:                                                               42 passed
```

## Commits made

- `13ed5eb` feat: add unified online conversation graph (6 files, +941/-5 lines)

## Key design decisions

- **Chat node invokes cached Chat Graph directly** rather than as a LangGraph subgraph node — this prevents `ChatGraphState` fields from polluting the online checkpoint (Online State is narrow by design).
- **Guided Flow subgraph added as a proper compiled subgraph node** — the state schemas overlap naturally, and the parent checkpointer owns persistence.
- **`chat_runner` override via runtime context** — tests inject a `StubChatRunner` to avoid needing a real chat model/database for routing tests.
- **`conversation_logger.log_conversation` called in `log_flow_output_node`** for guided-flow output only; chat output is logged by the Chat Graph's own `log_node` (no double-logging).

## Concerns

None.

---

## Post-review fixes (Task 6 code review findings)

### Fix 1: `get_online_graph` caching no longer silently ignores checkpointer parameter
**File:** `src/sales_agent/services/online_conversation.py:81-115`

The function previously cached `_online_graph` unconditionally on first call. If `get_online_graph(checkpointer=my_saver)` was called after the cache was populated, the custom checkpointer was silently discarded.

**Fix:** When a non-None checkpointer is passed, the function now skips the cache entirely and returns a fresh compilation. Only the default-path (checkpointer=None) result is cached. This guarantees every call with an explicit checkpointer gets exactly that checkpointer, while production callers (which pass None) still benefit from the singleton.

### Fix 2: `get_online_graph` now uses `get_online_checkpointer_sync()` from `checkpoints.py`
**File:** `src/sales_agent/services/online_conversation.py:110-113`

The module previously maintained its own `_online_checkpointer` module-level variable and lazily created an `InMemorySaver` inline. The function `get_online_checkpointer_sync()` existed in `checkpoints.py` and was exported from `graph/__init__.py` but was never called — it was dead code.

**Fix:** Removed the `_online_checkpointer` variable (and the `from langgraph.checkpoint.memory import InMemorySaver` import). The default path now calls `get_online_checkpointer_sync()` from `checkpoints.py`, making the intended design real.

### Test results after fix

```
tests/unit/graph/test_online_graph.py ...............                  15 passed
tests/unit/graph/guided_flow/ ........................                 21 passed
---------------------------------------------------------------------
Total:                                                                36 passed
```

### Commit

- `a12e2e0` fix: wire `get_online_graph` to `get_online_checkpointer_sync()` and fix caching
