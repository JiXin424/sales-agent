# Durable Short-Term Memory and Real Multi-Turn Flow Design

**Program:** Sales Agent Memory Program
**Sequence:** Spec 1 of 4
**Status:** Approved design

## 1. Goal

Make the existing Topic and Guided Flow behavior durable and directly testable through the real DingTalk user path before adding long-term memory.

After this spec, a user can continue a Topic or Guided Flow after a process restart or worker change, while a new Topic remains isolated from old temporary context.

## 2. Scope

### In scope

- PostgreSQL-backed Online Graph checkpoints in production.
- Stable thread identity that does not reset at midnight.
- Explicit separation of turn-scoped state from thread-scoped state.
- `ConversationTopic` as the semantic source of truth.
- Existing 30-minute Topic expiry and 24-hour explicit restore behavior.
- Existing five-class `turn_relation` and pending clarification flow.
- Real multi-turn execution through `handle_dingtalk_event()`.
- Correct model-backed router evaluation.
- Restart, worker-switch, duplicate-event, and concurrent-turn tests.

### Out of scope

- cross-session user memories;
- user profile projection;
- customer memory;
- external business actions;
- changes to the four Guided Flow product definitions beyond durability fixes.

## 3. State Ownership

### 3.1 `ConversationTopic`

`ConversationTopic` remains authoritative for conversational meaning:

- current goal;
- summary;
- retained entities;
- active constraints;
- retracted goals;
- clarification state;
- lifecycle timestamps and status.

Topic expiry is based on `last_active_at`, not thread date or server uptime.

### 3.2 Conversation messages

`ConversationMessage` is authoritative for the user/assistant history belonging to a Topic. Context loading must filter by tenant, conversation, role, and `topic_id` when a Topic exists.

### 3.3 LangGraph checkpoint

The checkpoint stores execution state needed to resume a Graph or Guided Flow. It must not become a second semantic Topic database. Checkpoints may be compacted or deleted after their retention window as long as Topic and message records remain valid.

## 4. Durable Checkpoint Architecture

The Online Graph must use `AsyncPostgresSaver` or the project’s equivalent PostgreSQL saver initialized during application startup.

Production requirements:

1. The saver schema is created through a dedicated migration/startup step.
2. Application readiness fails when durable checkpoint initialization fails.
3. Production never silently substitutes `InMemorySaver`.
4. Unit tests may inject `InMemorySaver`; integration tests use PostgreSQL.
5. API and DingTalk stream workers use the same checkpoint database and thread-key algorithm.

Long-term-memory unavailability may degrade personalization in later specs. Checkpoint unavailability is different: continuing a stateful flow without shared state is unsafe, so the system returns a recoverable processing message and records an operational error rather than inventing a new local state.

## 5. Thread Identity and Lifecycle

The production thread key is stable for a user scope:

```text
online:{tenant_id}:{agent_id}:{channel}:{session_user_id}
```

The calendar date is removed. Daily behavior belongs to Guided Flow business rules, not storage identity.

Topic transitions explicitly reset semantic state:

- `continue`: keep current Topic and refresh expiry;
- `revise`: keep Topic, update goal, record retracted goals;
- `switch`: close current Topic and create a child Topic with only selected retained entities;
- `new`: close current Topic and create a clean Topic;
- `ambiguous`: keep the Topic and create pending clarification without executing the uncertain request.

When a Topic or Guided Flow completes, its active fields are explicitly cleared. Retired turn outputs must never leak merely because LangGraph merges input with a checkpoint.

## 6. Turn-State Reset Contract

Every `invoke_online_turn()` supplies a complete new-turn envelope. Turn-scoped fields are reset before routing, including:

- rendered/final answer;
- sources and retrieval metadata;
- risk result and action;
- route decision and confidence;
- trace nodes and usage;
- transient errors;
- completed-flow/action fields that are not active state.

Thread-scoped fields are retained only when their owning state machine says so:

- active Topic reference;
- pending clarification;
- active Guided Flow and its current stage;
- checkpoint metadata required for idempotency.

The reset behavior must be represented by a typed constructor or a dedicated first node, not duplicated dictionaries across callers.

## 7. Real DingTalk Multi-Turn Harness

Tests construct normalized DingTalk events using a stable DingTalk user ID and conversation identity, then call `handle_dingtalk_event()` for every turn.

Only public outbound delivery is replaced with an in-memory `reply_fn`. The test must keep real:

- DingTalk user mapping;
- conversation mapping;
- command parsing;
- tenant and Agent resolution;
- Online Graph;
- Topic/message persistence;
- retrieval/risk routing where the scenario requires them;
- DingTalk rendering.

The harness returns a structured per-turn observation containing reply text, Topic ID, `turn_relation`, standalone query, active flow/stage, trace nodes, retrieval/risk decisions, and relevant persisted IDs. Database assertions are automated white-box checks; manual database inspection is not an acceptance mechanism.

## 8. Router Evaluation Repair

The router evaluator must support asynchronous production resolvers and supply their real input contract:

- current message;
- active Topic object or equivalent structured context;
- ordered recent messages;
- configured chat model;
- tenant/Agent prompt-resolution context.

Fixture mode remains synchronous and deterministic. The runner accepts both sync and async callables through one awaited adapter.

Metrics include:

- per-class Precision/Recall/F1 and confusion matrix;
- overall accuracy;
- new/switch-to-continue Topic leakage;
- standalone-query entity retention;
- clarification detection;
- clarification resolution on the following turn.

Classes with zero examples are reported as not applicable and are excluded from macro averages.

## 9. Concurrency and Idempotency

- Duplicate DingTalk `event_id` values return the already-recorded outcome and do not advance a flow twice.
- Turns for the same thread are serialized with a database/advisory lock or equivalent shared mechanism.
- Turns for different users run concurrently.
- A stale worker may not overwrite a newer Topic version.
- Tenant, Agent, user, and channel scope are present in all Topic queries and state locks.

## 10. Error Handling

- **Checkpoint unavailable:** fail readiness at startup; at runtime return a retryable user response and operational alert.
- **Topic query unavailable:** rollback the turn; do not answer using unscoped history.
- **Context resolver parse failure:** retry once, then use the existing deterministic fallback and record `reason_code`.
- **Ambiguous restoration:** ask the user which recent Topic to continue; never select arbitrarily.
- **Logging failure after reply generation:** record an error and prevent a checkpoint from claiming successful durable completion unless the transaction contract guarantees consistency.

## 11. Tests

### Unit

- thread-key stability across midnight and timezone changes;
- complete turn-field reset;
- Topic boundaries at 29:59, 30:00, and 30:01;
- restore-window boundary;
- exact clarification commands and fallback;
- lock scope and duplicate-event behavior.

### Graph integration

Use deterministic model doubles and PostgreSQL checkpoints to execute 2–6 turns for:

1. pronoun follow-up;
2. within-turn revision retaining the product entity;
3. unrelated new Topic;
4. ambiguous reference followed by continue/new/replace;
5. Guided Flow preemption by another explicit flow;
6. process restart and worker switch during a flow.

Assert state, trace, Topic/messages, and user-visible replies after every turn.

### Staging DingTalk release test

Run the same scenarios through the HTTP/stream-normalized DingTalk event boundary with a staging tenant and non-production recipients. Public outbound delivery may be captured, but the business path is real.

## 12. Acceptance Criteria

- Online Graph production state survives restart and worker changes.
- No production code path silently falls back to `InMemorySaver`.
- Midnight does not split an active Topic or Guided Flow.
- All five `turn_relation` classes execute correctly through the Graph.
- Clarification completion measures the second-turn resolution, not only first-turn ambiguity detection.
- Duplicate events and same-user concurrent turns do not duplicate side effects or corrupt state.
- Cross-tenant, cross-Agent, cross-user, and cross-Topic leakage tests report zero.
- Real DingTalk multi-turn scenarios are runnable through one documented command and generate a machine-readable report.
