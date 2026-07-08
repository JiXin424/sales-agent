# Sales Agent Memory Program Overview

**Status:** Approved design, split into four sequential delivery specs
**Date:** 2026-07-08
**Target:** Production DingTalk Sales Agent
**Delivery window:** One month

## 1. Objective

Turn the current Topic-based conversation handling into a production-grade memory system that:

- preserves an active conversation safely across process restarts and workers;
- distinguishes follow-ups, revisions, switches, new topics, and ambiguous references;
- stores governed cross-session memories instead of replaying arbitrary chat history;
- projects explainable and correctable sales-user profiles from those memories;
- is testable through the same DingTalk business path used by real users;
- continuously converts production failures into regression cases.

The program is optimized for an Agent application/development engineering portfolio: it demonstrates durable LangGraph state, memory governance, personalization, multi-turn evaluation, tenant isolation, reliability, and production feedback loops.

## 2. Current State and Gaps

The project already has `ConversationTopic`, `topic_id`-scoped message history, a five-class `turn_relation`, a 30-minute idle timeout, a 24-hour restore window, clarification state, Guided Flows, and substantial unit coverage.

The remaining gaps are:

1. The Online Graph uses a process-local `InMemorySaver` in production. Its state does not survive restart or worker changes.
2. Online thread IDs contain the calendar date, so midnight can split a still-valid 30-minute topic or Guided Flow.
3. The LangGraph Store interface is not used for cross-session memories.
4. User profiles do not exist as governed, evidence-backed projections.
5. Most memory tests target services or mocked nodes rather than consecutive turns through `handle_dingtalk_event()`.
6. The model-backed router evaluation invokes asynchronous production resolvers through a synchronous contract and does not execute the real runtime inputs.
7. The reported clarification completion rate measures ambiguous detection, not whether the next user answer resolves the clarification correctly.

## 3. Approved Architecture

Memory is split into three layers with separate responsibilities:

### 3.1 Short-term Topic memory

Answers “what is the user doing now?” It contains the active goal, retained entities, retracted goals, clarification state, current Guided Flow, and recent topic-scoped messages. `ConversationTopic` is the semantic source of truth. PostgreSQL LangGraph checkpoints provide execution recovery.

### 3.2 Long-term atomic memory

Answers “what remains useful in a later session?” Each memory stores one user fact, preference, coaching goal, or confirmed sales pattern with scope, source evidence, lifecycle, sensitivity, and expiry metadata. The model may propose a memory; deterministic policy decides whether it is persisted.

### 3.3 User profile projection

Answers “what is the current explainable view of this sales user?” A profile is a materialized projection over active atomic memories, never an independent source of truth. Every field points back to evidence memories and can be corrected, rebuilt, or deleted.

## 4. Shared Design Rules

1. All production conversations enter through the DingTalk business processor.
2. All state and memory reads are scoped by `tenant_id`, `agent_id`, and `user_id`; channel and subject scope are added where relevant.
3. Assistant-generated text is never learned as a user fact.
4. Explicit user statements and verified tool data are the only evidence sources in this phase.
5. Long-term memory cannot override system rules, risk controls, tenant knowledge, or retrieved product facts.
6. A new Topic may reuse user preferences, but must not inherit the prior Topic’s customer facts or temporary goals.
7. Corrections create a new version and supersede the old memory. They do not silently mutate history.
8. Forget operations exclude the target memory from all future retrievals and trigger profile rebuild.
9. Long-term-memory failure degrades personalization without blocking the ordinary answer. Checkpoint failure must not silently fall back to process-local state in production.
10. No raw production conversation, API key, sensitive personal data, or full production profile is committed to evaluation datasets.

## 5. Sequential Specs

| Order | Spec | Outcome | Dependency |
|---|---|---|---|
| 1 | Durable Short-Term Memory and Real Multi-Turn Flow | Topics and Guided Flows survive restart/worker changes; real DingTalk multi-turn harness exists | Current Online Graph |
| 2 | Governed Long-Term Atomic Memory | Users can explicitly remember, correct, and forget safe cross-session facts | Spec 1 |
| 3 | User Profile Projection and Memory Recall | Relevant profile memories personalize later sessions with provenance | Specs 1–2 |
| 4 | Memory Evaluation and Production Operations | Offline, release, and online quality gates continuously validate the system | Specs 1–3 |

Each spec receives its own implementation plan, branch/worktree, review loop, release evidence, and acceptance decision. A later spec does not start until the prior spec’s acceptance criteria pass.

## 6. One-Month Delivery Sequence

- **Week 1:** Durable Online Graph checkpoint, stable thread scope, explicit turn reset, corrected router evaluation, real DingTalk multi-turn harness.
- **Week 2:** Atomic memory schema, repository, policy engine, explicit remember/correct/forget, outbox retries.
- **Week 3:** Versioned user profile projection, task-aware recall gate, bounded prompt injection, “what do you remember about me?” transparency.
- **Week 4:** Versioned multi-turn datasets, model regressions, staging DingTalk release gate, online sampling, dashboards, runbooks, and demo evidence.

## 7. Explicitly Deferred

- customer and organization profiles;
- CRM, DingTalk task, or calendar actions;
- voice and meeting transcription;
- multi-agent orchestration;
- autonomous prompt optimization driven by unreviewed production conversations;
- a full visual profile administration console.

## 8. Program Completion

The program is complete only when:

- the six approved real-user scenarios pass repeatedly through DingTalk;
- restart and worker-switch recovery are deterministic;
- cross-tenant, cross-user, and cross-topic memory leakage remain zero;
- explicit remember, correction, and forget operations are explainable and reversible;
- profile fields are evidence-backed and rebuildable;
- model, prompt, and code changes are compared against versioned multi-turn baselines;
- production failures can be traced, classified, anonymized, and promoted into regression datasets.
