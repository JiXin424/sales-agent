# Task 6 Implementation Report: Memory Integration in Online Graph

## Status: DONE

## What was built

### Files modified (8 files)

1. **`src/sales_agent/graph/online/state.py`** -- Added memory state fields: `long_term_memory_enabled`, `memory_operation`, `memory_status`, `memory_reason_code`, `memory_ids`, `memory_candidate_count`. Also updated `flow_action` comment to include `"memory_command"`.

2. **`src/sales_agent/services/online_conversation.py`** -- Added memory defaults to `TURN_SCOPED_DEFAULTS`, added `long_term_memory_enabled` parameter to `build_online_turn_input()`, and wired `settings.long_term_memory.enabled` in `prepare_online_turn()`.

3. **`src/sales_agent/graph/online/nodes.py`** -- Added imports for `detect_memory_command`, `apply_memory_command`, `MemoryScope`, `MemoryOperationResult`, `AtomicMemoryRepository`. Added memory command routing (priority 3) in `normalize_turn_node`. Added `memory_command_node` (handles explicit remember/correct/forget) and `enqueue_memory_candidate_node` (enqueues inferred-memory outbox job after chat).

4. **`src/sales_agent/graph/online/edges.py`** -- Added `"memory_command"` to docstring return types. No code change needed as `route_online_message` already returns `flow_action` directly.

5. **`src/sales_agent/graph/online/graph.py`** -- Imported and added `memory_command_node` and `enqueue_memory_candidate_node` to the builder. Added `"memory_command"` to the conditional edge routing map. Changed `chat -> END` to `chat -> enqueue_memory_candidate -> END`. Added `memory_command -> END`.

6. **`src/sales_agent/integrations/dingtalk/turn_result.py`** -- Added `memory_operation`, `memory_status`, `memory_reason_code`, `memory_ids`, `memory_candidate_count` fields to `DingTalkTurnResult`.

7. **`src/sales_agent/integrations/dingtalk/processor.py`** -- Mapped memory fields from Graph result in both duplicate and normal return paths.

8. **`tests/unit/graph/test_online_graph.py`** -- Added `test_normalize_routes_explicit_memory_command_before_chat` and `test_duplicate_still_wins_before_memory_command`.

### Files created (1 file)

9. **`tests/integration/test_dingtalk_long_term_memory.py`** -- Integration test `test_dingtalk_explicit_remember_creates_active_memory` that exercises `handle_dingtalk_event` with monkeypatched config and verifies the resulting `AtomicMemory` DB row.

## Test results

```
tests/unit/graph/test_online_graph.py ............                      32 passed
tests/unit/dingtalk/test_online_flow_routing.py ...........             16 passed
tests/unit/memory ..................................................    18 passed
---------------------------------------------------------------------
Total:                                                                 66 passed
```

## Key design decisions

- **Memory command routing priority**: Duplicate > Reset > Memory command > Start > Cancel > Advance > Chat. This ensures deduplication and reset semantics are never overridden by memory commands.
- **Enqueue after chat**: The inferred-memory outbox job is enqueued AFTER the chat response is formed, so the assistant answer text is never used as memory evidence.
- **Fail-open enqueue**: `enqueue_memory_candidate_node` catches all exceptions so outbox failure never blocks the primary answer.
- **Graceful db missing**: `memory_command_node` returns a clear error message when DB is not available, rather than crashing.

## Critical behaviors verified

1. Duplicate always wins -- even over memory commands (`test_duplicate_still_wins_before_memory_command`)
2. Reset wins over memory commands (existing `test_normalize_turn_reset_priority_after_duplicate`)
3. Memory command detected before guided flow start (`test_normalize_routes_explicit_memory_command_before_chat`)
4. Enqueue after chat (graph wiring: `chat -> enqueue_memory_candidate -> END`)
5. Outbox failure never blocks primary answer (exception caught in `enqueue_memory_candidate_node`)
6. All DingTalk returns have memory fields (both duplicate and normal paths in processor.py)
7. Memory command node handles missing db gracefully (returns `memory_status="failed"` with `reason_code="missing_db"`)
