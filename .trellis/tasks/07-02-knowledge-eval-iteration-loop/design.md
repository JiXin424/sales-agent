# Knowledge Evaluation Optimization Loop — Technical Design

## 1. Design Summary

The system is a feedback controller around the existing Sales Agent evaluation path. It is not numerical gradient descent: evaluation metrics identify symptoms, deterministic trace analysis and counterfactual probes identify the likely cause, and a restricted optimization Agent proposes one-variable-class experiments. Every candidate is built and evaluated in isolation. A human approves publication. Publication creates an immutable release manifest and then triggers the next exploration-question generation cycle.

The recommended shape is a LangGraph optimization workflow backed by PostgreSQL, operated primarily through the existing React console and secondarily through a CLI that calls the same API.

## 2. System Boundaries

### 2.1 Optimization domains

The first version may modify:

1. Router rules, router prompt examples, and knowledge-retrieval trigger decisions.
2. Retrieval profile settings, query rewriting, tenant synonyms, ranking, reranking, and chunk configuration.
3. Tenant knowledge-document revisions supported by explicit evidence.

Answer-generation prompt optimization is diagnosed but not automatically modified.

### 2.2 Control plane and data plane

- The control plane owns iterations, diagnoses, candidates, approvals, releases, question sets, and audit state.
- The data plane executes the Sales Agent graph against a pinned release manifest.
- A graph node must not resolve "current active configuration" after run initialization. It consumes version IDs pinned in graph state.

## 3. End-to-End Flow

```text
Select tenant/Agent and baseline release
  -> generate or select fixed/exploration suites
  -> run baseline evaluation with full route/retrieval trace
  -> validate eval cases and metrics
  -> cluster failures
  -> deterministic attribution
  -> counterfactual probes where downstream checks were blocked
  -> restricted Agent proposes one-category candidate
  -> build sandbox knowledge/config version
  -> targeted evaluation
  -> sibling regression
  -> fixed regression
  -> safety and cross-tenant public regression
  -> combined release-manifest regression
  -> human approval
  -> atomic runtime-binding switch
  -> post-release canary
  -> generate next exploration suite
```

Each stage writes durable state and an idempotency key. A failed stage resumes without re-running completed work.

## 4. Failure Attribution

### 4.1 Ordered diagnosis

Attribution proceeds in this order because an upstream failure invalidates downstream conclusions:

| Order | Probe | Outcome |
|---|---|---|
| 0 | Is the question, reference, and source lineage valid? | `invalid_eval_case` |
| 1 | Should retrieval have fired, and did it? | `route_miss` |
| 2 | Does the required fact exist in the pinned corpus? | `document_missing`, `document_wrong`, `document_conflict` |
| 3 | Is a supporting chunk in the broad top-30 candidate set? | `retrieval_recall` |
| 4 | Is it in top-30 but outside final top-k? | `retrieval_ranking` |
| 5 | Is selected context noisy, truncated, or non-self-contained? | `context_noise`, `chunking_or_structure` |
| 6 | Is sufficient evidence present but unused or contradicted? | `generation_issue` |

### 4.2 Oracle corpus lookup

`oracle_corpus_lookup` searches the complete pinned knowledge version using required facts, source lineage, and full-corpus evidence scanning. It does not use the candidate's production retrieval settings. Its purpose is to distinguish a corpus gap from a retrieval failure.

### 4.3 Counterfactual probes

- Force retrieval when the route skipped it.
- Search with required-fact aliases to test vocabulary mismatch.
- Increase candidate depth without changing final top-k to test recall.
- Move a gold chunk into context to isolate generation behavior.
- Replay the same checkpoint with one candidate manifest.

Diagnoses may record secondary causes, but an experiment addresses exactly one primary cause.

### 4.4 Metric applicability

Context-dependent metrics have three states: `applicable`, `not_applicable`, and `invalid`. Empty context must not produce perfect Faithfulness or AnswerRecall. TaskCompletion uses a task definition derived from the question type rather than one global "sales coaching" rubric.

Deterministic measures such as route match, required-fact coverage, forbidden-claim violations, gold-chunk Recall@k, and MRR take priority over LLM Judge scores. Borderline Judge decisions are evaluated three times and use median score or majority vote.

## 5. Restricted Optimization Agent

### 5.1 Tools

- `inspect_failure_cluster`: returns cases, trace evidence, source facts, and shared symptoms.
- `propose_router_patch`: can change only tenant router rules, examples, and retrieval-trigger policy.
- `propose_retrieval_patch`: can change one parameter group such as tenant synonyms, query rewriting, ranking weights, reranker, or chunk configuration.
- `propose_document_patch`: emits an evidence-linked unified diff or opens a knowledge gap when evidence is insufficient.
- `build_sandbox_version`: creates immutable candidate knowledge/config versions.
- `run_candidate_eval`: runs staged evaluation and supports early stopping.
- `compare_candidate`: produces baseline/candidate deltas, regressions, cost, and uncertainty.
- `submit_for_release`: creates an approval request only after all gates pass.

### 5.2 Search limits

- Maximum three candidates per failure cluster by default.
- Stop after two consecutive attempts without measurable improvement.
- Never change more than one optimization domain in a candidate.
- Preserve the hypothesis, changed variables, exact patch, target cases, and result for every attempt.

## 6. Question-Bank Evolution

### 6.1 Fixed and exploration suites

- The fixed suite is immutable during an iteration and is the release gate.
- The exploration suite is generated from a newly published knowledge version and is first used in the next iteration.
- Promotion from exploration to fixed requires quality review and creates a new fixed-suite version.

### 6.2 Fact inventory

Question generation begins by extracting versioned facts with subject, predicate, object, qualifiers, effective dates, evidence spans, source revision, and conflict status. Question generators receive structured facts rather than entire source paragraphs, reducing document-phrase leakage.

### 6.3 Synthetic distribution

| Type | Target share |
|---|---:|
| Single-document factual | 25% |
| Paraphrase, alias, typo, and noise | 20% |
| Cross-document comparison or multi-hop | 15% |
| Simulated sales scenario | 20% |
| Unanswerable near-neighbor | 10% |
| Conflict, effective-date, or version-sensitive | 10% |

Simulated roles include new salesperson, experienced salesperson, enterprise HR, union purchaser, customer relay, skeptical customer, and contextual follow-up. Cross-document questions require a verified entity relationship. Unanswerable questions require a successful oracle confirmation that the answer is absent.

Each case records required facts, forbidden claims, expected route, source fact/document lineage, role, answerability, difficulty, generator version, and generation strategy.

## 7. PostgreSQL Data Model

The current PostgreSQL database remains the system of record. Existing application-level tenant filtering is retained initially; every new query must require tenant context. PostgreSQL row-level security is a compatible future hardening step for shared deployments.

### 7.1 Existing tables to extend

#### `eval_suites`

Add `suite_type`, `version`, `parent_suite_id`, `generator_version`, `knowledge_version_id`, `generation_config_json`, and `content_hash`. Enforce uniqueness on `(tenant_id, agent_id, name, version)`.

#### `eval_cases`

Add `question_type`, `answerability`, `difficulty`, `expected_answer`, `required_facts_json`, `forbidden_claims_json`, `source_fact_ids_json`, `source_document_ids_json`, `expected_route`, `role_type`, `generation_strategy`, `generator_version`, `lineage_case_id`, and `quality_status`.

#### `eval_runs`

Add `iteration_id`, `candidate_id`, `knowledge_version_id`, `retrieval_profile_id`, `router_profile_id`, `judge_model`, `judge_config_json`, `random_seed`, `run_type`, `artifact_prefix`, and `heartbeat_at`. The tuple `(tenant_id, iteration_id, candidate_id, eval_suite_id, run_type)` is the idempotency boundary.

#### `eval_run_results`

Add actual output, route details, retrieval trigger/skip details, query, source count, fact coverage, forbidden-claim count, token usage, error code, and trace completeness.

#### `document_chunks`

Add `knowledge_version_id`, `document_revision_id`, `chunker_version`, and `chunk_config_hash`. All retrieval queries must filter by both `tenant_id` and `knowledge_version_id` so old and candidate chunks cannot mix.

### 7.2 Evaluation details

#### `eval_metric_results`

One row per case and metric: tenant, run result, metric name/version, score, threshold, pass state, applicability, reason, judge model, raw output reference, and evaluation cost.

#### `retrieval_traces`

One row per evaluated retrieval: tenant, run result, retrieval profile, original query, rewritten queries, top-k, candidate-k, channel weights, RRF constant, chunk snapshot, and latency.

#### `retrieval_trace_hits`

One row per candidate and channel: tenant, trace, document/revision/chunk, channel, channel rank/score, final rank/score, selected-for-context, gold-document indicator, and gold-fact support.

### 7.3 Knowledge and configuration versions

#### `document_revisions`

Stores immutable document content revisions with parent revision, content hash, change source, candidate, evidence summary, effective dates, status, and creator. `documents` remains the logical document identity.

#### `knowledge_versions`

Stores tenant/Agent snapshots with parent version, status, source, candidate, manifest hash, counts, embedding model, and activation/retirement times.

#### `knowledge_version_documents`

Maps each knowledge version to one revision of every included logical document.

#### `retrieval_profiles`

Stores versioned retrieval mode, top-k, candidate-k, minimum score, keyword weight, RRF constant, reranker, query rewrite, tenant synonyms, chunk settings, config hash, candidate, and status.

#### `router_profiles`

Stores versioned router prompt reference, rules, confidence thresholds, knowledge-trigger rules, config hash, candidate, and status. Prompt text remains in `prompt_versions`.

### 7.4 Iteration control

#### `optimization_iterations`

The root record: tenant, Agent, iteration number, status, baseline version references, fixed/exploration suites, baseline eval run, release, budget, token/cost usage, timestamps, heartbeat, and errors.

#### `failure_diagnoses`

Stores cluster key, primary/secondary causes, confidence, evidence, affected cases, blocked checks, recommended action, status, and diagnoser version.

#### `optimization_candidates`

Stores diagnosis, attempt number, one change type, status, hypothesis, structured patch, document diff, changed variables, baseline/sandbox references, and creator.

#### `candidate_eval_runs`

Maps candidates to targeted, sibling, fixed, safety, and cross-tenant evaluation runs and their decisions.

### 7.5 Release and audit

#### `optimization_releases`

Stores immutable release number and manifest hash; knowledge, retrieval, router, prompt set, model snapshot, graph definition, and code revision; parent/rollback relationships; decision, approval, publication, and rollback data.

#### `agent_runtime_bindings`

Contains one atomic active-release pointer per tenant/Agent, previous release, lock version, activation actor, and activation time. Publication and rollback update this row transactionally with optimistic locking.

#### `release_events`

Stores immutable actor, event, status transition, payload, and timestamp for approval, publication, canary, and rollback events.

#### `knowledge_facts`

Stores versioned structured facts and evidence spans for oracle lookup and question generation.

#### `iteration_graph_checkpoints`

Maps optimization iteration/candidate/eval runs to LangGraph thread and checkpoint identifiers, stage, parent checkpoint, and all pinned version references.

### 7.6 Tenant constraints

- `tenant_id` is non-null on every business table.
- Unique and high-value indexes begin with `tenant_id`.
- Services derive tenant from authenticated execution context, not Agent-supplied tool arguments.
- Every cross-table lookup validates matching tenant ownership.
- Large regenerable reports are stored outside PostgreSQL; PostgreSQL stores their URI, hash, and summary.

## 8. Release Manifest, Time Travel, and Rollback

### 8.1 Pinned graph state

At graph entry, resolve the active release exactly once and put `release_id`, `knowledge_version_id`, `retrieval_profile_id`, `router_profile_id`, `graph_definition_version`, and prompt/model snapshot references into state. All downstream nodes consume these values.

### 8.2 Three rollback modes

1. Online rollback creates a new release whose manifest matches a selected historical release, then atomically switches the runtime binding.
2. Original replay resumes a historical checkpoint with its pinned historical release.
3. Forked replay resumes a checkpoint while explicitly substituting one candidate manifest for counterfactual comparison.

Rollback never rewrites history. A rollback is a new auditable release with `rollback_of_release_id`.

### 8.3 Retention

Knowledge versions, document revisions, chunks, and profiles are soft-retired. Garbage collection may delete them only when no release, checkpoint, eval run, iteration, or audit-retention policy references them.

### 8.4 Post-release protection

- Hard safety, tenant-leakage, or critical-fact canary failures trigger automatic rollback.
- Fixed-suite, latency, or error-rate degradation pauses iteration and requests rollback approval.
- Lower scores on newly generated exploration questions do not trigger rollback until reproduced on a stable suite.

## 9. Operator Experience

### 9.1 Web console

Add `/agents/:agentId/optimization` to the existing console. The workspace contains:

1. Overview: stage, progress, baseline/candidate metrics, budget, and live LangGraph node status.
2. Attribution: failure clusters with ordered trace evidence and counterfactual results.
3. Candidates: router/config/document diffs, hypotheses, scope, and risks.
4. Evaluation: aggregate, question-type, and per-case baseline/candidate comparisons with regressions emphasized.
5. Versions and Time Travel: release DAG, manifest comparison, checkpoint replay/fork, and rollback.
6. Next Question Set: generated exploration questions, lineage, quality status, and fixed-suite promotion.

An Agent conversation side panel explains decisions and requests constrained alternatives. It cannot bypass structured commands or approval.

### 9.2 CLI

CLI commands call the same APIs for start, watch, compare, approve, rollback, checkpoint replay/fork, and report export. CLI is intended for CI, recovery, and bulk operations rather than primary review.

### 9.3 Async execution

FastAPI creates durable jobs and returns immediately. An optimization worker leases PostgreSQL jobs using `FOR UPDATE SKIP LOCKED`, runs the LangGraph workflow, persists checkpoints/state, and emits SSE progress. Redis/Celery is unnecessary for the first version and remains a future scaling option.

## 10. Release Gates

Hard gates cannot be offset by an aggregate score:

- Target cluster improves by at least the configured threshold (initially 20%) or all targets pass.
- Fixed-suite pass rate does not regress beyond the configured tolerance (initially 2%).
- No new critical fact error.
- No increase in unanswerable-query fabrication.
- Safety and cross-tenant tests pass completely.
- Latency, token use, and error rate remain under configured limits.
- Document facts have evidence and human approval.

When multiple candidates are assembled into one release, rerun the full fixed and safety suites against the combined manifest.

## 11. Error Handling and Recovery

- Use `(tenant_id, iteration_id, candidate_id, stage)` as a stage idempotency key.
- Persist heartbeat and lease expiry for worker recovery.
- Retry only missing Judge metrics, not the entire Agent answer generation.
- Quarantine invalid cases and Judge-unstable cases from automated decisions.
- Convert unresolved document conflicts into human knowledge-review items.
- Preserve checkpoints and completed stage outputs on budget exhaustion or worker interruption.
- Prevent a failed tenant iteration from blocking other tenant jobs.

## 12. Verification Strategy

1. Attribution unit fixtures for route, corpus, recall, rank, context, and generation failures.
2. Tool-contract tests for tenant ownership, category whitelist, publish denial, and evidence requirements.
3. End-to-end sandbox fixtures containing a document gap, synonym gap, route miss, ranking miss, and unanswerable query.
4. Release tests for combined-candidate regression, optimistic locking, atomic switch, canary rollback, and audit events.
5. LangGraph tests for historical replay, candidate fork, pinned-release reproducibility, checkpoint authorization, and retention.
6. UI/API tests for SSE recovery, diff review, approval, rollback confirmation, and tenant-scoped navigation.

## 13. Compatibility and Migration

- Existing eval rows remain readable; new columns receive conservative defaults and JSON fields remain supported during migration.
- Existing document/chunk data is imported into an initial baseline `knowledge_version` and assigned revision 1.
- Existing active retrieval settings become baseline `retrieval_profile` version 1.
- Existing router prompt and rules become baseline `router_profile` version 1.
- Existing Agent configurations receive an initial release manifest and `agent_runtime_bindings` row.
- Retrieval code must switch to filtering the pinned knowledge version before multiple versions are built.
- Existing Graph Debug remains available; optimization threads use a separate authorized prefix and database ownership check.

## 14. Deliberate First-Version Exclusions

- Automatic answer-generation prompt modification.
- Unattended production publish.
- Real-log question generation.
- Automatic tenant-to-global promotion.
- Multi-domain candidate mutation.
- External workflow/trace platform dependency.

These exclusions keep the first release causally attributable, auditable, and reversible.
