# Memory Evaluation and Production Operations Design

**Program:** Sales Agent Memory Program
**Sequence:** Spec 4 of 4
**Dependencies:** Specs 1–3 accepted
**Status:** Approved design

## 1. Goal

Make multi-turn and memory quality measurable before release and maintainable after release. Tests must execute the real user path, expose state and trajectory, and turn production failures into durable regressions.

## 2. Evaluation Principles

1. Text quality, state correctness, trajectory correctness, and persistence correctness are separate concerns.
2. Deterministic properties use code assertions; LLM judges do not decide tenant isolation, memory deletion, Topic IDs, tool/risk actions, or exact state transitions.
3. Semantic evaluators judge standalone-query quality, relevance, final outcome, and conversation naturalness.
4. Database checks are automated integration assertions, not manual acceptance steps.
5. Every report records model, prompt, code, dataset, knowledge, and memory-schema versions.
6. Missing inputs and evaluator failures are `N/A` or errors, never perfect scores.
7. Production data must be anonymized and reviewed before entering a committed dataset.

## 3. Test Layers

### 3.1 Unit and property tests — every commit

No live model calls. Cover:

- Topic state machine and expiry boundaries;
- memory policy, sensitivity, supersede, delete, and expiry;
- profile projection and evidence links;
- scope isolation;
- idempotency and concurrency invariants;
- typed parsing and deterministic fallbacks.

Property tests generate event sequences to verify that no sequence produces two active single-valued memories or more than one active Topic in a scope.

### 3.2 Graph scenario tests — every pull request

Use deterministic model doubles plus PostgreSQL checkpoints and memory tables. Execute complete 2–6 turn scenarios and assert after each turn:

- user-visible response;
- `turn_relation`, standalone query, Topic ID and lifecycle;
- retained entities and retracted goals;
- active flow/stage;
- selected memory IDs and profile version;
- retrieval/risk decisions and trace nodes;
- persisted Topic, messages, memories, profile, and outbox state.

### 3.3 Model-backed multi-turn regression — nightly and model/prompt changes

Use the configured production model against a versioned dataset. Run deterministic metrics for every scenario and semantic judges only where needed. Ambiguous and boundary-heavy cases run three repetitions and report consistency.

### 3.4 Staging DingTalk release gate — every release

Execute normalized HTTP/stream events through `handle_dingtalk_event()` with staging users and databases. Only public outbound delivery is captured. Include restart and worker-switch steps between turns.

### 3.5 Production online evaluation — continuous

Sample 5% of eligible completed threads after 30 minutes of inactivity. Apply deterministic code checks and limited reference-free semantic evaluation. High-risk failures, user corrections, repeated clarification, and explicit negative feedback are always retained for review regardless of random sampling.

## 4. Dataset Schema

Each JSONL scenario contains:

```json
{
  "id": "memory-cross-session-001",
  "version": 1,
  "tags": ["long_term", "profile", "cross_session"],
  "initial_state": {},
  "turns": [
    {
      "input": "记住我负责华东区",
      "time_offset_seconds": 0,
      "restart_before": false,
      "expected": {
        "turn_relation": "new",
        "memory_operation": "activate",
        "active_memory_keys": ["sales_region"],
        "reply_contains": ["华东"]
      }
    }
  ],
  "final_expected": {
    "active_topic_count": 1,
    "cross_scope_leakage": false
  }
}
```

Time offsets, restart, worker selection, duplicate event IDs, and concurrent groups are first-class scenario controls rather than test-specific code.

The initial release contains at least 40 multi-turn scenarios across:

- five `turn_relation` classes;
- expiry/restore and clarification resolution;
- four Guided Flows and preemption;
- explicit/inferred remember;
- correction, forget, and expiry;
- profile recall and no-recall controls;
- cross-scope isolation;
- restart, duplicate, and concurrency behavior;
- long-term-memory degradation.

Existing single-turn router datasets remain and are linked to related multi-turn regressions.

## 5. Metrics

### 5.1 Turn and Topic

- per-class Precision/Recall/F1 and confusion matrix;
- overall `turn_relation` accuracy;
- standalone-query entity retention precision/recall;
- Topic leakage rate for new/switch cases;
- real clarification completion rate measured on the resolving turn;
- restore correctness and unnecessary-restore rate.

### 5.2 Memory write and lifecycle

- explicit operation accuracy;
- inferred activation precision/recall;
- prohibited-memory write count;
- correction/supersede accuracy;
- forget effectiveness;
- stale-memory activation/recall rate;
- evidence provenance completeness.

### 5.3 Recall and profile

- relevant-memory precision/recall;
- unnecessary-memory injection rate;
- profile field accuracy and evidence coverage;
- cross-Topic and cross-scope leakage;
- user correction rate after memory use.

### 5.4 Conversation and trajectory

- final task outcome;
- repetition and unnecessary clarification;
- expected node/decision subset;
- response correctness/relevance where reference or trusted context exists;
- P50/P95 latency, memory overhead, token use, and cost.

## 6. Release Gates

Initial gates:

- overall `turn_relation` accuracy at least 90%;
- no critical new/switch Topic leakage in the release dataset;
- zero cross-tenant, cross-Agent, cross-user, and prohibited-memory leakage;
- 100% explicit remember/correct/forget accuracy on deterministic cases;
- clarification completion at least 90%;
- relevant-memory precision at least 90%;
- 100% restart/worker-switch recovery on deterministic scenarios;
- zero assistant-output-to-user-memory activations;
- no unexplained regression beyond the calibrated metric-specific tolerance.

Thresholds are recalibrated from human-labeled results. Changes require a versioned rationale; thresholds are not lowered merely to make a release pass.

## 7. Reporting

Reports group results by:

- deterministic state and safety;
- semantic answer quality;
- trajectory;
- persistence and isolation;
- latency/cost;
- scenario slice and failure taxonomy.

Every metric records applicability, numerator, denominator, score, threshold, error, and sample IDs. Reports show P50/P95 and confusion matrices rather than only averages.

Baseline comparison excludes incompatible schema versions and clearly labels new, fixed, regressed, flaky, and evaluator-error cases.

## 8. Production Observability

Per-turn trace fields include:

- tenant/Agent/user hashed scope identifiers;
- Topic ID and transition;
- checkpoint/thread version;
- eligible and selected memory types/IDs;
- profile version;
- memory degradation/fallback reason;
- route, retrieval, risk, and Guided Flow decisions;
- latency, usage, model/prompt/code versions;
- explicit user correction/forget/negative-feedback signals.

Dashboards track:

- Topic leakage and clarification loops;
- memory candidate activation/rejection;
- recall hit and false-positive rates;
- stale/deleted memory blocks;
- profile rebuild lag and failures;
- outbox backlog/dead letters;
- restart recovery and checkpoint errors;
- model/prompt regression by tenant and scenario slice.

## 9. Feedback Loop

1. Detect a production failure through code checks, semantic sampling, user correction, or feedback.
2. Store the trace in restricted retention with identifiers protected.
3. Classify root cause: routing, Topic, memory write, recall, profile, RAG, model, rendering, or infrastructure.
4. Create a minimal anonymized regression scenario with explicit expected state and outcome.
5. Reproduce failure before fixing.
6. Compare candidate and baseline versions offline.
7. Pass staging DingTalk gate, canary, and rollback checks.
8. Release and monitor the affected slice.

## 10. Error Handling

- Judge timeout/error is reported separately and does not change product pass/fail counts.
- A scenario setup or database failure invalidates the scenario run rather than producing a product score.
- Flaky model cases are repeated and classified; they are not silently retried until green.
- Production online-evaluation failure cannot block the user response.
- Dataset ingestion rejects raw secrets, direct identifiers, and unreviewed production conversations.

## 11. Commands and Operations

The evaluation runner exposes documented modes:

```text
unit-memory
graph-multiturn
model-multiturn
dingtalk-staging
compare <baseline> <candidate>
online-sample
promote-trace <reviewed-trace-id>
```

Each mode writes JSON plus a human-readable report and exits non-zero only for its own configured quality gates or invalid execution.

## 12. Acceptance Criteria

- At least 40 versioned multi-turn scenarios run from one command.
- Real model mode awaits production resolvers with their actual runtime inputs.
- Real DingTalk staging tests cover all six approved user scenarios.
- Clarification completion measures the follow-up resolution.
- Every report exposes applicability, denominators, errors, and versions.
- Production failures can be promoted into anonymized regressions through a documented workflow.
- Online evaluation does not block user traffic and has bounded sampling/cost.
- Release gates are automated, reproducible, and fail closed for isolation/safety violations.
