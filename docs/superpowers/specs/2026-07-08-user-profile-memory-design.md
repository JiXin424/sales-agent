# User Profile Projection and Memory Recall Design

**Program:** Sales Agent Memory Program
**Sequence:** Spec 3 of 4
**Dependencies:** Specs 1–2 accepted
**Status:** Approved design

## 1. Goal

Turn active atomic memories into an explainable sales-user profile and use only task-relevant profile information to personalize later conversations.

The profile must improve answers without leaking old Topic state, replacing product evidence, or creating an unverifiable second source of truth.

## 2. Scope

### In scope

- versioned sales-user profile projection;
- evidence links from profile fields to atomic memories;
- incremental and full rebuild;
- task-aware memory retrieval gate;
- bounded memory injection into the Chat Graph;
- provenance in traces;
- “你记得我什么” transparency response;
- profile refresh after correction, forget, expiry, or new evidence.

### Out of scope

- customer or organization profiles;
- inferred demographic/personality traits;
- profile editing UI beyond service/API and DingTalk transparency interactions;
- autonomous actions based solely on profile data.

## 3. Profile Model

### 3.1 `user_memory_profiles`

One current profile per `(tenant_id, agent_id, user_id)`:

```text
id
tenant_id
agent_id
user_id
version
status                 # ready | rebuilding | degraded
profile_json
evidence_map_json      # profile path -> memory IDs
source_memory_version
generated_at
created_at
updated_at
```

`profile_json` contains only approved sections:

```json
{
  "work_context": {
    "role": null,
    "sales_region": null,
    "product_focus": []
  },
  "response_preferences": {
    "verbosity": null,
    "format": [],
    "coaching_style": null
  },
  "development": {
    "coaching_goals": [],
    "recurring_challenges": [],
    "confirmed_sales_patterns": []
  }
}
```

No free-form personality summary is stored.

## 4. Projection Rules

The projection is deterministic over active atomic memories:

- single-valued keys use the newest active confirmed memory;
- list-valued keys contain active, non-expired memories ordered by recency and confirmation strength;
- superseded, deleted, expired, rejected, and candidate memories are excluded;
- every output field records contributing memory IDs;
- an empty memory set produces an empty profile, not model-generated defaults.

The projection worker is idempotent. Corrections, deletes, expiries, and activations enqueue a rebuild event. A scheduled reconciliation job detects missed rebuilds by comparing memory and profile versions.

## 5. Retrieval Gate

Long-term memory is not injected into every prompt. The retrieval gate receives the resolved Topic relation, standalone query, task type, and allowed profile sections.

Examples:

- a request for a concise sales script may use response preferences and product focus;
- coaching feedback may use confirmed coaching goals and recurring challenges;
- a product-price question may use verbosity preference but must get price facts from tenant knowledge retrieval;
- a new customer/topic must not receive prior Topic entities, goals, or temporary customer facts;
- greeting, help, reset, and destructive memory commands do not perform semantic profile retrieval.

Hard scope filters run before semantic ranking. Results must match tenant, Agent, user, active status, non-expiry, and allowed memory types.

## 6. Bounded Recall

The prompt receives at most five memories and at most 1,200 Chinese characters of memory context. Configuration may lower these limits but not exceed them without a reviewed change.

Ranking combines:

- task/type eligibility;
- semantic relevance to standalone query;
- confirmation strength;
- recency and last confirmation;
- diversity across normalized keys.

Ranking thresholds are calibrated on the evaluation dataset rather than hard-coded from raw embedding scores.

Injected memory is placed in a separate structured block:

```text
USER_MEMORY_CONTEXT
- memory_id
- memory_type
- normalized fact/preference
- last_confirmed_at
END_USER_MEMORY_CONTEXT
```

The system prompt states that memory may personalize style and coaching context but cannot override safety, tenant instructions, tool results, or knowledge evidence.

## 7. Transparency and Correction

“你记得我什么” returns an understandable profile grouped by approved section and offers correction/forget instructions. It does not reveal embeddings, internal scores, hidden prompts, or restricted audit content.

For each remembered item, the service can expose:

- what is remembered;
- memory type;
- when it was last confirmed;
- whether it was explicitly stated or corroborated;
- a stable reference for correction/deletion.

If a user corrects an item from this response, the atomic-memory service performs the correction and the profile is rebuilt before a success confirmation is returned.

## 8. Graph Integration

The Online Graph order is:

1. normalize DingTalk event;
2. resolve Topic and `turn_relation`;
3. construct standalone query;
4. route task/evidence requirements;
5. decide eligible profile sections;
6. retrieve bounded active memories;
7. invoke Chat Graph with Topic history, tenant knowledge evidence, and separately labeled user memory;
8. render response;
9. enqueue candidate extraction and profile reconciliation asynchronously.

Trace output includes eligible memory types, candidate count, selected memory IDs, exclusion reasons, retrieval latency, and profile version. Raw sensitive content is not copied into operational logs.

## 9. Error and Degradation Behavior

- Profile missing/stale: retrieve eligible active atomic memories directly or answer without personalization; enqueue rebuild.
- Memory repository unavailable: continue normal answer without user memory and mark trace `memory_degraded`.
- Projection worker failure: retry idempotently; atomic memory remains authoritative.
- Retrieval timeout: return ordinary answer; never substitute unscoped chat history.
- Invalid evidence link: omit the profile field and alert reconciliation.
- Deleted/expired memory found in cache: reject at final authorization filter and invalidate the cache.

## 10. Tests

### Projection tests

- empty profile;
- single and list-valued fields;
- correction supersedes prior field;
- forget and expiry remove fields;
- evidence map completeness;
- idempotent rebuild;
- reconciliation after missed event.

### Retrieval tests

- task/type allowlists;
- top-five and character budget;
- tenant/Agent/user hard isolation;
- relevance and diversity;
- new Topic excludes temporary prior context;
- product facts still require knowledge evidence;
- deleted/expired memory blocked after cache hit.

### Real user scenarios

1. Day one explicit region and brevity preference; day two new Topic recalls both without restoring the old task.
2. Region correction changes subsequent personalization and removes the old value.
3. Forget region removes it from transparency and later prompts.
4. Same preference applies across Topics; prior customer/goal does not.
5. “你记得我什么” matches profile evidence and supports correction.
6. Long-term memory outage yields a correct non-personalized answer with degraded trace.

## 11. Acceptance Criteria

- Every non-empty profile field references active atomic memories.
- Deleted, superseded, rejected, candidate, and expired memories never appear.
- Cross-tenant, cross-Agent, and cross-user retrieval leakage is zero.
- Memory context respects the five-item and 1,200-character limits.
- Explicit correction/forget is visible in the next turn and transparency response.
- Product/policy facts continue to require trusted tenant knowledge evidence.
- Memory outage does not prevent ordinary answering and is observable.
- Profile rebuilds are idempotent and reconcile after missed events.
