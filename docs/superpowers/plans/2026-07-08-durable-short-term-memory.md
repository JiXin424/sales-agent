# Durable Short-Term Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Online Conversation Graph, Topic lifecycle, clarification, and Guided Flows durable across restart and worker changes, then verify them through the real DingTalk business path.

**Architecture:** `ConversationTopic` and topic-scoped messages remain the semantic source of truth, while a fail-closed PostgreSQL LangGraph checkpointer persists execution state. A stable user-scoped thread ID, explicit new-turn envelope, PostgreSQL advisory lock, bounded Topic restore protocol, and shared standard/stream invocation preparation remove process, midnight, stale-state, duplicate, and concurrency failure modes.

**Tech Stack:** Python 3.10+; LangGraph 1.2+; `langgraph-checkpoint-postgres`; `psycopg_pool`; FastAPI lifespan; SQLAlchemy asyncio; PostgreSQL; Pydantic 2; pytest; pytest-asyncio; JSONL; DingTalk Stream/HTTP adapters.

## Global Constraints

- `ConversationTopic` is the semantic source of truth; checkpoints store execution recovery state and must not become a second Topic database.
- Production Online Graph state uses PostgreSQL and never silently falls back to `InMemorySaver`; tests may inject `InMemorySaver`.
- The production thread key is exactly `online:{tenant_id}:{agent_id}:{channel}:{session_user_id}` and has no calendar-date component.
- Topic idle expiry remains exactly 30 minutes and explicit restore remains bounded to topics closed within 24 hours.
- The five `turn_relation` values remain `continue`, `revise`, `switch`, `new`, and `ambiguous`.
- Every production turn uses a complete turn-scoped reset envelope; only Topic, pending clarification, active Guided Flow, and idempotency state may carry across turns.
- HTTP and Stream DingTalk paths use the same thread-key, turn-envelope, locking, Graph, and Topic rules.
- Duplicate DingTalk `event_id` values must not advance a flow, invoke Chat twice, or emit a second user reply.
- Turns for the same thread are serialized across workers; turns for different users remain concurrent.
- All Topic queries, locks, and observations are scoped by tenant, Agent, user, and channel.
- Tests execute against an isolated PostgreSQL test database; never run a schema-dropping test against a development or production tenant database.
- Real DingTalk business-flow tests replace only public outbound delivery; user mapping, conversation mapping, command parsing, Agent resolution, Online Graph, Topic/message persistence, and rendering remain real.
- Existing unrelated worker changes in `eval/deepeval_eval.py`, `eval/deepeval_html_report.py`, `.claude/`, and `.deepeval/` are not part of this plan and must not be staged or overwritten.

---

## File Map

### Create

- `src/sales_agent/graph/checkpoint_runtime.py` — fail-closed PostgreSQL checkpointer lifecycle and readiness state.
- `src/sales_agent/services/online_turn_lock.py` — stable advisory-lock key and same-thread serialization.
- `src/sales_agent/services/topic_restore.py` — bounded restore selection and structured restore decisions.
- `src/sales_agent/prompts/topic_restore_resolver_prompt.py` — closed candidate selection prompt.
- `src/sales_agent/integrations/dingtalk/turn_result.py` — immutable observable result from the shared DingTalk processor.
- `tests/unit/graph/test_checkpoint_runtime.py`
- `tests/unit/test_health_readiness.py`
- `tests/unit/test_online_turn_lock.py`
- `tests/unit/test_topic_restore.py`
- `tests/integration/test_online_checkpoint_postgres.py`
- `tests/integration/test_online_turn_concurrency.py`
- `tests/integration/test_dingtalk_multiturn_memory.py`
- `tests/support/dingtalk_scenario.py`
- `eval/router/clarification_resolution_cases.jsonl`
- `eval/memory/short_term_scenarios.jsonl`
- `eval/run_short_term_memory_eval.py`
- `tests/unit/eval/test_short_term_memory_eval.py`
- `tests/unit/test_short_term_memory_gate.sh`
- `scripts/run_short_term_memory_gate.sh`
- `docs/runbooks/short-term-memory.md`

### Modify

- `src/sales_agent/graph/checkpoints.py`
- `src/sales_agent/graph/__init__.py`
- `src/sales_agent/graph/online/state.py`
- `src/sales_agent/graph/online/nodes.py`
- `src/sales_agent/graph/chat/nodes/logging_node.py`
- `src/sales_agent/services/online_conversation.py`
- `src/sales_agent/services/topic_manager.py`
- `src/sales_agent/services/structured_router_output.py`
- `src/sales_agent/main.py`
- `src/sales_agent/roles/stream_runner.py`
- `src/sales_agent/roles/worker_runner.py`
- `src/sales_agent/api/routes/health.py`
- `src/sales_agent/integrations/dingtalk/processor.py`
- `src/sales_agent/integrations/dingtalk/graph_stream.py`
- `eval/router/run_router_eval.py`
- `tests/unit/graph/test_checkpoints.py`
- `tests/unit/graph/test_online_graph.py`
- `tests/unit/graph/test_context_routing_nodes.py`
- `tests/unit/dingtalk/test_online_flow_routing.py`
- `tests/unit/test_router_eval.py`
- `tests/unit/test_topic_manager.py`
- `tests/unit/test_process_role.py`
- `tests/unit/graph/guided_flow/test_graph.py`
- `tests/integration/test_topic_memory_flow.py`
- `tests/integration/test_online_guided_flows.py`
- `README.md`

### Delete

- No production module is deleted in Spec 1.

---

### Task 1: Build the fail-closed PostgreSQL checkpoint runtime

**Files:**
- Create: `src/sales_agent/graph/checkpoint_runtime.py`
- Modify: `src/sales_agent/graph/checkpoints.py`
- Modify: `src/sales_agent/graph/__init__.py`
- Create: `tests/unit/graph/test_checkpoint_runtime.py`
- Modify: `tests/unit/graph/test_checkpoints.py`

**Interfaces:**
- Consumes: `Settings.database.url`, `AsyncConnectionPool`, `AsyncPostgresSaver`.
- Produces: `CheckpointUnavailableError`; `initialize_production_checkpointer(database_url: str | None = None) -> AsyncPostgresSaver`; `get_production_checkpointer() -> AsyncPostgresSaver`; strict compatibility accessor `get_checkpointer() -> AsyncPostgresSaver`; `close_production_checkpointer() -> None`; `production_checkpoint_ready() -> bool`; test-only `get_checkpointer_sync() -> InMemorySaver`.

- [ ] **Step 1: Write failing lifecycle tests**

Create fakes that record `pool.open()`, `saver.setup()`, and `pool.close()`. Assert initialization caches one saver, access before initialization raises, setup failure leaves readiness false, and close clears readiness:

```python
@pytest.mark.asyncio
async def test_initialize_opens_pool_runs_setup_and_caches(monkeypatch):
    events = []

    class FakePool:
        def __init__(self, conninfo, min_size, max_size, open):
            assert open is False
            events.append(("created", conninfo, min_size, max_size))
        async def open(self):
            events.append("opened")
        async def close(self):
            events.append("closed")

    class FakeSaver:
        def __init__(self, conn):
            self.conn = conn
        async def setup(self):
            events.append("setup")

    monkeypatch.setattr(runtime, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(runtime, "AsyncPostgresSaver", FakeSaver)

    saver = await runtime.initialize_production_checkpointer(
        "postgresql+asyncpg://user:pass@db/app"
    )
    assert saver is runtime.get_production_checkpointer()
    assert runtime.production_checkpoint_ready() is True
    assert events == [
        ("created", "postgresql://user:pass@db/app", 1, 5),
        "opened", "setup",
    ]

    await runtime.close_production_checkpointer()
    assert events[-1] == "closed"
    assert runtime.production_checkpoint_ready() is False


def test_access_before_initialize_fails_closed():
    with pytest.raises(runtime.CheckpointUnavailableError):
        runtime.get_production_checkpointer()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/graph/test_checkpoint_runtime.py
```

Expected: FAIL because `checkpoint_runtime.py` and its lifecycle functions do not exist.

- [ ] **Step 3: Implement the lifecycle module**

Use an unopened pool, explicitly open it, run the library schema setup, cache only after setup succeeds, and close a partially initialized pool on failure:

```python
class CheckpointUnavailableError(RuntimeError):
    pass


_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


async def initialize_production_checkpointer(
    database_url: str | None = None,
) -> AsyncPostgresSaver:
    global _pool, _saver
    if _saver is not None:
        return _saver

    url = database_url or get_settings().database.url
    conninfo = url.replace("+asyncpg", "")
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=5,
        open=False,
    )
    try:
        await pool.open()
        saver = AsyncPostgresSaver(conn=pool)
        await saver.setup()
    except Exception as exc:
        await pool.close()
        raise CheckpointUnavailableError(
            "PostgreSQL checkpoint initialization failed"
        ) from exc

    _pool = pool
    _saver = saver
    return saver


def get_production_checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise CheckpointUnavailableError(
            "Production checkpoint runtime is not initialized"
        )
    return _saver
```

Keep `get_checkpointer_sync()` in `checkpoints.py` for tests. Remove `get_online_checkpointer_sync()` and the fallback that catches every error and returns `InMemorySaver`. Preserve the existing awaited call contract as a strict accessor so Task 1 does not break current callers:

```python
async def get_checkpointer() -> AsyncPostgresSaver:
    return get_production_checkpointer()
```

Re-export strict production lifecycle functions, this strict compatibility accessor, and the explicit test saver.

- [ ] **Step 4: Run checkpoint unit tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_checkpoint_runtime.py \
  tests/unit/graph/test_checkpoints.py
```

Expected: PASS; no test expects production fallback.

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/graph/checkpoint_runtime.py \
  src/sales_agent/graph/checkpoints.py src/sales_agent/graph/__init__.py \
  tests/unit/graph/test_checkpoint_runtime.py tests/unit/graph/test_checkpoints.py
git commit -m "refactor(graph): add strict postgres checkpoint runtime"
```

---

### Task 2: Wire checkpoint startup, shutdown, cached Online Graph, and readiness

**Files:**
- Modify: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/main.py`
- Modify: `src/sales_agent/roles/stream_runner.py`
- Modify: `src/sales_agent/roles/worker_runner.py`
- Modify: `src/sales_agent/api/routes/health.py`
- Modify: `src/sales_agent/integrations/dingtalk/graph_stream.py`
- Modify: `tests/unit/graph/test_online_graph.py`
- Create: `tests/integration/test_online_checkpoint_postgres.py`
- Create: `tests/unit/test_health_readiness.py`
- Modify: `tests/unit/test_process_role.py`

**Interfaces:**
- Consumes: Task 1 lifecycle functions and `build_online_graph()`.
- Produces: `initialize_online_runtime() -> CompiledStateGraph`; `close_online_runtime() -> None`; strict `get_online_graph(*, checkpointer=None) -> CompiledStateGraph`; readiness field `checkpoint.ready`.

- [ ] **Step 1: Write failing startup and strict-default tests**

Assert the default graph cannot be fetched before initialization, injected test checkpointers still compile independently, and initialization compiles exactly once with the production saver:

```python
def test_default_online_graph_requires_initialized_runtime(monkeypatch):
    monkeypatch.setattr(online_conversation, "_online_graph", None)
    with pytest.raises(CheckpointUnavailableError):
        online_conversation.get_online_graph()


@pytest.mark.asyncio
async def test_initialize_online_runtime_compiles_once(monkeypatch):
    saver = object()
    compiled = object()
    init = AsyncMock(return_value=saver)
    compile_graph = MagicMock(return_value=compiled)
    monkeypatch.setattr(online_conversation, "initialize_production_checkpointer", init)
    monkeypatch.setattr(online_conversation, "_compile_online_graph", compile_graph)

    first = await online_conversation.initialize_online_runtime()
    second = await online_conversation.initialize_online_runtime()

    assert first is second is compiled
    init.assert_awaited_once()
    compile_graph.assert_called_once_with(saver)
```

Add `tests/unit/test_health_readiness.py` and assert `checkpoint: {"ready": false}` makes `/ready` return `not_ready`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py \
  tests/unit/test_health_readiness.py \
  tests/unit/test_process_role.py \
  tests/integration/test_online_checkpoint_postgres.py
```

Expected: FAIL because startup does not initialize the strict runtime and the default Online Graph still uses process memory.

- [ ] **Step 3: Implement Online Graph lifecycle**

Add a small compile seam and strict cache:

```python
def _compile_online_graph(checkpointer) -> CompiledStateGraph:
    return build_online_graph().compile(checkpointer=checkpointer)


async def initialize_online_runtime() -> CompiledStateGraph:
    global _online_graph
    if _online_graph is not None:
        return _online_graph
    saver = await initialize_production_checkpointer()
    _online_graph = _compile_online_graph(saver)
    return _online_graph


async def close_online_runtime() -> None:
    global _online_graph
    _online_graph = None
    await close_production_checkpointer()


def get_online_graph(*, checkpointer=None) -> CompiledStateGraph:
    if checkpointer is not None:
        return _compile_online_graph(checkpointer)
    if _online_graph is None:
        raise CheckpointUnavailableError(
            "Online Graph is unavailable before startup initialization"
        )
    return _online_graph
```

- [ ] **Step 4: Wire every process lifecycle**

Call `await initialize_online_runtime()` immediately after `await init_db()` in FastAPI lifespan, `stream_runner.run()`, and `worker_runner.run()`. Call `await close_online_runtime()` before `close_db()` on every shutdown/early-exit path.

Do not catch `CheckpointUnavailableError` as a warning. Initialization failure must abort the role startup. Add checkpoint readiness to `/ready` without making a network call per request:

```python
if not production_checkpoint_ready():
    errors.append("PostgreSQL checkpoint runtime is not ready")

ready_detail["checkpoint"] = {
    "backend": "postgresql",
    "ready": production_checkpoint_ready(),
}
```

Update `graph_stream.py` to obtain the already initialized graph instead of calling the removed `get_checkpointer()`.

- [ ] **Step 5: Prove PostgreSQL checkpoint persistence**

In the integration test, initialize a saver against `TEST_DATABASE_URL`, compile Graph instance A, execute a Guided Flow turn, close only the compiled reference, compile instance B with a new saver/pool, then execute the next turn with the same thread ID. Assert instance B sees the prior `active_flow` and advances the stage.

The test must use a unique thread ID and delete only its own checkpoint rows during cleanup; it must not drop shared test schemas.

- [ ] **Step 6: Run lifecycle and persistence tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_checkpoint_runtime.py \
  tests/unit/graph/test_online_graph.py \
  tests/unit/test_health_readiness.py \
  tests/integration/test_online_checkpoint_postgres.py \
  tests/unit/test_process_role.py
```

Expected: PASS; restart simulation advances the same Guided Flow.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/services/online_conversation.py src/sales_agent/main.py \
  src/sales_agent/roles/stream_runner.py src/sales_agent/roles/worker_runner.py \
  src/sales_agent/api/routes/health.py \
  src/sales_agent/integrations/dingtalk/graph_stream.py \
  tests/unit/graph/test_online_graph.py \
  tests/unit/test_health_readiness.py \
  tests/integration/test_online_checkpoint_postgres.py \
  tests/unit/test_process_role.py
git commit -m "feat(graph): persist online state in postgres"
```

---

### Task 3: Make thread identity stable and reset every turn explicitly

**Files:**
- Modify: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/graph/online/state.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `tests/unit/graph/test_online_graph.py`
- Modify: `tests/unit/graph/test_context_routing_nodes.py`
- Modify: `tests/unit/graph/guided_flow/test_graph.py`
- Modify: `tests/integration/test_online_guided_flows.py`

**Interfaces:**
- Consumes: initialized Online Graph from Task 2.
- Produces: `build_online_thread_id(tenant_id: str, agent_id: str, channel: str, session_user_id: str) -> str`; `build_online_turn_input(*, tenant_id: str, agent_id: str, user_id: str, session_user_id: str, channel: str, conversation_id: str, message: str, entry_action: str | None, event_id: str | None, guided_flows_enabled: bool, topic_routing_enabled: bool, reset_requested: bool = False) -> OnlineConversationState`; constant `TURN_SCOPED_DEFAULTS`.

- [ ] **Step 1: Write failing thread-key tests**

Replace the date-oriented expectations with a stable exact key and prove the key is unchanged across midnight:

```python
def test_thread_id_is_stable_across_midnight():
    before = build_online_thread_id("t1", "a1", "dingtalk", "u1")
    after = build_online_thread_id("t1", "a1", "dingtalk", "u1")
    assert before == after == "online:t1:a1:dingtalk:u1"


def test_thread_id_scope_changes_for_each_identity_dimension():
    base = build_online_thread_id("t1", "a1", "dingtalk", "u1")
    variants = {
        build_online_thread_id("t2", "a1", "dingtalk", "u1"),
        build_online_thread_id("t1", "a2", "dingtalk", "u1"),
        build_online_thread_id("t1", "a1", "web", "u1"),
        build_online_thread_id("t1", "a1", "dingtalk", "u2"),
    }
    assert base not in variants
    assert len(variants) == 4
```

- [ ] **Step 2: Write a failing stale-state test**

Invoke the same checkpointed Graph twice. Seed the first turn with response/routing/completion fields, then build the second input and assert all turn-scoped fields are neutral before nodes repopulate them while `active_flow`, `flow_stage`, `flow_payload`, `last_event_id`, and the active Topic reference can carry:

```python
def test_new_turn_input_clears_transient_fields_but_not_thread_state():
    turn = build_online_turn_input(
        tenant_id="t1", agent_id="a1", user_id="u1",
        session_user_id="du1", channel="dingtalk",
        conversation_id="c1", message="新消息", event_id="e2",
        entry_action=None, guided_flows_enabled=True,
        topic_routing_enabled=True,
    )
    assert turn["answer_dict"] == {}
    assert turn["response_kind"] == "pending"
    assert turn["completed_flow"] is None
    assert turn["turn_relation"] is None
    assert turn["retained_entities"] == []
    assert turn["knowledge_scope"] == []
    assert "active_flow" not in turn
    assert "flow_stage" not in turn
    assert "last_event_id" not in turn
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py \
  tests/unit/graph/test_context_routing_nodes.py
```

Expected: FAIL because the thread ID contains a date and invocation input resets only a subset of stale fields.

- [ ] **Step 4: Implement stable key and one turn-envelope constructor**

Remove `now`, `timezone_name`, `ZoneInfo`, and date formatting from `build_online_thread_id()`. Add one constructor used by every caller:

```python
TURN_SCOPED_DEFAULTS: dict[str, Any] = {
    "requested_flow": None,
    "flow_action": "chat",
    "completed_flow": None,
    "answer_dict": {},
    "response_kind": "pending",
    "previous_topic_id": None,
    "turn_relation": None,
    "standalone_query": None,
    "retained_entities": [],
    "retracted_goals": [],
    "pending_clarification_id": None,
    "clarification_state": None,
    "context_status": None,
    "original_message": None,
    "task_type": None,
    "route_confidence": None,
    "knowledge_policy": None,
    "knowledge_scope": [],
    "retrieval_query": None,
    "needs_retrieval": None,
    "route_trace": None,
}


def build_online_turn_input(
    *, tenant_id: str, agent_id: str, user_id: str,
    session_user_id: str, channel: str, conversation_id: str,
    message: str, entry_action: str | None, event_id: str | None,
    guided_flows_enabled: bool, topic_routing_enabled: bool,
    reset_requested: bool = False,
) -> OnlineConversationState:
    return {
        **copy.deepcopy(TURN_SCOPED_DEFAULTS),
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "session_user_id": session_user_id,
        "channel": channel,
        "conversation_id": conversation_id,
        "message": message,
        "entry_action": entry_action,
        "event_id": event_id,
        "guided_flows_enabled": guided_flows_enabled,
        "topic_routing_enabled": topic_routing_enabled,
        "reset_requested": reset_requested,
    }
```

Use fresh list/dict values per call; do not return shared mutable objects from the module constant. Implement with a factory/copy so modifying one turn cannot change the next.

- [ ] **Step 5: Clear completed Guided Flow state at the owning state machine**

When a Guided Flow finishes or is canceled, return explicit neutral thread values:

```python
{
    "active_flow": None,
    "flow_stage": None,
    "flow_payload": {},
    "completed_flow": completed_flow,
}
```

Do not clear an active flow in the generic turn envelope. Add one test completing a flow, then sending a normal message; the normal message must route to chat instead of advancing the completed flow.

- [ ] **Step 6: Run focused and Guided Flow regressions**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/unit/graph/guided_flow/test_graph.py \
  tests/integration/test_online_guided_flows.py
```

Expected: PASS; no stale answer, route, completion, or flow state appears on the next turn.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/services/online_conversation.py \
  src/sales_agent/graph/online/state.py src/sales_agent/graph/online/nodes.py \
  tests/unit/graph/test_online_graph.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/unit/graph/guided_flow/test_graph.py \
  tests/integration/test_online_guided_flows.py
git commit -m "fix(graph): isolate online turn state"
```

---

### Task 4: Serialize same-thread turns and make duplicate delivery a no-op

**Files:**
- Create: `src/sales_agent/services/online_turn_lock.py`
- Modify: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`
- Create: `tests/unit/test_online_turn_lock.py`
- Modify: `tests/unit/graph/test_context_routing_nodes.py`
- Modify: `tests/unit/dingtalk/test_online_flow_routing.py`
- Create: `tests/integration/test_online_turn_concurrency.py`

**Interfaces:**
- Consumes: stable thread ID and initialized Graph from Tasks 2–3.
- Produces: `online_turn_lock_key(thread_id: str) -> int`; `acquire_online_turn_lock(db: AsyncSession, thread_id: str) -> None`; duplicate Graph result contract `response_kind == "duplicate"` with no outbound reply.

- [ ] **Step 1: Write deterministic lock-key tests**

```python
def test_lock_key_is_stable_signed_bigint():
    key = online_turn_lock_key("online:t1:a1:dingtalk:u1")
    assert key == online_turn_lock_key("online:t1:a1:dingtalk:u1")
    assert -(2**63) <= key < 2**63


@pytest.mark.asyncio
async def test_acquire_uses_transaction_advisory_lock():
    db = AsyncMock()
    await acquire_online_turn_lock(db, "online:t1:a1:dingtalk:u1")
    statement, params = db.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {"lock_key": online_turn_lock_key("online:t1:a1:dingtalk:u1")}
```

- [ ] **Step 2: Write failing duplicate and concurrency tests**

Use two independent sessions and a stub Chat runner that blocks on an event. Start two turns for the same thread and assert the second does not enter Chat until the first session commits. Repeat with different users and assert both enter concurrently.

Then deliver the same `event_id` twice after the first commit:

```python
assert chat_runner.call_count == 1
assert first["response_kind"] == "chat"
assert duplicate["response_kind"] == "duplicate"
assert replies == [first_rendered_reply]
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/test_online_turn_lock.py \
  tests/integration/test_online_turn_concurrency.py \
  tests/unit/dingtalk/test_online_flow_routing.py -k duplicate
```

Expected: FAIL because there is no cross-worker serialization and the processor renders a second empty/old response for a duplicate.

- [ ] **Step 4: Implement a stable PostgreSQL advisory lock**

Hash the full thread ID with an eight-byte BLAKE2b digest and convert it to a signed PostgreSQL bigint:

```python
def online_turn_lock_key(thread_id: str) -> int:
    digest = hashlib.blake2b(thread_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def acquire_online_turn_lock(db: AsyncSession, thread_id: str) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": online_turn_lock_key(thread_id)},
    )
```

Document that the caller owns the transaction and must commit/rollback after the Graph and persistence complete. Do not use a Python process lock.

- [ ] **Step 5: Acquire the lock before reading the checkpoint**

In `invoke_online_turn()`, build the thread ID, acquire the lock, then call `graph.ainvoke()`. Do not load/invoke the Graph before the lock because the second worker must see the first worker’s committed/latest checkpoint.

Ensure HTTP and Stream worker transaction boundaries commit after processing and roll back on error.

- [ ] **Step 6: Make duplicate processing silent and side-effect free**

Keep `duplicate_node()` free of state mutation. In `handle_dingtalk_event()`, return the structured duplicate result without calling `reply_fn`:

```python
if result.get("response_kind") == "duplicate":
    logger.info("Skipping duplicate DingTalk event: %s", event_id)
    return None
```

No Chat, Guided Flow advance, conversation log, renderer, or outbound sender may run for the duplicate delivery.

- [ ] **Step 7: Run concurrency and existing routing regressions**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/test_online_turn_lock.py \
  tests/integration/test_online_turn_concurrency.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/unit/dingtalk/test_online_flow_routing.py
```

Expected: PASS; same-thread max in-flight count is one, different-user max in-flight count reaches two, duplicate Chat call count remains one.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/services/online_turn_lock.py \
  src/sales_agent/services/online_conversation.py \
  src/sales_agent/graph/online/nodes.py \
  src/sales_agent/integrations/dingtalk/processor.py \
  tests/unit/test_online_turn_lock.py \
  tests/integration/test_online_turn_concurrency.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/unit/dingtalk/test_online_flow_routing.py
git commit -m "fix(graph): serialize online turns across workers"
```

---

### Task 5: Complete expiry restore and bounded restore clarification

**Files:**
- Create: `src/sales_agent/services/topic_restore.py`
- Create: `src/sales_agent/prompts/topic_restore_resolver_prompt.py`
- Modify: `src/sales_agent/services/structured_router_output.py`
- Modify: `src/sales_agent/services/topic_manager.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `src/sales_agent/graph/online/edges.py`
- Create: `tests/unit/test_topic_restore.py`
- Modify: `tests/unit/test_topic_manager.py`
- Modify: `tests/unit/graph/test_context_routing_nodes.py`

**Interfaces:**
- Consumes: existing 30-minute expiry, 24-hour `find_restorable_topics()`, pending clarification JSON, and the same-thread lock from Task 4.
- Produces: `TopicScope`; `TopicRestoreDecision`; `resolve_topic_restore(*, message: str, candidates: list[ConversationTopic], attempt_count: int, chat_model: Any, db: AsyncSession | None, tenant_id: str, agent_id: str) -> TopicRestoreDecision`; `TopicManager.create_restore_anchor(session: AsyncSession, *, scope: TopicScope, conversation_id: str, event_id: str, original_message: str, candidates: list[ConversationTopic], now: datetime) -> ConversationTopic`; `TopicManager.load_recent_topic_messages(session: AsyncSession, *, scope: TopicScope, conversation_id: str, topic_id: str, limit: int = 6) -> list[dict[str, str]]`; pending JSON kind `topic_restore`; context statuses `resolved`, `clarify`, `control`, and `cancel`.

- [ ] **Step 1: Write failing unique-restore tests**

Cover the exact behaviors:

```python
@pytest.mark.asyncio
async def test_explicit_continue_restores_unique_topic_without_replaying_old_goal(
    db_session, unique_restorable_topic, run_context_turn,
):
    # closed 31 minutes ago, inside the 24-hour window
    result = await run_context_turn(message="继续", restorable=[old_topic])
    assert old_topic.status == "active"
    assert result["topic_id"] == old_topic.id
    assert result["turn_relation"] == "continue"
    assert result["context_status"] == "control"
    assert result["response_kind"] == "topic_restored"
    assert result["standalone_query"] == ""


@pytest.mark.asyncio
async def test_continue_with_suffix_restores_and_executes_suffix(
    db_session, unique_restorable_topic, run_context_turn,
):
    result = await run_context_turn(
        message="继续，帮我查一下价格", restorable=[old_topic]
    )
    assert result["context_status"] == "resolved"
    assert result["standalone_query"] == "帮我查一下价格"
```

Ordinary unrelated input after expiry must create a new Topic and must not restore.

With an active Topic and a resolver decision of `new`, assert the old Topic is closed, a different active Topic is created in the same turn, and the returned `topic_id` is the new active ID rather than the closed ID.

Add a scoped-history test with messages from two Topic IDs. Capture `recent_messages` passed to the resolver and assert it contains only the active/restored Topic’s latest six user/assistant messages in chronological order.

- [ ] **Step 2: Write failing multiple-candidate tests**

When two or more topics are restorable, exact “继续” creates one active clarification anchor and lists at most three numbered candidate summaries. No closed candidate becomes active yet.

Cover:

- `第1个` restores candidate one;
- `第二个，继续查价格` restores candidate two and executes only the suffix;
- `新问题，帮我写开场白` closes the anchor and creates a clean Topic for the suffix;
- model output containing an ID outside the supplied candidate list is rejected as ambiguous;
- after two unresolved answers, the safe resolution is `new`;
- candidates older than 24 hours never appear.

- [ ] **Step 3: Run restore tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/test_topic_restore.py \
  tests/unit/test_topic_manager.py \
  tests/unit/graph/test_context_routing_nodes.py -k "restore or expired"
```

Expected: FAIL because the current Online node discovers restorable topics but never calls `restore_topic()`, and pending state on a closed Topic cannot be found on the next turn.

- [ ] **Step 4: Add a closed, validated restore decision schema**

```python
class TopicRestoreDecision(BaseModel):
    resolution: Literal["restore", "new", "ambiguous"]
    selected_topic_id: str | None = None
    supplemental_message: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: str

    @model_validator(mode="after")
    def validate_selection(self):
        if self.resolution == "restore" and not self.selected_topic_id:
            raise ValueError("restore requires selected_topic_id")
        return self


@dataclass(frozen=True)
class TopicScope:
    tenant_id: str
    agent_id: str
    user_id: str
    channel: str
```

`resolve_topic_restore()` must first handle exact numeric/new commands, then call the model with candidate IDs and summaries. Validate the returned ID against the supplied candidate map. Never allow the model to invent an arbitrary Topic ID.

Use this static fallback prompt through `resolve_router_prompt(..., "topic_restore_resolver", ...)`:

```python
TOPIC_RESTORE_RESOLVER_PROMPT = """你负责从有限候选中判断用户想恢复哪个旧话题。
只能输出 JSON，字段为：
{"resolution":"restore|new|ambiguous","selected_topic_id":null,
 "supplemental_message":null,"confidence":0.0,"reason_code":""}

规则：
1. 只能选择候选列表中真实存在的 topic_id。
2. 用户明确说新问题、换话题时 resolution=new。
3. 用户内容能唯一对应一个摘要时 resolution=restore。
4. 无法唯一确定时 resolution=ambiguous，selected_topic_id=null。
5. 不得补充候选列表之外的事实。
"""
```

Pass candidates as JSON in the user message and reject any selected ID not present in that exact JSON array.

- [ ] **Step 5: Store restore clarification on an active anchor**

Add TopicManager operations that create an active anchor and store:

```json
{
  "kind": "topic_restore",
  "event_id": "evt-1",
  "original_message": "继续",
  "candidate_topic_ids": ["topic-a", "topic-b"],
  "candidate_summaries": ["福多多价格", "竞品分析"],
  "created_at": "2026-07-08T00:00:00+00:00"
}
```

The anchor contains no retained entities or prior goal. Closing the anchor must be flushed before restoring a candidate so the partial unique active-Topic index cannot be violated.

- [ ] **Step 6: Integrate restore before general context resolution**

In `context_resolution_node()`:

1. resolve an active pending `topic_restore` anchor before generic clarification;
2. close an expired active Topic;
3. load restorable candidates;
4. for explicit continue or a resolver `continue` decision: restore a unique candidate or create a restore anchor for multiple candidates;
5. for `new` with or without an active Topic: close/flush the old Topic when present, then create and return a clean active Topic for the current message;
6. for ambiguous references with candidates: create a restore anchor rather than attaching pending state to a closed Topic.

Before calling `resolve_context()` for an active or restored Topic, load the latest six user/assistant messages through `TopicManager.load_recent_topic_messages()`. Its query filters `tenant_id`, `conversation_id`, `topic_id`, and role, orders newest-first for the limit, then reverses to chronological order. It must never load messages from a closed non-selected Topic into the resolver.

Return a control response when restoration/new-topic selection contains no supplemental task:

```python
{
    "context_status": "control",
    "response_kind": "topic_restored",
    "turn_relation": "continue",
    "topic_id": restored.id,
    "standalone_query": "",
    "answer_dict": {
        "summary": f"已继续之前的话题：{restored.summary}。请告诉我接下来要处理什么。"
    },
}
```

Add `control -> log_control_response -> END` to `route_context_resolution()`. A supplemental task uses `resolved -> evidence_routing -> chat`.

- [ ] **Step 7: Run all Topic and context-routing tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/test_topic_restore.py \
  tests/unit/test_topic_manager.py \
  tests/unit/test_context_resolver.py \
  tests/unit/graph/test_context_routing_nodes.py
```

Expected: PASS; no test leaves pending restore state only on a closed Topic.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/services/topic_restore.py \
  src/sales_agent/prompts/topic_restore_resolver_prompt.py \
  src/sales_agent/services/structured_router_output.py \
  src/sales_agent/services/topic_manager.py \
  src/sales_agent/graph/online/nodes.py src/sales_agent/graph/online/edges.py \
  tests/unit/test_topic_restore.py tests/unit/test_topic_manager.py \
  tests/unit/graph/test_context_routing_nodes.py
git commit -m "feat(memory): complete bounded topic restore"
```

---

### Task 6: Unify standard and streaming execution, including reset semantics

**Files:**
- Modify: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/graph/online/state.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `src/sales_agent/graph/online/edges.py`
- Modify: `src/sales_agent/graph/online/graph.py`
- Modify: `src/sales_agent/graph/chat/nodes/logging_node.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`
- Modify: `src/sales_agent/integrations/dingtalk/graph_stream.py`
- Create: `src/sales_agent/integrations/dingtalk/turn_result.py`
- Modify: `tests/unit/dingtalk/test_online_flow_routing.py`
- Modify: `tests/unit/graph/test_online_graph.py`
- Modify: `tests/unit/graph/test_context_routing_nodes.py`
- Modify: `tests/integration/test_topic_memory_flow.py`
- Modify: `tests/integration/test_online_guided_flows.py`

**Interfaces:**
- Consumes: strict cached Graph, stable turn envelope, advisory lock, and Topic restore from Tasks 2–5.
- Produces: immutable `PreparedOnlineTurn`; `resolve_online_models(*, db, tenant_id: str, chat_model=None, embedding_model=None) -> tuple[Any, Any]`; `prepare_online_turn(*, db, tenant_id: str, agent_id: str | None, user_id: str, session_user_id: str, channel: str, conversation_id: str, message: str, entry_action: str | None = None, event_id: str | None = None, reset_requested: bool = False, chat_model=None, embedding_model=None, now=None, checkpointer=None) -> PreparedOnlineTurn`; `invoke_online_turn(*, db, tenant_id: str, agent_id: str | None, user_id: str, session_user_id: str, channel: str, conversation_id: str, message: str, entry_action: str | None = None, event_id: str | None = None, reset_requested: bool = False, chat_model=None, embedding_model=None, now=None, checkpointer=None) -> dict[str, Any]`; `update_dingtalk_card_from_graph_chunk(*, chunk, card_id: str, card_sender) -> None`; `DingTalkTurnResult`; shared reset flag `reset_requested`.

- [ ] **Step 1: Write failing standard/stream parity tests**

Patch only `prepare_online_turn()` and assert both `invoke_online_turn()` and `handle_dingtalk_stream_via_graph()` use its exact thread ID, input state, config, context, and Graph. Neither streaming module may build its own thread ID, obtain its own checkpointer, or duplicate the input-state dictionary.

Assert both paths call `acquire_online_turn_lock(db, prepared.thread_id)` before `ainvoke`/`astream`.

- [ ] **Step 2: Write failing reset tests**

Through `handle_dingtalk_event()` execute:

1. start a Guided Flow;
2. send a reset command with no suffix;
3. assert the active Topic is closed, active flow/stage/payload are cleared, response is “已开启新话题…”, and the next ordinary message does not advance the old flow;
4. repeat with `重新开始，帮我写开场白` and assert the remaining message creates a clean Topic and reaches Chat in the same turn.

The thread ID stays unchanged; reset is an explicit state transition, not a new random conversation ID or date bucket.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/dingtalk/test_online_flow_routing.py \
  tests/unit/graph/test_online_graph.py -k "reset or parity or stream"
```

Expected: FAIL because streaming builds a separate `conversation_id` thread/config/input and processor reset bypasses the Graph without clearing checkpointed flow state.

- [ ] **Step 4: Add one prepared-turn contract**

```python
@dataclass(frozen=True)
class PreparedOnlineTurn:
    graph: CompiledStateGraph
    thread_id: str
    input_state: OnlineConversationState
    config: dict[str, Any]
    context: dict[str, Any]


async def resolve_online_models(
    *, db, tenant_id: str, chat_model=None, embedding_model=None,
) -> tuple[Any, Any]:
    if chat_model is not None and embedding_model is not None:
        return chat_model, embedding_model
    resolver = TenantResolver(db)
    tenant_info = await resolver.resolve(tenant_id)
    provider = resolver.get_model_provider(tenant_info)
    return chat_model or provider.chat, embedding_model or provider.embedding


async def prepare_online_turn(
    *, db, tenant_id: str, agent_id: str | None, user_id: str,
    session_user_id: str, channel: str, conversation_id: str,
    message: str, entry_action: str | None = None,
    event_id: str | None = None, reset_requested: bool = False,
    chat_model=None, embedding_model=None, now=None, checkpointer=None,
) -> PreparedOnlineTurn:
    agent = await resolve_tenant_agent_id(db, tenant_id, agent_id)
    resolved_chat, resolved_embedding = await resolve_online_models(
        db=db,
        tenant_id=tenant_id,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
    thread_id = build_online_thread_id(
        tenant_id, agent.id, channel, session_user_id,
    )
    settings = get_settings()
    input_state = build_online_turn_input(
        tenant_id=tenant_id,
        agent_id=agent.id,
        user_id=user_id,
        session_user_id=session_user_id,
        channel=channel,
        conversation_id=conversation_id,
        message=message,
        entry_action=entry_action,
        event_id=event_id,
        guided_flows_enabled=settings.guided_flows.enabled,
        topic_routing_enabled=settings.topic_routing.enabled,
        reset_requested=reset_requested,
    )
    return PreparedOnlineTurn(
        graph=get_online_graph(checkpointer=checkpointer),
        thread_id=thread_id,
        input_state=input_state,
        config={"configurable": {"thread_id": thread_id}},
        context={
            "db": db,
            "chat_model": resolved_chat,
            "embedding_model": resolved_embedding,
            "now": now,
        },
    )
```

This function resolves the Agent and models once, builds the stable thread ID and new-turn input, selects the strict cached/test-injected Graph, and returns runtime context. It does not invoke or stream.

`invoke_online_turn()` retains its current explicit public signature and its body becomes:

```python
prepared = await prepare_online_turn(
    db=db, tenant_id=tenant_id, agent_id=agent_id,
    user_id=user_id, session_user_id=session_user_id,
    channel=channel, conversation_id=conversation_id, message=message,
    entry_action=entry_action, event_id=event_id,
    reset_requested=reset_requested, chat_model=chat_model,
    embedding_model=embedding_model, now=now, checkpointer=checkpointer,
)
await acquire_online_turn_lock(db, prepared.thread_id)
graph_result = await prepared.graph.ainvoke(
    prepared.input_state,
    prepared.config,
    context=prepared.context,
)
return {**graph_result, "thread_id": prepared.thread_id}
```

- [ ] **Step 5: Add reset as an Online Graph transition**

Add `reset_requested` and `force_new_topic` to Online state. `normalize_turn_node()` gives reset priority after duplicate detection. Add `reset_context_node()` that:

- closes the active Topic in the caller transaction;
- clears pending clarification;
- returns `active_flow=None`, `flow_stage=None`, `flow_payload={}`;
- sets `force_new_topic=True`, `turn_relation="new"`, and `last_event_id` only after logging;
- emits the reset confirmation when message remainder is empty.

Routing is:

```text
normalize_turn --reset--> reset_context
reset_context --empty message--> log_control_response --> END
reset_context --remaining message--> context_resolution --> evidence_routing --> chat
```

When `force_new_topic=True`, `context_resolution_node()` skips active/restorable Topic selection, creates a clean Topic for the remaining message, and clears the flag in its output.

- [ ] **Step 6: Make terminal persistence part of successful completion**

Add failing tests where `conversation_logger.log_conversation()` raises on Chat, control, and Guided Flow terminal nodes. Assert Graph invocation raises, `last_event_id` is not advanced, `reply_fn` is not called with a success answer, and the outer worker rolls back.

Remove catch-and-continue behavior from terminal conversation logging and Topic summary persistence. Log with `logger.exception()` and re-raise so the node remains retryable:

Replace each existing catch block after its unchanged `log_conversation()` call with:

```python
except Exception:
    logger.exception("Failed to persist completed online turn")
    raise
```

The worker/session owner retains commit/rollback responsibility. Intermediate LangGraph checkpoints may resume the failed terminal node, but a failed terminal node cannot write `last_event_id` or be presented as durable success.

- [ ] **Step 7: Define the DingTalk turn result**

```python
@dataclass(frozen=True)
class DingTalkTurnResult:
    event_id: str
    tenant_id: str
    agent_id: str | None
    internal_user_id: str | None
    dingtalk_user_id: str
    conversation_id: str | None
    thread_id: str | None
    normalized_message: str
    rendered_text: str
    answer_dict: dict[str, Any]
    response_kind: str
    topic_id: str | None
    previous_topic_id: str | None
    turn_relation: str | None
    standalone_query: str | None
    active_flow: str | None
    flow_stage: str | None
    completed_flow: str | None
    flow_action: str | None
    last_event_id: str | None
    latency_ms: int
    error: str | None = None
```

Make `handle_dingtalk_event() -> DingTalkTurnResult` return a populated result for fallback, media failure, reset, normal, clarification, and duplicate branches. A duplicate has empty `rendered_text` and does not call `reply_fn`. On Graph or persistence failure, send the existing best-effort error reply and re-raise so the worker rolls back; do not return a success-shaped result from a failed transaction.

- [ ] **Step 8: Migrate streaming to the prepared turn**

Delete its `get_checkpointer()`, UUID/conversation thread choice, and hand-built input. Use:

```python
prepared = await prepare_online_turn(
    db=db, tenant_id=tenant_id, agent_id=agent_id,
    user_id=user_id, session_user_id=dingtalk_user_id,
    channel="dingtalk", conversation_id=conversation_id, message=message,
    entry_action=None, event_id=event_id, reset_requested=False,
    chat_model=chat_model, embedding_model=embedding_model,
)
await acquire_online_turn_lock(db, prepared.thread_id)
async for chunk in prepared.graph.astream(
    prepared.input_state,
    prepared.config,
    context=prepared.context,
    stream_mode=["messages", "updates", "custom"],
):
    await update_dingtalk_card_from_graph_chunk(
        chunk=chunk,
        card_id=card_id,
        card_sender=card_sender,
    )
```

Extract the module's existing chunk parsing/card update body into `update_dingtalk_card_from_graph_chunk()`; keep only interactive-card progress/rendering logic in `graph_stream.py`.

When streaming completes, add `thread_id=prepared.thread_id` to the returned final state so `DingTalkTurnResult` and evaluation traces use the same observable identity as the standard path.

- [ ] **Step 9: Run standard, streaming, reset, persistence-failure, and Guided Flow tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/dingtalk/test_online_flow_routing.py \
  tests/unit/graph/test_online_graph.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/integration/test_topic_memory_flow.py \
  tests/integration/test_online_guided_flows.py
```

Expected: PASS; standard and stream calls have identical thread/input/state semantics.

- [ ] **Step 10: Commit**

```bash
git add src/sales_agent/services/online_conversation.py \
  src/sales_agent/graph/online/state.py src/sales_agent/graph/online/nodes.py \
  src/sales_agent/graph/online/edges.py src/sales_agent/graph/online/graph.py \
  src/sales_agent/graph/chat/nodes/logging_node.py \
  src/sales_agent/integrations/dingtalk/processor.py \
  src/sales_agent/integrations/dingtalk/graph_stream.py \
  src/sales_agent/integrations/dingtalk/turn_result.py \
  tests/unit/dingtalk/test_online_flow_routing.py \
  tests/unit/graph/test_online_graph.py \
  tests/unit/graph/test_context_routing_nodes.py \
  tests/integration/test_topic_memory_flow.py \
  tests/integration/test_online_guided_flows.py
git commit -m "refactor(dingtalk): unify durable online turn execution"
```

---

### Task 7: Repair model-backed Router evaluation and measure the resolving turn

**Files:**
- Modify: `eval/router/run_router_eval.py`
- Create: `eval/router/clarification_resolution_cases.jsonl`
- Modify: `tests/unit/test_router_eval.py`

**Interfaces:**
- Consumes: production `resolve_context`, `route_intent_evidence`, `resolve_clarification`, configured tenant model, and existing JSONL datasets.
- Produces: async `evaluate_turn_relation(cases: list[EvalCase], resolver_fn: ContextResolverFn, dependencies: RouterEvalDependencies) -> EvalReport`; `evaluate_evidence_policy(cases: list[EvalCase], resolver_fn: EvidenceRouterFn, dependencies: RouterEvalDependencies) -> EvalReport`; `evaluate_clarification_resolution(cases: list[ClarificationEvalCase], resolver_fn: ClarificationResolverFn, dependencies: RouterEvalDependencies) -> ClarificationEvalReport`; `RouterEvalDependencies`; separate `clarification_detection_rate` and `clarification_completion_rate`.

- [ ] **Step 1: Write failing async-callable tests**

```python
@pytest.mark.asyncio
async def test_evaluator_awaits_production_style_context_resolver():
    calls = []

    async def resolver(*, message, topic, recent_messages, chat_model,
                       db=None, tenant_id=None, agent_id=None):
        calls.append((message, topic.summary, recent_messages, chat_model))
        return ContextDecision(
            turn_relation="continue",
            standalone_query="查询福多多价格",
            retained_entities=["福多多"],
            retracted_goals=[], missing_references=[],
            confidence=1.0, reason_code="test",
        )

    report = await evaluate_turn_relation(
        [case], resolver, RouterEvalDependencies(chat_model=object())
    )
    assert report.correct == 1
    assert calls[0][1] == "福多多产品"
```

Add the same coverage for async evidence and clarification resolvers. Assert a coroutine object is never treated as a result dictionary.

- [ ] **Step 2: Write failing metric-correctness tests**

Assert macro accuracy averages only classes that have examples:

```python
assert report.populated_classes == ["continue", "new"]
assert report.macro_accuracy == 1.0
```

Add a paired clarification case where the first expected relation is `ambiguous` and the user’s next answer is “新问题，帮我写开场白”. Detection is correct only if turn one is ambiguous; completion is correct only if the resolving result is `new` and its supplemental/replacement text preserves “帮我写开场白”.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/test_router_eval.py
```

Expected: FAIL because the runner calls async functions synchronously with the wrong keyword arguments, empty classes lower the macro score, and clarification completion is currently the first-turn ambiguous-detection count.

- [ ] **Step 4: Add explicit evaluation dependencies and adapters**

```python
@dataclass
class RouterEvalDependencies:
    chat_model: Any = None
    db: Any = None
    tenant_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class ClarificationEvalCase:
    id: str
    pending_context: dict[str, Any]
    resolving_input: str
    expected_resolution: str
    expected_query_contains: list[str]


@dataclass
class ClarificationEvalReport:
    total_cases: int
    correct: int
    completion_rate: float
    results: list[EvalResult]


ClarificationResolverFn = Callable[..., Any]


async def _await_result(value):
    return await value if inspect.isawaitable(value) else value
```

Build a lightweight `EvalTopicContext` from `case.context["topic"]` with `summary` and `current_goal`, preserve ordered `recent_messages`, and invoke the production contract exactly. Convert Pydantic results with `model_dump()` before scoring.

Fixture functions remain deterministic but pass through the same awaited adapter.

- [ ] **Step 5: Correct report semantics**

Add `populated_classes`, `clarification_detection_rate`, and a separately supplied `clarification_completion_rate` to `EvalReport`. Compute macro accuracy only across `ClassMetrics.total > 0`. Keep zero-example classes in the per-class table with `applicable=false`.

`clarification_completion_rate` denominator is the number of paired resolving turns, not the number of first-turn ambiguous cases.

- [ ] **Step 6: Add at least 20 resolution cases**

`clarification_resolution_cases.jsonl` includes exact continue/new/cancel commands, replacement text, ambiguous free text, second-attempt safety fallback, unique Topic restore, and multiple-candidate numbered selection. Each record contains first-turn pending context, resolving input, and expected resolution/query.

Use this exact case manifest:

| ID | Resolving input | Expected |
|---|---|---|
| `cr-001` | `继续` | `continue`, no supplemental text |
| `cr-002` | `继续，重点讲价格` | `continue`, supplemental contains `价格` |
| `cr-003` | `接着刚才` | `continue` |
| `cr-004` | `接着刚才，给个话术` | `continue`, supplemental contains `话术` |
| `cr-005` | `新问题` | `new` |
| `cr-006` | `新问题，帮我准备拜访` | `new`, replacement contains `准备拜访` |
| `cr-007` | `换个话题` | `new` |
| `cr-008` | `换个话题，分析竞品` | `new`, replacement contains `分析竞品` |
| `cr-009` | `取消` | `cancel` |
| `cr-010` | `算了` | `cancel` |
| `cr-011` | `不是价格，是交付周期` | `replace`, replacement contains `交付周期` |
| `cr-012` | `补充一下，他还关心售后` | `continue`, supplemental contains `售后` |
| `cr-013` | `那个` | `ambiguous` on first attempt |
| `cr-014` | `就之前说的` | `ambiguous` on first attempt |
| `cr-015` | `还是那个` with `attempt_count=2` | safe fallback `new` |
| `cr-016` | `继续` with one restore candidate | restore that candidate |
| `cr-017` | `第1个` with two restore candidates | restore candidate 1 |
| `cr-018` | `第二个，查价格` | restore candidate 2, supplemental contains `查价格` |
| `cr-019` | `福多多价格那个` | model selects the uniquely matching candidate |
| `cr-020` | `随便一个` | `ambiguous`; no candidate ID |

Do not copy production user messages. Use synthetic product/customer names already present in test fixtures.

- [ ] **Step 7: Make the CLI genuinely model-backed**

Change to `async def main_async(args) -> int` and `sys.exit(asyncio.run(main_async(args)))`. In non-fixture mode:

1. initialize DB;
2. resolve tenant and Agent from `--tenant-id` and optional `--agent-id`;
3. resolve the configured chat model through `TenantResolver`;
4. run all three async evaluators sequentially on one read-only evaluation session;
5. close DB resources.

Fixture mode must require no DB or model.

- [ ] **Step 8: Run fixture and unit verification**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/test_router_eval.py
PYTHONPATH=src python eval/router/run_router_eval.py --fixture-mode \
  --output /tmp/router-eval-fixture
```

Expected: unit tests PASS; fixture command writes turn, evidence, and clarification JSON/Markdown reports and exits according to fixture thresholds without coroutine warnings.

- [ ] **Step 9: Commit**

```bash
git add eval/router/run_router_eval.py \
  eval/router/clarification_resolution_cases.jsonl \
  tests/unit/test_router_eval.py
git commit -m "fix(eval): execute production router contracts asynchronously"
```

---

### Task 8: Add real DingTalk multi-turn scenarios and a machine-readable gate

**Files:**
- Create: `tests/support/dingtalk_scenario.py`
- Create: `tests/integration/test_dingtalk_multiturn_memory.py`
- Create: `eval/memory/short_term_scenarios.jsonl`
- Create: `eval/run_short_term_memory_eval.py`
- Create: `tests/unit/eval/test_short_term_memory_eval.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`

**Interfaces:**
- Consumes: `DingTalkTurnResult`, strict PostgreSQL runtime, stable thread ID, lock, Topic restore, and router services from Tasks 1–7.
- Produces: `DingTalkScenarioRunner`; `ShortTermScenario`; `ScenarioTurn`; `ScenarioReport`; CLI modes `fixture` and `model`.

- [ ] **Step 1: Define a strict scenario schema and failing parser tests**

```python
class ExpectedTurn(BaseModel):
    response_kind: str | None = None
    turn_relation: str | None = None
    standalone_query_contains: list[str] = Field(default_factory=list)
    retained_entities: list[str] = Field(default_factory=list)
    topic_transition: Literal["same", "new", "restored", "none"] | None = None
    active_flow: str | None = None
    flow_stage: str | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_count: int = 1


class ScenarioTurn(BaseModel):
    input: str
    event_id: str | None = None
    time_offset_seconds: int = 0
    restart_before: bool = False
    duplicate_previous_event: bool = False
    concurrent_group: str | None = None
    expected: ExpectedTurn


class ShortTermScenario(BaseModel):
    id: str
    tags: list[str]
    turns: list[ScenarioTurn] = Field(min_length=2, max_length=6)
```

Reject duplicate scenario IDs, missing expectations, unknown relation values, and a duplicate-first turn.

- [ ] **Step 2: Write failing real-processor tests**

Build one scenario runner that creates normalized event arguments and calls `handle_dingtalk_event()` for every turn with the same DingTalk staff ID. It captures `reply_fn`, commits the session, and automatically queries scoped Topic/messages after the turn.

Do not mock:

- `DingTalkUserMapper`;
- `DingTalkConversationMapper`;
- command parser;
- Agent resolver;
- Online Graph;
- TopicManager/repository;
- message logger;
- renderer.

Use deterministic chat/embedding model doubles by patching only the tenant model provider. Use the PostgreSQL checkpointer from `TEST_DATABASE_URL`.

First failing tests:

1. pronoun follow-up retains `福多多` and keeps Topic ID;
2. “算了，还是看竞品” is `revise`, keeps the entity, and records the old goal;
3. unrelated input creates a new Topic without old entities;
4. ambiguous input asks once, then “新问题…” resolves to `new`;
5. Guided Flow survives `close_online_runtime()`/reinitialize between turns;
6. duplicate event produces zero additional replies/log rows.

- [ ] **Step 3: Run the integration tests and verify RED**

Run with an isolated PostgreSQL test database:

```bash
PYTHONPATH=src pytest -q tests/integration/test_dingtalk_multiturn_memory.py
```

Expected: FAIL until all prior contracts are correctly exposed through the shared processor and observable result.

- [ ] **Step 4: Implement the reusable scenario runner**

`DingTalkScenarioRunner.run_turn()` returns:

```python
@dataclass(frozen=True)
class ObservedScenarioTurn:
    result: DingTalkTurnResult
    replies: list[str]
    active_topic_ids: list[str]
    closed_topic_ids: list[str]
    persisted_message_count: int
```

The runner owns time injection, event IDs, runtime restart, and session commit/rollback. It uses a unique tenant/Agent/user namespace per scenario so tests do not share checkpoints or Topics.

- [ ] **Step 5: Add 24 synthetic short-term scenarios**

The JSONL dataset contains at least:

- four `continue` scenarios, including pronouns and omitted product names;
- four `revise` scenarios, including within-turn self-contradiction;
- three `switch` scenarios;
- three `new` scenarios;
- three `ambiguous` plus resolving-turn scenarios;
- four Guided Flow start/advance/preempt/cancel scenarios covering all four quick entries across the set;
- one restart/worker-switch scenario;
- one duplicate-event scenario;
- one same-user concurrency scenario.

Every expected Topic transition is explicit. Synthetic names are used; no production conversation is copied.

Use this exact 24-scenario manifest:

| ID | Turn sequence | Primary assertion |
|---|---|---|
| `st-001` | `查福多多产品` → `它多少钱` | `continue`; retain `福多多`; same Topic |
| `st-002` | `客户关心售后` → `怎么回答` | `continue`; standalone query includes `售后` |
| `st-003` | expired Topic → `继续，查交付周期` | unique restore; execute suffix only |
| `st-004` | `分析竞品A` → `还有竞品B呢` | `continue`; same comparison Topic |
| `st-005` | `查福多多功能，算了还是看竞品` | `revise`; keep `福多多`; retract function goal |
| `st-006` | `准备价格话术` → `不谈价格，改成交付` | `revise`; record price goal retraction |
| `st-007` | `福多多有什么优势` → `换成竞品对比` | `revise`; retain product entity |
| `st-008` | `客户预算10万` → `说错了，是8万` | `revise`; standalone query contains `8万` only |
| `st-009` | `讨论客户甲` → `再说客户乙的需求` | `switch`; child Topic; no customer甲 entity |
| `st-010` | `福多多价格` → `换一个，看看另一款产品` | `switch`; new child Topic |
| `st-011` | `产品演示安排` → `先切到合同条款` | `switch`; previous Topic closed |
| `st-012` | `福多多价格` → `今天天气怎么样` | `new`; no old product entity |
| `st-013` | `客户异议处理` → `帮我写自我介绍` | `new`; clean Topic |
| `st-014` | expired Topic → `帮我准备新的客户开场` | `new`; no automatic restore |
| `st-015` | two old Topics → `继续` → `第1个` | first turn clarify; resolving turn restores candidate 1 |
| `st-016` | ambiguous `那个项目` → `新问题，分析竞品` | completion `new`; execute replacement |
| `st-017` | ambiguous `不是这个` → `不是价格，是交付` | completion `replace`; same Topic |
| `st-018` | `小赢欣赏` → two answers | start/advance `small_win_appreciation` |
| `st-019` | `卡点破框` → answer → `取消` | start/advance/cancel `sales_block_breakthrough` |
| `st-020` | `访前准备` → answer → `访后复盘` | new explicit flow preempts visit preparation |
| `st-021` | `访后复盘` → three answers | reaches expected post-visit stage/completion |
| `st-022` | start `访前准备` → restart runtime → answer | same flow and next stage after restart |
| `st-023` | ordinary Chat turn → repeat identical event ID | one Chat call, one log, one reply |
| `st-024` | two concurrent same-user turns plus one other-user turn | same user serialized; other user concurrent |

- [ ] **Step 6: Implement deterministic report aggregation**

The runner reports:

- relation confusion matrix and per-class Precision/Recall/F1;
- overall relation accuracy;
- Topic leakage count/rate;
- entity retention precision/recall;
- clarification detection and resolving-turn completion;
- duplicate side-effect violations;
- restart recovery;
- per-turn P50/P95 latency;
- failures with scenario and turn IDs.

Do not call an LLM judge in Spec 1. Model mode changes only the product resolver/model; expectations remain deterministic.

- [ ] **Step 7: Add CLI and exact exit behavior**

```bash
PYTHONPATH=src python eval/run_short_term_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/short_term_scenarios.jsonl \
  --output /tmp/short-term-memory-eval
```

Exit `0` only when:

- relation accuracy is at least 90%;
- Topic leakage count is zero;
- duplicate side-effect count is zero;
- restart recovery is 100%;
- clarification completion is at least 90%;
- cross-scope leakage count is zero.

Exit `2` for invalid dataset/setup and `1` for product threshold failures. Write `report.json` and `report.md` in both pass and product-failure cases.

- [ ] **Step 8: Run unit, integration, and fixture gate**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/eval/test_short_term_memory_eval.py \
  tests/integration/test_dingtalk_multiturn_memory.py
PYTHONPATH=src python eval/run_short_term_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/short_term_scenarios.jsonl \
  --output /tmp/short-term-memory-eval
```

Expected: PASS/exit 0 with 24 scenarios represented in both reports.

- [ ] **Step 9: Commit**

```bash
git add tests/support/dingtalk_scenario.py \
  tests/integration/test_dingtalk_multiturn_memory.py \
  eval/memory/short_term_scenarios.jsonl \
  eval/run_short_term_memory_eval.py \
  tests/unit/eval/test_short_term_memory_eval.py \
  src/sales_agent/integrations/dingtalk/processor.py
git commit -m "test(memory): add real dingtalk multi-turn gate"
```

---

### Task 9: Package release gates, operational checks, and the rollback runbook

**Files:**
- Create: `scripts/run_short_term_memory_gate.sh`
- Create: `docs/runbooks/short-term-memory.md`
- Modify: `README.md`
- Create: `tests/unit/test_short_term_memory_gate.sh`

**Interfaces:**
- Consumes: all Spec 1 tests/runners and strict readiness from Tasks 1–8.
- Produces: one deterministic pre-release command; documented model/staging commands; checkpoint failure/recovery/rollback procedure.

- [ ] **Step 1: Write failing shell-contract tests**

Create `tests/unit/test_short_term_memory_gate.sh` to assert the new gate:

- rejects a missing/empty `TEST_DATABASE_URL`;
- refuses a database name that does not contain `test`;
- sets `PYTHONPATH=src`;
- runs checkpoint, Topic, Graph, DingTalk, concurrency, and evaluator tests;
- runs router fixture and 24-scenario fixture reports;
- preserves the first non-zero exit code;
- never invokes a production outbound DingTalk sender.

- [ ] **Step 2: Run the shell test and verify RED**

Run:

```bash
bash tests/unit/test_short_term_memory_gate.sh
```

Expected: FAIL because `scripts/run_short_term_memory_gate.sh` does not exist.

- [ ] **Step 3: Implement the deterministic gate**

The script begins with:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}"
```

Then run, in order:

1. checkpoint/Topic/router unit tests;
2. Online/Guided Flow Graph tests;
3. PostgreSQL checkpoint/concurrency tests;
4. real-processor DingTalk multi-turn tests;
5. router fixture report;
6. short-term fixture report.

Write reports under `${OUTPUT_DIR:-/tmp/sales-agent-short-term-memory-gate}`.

- [ ] **Step 4: Write the operations runbook**

Document:

- architecture and state ownership;
- startup/readiness expectations;
- required PostgreSQL checkpoint tables and how library `setup()` is invoked;
- how to run deterministic, model-backed, and staging gates;
- how to inspect a thread by hashed/scoped ID without exposing message content;
- checkpoint pool and advisory-lock diagnostics;
- symptoms of stale turn state, Topic leakage, duplicate events, lock contention, and lost flow state;
- restart and multi-worker validation;
- rollback steps.

Rollback must deploy the prior application version without dropping checkpoint tables. If thread-state schema compatibility is uncertain, disable affected DingTalk traffic, archive/delete only the selected test/staging thread checkpoints, and restore service. Never drop the global checkpoint schema during rollback.

- [ ] **Step 5: Verify model-backed Router and short-term scenarios**

With evaluation credentials and a staging tenant configured:

```bash
PYTHONPATH=src python eval/router/run_router_eval.py \
  --tenant-id "$EVAL_TENANT_ID" \
  --output /tmp/router-eval-model

PYTHONPATH=src python eval/run_short_term_memory_eval.py \
  --mode model \
  --tenant-id "$EVAL_TENANT_ID" \
  --dataset eval/memory/short_term_scenarios.jsonl \
  --output /tmp/short-term-memory-model
```

Expected: both commands finish without coroutine warnings, produce JSON/Markdown, and meet the configured Spec 1 gates. If credentials are unavailable, execution is blocked rather than substituting fixture results as model evidence.

- [ ] **Step 6: Prove no production in-memory fallback remains**

Run:

```bash
grep -R -n "get_online_checkpointer_sync\|falling back to InMemorySaver" \
  src/sales_agent --include='*.py'
```

Expected: no output.

Allow `InMemorySaver` only in explicit unit/test helpers:

```bash
grep -R -n "InMemorySaver" src/sales_agent --include='*.py'
```

Expected: only the documented `get_checkpointer_sync()` test factory; no production caller imports it.

- [ ] **Step 7: Run deterministic release gate and full regression suite**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  bash scripts/run_short_term_memory_gate.sh

PYTHONPATH=src pytest -q
```

Expected: the Spec 1 gate exits 0. The full suite introduces zero failures relative to the clean execution baseline, and every failure in the baseline’s memory/Online Graph/DingTalk/router slices is resolved. Do not modify unrelated ChatPipeline/DeepEval/Ontology behavior merely to hide a pre-existing failure; record unrelated baseline failures separately and require owner resolution before claiming a globally green suite.

- [ ] **Step 8: Update active documentation and commit**

Add a concise README section linking the runbook and explaining that production Online Graph state is PostgreSQL-backed while Topic state remains in application tables.

```bash
git add scripts/run_short_term_memory_gate.sh \
  docs/runbooks/short-term-memory.md README.md \
  tests/unit/test_short_term_memory_gate.sh
git commit -m "docs(memory): add short-term memory release gate"
```

---

## Final Verification Checklist

- [ ] A new worktree was created from the latest clean main branch before execution.
- [ ] All nine task commits exist and each task review approved both spec compliance and code quality.
- [ ] PostgreSQL checkpoint initialization is fail-closed in API, Stream, and Worker roles.
- [ ] `/ready` reports checkpoint readiness without opening a new connection per request.
- [ ] The stable thread key contains tenant, Agent, channel, and user, and contains no date.
- [ ] New-turn transient fields are reset; active Topic/flow and idempotency fields carry only under their state machines.
- [ ] Reset explicitly clears Topic/flow state and supports a remaining message in the same turn.
- [ ] Unique Topic restore, multiple-candidate clarification, numeric/model selection, and 24-hour expiry all pass.
- [ ] Standard and streaming paths share preparation, thread key, lock, Graph, and turn input.
- [ ] Duplicate events produce no repeated Chat/flow/log/reply side effect.
- [ ] Same-thread turns serialize across database sessions while different-user turns remain concurrent.
- [ ] The model-backed router awaits real production contracts and reports populated-class macro metrics.
- [ ] Clarification completion is measured on the resolving turn.
- [ ] At least 24 real-processor short-term scenarios produce machine-readable reports.
- [ ] Restart/worker-switch recovery is 100% on deterministic scenarios.
- [ ] Cross-tenant, cross-Agent, cross-user, and cross-Topic leakage counts are zero.
- [ ] Deterministic gate exits 0 and model-backed evidence is stored outside git.
- [ ] Full-suite results are compared against the execution baseline with no new regressions.
- [ ] The final whole-branch review approves the combined diff before merge.
