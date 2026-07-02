# Knowledge Evaluation Optimization Loop

## Goal

Create a multi-tenant, human-approved optimization loop that converts evaluation failures into attributable, testable improvements to knowledge routing, retrieval behavior, or tenant knowledge documents. The loop must validate every candidate in an isolated version, publish only after approval, generate a new exploration set after release, and preserve full rollback and replay capability.

## User Value

- Turn the existing question-generation and DeepEval evaluation chain into a repeatable improvement cycle.
- Explain why a question failed before proposing a change.
- Prevent one tenant's optimization from affecting another tenant.
- Make every change, score movement, release, and rollback auditable.
- Allow non-engineering operators to run and inspect iterations from the existing Web console.

## Confirmed Context

- The evaluated Agent is implemented with LangGraph and uses PostgreSQL checkpoint persistence in production.
- The repository already contains eval suites, eval cases, eval runs, per-case results, review items, knowledge gaps, document chunks, prompt versions, and Agent run-step traces.
- Existing DeepEval output records aggregate scores and reasons, but does not persist enough route and ranked-retrieval evidence for reliable failure attribution.
- In the reviewed `taishankaifa2` run, most failed knowledge questions were routed to `general_sales_coaching`, never invoked retrieval, and therefore had zero sources. Document or retrieval tuning alone cannot fix these cases.
- The current synthesizer generates questions directly from document contexts and favors literal, answerable questions. It does not provide sufficient coverage of conversational phrasing, cross-document reasoning, unanswerable queries, conflicts, or version-sensitive questions.
- There are no real production sales-query logs available yet. The first version will simulate realistic questions through roles and language perturbations; anonymized production logs may be added later.

## Requirements

### R1. Multi-tenant isolation

- Every iteration, question set, evaluation, trace, diagnosis, candidate, release, and checkpoint mapping must belong to a tenant.
- Agent scope must be supported within a tenant.
- A tenant candidate may only modify that tenant's router override, retrieval profile, or documents.
- Tenant experiments must not directly modify global defaults.

### R2. Dual question-bank model

- Maintain an immutable fixed regression suite across rounds.
- Generate a separate exploration suite after each release.
- A newly generated question cannot affect the score used to approve the release that generated it.
- Exploration questions may enter the fixed suite only after quality review and promotion.

### R3. Diverse synthetic questions

- Extract structured facts before generating questions; do not generate only by paraphrasing full document chunks.
- Cover factual, paraphrased/noisy, cross-document, simulated sales-scenario, unanswerable, conflict, and temporal/version questions.
- Record role, difficulty, answerability, required facts, forbidden claims, source facts, source documents, generation strategy, and generator version for each question.
- Verify unanswerable questions against the full tenant corpus.

### R4. Trace-first attribution

- Persist expected and actual route, route confidence, router type, retrieval trigger and skip reason, original and rewritten queries, per-channel retrieval candidates, ranks, scores, final context selection, document revisions, and chunk versions.
- Use evaluation metrics to detect symptoms, then use trace evidence and counterfactual probes to attribute causes.
- Distinguish at least: invalid evaluation, route miss, document missing/incorrect/conflicting, retrieval recall miss, retrieval ranking miss, context noise/chunking problem, and generation problem.
- Allow primary and secondary causes, but optimize only one primary cause in a candidate experiment.

### R5. Restricted optimization Agent

- Use a deterministic diagnosis DAG before invoking semantic Agent reasoning.
- The Agent may propose router, retrieval, or document candidates through constrained tools.
- A candidate may modify only one change category.
- The Agent cannot publish directly.
- Unsupported document facts must become human-review knowledge gaps rather than fabricated patches.
- A failure cluster may try at most three candidates by default and must stop after two consecutive non-improving attempts.

### R6. Isolated evaluation and release gates

- Build candidates as isolated, immutable knowledge/config versions.
- Run targeted, sibling, fixed, safety, and cross-tenant public regression stages.
- Block release on new critical factual errors, safety regressions, tenant leakage, or unacceptable operational cost.
- Require human approval before production publication.
- Re-run a combined manifest regression when a release contains multiple separately accepted candidates.

### R7. Versioning, replay, and rollback

- Bind every Agent run to an immutable release manifest containing knowledge, retrieval, router, prompt/model snapshot, graph definition, and code revision references.
- Store these references in LangGraph state so checkpoint replay uses the historical configuration.
- Support online release rollback, original checkpoint replay, and checkpoint fork with a candidate manifest.
- Do not delete a historical version while a release, eval run, iteration, or checkpoint references it.

### R8. PostgreSQL persistence

- Reuse and extend existing eval tables where appropriate.
- Store structured iteration state, versions, metrics, trace hits, diagnoses, candidates, approvals, releases, fact inventory, and audit events in PostgreSQL.
- Store only artifact references and hashes for large regenerable HTML/verbose outputs.
- Use idempotency keys and resumable stage state for long-running iterations.

### R9. Operator experience

- Add an Agent-scoped "Knowledge Iteration" workspace to the existing React console.
- Provide iteration overview, failure attribution, candidate diffs, eval comparison, release/version history, time travel, rollback, and next-round question review.
- Stream long-running progress through SSE.
- Provide a conversational side panel for explanation and constrained candidate requests; chat must not bypass structured approval.
- Provide CLI commands backed by the same API for CI, recovery, export, and bulk operations.

## Initial Release Gates

- The target failure cluster improves by at least 20%, or all target cases pass.
- Fixed-suite pass rate does not decrease by more than 2%.
- No new critical factual error is introduced.
- Routing accuracy, Recall@k, and MRR are evaluated independently rather than hidden by one aggregate score.
- Unanswerable-query fabrication rate does not increase.
- Safety and cross-tenant leakage checks pass completely.
- Latency and token-cost increases stay within configured limits.
- Document patches include evidence and receive human approval.

Exact thresholds must be configurable per tenant, except global safety and isolation gates.

## Acceptance Criteria

- [ ] An operator can start a tenant- and Agent-scoped iteration from the Web console or CLI.
- [ ] The iteration pins baseline release, fixed suite, exploration suite, budget, and allowed change categories.
- [ ] Evaluation traces contain enough ranked evidence to distinguish route, corpus, recall, ranking, context, and generation failures.
- [ ] Empty retrieval context is recorded as not applicable for context-dependent metrics rather than as a perfect score.
- [ ] The system clusters failures and emits evidence-backed primary causes with confidence.
- [ ] The optimization Agent can create isolated router, retrieval, or document candidates but cannot publish them.
- [ ] Each candidate changes only one category and records a hypothesis, diff, changed variables, and evaluation evidence.
- [ ] A candidate that fails any hard release gate cannot enter approval.
- [ ] An approved release atomically switches the Agent runtime binding to an immutable release manifest.
- [ ] A published release automatically creates a new exploration suite without modifying the fixed suite.
- [ ] An operator can compare releases, replay a historical LangGraph checkpoint, fork it with a candidate, and roll back production to a historical manifest.
- [ ] All operations enforce tenant ownership and produce immutable audit events.
- [ ] Interrupted iterations can resume without repeating completed stages.

## Out of Scope for the First Version

- Automatic optimization of answer-generation prompts.
- Unattended production publication.
- Question generation from real user logs.
- Automatic promotion of tenant overrides into global defaults.
- Multi-category candidate changes within one experiment.
- Replacing PostgreSQL with a separate workflow or trace platform.

## Open Questions

None blocking the technical design. Implementation sequencing and migration details will be written after this specification is reviewed.
