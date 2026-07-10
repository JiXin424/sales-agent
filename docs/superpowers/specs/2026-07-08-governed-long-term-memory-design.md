# Governed Long-Term Atomic Memory Design

**Program:** Sales Agent Memory Program
**Sequence:** Spec 2 of 4
**Dependency:** Spec 1 accepted
**Status:** Approved design

## 1. Goal

Add safe cross-session memory for the sales user. Users can explicitly ask the Agent to remember, correct, or forget stable non-sensitive information. Inferred memories are persisted only after deterministic policy requirements are met.

This spec builds the atomic memory source of truth. User-profile projection and automatic answer personalization are delivered in Spec 3.

## 2. Scope

### In scope

- sales-user memory only;
- atomic facts, explicit preferences, coaching goals, and confirmed sales patterns;
- evidence, lifecycle, sensitivity, expiry, correction, and deletion;
- explicit remember/correct/forget interactions through DingTalk;
- candidate extraction after ordinary conversations;
- deterministic write policy;
- asynchronous inferred-memory persistence through an outbox;
- repository APIs and audit/metrics events.

### Out of scope

- customer or organization memories;
- automatic prompt injection and profile personalization;
- unreviewed personality or performance judgments;
- learning from assistant-generated answers;
- external CRM facts without a verified tool source.

## 3. Memory Types

Allowed memory types are a closed enum:

- `user_fact`: stable role, sales region, responsibilities, or product focus explicitly stated by the user;
- `response_preference`: preferred answer length, format, tone, or coaching style;
- `coaching_goal`: a durable skill or development objective stated or confirmed by the user;
- `sales_pattern`: a strategy or behavior the user explicitly confirms as repeatedly effective;
- `recurring_challenge`: a recurring blocker confirmed by the user, phrased neutrally and never as a personality label.

One record contains one atomic claim. Compound statements are split so each claim can expire, be corrected, and be forgotten independently.

## 4. Data Model

### 4.1 `agent_memories`

Required fields:

```text
id
tenant_id
agent_id
subject_type          # fixed to "user" in this phase
subject_id            # internal user ID
memory_type
normalized_key        # e.g. "sales_region"
content_json
search_text
status                # candidate | active | superseded | deleted | expired | rejected
source_kind           # explicit_user | inferred_user | verified_tool
source_conversation_id
source_message_ids_json
evidence_count
confidence_band       # confirmed | corroborated | candidate; not raw LLM probability
sensitivity           # normal | restricted | prohibited
supersedes_id
observed_at
last_confirmed_at
expires_at
created_at
updated_at
```

Indexes enforce tenant/Agent/user scope and support lookup by normalized key, status, expiry, and source message. An active-memory uniqueness rule prevents two simultaneously active single-valued facts such as `sales_region`.

### 4.2 `memory_outbox`

Inferred-memory work is queued transactionally:

```text
id, tenant_id, agent_id, user_id, conversation_id, event_id,
payload_json, status, attempts, available_at, last_error, created_at, updated_at
```

`event_id` plus operation type is idempotent. A worker retries transient errors with bounded backoff and moves exhausted jobs to a visible dead-letter state.

### 4.3 Audit events

Every activation, rejection, supersede, expiry, delete, and failed write emits an auditable event without copying prohibited content into logs.

## 5. Candidate Extraction

The extractor receives only:

- the current user message;
- the current Topic summary/goal when relevant;
- verified tool facts, if any;
- a closed memory-type schema.

It does not receive the assistant answer as evidence.

It returns structured candidates with atomic content, normalized key, claimed evidence span, stability classification, and sensitivity classification. Parsing failure produces no memory and does not affect the user’s primary answer.

## 6. Deterministic Write Policy

The policy engine, not the model, decides persistence.

### 6.1 Explicit remember

Messages with an unambiguous user instruction such as “记住我负责华东区” are handled synchronously:

1. extract the requested fact;
2. reject prohibited/sensitive categories;
3. detect conflict with active memories;
4. activate or supersede within the same transaction;
5. confirm exactly what was remembered.

If the target fact is ambiguous, the Agent asks a clarification and does not write.

### 6.2 Ordinary inferred candidate

An inferred candidate does not become active merely because an LLM reports high confidence. It becomes active only when:

- the same normalized claim is supported by two distinct user messages in separate turns; or
- the user explicitly confirms a pending candidate.

Until then it remains `candidate` and is excluded from answer recall and profiles.

### 6.3 Correction

“我不负责华东了，现在负责华南” creates a new confirmed memory, marks the old active memory `superseded`, links `supersedes_id`, and emits a projection-rebuild event. The old memory remains auditable but is not retrievable for personalization.

### 6.4 Forget

Forget resolves targets by scope and normalized key. Exact single matches are marked `deleted`. Multiple matches require clarification. Broad requests such as “忘记关于我的所有信息” require explicit confirmation and then delete all eligible user memories in scope.

Deleted content is excluded immediately from reads and profile projection. Physical retention or erasure follows the project’s privacy policy, while application-level forget semantics are immediate and testable.

## 7. Safety Rules

Prohibited by default:

- passwords, access tokens, government IDs, banking data, precise personal contact data;
- medical, political, religious, or other sensitive-trait inference;
- negative personality, competence, or performance labels inferred by the model;
- temporary moods and one-off task parameters;
- customer facts stored under a sales-user subject;
- claims derived only from the assistant’s prior response;
- data whose tenant, Agent, user, or source cannot be verified.

Restricted categories may be added only through a later reviewed schema and retention policy.

## 8. Expiry

Default lifecycle by type:

- response preference: no automatic expiry, but user correction supersedes it;
- role/region/product focus: expires after 180 days without reconfirmation;
- coaching goal: expires after 90 days without activity or confirmation;
- recurring challenge and sales pattern: expires after 90 days without reconfirmation.

Expiry is evaluated by a scheduled job and lazily during reads. Expired records are never returned as active even if the scheduled job is delayed.

## 9. APIs and Services

The memory domain exposes typed operations rather than direct model access to storage:

```text
propose_candidates(evidence) -> list[MemoryCandidate]
apply_explicit_memory(command) -> MemoryOperationResult
corroborate_candidate(candidate) -> MemoryOperationResult
correct_memory(command) -> MemoryOperationResult
forget_memory(command) -> MemoryOperationResult
list_active_memories(scope, filters) -> list[AtomicMemory]
get_memory_with_provenance(scope, memory_id) -> AtomicMemory
expire_due_memories(now) -> ExpiryResult
```

All repository queries require an immutable `MemoryScope(tenant_id, agent_id, user_id)` object. APIs without scope do not exist.

## 10. Error Handling

- Explicit memory write failure returns a clear “not saved, please retry” response; it must not claim success.
- Inferred candidate extraction/write failure does not block the ordinary conversation and is retried through the outbox.
- Dead-letter jobs trigger an operational alert and remain replayable.
- Concurrent corrections use row/version locking so only one active single-valued fact remains.
- Duplicate event delivery returns the original operation result.
- Sensitivity uncertainty defaults to rejection or clarification, not storage.

## 11. Tests

### Unit and property tests

- type allowlist and prohibited categories;
- atomic splitting and normalization;
- explicit/inferred policy differences;
- two-evidence activation;
- single-valued conflict and supersede;
- forget targeting and confirmation;
- expiry boundaries;
- scope construction and repository query enforcement;
- idempotent outbox processing;
- no assistant-answer evidence.

### Database integration

- tenant/Agent/user isolation;
- active uniqueness under concurrent corrections;
- outbox and memory transaction atomicity;
- lazy expiry excludes stale records;
- deleted/superseded memory never appears in active queries.

### Real DingTalk scenarios

1. “记住我负责华东区” → confirmed active memory.
2. A normal one-off statement → candidate only.
3. The same stable statement in a later turn → active after corroboration.
4. “现在改成华南” → old memory superseded.
5. “忘记我的区域信息” → deleted and no longer listed.
6. Sensitive-memory request → refused without persistence.

## 12. Acceptance Criteria

- Explicit remember/correct/forget success rate is 100% on deterministic labeled cases.
- Cross-tenant and cross-user leakage is zero.
- No assistant-generated content is activated as a user memory.
- Inferred memories require two independent user evidences or confirmation.
- Concurrent corrections leave one active single-valued fact.
- Forget excludes the target immediately from all active reads.
- Every active memory is traceable to source user messages.
- Primary conversation latency is not blocked by inferred-memory extraction or profile work.
