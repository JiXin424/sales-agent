# Short-Term Memory — Operations Runbook

## Architecture

The Online Conversation Graph state is **durable** across process restarts and worker changes:

- **`ConversationTopic`** (application tables, `conversation_topic`) is the semantic source of truth for topic lifecycle, summaries, entities, and goals.
- **PostgreSQL LangGraph checkpointer** (tables `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) stores execution recovery state (flow progress, dedup markers, pending actions). It is **not** a second Topic database — topic metadata lives in application tables.
- **Stable thread ID** (`online:<tenant_id>:<agent_id>:<channel>:<session_user_id>`) has no calendar-date component; the same user always maps to the same thread, regardless of midnight.
- **Turn-scoped reset envelope**: every production turn resets all transient fields (answer, route, etc.) before processing. Only active Topic reference, pending clarification, active Guided Flow, and idempotency state may carry across turns.
- **PostgreSQL advisory lock** (`pg_advisory_xact_lock`, transaction-scoped, blake2b-signed bigint key) serializes same-thread turns across workers and processes.

## Startup / Readiness

- All three process roles (FastAPI `all`, Stream `stream`, Worker `worker`) call `await initialize_online_runtime()` immediately after `await init_db()`.
- `initialize_online_runtime()` opens an unopened `AsyncConnectionPool`, runs `AsyncPostgresSaver.setup()` (idempotent — creates checkpoint tables if missing), compiles the Online Graph once, and caches it.
- **Fail-closed**: if `initialize_online_runtime()` fails (e.g., PostgreSQL unreachable), the role aborts startup immediately. No fallback to InMemorySaver in production.
- `/ready` reports `checkpoint.backend: "postgresql"` and `checkpoint.ready: true` without opening a new connection per request (a pure flag check).
- Shutdown (`close_online_runtime()` before `close_db()`) closes the pool and clears the cached graph.

## Required PostgreSQL checkpoint tables

Created by `langgraph-checkpoint-postgres` during `setup()`:
- `checkpoints` — per-thread state snapshots.
- `checkpoint_writes` — pending writes per checkpoint.
- `checkpoint_blobs` — serialized large values.

These are NOT managed by Alembic. The library's `setup()` is idempotent (CREATE IF NOT EXISTS).

## Running the evaluation gates

```bash
# Deterministic fixture gate (no model credentials required)
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent_test \
  bash scripts/run_short_term_memory_gate.sh

# Router fixture
PYTHONPATH=src:. python3 eval/router/run_router_eval.py \
  --fixture-mode --output /tmp/router-fixture

# Short-term-memory fixture
PYTHONPATH=src:. python3 eval/run_short_term_memory_eval.py \
  --mode fixture --dataset eval/memory/short_term_scenarios.jsonl \
  --output /tmp/short-term-memory-fixture

# Model-backed (requires staging tenant and model credentials)
PYTHONPATH=src:. python3 eval/router/run_router_eval.py \
  --tenant-id "$EVAL_TENANT_ID" --output /tmp/router-eval-model

PYTHONPATH=src:. python3 eval/run_short_term_memory_eval.py \
  --mode model --tenant-id "$EVAL_TENANT_ID" \
  --dataset eval/memory/short_term_scenarios.jsonl \
  --output /tmp/short-term-memory-model
```

## Inspecting a thread

The thread ID is deterministic: `online:<tenant_id>:<agent_id>:<channel>:<user_id>`. To find a user's checkpoint state without exposing message content:

```sql
SELECT thread_id, checkpoint_id, checkpoint->'channel_versions'
  FROM checkpoints
 WHERE thread_id = 'online:t1:a1:dingtalk:u1'
 ORDER BY checkpoint_id DESC LIMIT 1;
```

For topic state (semantic source of truth):

```sql
SELECT id, status, summary, current_goal, created_at, closed_at
  FROM conversation_topic
 WHERE tenant_id = 't1' AND agent_id = 'a1'
   AND user_id = 'u1' AND channel = 'dingtalk'
 ORDER BY created_at DESC;
```

## Diagnostics

| Symptom | What to check |
|---------|--------------|
| `/ready` reports checkpoint not ready | PostgreSQL reachable? Connection pool exhausted? |
| Stale turn state (old answer appears on new turn) | `TURN_SCOPED_DEFAULTS` applied? `build_online_turn_input` used (not hand-built dict)? |
| Topic leakage (old entities on a new topic) | `force_new_topic` honored? `retained_entities` empty on `turn_relation=new`? |
| Duplicate event still sends a reply | `duplicate_node` returns `response_kind=duplicate`? Processor returns early? |
| Lock contention (high latency on same-user turns) | `pg_locks` view for `pg_advisory_xact_lock` hold time? Worker commits promptly? |
| Lost flow state after restart | Checkpoint pool reconnects? Thread ID stable (no date)? |
| Cross-tenant/agent/user leakage | `TopicManager` scoped by all four identity dimensions? `find_restorable_topics` scoped? |

## Restart / multi-worker validation

1. Send a message that starts a guided flow (e.g. `小赢欣赏`).
2. Restart the worker process (`docker restart <container>`).
3. Send a follow-up message with the same user. The restarted worker must see the prior `active_flow` and advance the stage (verified by `test_online_checkpoint_postgres.py`).
4. Repeat with two concurrent turns for the same user from different workers — the second turn must be serialized (verified by `test_online_turn_concurrency.py`).

## Rollback

If the new checkpoint/short-term-memory changes must be reverted:

1. Deploy the prior application version. Checkpoint tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) are separate from application tables and can remain in the database — the old code simply won't use them.
2. If thread-state schema compatibility between versions is uncertain:
   a. Disable affected DingTalk traffic (e.g., `DINGTALK_ENABLED=false`).
   b. Archive or delete ONLY the affected thread checkpoints:
      ```sql
      DELETE FROM checkpoints WHERE thread_id LIKE 'online:t1:a1:%';
      DELETE FROM checkpoint_writes WHERE thread_id LIKE 'online:t1:a1:%';
      DELETE FROM checkpoint_blobs WHERE thread_id LIKE 'online:t1:a1:%';
      ```
   c. Restore service.
3. **Never drop the global checkpoint schema during rollback** — it is shared and may contain production data from non-reverted tenants.
