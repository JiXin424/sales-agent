# Bounded Intent Routing and Topic Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在统一 Online Conversation Graph 中加入句内修正解析、Topic 生命周期、关系澄清闭环和三态知识策略，使模型能受约束地判断“承接/修订/切换/新话题”以及“直接回复/检索/澄清”。

**Architecture:** Guided Flow 的确定性门保持最高优先级；普通 Chat 先经过 Context Resolver 生成 `standalone_query` 和 `turn_relation`，再经过 Intent/Evidence Router 产生受 Schema 限制的任务意图与 `none/optional/required` 知识策略。Topic 与 pending clarification 持久化到 PostgreSQL，Online Graph 负责状态转换，Chat Graph 只消费已经消歧和策略校验后的输入。

**Tech Stack:** Python 3.10+、FastAPI、LangGraph 1.2+、Pydantic 2、SQLAlchemy asyncio、Alembic、PostgreSQL、pytest、pytest-asyncio。

---

## Preconditions and scope

- Base commit: `6c55ef8` or later, containing the completed Unified Guided Flow implementation.
- `src/sales_agent/migrations/versions/0010_ontology_retrieval_profile.py` currently exists as another worker's untracked work. Before execution, that worker must commit it; this plan then uses revision `0011_topic_memory` with `down_revision="0010_ontology_retrieval_profile"`.
- Do not modify or stage unrelated eval artifacts, optimizer data, `0010_ontology_retrieval_profile.py`, or the other worker's untracked files.
- Long-term user Memory, Daily Evaluation redesign, and open-ended tool selection remain out of scope.

## File map

### Create

- `src/sales_agent/models/conversation_topic.py` — Topic ORM model and lifecycle fields.
- `src/sales_agent/migrations/versions/0011_topic_memory.py` — Topic table and message `topic_id` migration.
- `src/sales_agent/services/structured_router_output.py` — validated JSON extraction shared by both routers.
- `src/sales_agent/services/context_resolver.py` — discourse correction, relation and standalone-query resolver.
- `src/sales_agent/services/topic_manager.py` — active/expired/restorable Topic and clarification transitions.
- `src/sales_agent/services/evidence_router.py` — intent and three-state knowledge policy.
- `src/sales_agent/prompts/context_resolver_prompt.py` — bounded Context Resolver prompt.
- `src/sales_agent/prompts/clarification_resolver_prompt.py` — pending clarification resolver prompt.
- `src/sales_agent/prompts/evidence_router_prompt.py` — bounded evidence routing prompt.
- `src/sales_agent/graph/nodes/context_resolution.py` — Online Graph Context Resolver node.
- `src/sales_agent/graph/nodes/evidence_routing.py` — Online Graph Evidence Router node.
- `src/sales_agent/graph/nodes/evidence_gate.py` — required-evidence enforcement before generation.
- `tests/unit/test_structured_router_output.py`
- `tests/unit/test_context_resolver.py`
- `tests/unit/test_topic_manager.py`
- `tests/unit/test_evidence_router.py`
- `tests/unit/graph/test_context_routing_nodes.py`
- `tests/integration/test_topic_memory_flow.py`
- `eval/router/turn_relation_cases.jsonl`
- `eval/router/evidence_policy_cases.jsonl`
- `eval/router/run_router_eval.py`

### Modify

- `src/sales_agent/models/conversation.py`、`src/sales_agent/models/__init__.py`
- `src/sales_agent/graph/online_state.py`、`src/sales_agent/graph/online_graph.py`
- `src/sales_agent/graph/state.py`、`src/sales_agent/graph/chat_graph.py`
- `src/sales_agent/graph/nodes/context_load.py`、`routing.py`、`retrieval.py`
- `src/sales_agent/graph/edges/path_conditions.py`
- `src/sales_agent/services/task_router.py`、`conversation_logger.py`、`online_conversation.py`
- `src/sales_agent/services/prompt_defaults.py`
- `tests/unit/graph/test_online_graph.py`、`tests/unit/test_task_router.py`
- `README.md`、`changelog/2026-07-06.md`

---

### Task 1: Establish baseline and add Topic persistence

**Files:**
- Create: `src/sales_agent/models/conversation_topic.py`
- Create: `src/sales_agent/migrations/versions/0011_topic_memory.py`
- Modify: `src/sales_agent/models/conversation.py:47-73`
- Modify: `src/sales_agent/models/__init__.py:28-33,90-160`
- Test: `tests/unit/test_topic_model.py`

- [ ] **Step 1: Verify the migration prerequisite and test baseline**

Run:

```bash
git ls-files --error-unmatch src/sales_agent/migrations/versions/0010_ontology_retrieval_profile.py
pytest -q tests/unit/graph tests/unit/test_task_router.py tests/integration/test_online_guided_flows.py
```

Expected: `0010_ontology_retrieval_profile.py` is tracked; record the exact existing pass/fail count before changes. Stop if the migration is still untracked.

- [ ] **Step 2: Write failing Topic model tests**

Create `tests/unit/test_topic_model.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from sales_agent.models.conversation_topic import ConversationTopic


@pytest.mark.asyncio
async def test_topic_round_trip(db_session, active_agent):
    now = datetime.now(timezone.utc)
    topic = ConversationTopic(
        id="topic-1",
        tenant_id=active_agent.tenant_id,
        agent_id=active_agent.id,
        user_id="u1",
        channel="dingtalk",
        conversation_id="c1",
        status="active",
        summary="讨论福多多产品",
        key_entities_json='["福多多"]',
        current_goal="查询产品",
        last_active_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(topic)
    await db_session.flush()
    loaded = await db_session.scalar(select(ConversationTopic).where(ConversationTopic.id == "topic-1"))
    assert loaded is not None
    assert loaded.current_goal == "查询产品"
    assert loaded.status == "active"
```

- [ ] **Step 3: Run the model test to verify it fails**

Run:

```bash
pytest -q tests/unit/test_topic_model.py
```

Expected: collection FAIL because `conversation_topic.py` does not exist.

- [ ] **Step 4: Add the Topic model**

Create `src/sales_agent/models/conversation_topic.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class ConversationTopic(TimestampMixin, Base):
    __tablename__ = "conversation_topics"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    parent_topic_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_entities_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    current_goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active_constraints_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    retracted_goals_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pending_clarification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_conversation_topic_active_scope",
            "tenant_id", "agent_id", "user_id", "channel",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
```

- [ ] **Step 5: Add `topic_id` to messages and register the model**

Add to `ConversationMessage`:

```python
topic_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
```

Import/export `ConversationTopic` in `models/__init__.py`.

- [ ] **Step 6: Create the Alembic migration**

Create `0011_topic_memory.py` with revision `0011_topic_memory`, down revision `0010_ontology_retrieval_profile`, all Topic columns above, the partial unique index, and nullable/indexed `conversation_messages.topic_id`. Downgrade drops `topic_id` before `conversation_topics`.

- [ ] **Step 7: Run model and migration checks**

Run:

```bash
pytest -q tests/unit/test_topic_model.py
python -m compileall -q src/sales_agent/models src/sales_agent/migrations/versions/0011_topic_memory.py
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" alembic downgrade 0010_ontology_retrieval_profile
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
```

Expected: test passes, compilation exits 0, and upgrade/downgrade/upgrade completes without errors against the development database.

- [ ] **Step 8: Commit Topic persistence**

```bash
git add src/sales_agent/models/conversation_topic.py src/sales_agent/models/conversation.py src/sales_agent/models/__init__.py src/sales_agent/migrations/versions/0011_topic_memory.py tests/unit/test_topic_model.py
git commit -m "feat: add conversation topic persistence"
```

---

### Task 2: Define validated router contracts and JSON parsing

**Files:**
- Create: `src/sales_agent/services/structured_router_output.py`
- Test: `tests/unit/test_structured_router_output.py`

- [ ] **Step 1: Write failing contract tests**

```python
import pytest
from pydantic import ValidationError

from sales_agent.services.structured_router_output import (
    ClarificationDecision,
    ContextDecision,
    EvidenceDecision,
    parse_model_json,
)


def test_parse_fenced_json():
    result = parse_model_json('```json\n{"turn_relation":"new","standalone_query":"你好","confidence":0.9,"reason_code":"new_topic"}\n```', ContextDecision)
    assert result.turn_relation == "new"


def test_unknown_relation_is_rejected():
    with pytest.raises(ValidationError):
        ContextDecision(turn_relation="maybe", standalone_query="x", confidence=0.5, reason_code="x")


def test_required_policy_requires_retrieval_query():
    with pytest.raises(ValueError):
        EvidenceDecision(intent="knowledge_qa", response_mode="retrieve", knowledge_policy="required", knowledge_scope=["product"], retrieval_query=None, confidence=0.9, reason_code="fact")


def test_clarification_enum_is_closed():
    decision = ClarificationDecision(resolution="continue", supplemental_message="重点看价格", confidence=0.9)
    assert decision.resolution == "continue"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/unit/test_structured_router_output.py`

Expected: import FAIL.

- [ ] **Step 3: Implement Pydantic contracts**

Define `ContextDecision`, `ClarificationDecision`, and `EvidenceDecision` with `Literal` enums exactly matching the design. Use `Field(ge=0, le=1)` for confidence, default empty lists for retained/retracted/missing/scope, and a model validator that requires `retrieval_query` when `knowledge_policy="required"`.

Implement:

```python
T = TypeVar("T", bound=BaseModel)


def parse_model_json(raw: str, schema: type[T]) -> T:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = repair_json(text, return_objects=True)
    if not isinstance(payload, dict):
        raise ValueError("router output must be a JSON object")
    return schema.model_validate(payload)
```

- [ ] **Step 4: Run contract tests**

Run: `pytest -q tests/unit/test_structured_router_output.py`

Expected: all tests pass.

- [ ] **Step 5: Commit contracts**

```bash
git add src/sales_agent/services/structured_router_output.py tests/unit/test_structured_router_output.py
git commit -m "feat: define bounded router output contracts"
```

---

### Task 3: Implement Context Resolver and within-turn correction

**Files:**
- Create: `src/sales_agent/prompts/context_resolver_prompt.py`
- Create: `src/sales_agent/services/context_resolver.py`
- Test: `tests/unit/test_context_resolver.py`

- [ ] **Step 1: Write failing Context Resolver tests**

Cover these exact cases with a stub model returning structured JSON:

```python
@pytest.mark.asyncio
async def test_within_turn_revision_retains_entity():
    model = StubModel({
        "turn_relation": "new",
        "standalone_query": "查询福多多的主要竞品及对比资料",
        "retained_entities": ["福多多"],
        "retracted_goals": ["查询福多多产品"],
        "missing_references": [],
        "confidence": 0.95,
        "reason_code": "within_turn_correction",
    })
    result = await resolve_context(
        message="帮我找一下福多多的产品，算了，还是找一下竞品吧。",
        topic=None,
        recent_messages=[],
        chat_model=model,
    )
    assert result.standalone_query == "查询福多多的主要竞品及对比资料"
    assert result.retained_entities == ["福多多"]
    assert result.retracted_goals == ["查询福多多产品"]
```

Also test pronoun continuation, explicit revision, unrelated new topic, ambiguous missing reference, malformed output retry once, and second failure returning a deterministic ambiguous decision rather than concatenating all history.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/unit/test_context_resolver.py`

Expected: import FAIL.

- [ ] **Step 3: Add the bounded prompt**

The prompt must enumerate only five relations, demand a standalone query, show the “福多多产品 → 竞品” correction example, and explicitly state that replacing an action does not remove entities still referenced by the new action. Limit history to formatted current-topic messages passed by the caller.

- [ ] **Step 4: Implement `resolve_context`**

Use signature:

```python
async def resolve_context(
    *,
    message: str,
    topic: ConversationTopic | None,
    recent_messages: list[dict[str, str]],
    chat_model,
) -> ContextDecision:
```

Call the model at temperature 0 with max 500 tokens. Parse via `parse_model_json`; retry once with an appended schema-error message. On two failures use deterministic fallback: explicit continuation markers produce `continue` only when a topic exists, explicit new-topic markers produce `new`, otherwise return `ambiguous` with empty standalone query and `reason_code="resolver_failure"`.

- [ ] **Step 5: Run Context Resolver tests**

Run: `pytest -q tests/unit/test_context_resolver.py`

Expected: all correction, relation and failure tests pass.

- [ ] **Step 6: Commit Context Resolver**

```bash
git add src/sales_agent/prompts/context_resolver_prompt.py src/sales_agent/services/context_resolver.py tests/unit/test_context_resolver.py
git commit -m "feat: resolve discourse and topic relation"
```

---

### Task 4: Implement Topic Manager, expiry, restore, and clarification loop

**Files:**
- Create: `src/sales_agent/prompts/clarification_resolver_prompt.py`
- Create: `src/sales_agent/services/topic_manager.py`
- Test: `tests/unit/test_topic_manager.py`

- [ ] **Step 1: Write failing Topic lifecycle tests**

Use `db_session`, fixed UTC timestamps and real Topic rows. Test:

- active topic continues at 29 minutes;
- active topic closes at 30 minutes;
- ordinary message after expiry creates `new`;
- explicit “继续刚才” restores a unique topic closed less than 24 hours ago;
- multiple restorable topics produces pending clarification;
- topic older than 24 hours cannot restore;
- `switch` creates child Topic and carries only selected entities;
- `revise` keeps Topic ID and records retracted goals.

- [ ] **Step 2: Write failing clarification-loop tests**

Assert pending JSON retains original message and candidate query; `继续，不过重点看价格` resolves to continue with supplemental text; `新问题，我想问东方福利网` resolves new; a complete replacement message produces replace; second ambiguous answer defaults new; duplicate event ID resolves pending only once.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest -q tests/unit/test_topic_manager.py`

Expected: import FAIL.

- [ ] **Step 4: Implement Topic repository and lifecycle constants**

In `topic_manager.py` define:

```python
TOPIC_IDLE_TIMEOUT = timedelta(minutes=30)
TOPIC_RESTORE_WINDOW = timedelta(hours=24)
CLARIFICATION_TIMEOUT = timedelta(minutes=30)
MAX_CLARIFICATION_ATTEMPTS = 2
```

Implement `get_active_topic`, `close_if_expired`, `find_restorable_topics`, `create_topic`, `apply_context_decision`, `set_pending_clarification`, `cancel_pending`, and `resolve_pending`. All queries filter tenant, Agent, user and channel. Topic mutation and pending mutation use the caller's transaction; do not call `commit()` inside the service.

- [ ] **Step 5: Implement Clarification Resolver**

Use a structured prompt and `ClarificationDecision`. Exact commands map without LLM: `继续/接着刚才` → continue, `新问题/换个话题` → new, `取消/算了` → cancel. Other replies use the model. A full new question may return `replace`. Two ambiguous attempts return `new` and clear old-context carry-over.

- [ ] **Step 6: Run Topic tests**

Run: `pytest -q tests/unit/test_topic_manager.py`

Expected: all lifecycle, restore, clarification, transaction and idempotency tests pass.

- [ ] **Step 7: Commit Topic Manager**

```bash
git add src/sales_agent/prompts/clarification_resolver_prompt.py src/sales_agent/services/topic_manager.py tests/unit/test_topic_manager.py
git commit -m "feat: manage topic lifecycle and clarification"
```

---

### Task 5: Implement Intent/Evidence Router and policy guard

**Files:**
- Create: `src/sales_agent/prompts/evidence_router_prompt.py`
- Create: `src/sales_agent/services/evidence_router.py`
- Modify: `src/sales_agent/services/task_router.py:58-74,77-87,270-394`
- Modify: `src/sales_agent/services/prompt_defaults.py`
- Test: `tests/unit/test_evidence_router.py`
- Modify test: `tests/unit/test_task_router.py`

- [ ] **Step 1: Write failing Evidence Router tests**

Parameterized cases must include:

```text
你好                         -> general_sales_coaching / none / direct
我有点焦虑                   -> emotional_support / none / direct
福多多有哪些产品             -> knowledge_qa / required / retrieve
福多多多少钱                 -> knowledge_qa / required / retrieve
帮我写一句提醒客户回复的话     -> script_generation / none / direct
结合福多多零风险承诺写话术     -> script_generation / required / retrieve
客户一直不回复，我该怎么办     -> follow_up_planning / optional / retrieve
```

Also test invalid enum retry, company-fact guard upgrading `none` to `required`, optional retrieval failure semantics, and router failure on a company fact falling back to required.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/unit/test_evidence_router.py`

Expected: import FAIL.

- [ ] **Step 3: Implement the bounded prompt and service**

Expose:

```python
async def route_intent_evidence(
    *,
    standalone_query: str,
    chat_model,
    stable_identity: dict[str, str] | None = None,
) -> EvidenceDecision:
```

The prompt lists the existing 12 task types, three policies, allowed scopes, and contrasting script-generation examples. Parse/retry like Context Resolver.

- [ ] **Step 4: Implement local policy guard**

Add `apply_evidence_policy_guard(query, decision)`. Product, company, price, policy, case, competitor, contract and delivery fact signals force `required/retrieve`. Greetings, emotional acknowledgements and control replies force `none/direct`. If required query is absent, use `standalone_query` as `retrieval_query`. Do not expose arbitrary node or tool names.

- [ ] **Step 5: Remove unconditional retrieval from the legacy router**

Delete the unconditional `llm_result.needs_retrieval = True`. Preserve `RouteResult.needs_retrieval` for compatibility but derive it from `knowledge_policy != "none"`. Add `knowledge_policy`, `knowledge_scope`, and `retrieval_query` fields to `RouteResult`. Rules-only fallback must pass through the same policy guard.

- [ ] **Step 6: Register prompts and run router tests**

Run:

```bash
pytest -q tests/unit/test_evidence_router.py tests/unit/test_task_router.py tests/unit/test_task_router_prompt_injection.py
```

Expected: all tests pass; old assertions expecting every task to retrieve are replaced with policy-specific assertions.

- [ ] **Step 7: Commit Evidence Router**

```bash
git add src/sales_agent/prompts/evidence_router_prompt.py src/sales_agent/services/evidence_router.py src/sales_agent/services/task_router.py src/sales_agent/services/prompt_defaults.py tests/unit/test_evidence_router.py tests/unit/test_task_router.py
git commit -m "feat: route intent with bounded evidence policy"
```

---

### Task 6: Integrate Context, clarification, and evidence routing into Online Graph

**Files:**
- Modify: `src/sales_agent/graph/online_state.py:21-46`
- Create: `src/sales_agent/graph/nodes/context_resolution.py`
- Create: `src/sales_agent/graph/nodes/evidence_routing.py`
- Modify: `src/sales_agent/graph/online_graph.py:83-271`
- Modify: `src/sales_agent/services/online_conversation.py:123-232`
- Test: `tests/unit/graph/test_context_routing_nodes.py`
- Modify test: `tests/unit/graph/test_online_graph.py`

- [ ] **Step 1: Write failing graph-routing tests**

Assert Graph order and outcomes:

- Guided Flow trigger bypasses Topic routing.
- Chat without pending executes Context Resolver then Evidence Router then Chat.
- Ambiguous relation writes pending and returns a clarification question without invoking Chat.
- A following `继续` restores the original message, not the literal word “继续”.
- A following `新问题` reruns the original message without old Topic context.
- A complete replacement clears pending and routes replacement text.
- Duplicate event ID cannot resolve pending twice.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/unit/graph/test_context_routing_nodes.py tests/unit/graph/test_online_graph.py`

Expected: new tests fail because Online Graph routes Chat directly.

- [ ] **Step 3: Extend Online state**

Add `topic_id`, `previous_topic_id`, `turn_relation`, `standalone_query`, retained/retracted lists, `pending_clarification_id`, `clarification_state`, `task_type`, `route_confidence`, `knowledge_policy`, `knowledge_scope`, `retrieval_query`, `needs_retrieval`, `original_message`, and `route_trace`.

- [ ] **Step 4: Add Context and Evidence nodes**

`context_resolution_node` loads/expirs/restores Topic via Topic Manager, resolves pending first, calls Context Resolver, persists ambiguous state, and returns either `context_status="clarify"` with an answer or `context_status="resolved"` with a Topic and standalone query.

`evidence_routing_node` calls Evidence Router only for resolved context and maps the validated decision to state.

- [ ] **Step 5: Rewire Online Graph**

Keep current normalize priority. Replace only the `chat` branch:

```text
normalize_turn
  -> context_resolution
       -> clarification_response -> log_control_response -> END
       -> evidence_routing -> chat -> END
```

Guided Flow, duplicate and cancel paths remain unchanged. `chat_node` passes `standalone_query` as the Chat message and preserves `original_message` for logging.

- [ ] **Step 6: Pass time and resolver seams through runtime context**

`invoke_online_turn` adds `now` and optional resolver overrides to runtime context for deterministic tests. Production uses resolved tenant models. The Online date-scoped checkpointer remains for Guided Flow state; Topic correctness relies on PostgreSQL rather than the date key.

- [ ] **Step 7: Run Online Graph tests**

Run:

```bash
pytest -q tests/unit/graph/test_context_routing_nodes.py tests/unit/graph/test_online_graph.py tests/integration/test_online_guided_flows.py
```

Expected: Topic routing tests and existing Guided Flow regressions pass.

- [ ] **Step 8: Commit Online integration**

```bash
git add src/sales_agent/graph/online_state.py src/sales_agent/graph/nodes/context_resolution.py src/sales_agent/graph/nodes/evidence_routing.py src/sales_agent/graph/online_graph.py src/sales_agent/services/online_conversation.py tests/unit/graph/test_context_routing_nodes.py tests/unit/graph/test_online_graph.py
git commit -m "feat: integrate topic-aware bounded routing"
```

---

### Task 7: Make Chat Graph consume Topic context and enforce evidence

**Files:**
- Modify: `src/sales_agent/graph/state.py:27-106`
- Modify: `src/sales_agent/graph/nodes/context_load.py:20-55`
- Modify: `src/sales_agent/graph/nodes/routing.py:18-58`
- Create: `src/sales_agent/graph/nodes/evidence_gate.py`
- Modify: `src/sales_agent/graph/chat_graph.py:87-155`
- Modify: `src/sales_agent/graph/edges/path_conditions.py:36-83`
- Modify: `src/sales_agent/services/conversation_logger.py:17-162`
- Test: `tests/unit/graph/test_evidence_gate.py`
- Test: `tests/integration/test_topic_memory_flow.py`

- [ ] **Step 1: Write failing Topic context and evidence-gate tests**

Test that `context_load_node` with `topic_id` reads only messages from that Topic; without topic ID it preserves backward-compatible conversation loading. Test required+no sources returns an evidence-insufficient answer and skips generation; optional+no sources continues; none never calls Retriever.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/unit/graph/test_evidence_gate.py tests/integration/test_topic_memory_flow.py`

Expected: failures because state/logging/messages do not carry Topic metadata and no evidence gate exists.

- [ ] **Step 3: Extend Chat state and bypass duplicate routing work**

Add Topic and precomputed route fields to `ChatGraphState`. In `routing_node`, if `precomputed_route=True`, return existing task/policy fields without calling `route_task`; otherwise retain direct ChatGraph compatibility.

- [ ] **Step 4: Filter short-term history by Topic**

When state has `topic_id`, add `ConversationMessage.topic_id == state["topic_id"]` to the query. Keep the same 4–8-turn limit and chronological reversal. Do not load messages from sibling or parent Topics.

- [ ] **Step 5: Persist `topic_id` on user and assistant messages**

Add optional `topic_id` to `log_conversation` and `log_message` signatures and set it on both rows. Update all call sites with keyword arguments so existing callers remain source compatible.

- [ ] **Step 6: Add evidence gate and edges**

Insert `evidence_gate` between retrieve and generate. For `required` with no accepted sources, set:

```python
{
    "skip_generation": True,
    "answer_dict": {
        "summary": "当前知识库中没有找到足够依据，我不能确认这项企业信息。",
        "sections": [],
    },
    "path_reason": "required_evidence_missing",
}
```

For optional or sources present, return no blocking update. `select_retrieval_path` skips retrieval only for policy `none`.

- [ ] **Step 7: Update Topic summary after successful turns**

After message logging, update Topic summary, key entities, current goal and `last_active_at/expires_at`. Use Context Resolver output rather than asking the generation model to infer hidden relation state. Cap summary at 500 characters.

- [ ] **Step 8: Run Chat and integration regressions**

Run:

```bash
pytest -q tests/unit/graph tests/unit/test_task_router.py tests/integration/test_graph_pipeline_parity.py tests/integration/test_topic_memory_flow.py tests/integration/test_online_guided_flows.py
```

Expected: zero failures.

- [ ] **Step 9: Commit Topic-aware Chat**

```bash
git add src/sales_agent/graph src/sales_agent/services/conversation_logger.py tests/unit/graph tests/integration/test_topic_memory_flow.py
git commit -m "feat: enforce topic context and evidence gates"
```

---

### Task 8: Build router evaluation datasets and acceptance metrics

**Files:**
- Create: `eval/router/turn_relation_cases.jsonl`
- Create: `eval/router/evidence_policy_cases.jsonl`
- Create: `eval/router/run_router_eval.py`
- Test: `tests/unit/test_router_eval.py`

- [ ] **Step 1: Create deterministic evaluation fixtures**

Add at least 100 turn-relation cases balanced across continue/revise/switch/new/ambiguous, including within-turn corrections, pronouns, omitted subjects, 30-minute expiry, 24-hour restore and clarification triples. Add at least 60 evidence cases balanced across none/optional/required and all high-risk enterprise-fact categories. Every JSONL row includes ID, input, context, expected structured fields and rationale.

- [ ] **Step 2: Write failing metric tests**

Test confusion-matrix counts, macro accuracy, required false-negative rate, Topic leakage rate, and clarification completion rate using a small fixed fixture where exact expected values are known.

- [ ] **Step 3: Implement the eval runner**

The runner loads JSONL, invokes injectable resolver functions, validates schemas, calculates per-class accuracy, overall accuracy, required false-negative rate, unnecessary retrieval rate and P50/P95 latency, then writes JSON and Markdown reports. It exits nonzero when thresholds fail.

- [ ] **Step 4: Run eval unit tests and offline fixture mode**

Run:

```bash
pytest -q tests/unit/test_router_eval.py
python eval/router/run_router_eval.py --fixture-mode
```

Expected: tests pass and fixture report exits 0.

- [ ] **Step 5: Run model-backed evaluation**

Run:

```bash
python eval/router/run_router_eval.py --output eval/results/router
```

Expected gates: turn-relation accuracy ≥90%, required false-negative rate <2%, Topic leakage <1%, hard-rule and clarification state-machine tests 100%.

- [ ] **Step 6: Commit eval assets**

```bash
git add eval/router tests/unit/test_router_eval.py
git commit -m "test: add bounded router evaluation suite"
```

---

### Task 9: Full verification, rollout switch, and documentation

**Files:**
- Modify: `src/sales_agent/core/config.py`
- Modify: `config/default.yaml`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `changelog/2026-07-06.md`

- [ ] **Step 1: Add a rollout switch**

Add `topic_routing.enabled` default false, `idle_minutes=30`, `restore_hours=24`, and `max_clarification_attempts=2` with `TOPIC_ROUTING_ENABLED` environment override. When disabled, Online Graph follows the current Chat branch unchanged.

- [ ] **Step 2: Test the rollout switch**

Assert disabled mode never creates Topic rows and preserves current Chat behavior; enabled mode creates Topic and follows Context/Evidence routing.

- [ ] **Step 3: Run static and migration verification**

Run:

```bash
python -m compileall -q src/sales_agent eval/router
git diff --check
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
DATABASE_URL="$TEST_DATABASE_URL" alembic downgrade 0010_ontology_retrieval_profile
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
```

Expected: all commands exit 0.

- [ ] **Step 4: Run focused suites**

Run:

```bash
pytest -q \
  tests/unit/test_structured_router_output.py \
  tests/unit/test_context_resolver.py \
  tests/unit/test_topic_manager.py \
  tests/unit/test_evidence_router.py \
  tests/unit/test_task_router.py \
  tests/unit/graph \
  tests/integration/test_topic_memory_flow.py \
  tests/integration/test_online_guided_flows.py \
  tests/integration/test_graph_pipeline_parity.py
```

Expected: zero failures.

- [ ] **Step 5: Run the full non-live suite**

Run:

```bash
pytest -q --ignore=tests/integration/test_ontology_neo4j_live.py
```

Expected: zero new failures relative to Task 1 baseline.

- [ ] **Step 6: Run sanitized end-to-end scenarios**

Verify through HTTP and DingTalk test instances: correction retaining entity, pronoun continuation, unrelated new Topic, ambiguous relation then continue, ambiguous relation then new, second ambiguous default new, 31-minute expiry, explicit restore within 24 hours, multiple restore candidates, required evidence missing, simple reply without retrieval, and Guided Flow priority. Record only sanitized traces and reason codes.

- [ ] **Step 7: Document operation and rollback**

README documents Topic semantics, 30-minute expiry, 24-hour explicit restore, router schemas, evidence policies and `TOPIC_ROUTING_ENABLED=false` rollback. Changelog records migration, eval metrics and known limitations. State explicitly that long-term user Memory remains out of scope.

- [ ] **Step 8: Review acceptance criteria and commit**

Compare evidence against `docs/superpowers/specs/2026-07-06-bounded-intent-routing-memory-design.md:500-513`. Then run:

```bash
git add src/sales_agent/core/config.py config/default.yaml .env.example README.md changelog/2026-07-06.md
git commit -m "docs: document bounded routing rollout"
```

Do not declare completion if any acceptance criterion lacks a passing test, model-backed eval, or sanitized smoke result.

---

## Follow-up work explicitly excluded

Create a separate design for long-term user Memory covering write authorization, provenance, TTL, user inspection/deletion, contradiction resolution and selective retrieval. Do not extend this plan by silently injecting inferred user traits or unrelated historical conversations into Context Resolver.
