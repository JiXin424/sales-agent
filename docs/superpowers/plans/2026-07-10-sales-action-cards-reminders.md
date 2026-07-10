# Sales Action Cards and Proactive Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Keep commits small and do not touch unrelated files such as `tasks/plan-stream-websockets-fix.md`.

**Goal:** Add sales action cards, one-time reminders, daily digests, DingTalk proactive cards, and a read-only operations page without turning temporary tasks into long-term memory.

**Architecture:** Add a new `sales_actions` service domain with database-backed task/reminder/delivery/event records. The Online Graph detects explicit action commands and pending clarifications, while a worker scans due reminders and daily digest windows with database locks and idempotency keys. DingTalk cards provide the active user loop; API and console pages provide operational visibility.

**Tech Stack:** Python 3.10+; SQLAlchemy asyncio; Alembic; PostgreSQL `FOR UPDATE SKIP LOCKED`; Pydantic 2; LangGraph Online Graph; DingTalk card APIs; FastAPI; React + Ant Design console; pytest; pytest-asyncio; JSONL eval datasets.

## Global Constraints

- Clear user commands create tasks directly and the reply must state the concrete action and scheduled time.
- Incomplete time or incomplete action asks a clarification and creates no task.
- Agent-inferred action items require user confirmation.
- Tasks are temporary operational data and must not enter `agent_memories`, `user_memory_profiles`, or profile recall.
- Reads and writes are scoped by `tenant_id`, `agent_id`, and `user_id`; DingTalk sends are additionally scoped by `dingtalk_user_id`.
- Due reminder delivery is idempotent across worker restarts and multiple workers.
- DingTalk card button actions are idempotent.
- Daily morning digest runs at 09:00 and evening digest runs at 18:30 in `Asia/Shanghai` by default.
- No user-defined recurring tasks, external calendar integration, CRM opportunity management, admin task editing, or automatic customer messaging in v1.
- Real DingTalk business-path tests replace only public outbound delivery.
- Existing unrelated untracked or dirty files must not be staged.

---

## File Map

### Create

- `src/sales_agent/models/sales_action.py` — SQLAlchemy models for action cards, reminders, deliveries, and events.
- `src/sales_agent/migrations/versions/0016_sales_action_cards.py` — schema migration.
- `src/sales_agent/services/sales_actions/__init__.py` — domain exports.
- `src/sales_agent/services/sales_actions/contracts.py` — enums and typed Pydantic DTOs.
- `src/sales_agent/services/sales_actions/time_parser.py` — LLM time/action extraction plus deterministic validation.
- `src/sales_agent/services/sales_actions/detector.py` — command/suggestion/clarification intent detection.
- `src/sales_agent/services/sales_actions/repository.py` — scoped persistence and state transitions.
- `src/sales_agent/services/sales_actions/service.py` — orchestration for create, complete, cancel, snooze, list, confirm, and clarify.
- `src/sales_agent/services/sales_actions/card_renderer.py` — DingTalk card markdown and digest rendering.
- `src/sales_agent/services/sales_actions/scheduler.py` — due reminder and digest worker loop.
- `src/sales_agent/api/routes/sales_actions.py` — operations/test API.
- `src/sales_agent/integrations/dingtalk/sales_action_callbacks.py` — DingTalk card callback endpoint and idempotency handling.
- `eval/sales_actions/action_scenarios.jsonl` — deterministic action/reminder scenarios.
- `eval/run_sales_action_eval.py` — fixture eval runner.
- `tests/unit/sales_actions/test_time_parser.py`
- `tests/unit/sales_actions/test_detector.py`
- `tests/unit/sales_actions/test_repository_state.py`
- `tests/unit/sales_actions/test_card_renderer.py`
- `tests/unit/sales_actions/test_scheduler.py`
- `tests/unit/eval/test_sales_action_eval.py`
- `tests/integration/test_sales_action_repository.py`
- `tests/integration/test_dingtalk_sales_actions.py`
- `tests/integration/test_sales_action_scheduler.py`
- `tests/integration/test_sales_action_api.py`
- `console/src/api/salesActions.ts`
- `console/src/pages/Agents/AgentSalesActionsPage.tsx`
- `docs/runbooks/sales-actions.md`
- `scripts/run_sales_action_gate.sh`

### Modify

- `src/sales_agent/core/config.py` — add `SalesActionsConfig`.
- `src/sales_agent/models/__init__.py` — register new models.
- `src/sales_agent/main.py` — include API and DingTalk callback routers.
- `src/sales_agent/roles/worker_runner.py` — start scheduler loop when enabled.
- `src/sales_agent/graph/online/state.py` — add sales-action fields.
- `src/sales_agent/graph/online/graph.py` — add action nodes and routes.
- `src/sales_agent/graph/online/edges.py` — include action route labels.
- `src/sales_agent/graph/online/nodes.py` — implement action command, clarification, list, and post-chat suggestion nodes.
- `src/sales_agent/integrations/dingtalk/turn_result.py` — expose action result fields for tests.
- `src/sales_agent/integrations/dingtalk/processor.py` — map action result fields.
- `tests/support/dingtalk_scenario.py` — support action expectations.
- `console/src/App.tsx` — add Agent route.
- `console/src/layout/AgentLayout.tsx` or the active Agent sidebar file — add menu entry.
- `README.md` — document feature flag and gate.

### Delete

- No production module is deleted.

---

### Task 1: Add schema, settings, and model registration

**Files:**
- Create: `src/sales_agent/models/sales_action.py`
- Create: `src/sales_agent/migrations/versions/0016_sales_action_cards.py`
- Modify: `src/sales_agent/models/__init__.py`
- Modify: `src/sales_agent/core/config.py`
- Create: `tests/integration/test_sales_action_repository.py`
- Create: `tests/unit/sales_actions/test_repository_state.py`

**Interfaces:**
- Produces models: `SalesActionCard`, `SalesActionReminder`, `SalesActionDelivery`, `SalesActionEvent`.
- Produces settings: `settings.sales_actions`.

- [ ] **Step 1: Write failing schema registration test**

Append to `tests/integration/test_sales_action_repository.py`:

```python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_sales_action_tables_and_indexes_exist(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE tablename IN (
                   'sales_action_cards',
                   'sales_action_reminders',
                   'sales_action_deliveries',
                   'sales_action_events'
                 )
                """
            )
        )
    ).scalars().all()
    indexes = set(rows)
    assert "ix_sales_action_cards_scope_status" in indexes
    assert "ix_sales_action_cards_scheduled" in indexes
    assert "uq_sales_action_reminders_idempotency" in indexes
    assert "ix_sales_action_reminders_due" in indexes
    assert "ix_sales_action_deliveries_scope" in indexes
    assert "ix_sales_action_events_action" in indexes
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_sales_action_repository.py::test_sales_action_tables_and_indexes_exist
```

Expected: FAIL because the tables do not exist.

- [ ] **Step 3: Add `SalesActionsConfig`**

In `src/sales_agent/core/config.py`, add:

```python
class SalesActionsConfig(BaseModel):
    """Sales action cards and proactive reminders."""

    enabled: bool = False
    scheduler_enabled: bool = True
    scan_interval_seconds: float = 30.0
    batch_size: int = 50
    max_attempts: int = 5
    default_timezone: str = "Asia/Shanghai"
    morning_digest_time: str = "09:00"
    evening_digest_time: str = "18:30"
    default_snooze_minutes: int = 30
    expire_after_days: int = 7
    llm_confidence_threshold: float = 0.75
```

Add to `Settings`:

```python
sales_actions: SalesActionsConfig = SalesActionsConfig()
```

- [ ] **Step 4: Add SQLAlchemy models**

Create `src/sales_agent/models/sales_action.py` with:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class SalesActionCard(TimestampMixin, Base):
    __tablename__ = "sales_action_cards"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="dingtalk")
    dingtalk_user_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    topic_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    source_event_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Shanghai")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    context_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    agent_advice: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        Index("ix_sales_action_cards_scope_status", "tenant_id", "agent_id", "user_id", "status"),
        Index("ix_sales_action_cards_scheduled", "status", "scheduled_at"),
        Index("ix_sales_action_cards_source_event", "tenant_id", "source_event_id"),
    )


class SalesActionReminder(TimestampMixin, Base):
    __tablename__ = "sales_action_reminders"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    action_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminder_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="scheduled")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_sales_action_reminders_idempotency"),
        Index("ix_sales_action_reminders_due", "status", "remind_at", "next_attempt_at"),
        Index("ix_sales_action_reminders_scope", "tenant_id", "agent_id", "user_id"),
    )


class SalesActionDelivery(TimestampMixin, Base):
    __tablename__ = "sales_action_deliveries"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    action_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    reminder_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False, default="dingtalk")
    delivery_type: Mapped[str] = mapped_column(Text, nullable=False)
    dingtalk_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_instance_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sales_action_deliveries_scope", "tenant_id", "agent_id", "user_id", "status"),
    )


class SalesActionEvent(TimestampMixin, Base):
    __tablename__ = "sales_action_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    action_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_sales_action_events_action", "action_id", "created_at"),
        Index("ix_sales_action_events_scope", "tenant_id", "agent_id", "user_id", "event_type"),
    )
```

- [ ] **Step 5: Register models and migration**

Register all four models in `src/sales_agent/models/__init__.py`, then create `src/sales_agent/migrations/versions/0016_sales_action_cards.py` with matching `create_table`, indexes, unique constraint, and downgrade. `down_revision` must be the current head after memory operations, expected `0015_memory_eval_operations`.

- [ ] **Step 6: Run schema test**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_sales_action_repository.py::test_sales_action_tables_and_indexes_exist
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/models/sales_action.py \
  src/sales_agent/migrations/versions/0016_sales_action_cards.py \
  src/sales_agent/models/__init__.py src/sales_agent/core/config.py \
  tests/integration/test_sales_action_repository.py \
  tests/unit/sales_actions/test_repository_state.py
git commit -m "feat: add sales action schema"
```

---

### Task 2: Add contracts, time parsing, and action detection

**Files:**
- Create: `src/sales_agent/services/sales_actions/contracts.py`
- Create: `src/sales_agent/services/sales_actions/time_parser.py`
- Create: `src/sales_agent/services/sales_actions/detector.py`
- Create: `src/sales_agent/services/sales_actions/__init__.py`
- Create: `src/sales_agent/prompts/sales_action_extractor_prompt.py`
- Create: `tests/unit/sales_actions/test_time_parser.py`
- Create: `tests/unit/sales_actions/test_detector.py`

**Interfaces:**
- Produces `SalesActionScope`, `SalesActionExtraction`, `SalesActionDecision`, `parse_sales_action_request()`, `validate_action_extraction()`, `detect_fast_action_intent()`.

- [ ] **Step 1: Write failing parser tests**

Create `tests/unit/sales_actions/test_time_parser.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sales_agent.services.sales_actions.contracts import SalesActionExtraction
from sales_agent.services.sales_actions.time_parser import validate_action_extraction


NOW = datetime(2026, 7, 10, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def extraction(**overrides):
    data = {
        "intent": "create_action",
        "explicit_create": True,
        "title": "给张总回电话",
        "customer_name": "张总",
        "action_type": "call_back",
        "time_text": "半小时后",
        "scheduled_at": "2026-07-10T15:30:00+08:00",
        "timezone": "Asia/Shanghai",
        "confidence": 0.92,
        "missing_fields": [],
        "needs_clarification": False,
        "clarification_question": None,
    }
    data.update(overrides)
    return SalesActionExtraction(**data)


def test_clear_future_time_is_accepted():
    decision = validate_action_extraction(extraction(), now=NOW)
    assert decision.action == "create"
    assert decision.scheduled_at.isoformat() == "2026-07-10T15:30:00+08:00"


def test_low_confidence_requires_clarification():
    decision = validate_action_extraction(extraction(confidence=0.5), now=NOW)
    assert decision.action == "clarify"


def test_past_time_requires_clarification():
    decision = validate_action_extraction(
        extraction(scheduled_at="2026-07-10T14:00:00+08:00"),
        now=NOW,
    )
    assert decision.action == "clarify"
    assert "过去" in decision.response_text


def test_missing_title_requires_clarification():
    decision = validate_action_extraction(extraction(title="", missing_fields=["title"]), now=NOW)
    assert decision.action == "clarify"


def test_too_fuzzy_time_requires_clarification():
    decision = validate_action_extraction(
        extraction(time_text="这两天", needs_clarification=True, clarification_question="你想具体哪天提醒？"),
        now=NOW,
    )
    assert decision.action == "clarify"
    assert "哪天" in decision.response_text
```

- [ ] **Step 2: Write failing detector tests**

Create `tests/unit/sales_actions/test_detector.py`:

```python
from sales_agent.services.sales_actions.detector import detect_fast_action_intent


def test_detect_explicit_create_phrase():
    assert detect_fast_action_intent("半小时后提醒我给张总回电话") == "create_action"


def test_detect_complete_phrase():
    assert detect_fast_action_intent("张总那个电话我打完了") == "complete_action"


def test_detect_cancel_phrase():
    assert detect_fast_action_intent("取消明天给王总发资料的提醒") == "cancel_action"


def test_detect_list_phrase():
    assert detect_fast_action_intent("我今天还有哪些任务") == "list_actions"


def test_non_action_chat_returns_none():
    assert detect_fast_action_intent("客户说价格贵怎么回") == "none"
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sales_actions/test_time_parser.py tests/unit/sales_actions/test_detector.py
```

Expected: FAIL because sales action service modules do not exist.

- [ ] **Step 4: Implement contracts**

Create contracts with closed literals:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ActionIntent = Literal["create_action", "complete_action", "cancel_action", "snooze_action", "list_actions", "suggest_action", "none"]
ActionType = Literal["call_back", "send_proposal", "follow_up_quote", "visit_prepare", "post_visit_review", "send_material", "other"]


class SalesActionScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    agent_id: str
    user_id: str
    channel: str = "dingtalk"
    dingtalk_user_id: str | None = None


class SalesActionExtraction(BaseModel):
    intent: ActionIntent
    explicit_create: bool = False
    title: str = ""
    customer_name: str | None = None
    action_type: ActionType = "other"
    time_text: str | None = None
    scheduled_at: str | None = None
    timezone: str = "Asia/Shanghai"
    confidence: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None


@dataclass(frozen=True)
class SalesActionDecision:
    action: Literal["create", "clarify", "suggest", "ignore"]
    title: str = ""
    customer_name: str | None = None
    action_type: str = "other"
    scheduled_at: datetime | None = None
    timezone: str = "Asia/Shanghai"
    response_text: str = ""
    reason_code: str = ""
```

- [ ] **Step 5: Implement deterministic detection and validation**

`detector.py` uses regex for fast routing. `time_parser.py` validates extraction. Validation must:

- reject low confidence;
- reject missing title or scheduled time for create;
- reject past time;
- preserve explicit “needs clarification” from LLM;
- return `suggest` for non-explicit actionable plans;
- format creation text as `已创建提醒：YYYY-MM-DD HH:mm，提醒你{title}。`.

- [ ] **Step 6: Add prompt and LLM parser seam**

Create `sales_action_extractor_prompt.py` with the closed JSON schema and examples. Implement `parse_sales_action_request(message, chat_model, now, timezone)` that calls `chat_model.generate()` and parses `SalesActionExtraction`; if parsing fails, return a low-confidence `none` extraction so callers degrade to ordinary chat.

- [ ] **Step 7: Run tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sales_actions/test_time_parser.py tests/unit/sales_actions/test_detector.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/services/sales_actions src/sales_agent/prompts/sales_action_extractor_prompt.py \
  tests/unit/sales_actions/test_time_parser.py tests/unit/sales_actions/test_detector.py
git commit -m "feat: add sales action understanding"
```

---

### Task 3: Implement repository state machine and orchestration service

**Files:**
- Create: `src/sales_agent/services/sales_actions/repository.py`
- Create: `src/sales_agent/services/sales_actions/service.py`
- Modify: `tests/integration/test_sales_action_repository.py`
- Modify: `tests/unit/sales_actions/test_repository_state.py`

**Interfaces:**
- Produces `SalesActionRepository` and `SalesActionService` with `create_action`, `complete_action`, `cancel_action`, `snooze_action`, `list_actions`, `claim_due_reminders`, `record_delivery_success`, `record_delivery_failure`.

- [ ] **Step 1: Write repository integration tests**

Add tests that create an action, create its one-time reminder, complete it, cancel unsent reminders, snooze it, and assert duplicate complete is a no-op:

```python
@pytest.mark.asyncio
async def test_create_complete_and_duplicate_complete_are_idempotent(db_session):
    repo = SalesActionRepository(db_session)
    scope = SalesActionScope(tenant_id="t1", agent_id="a1", user_id="u1", dingtalk_user_id="du1")
    action = await repo.create_action(
        scope,
        title="给张总回电话",
        customer_name="张总",
        action_type="call_back",
        scheduled_at=datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
        conversation_id="conv1",
        topic_id=None,
        source_event_id="evt1",
        source_kind="explicit_user",
        context_snapshot={"source": "test"},
        agent_advice="先确认对方是否方便。",
    )
    assert action.status == "pending"

    first = await repo.complete_action(scope, action.id, event_id="click1")
    second = await repo.complete_action(scope, action.id, event_id="click1")
    assert first.status == "done"
    assert second.status == "done"
    assert second.reason_code == "already_done"
```

- [ ] **Step 2: Write due reminder claim test**

```python
@pytest.mark.asyncio
async def test_claim_due_reminders_marks_sending_once(db_session):
    repo = SalesActionRepository(db_session)
    scope = SalesActionScope(tenant_id="t1", agent_id="a1", user_id="u1", dingtalk_user_id="du1")
    action = await repo.create_action(
        scope,
        title="给张总回电话",
        customer_name="张总",
        action_type="call_back",
        scheduled_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
        conversation_id="conv1",
        topic_id=None,
        source_event_id="evt1",
        source_kind="explicit_user",
        context_snapshot={"source": "test"},
        agent_advice="先确认对方是否方便。",
    )
    reminders = await repo.claim_due_reminders(now=datetime(2026, 7, 10, 15, 1, tzinfo=timezone.utc), limit=10)
    assert len(reminders) == 1
    assert reminders[0].action_id == action.id
    again = await repo.claim_due_reminders(now=datetime(2026, 7, 10, 15, 1, tzinfo=timezone.utc), limit=10)
    assert again == []
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_sales_action_repository.py tests/unit/sales_actions/test_repository_state.py
```

Expected: FAIL because repository and service do not exist.

- [ ] **Step 4: Implement repository**

Repository requirements:

- all queries include `tenant_id`, `agent_id`, and `user_id`;
- `create_action()` creates one `sales_action_cards`, one `sales_action_reminders` with idempotency key `one_time:{tenant}:{agent}:{user}:{action_id}:{scheduled_at}`;
- `complete_action()` sets terminal state and cancels scheduled/sending reminders;
- `cancel_action()` sets terminal state and cancels scheduled/sending reminders;
- `snooze_action()` creates a new reminder with `reminder_type="snooze"` and idempotency key `snooze:{action_id}:{event_id}:{new_time}`;
- `claim_due_reminders()` uses `FOR UPDATE SKIP LOCKED` and updates rows to `sending`;
- success/failure methods write delivery rows and events.

- [ ] **Step 5: Implement orchestration service**

`SalesActionService` wraps repository plus parser decisions:

- creates user-facing response text;
- merges pending clarification partials;
- resolves “张总那个任务” references by active user tasks;
- returns typed operation result with `response_kind="sales_action"`.

- [ ] **Step 6: Run state-machine tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_sales_action_repository.py tests/unit/sales_actions/test_repository_state.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/services/sales_actions/repository.py \
  src/sales_agent/services/sales_actions/service.py \
  tests/integration/test_sales_action_repository.py \
  tests/unit/sales_actions/test_repository_state.py
git commit -m "feat: add sales action state machine"
```

---

### Task 4: Integrate action commands and suggestions into the Online Graph

**Files:**
- Modify: `src/sales_agent/graph/online/state.py`
- Modify: `src/sales_agent/graph/online/graph.py`
- Modify: `src/sales_agent/graph/online/edges.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `src/sales_agent/integrations/dingtalk/turn_result.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`
- Create: `tests/integration/test_dingtalk_sales_actions.py`
- Modify: `tests/unit/graph/test_online_graph.py`

**Interfaces:**
- Produces Online state fields `sales_action_operation`, `sales_action_status`, `sales_action_id`, `sales_action_scheduled_at`, `sales_action_pending_clarification`, `suggested_sales_action`.

- [ ] **Step 1: Write graph routing tests**

Add unit tests asserting duplicate/reset/guided-flow still win, then explicit action routes to `sales_action_command`, and ordinary chat does not route there.

- [ ] **Step 2: Write DingTalk create test**

Use `handle_dingtalk_event()` with fake chat model/parser override and in-memory `reply_fn`:

```python
@pytest.mark.asyncio
async def test_dingtalk_explicit_reminder_creates_action(db_session, sample_tenant, active_agent, monkeypatch):
    replies = []
    async def reply_fn(text): replies.append(text)
    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_user_scope",
        AsyncMock(return_value=SalesActionScope(tenant_id=sample_tenant.id, agent_id=active_agent.id, user_id="u1", dingtalk_user_id="du1")),
    )
    result = await handle_dingtalk_event(
        corp_id="corp1",
        sender_staff_id="du1",
        chatbot_user_id="bot1",
        conversation_id="conv1",
        event_id="evt1",
        text="半小时后提醒我给张总回电话",
        reply_fn=reply_fn,
    )
    assert result.response_kind == "sales_action"
    assert result.sales_action_status == "created"
    assert "张总" in replies[0]
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/graph/test_online_graph.py tests/integration/test_dingtalk_sales_actions.py
```

Expected: FAIL because graph nodes are not wired.

- [ ] **Step 4: Add state and turn defaults**

Add sales-action fields to `OnlineConversationState` and `TURN_SCOPED_DEFAULTS`, including operation/status/action ID/scheduled time and suggestion metadata.

- [ ] **Step 5: Add nodes**

Add to `online/nodes.py`:

- `sales_action_command_node()` — handles create/list/complete/cancel/snooze and pending clarification completion;
- `sales_action_clarification_node()` — formats clarification response and stores pending clarification in Topic;
- `sales_action_suggestion_node()` — runs after chat and appends a confirmation prompt only when a strong actionable plan is detected.

Action command node uses runtime context:

```python
service = SalesActionService(db)
result = await service.handle_message(
    scope=scope,
    message=state["message"],
    conversation_id=state["conversation_id"],
    topic_id=state.get("current_topic_id"),
    source_event_id=state["event_id"],
    now=runtime.now,
)
```

- [ ] **Step 6: Wire graph**

Route priority:

1. duplicate;
2. reset;
3. guided flow;
4. pending sales-action clarification;
5. explicit sales-action command;
6. normal context/evidence/chat;
7. post-chat suggestion.

Do not let `suggest_action` block the normal answer.

- [ ] **Step 7: Map DingTalk result fields**

Add fields to `DingTalkTurnResult` and map them in `processor.py`:

```python
sales_action_operation
sales_action_status
sales_action_id
sales_action_scheduled_at
sales_action_reason_code
```

- [ ] **Step 8: Run tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/graph/test_online_graph.py tests/integration/test_dingtalk_sales_actions.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/sales_agent/graph/online/state.py src/sales_agent/graph/online/graph.py \
  src/sales_agent/graph/online/edges.py src/sales_agent/graph/online/nodes.py \
  src/sales_agent/integrations/dingtalk/turn_result.py \
  src/sales_agent/integrations/dingtalk/processor.py \
  tests/unit/graph/test_online_graph.py tests/integration/test_dingtalk_sales_actions.py
git commit -m "feat: route sales actions through online graph"
```

---

### Task 5: Add DingTalk card delivery, callbacks, and scheduler worker

**Files:**
- Create: `src/sales_agent/services/sales_actions/card_renderer.py`
- Create: `src/sales_agent/services/sales_actions/scheduler.py`
- Create: `src/sales_agent/integrations/dingtalk/sales_action_callbacks.py`
- Modify: `src/sales_agent/integrations/dingtalk/card_sender.py`
- Modify: `src/sales_agent/main.py`
- Modify: `src/sales_agent/roles/worker_runner.py`
- Create: `tests/unit/sales_actions/test_card_renderer.py`
- Create: `tests/unit/sales_actions/test_scheduler.py`
- Create: `tests/integration/test_sales_action_scheduler.py`

**Interfaces:**
- Produces scheduler loop `sales_action_scheduler_loop()`, one-shot `run_sales_action_scheduler_once()`, renderer functions, and callback router.

- [ ] **Step 1: Write renderer and scheduler tests**

Test due card contains title/time/customer/buttons metadata and scheduler claims only once. Test digest idempotency keys:

```python
assert build_digest_idempotency_key("morning_digest", "t1", "a1", "u1", date(2026, 7, 10)) == "morning_digest:t1:a1:u1:2026-07-10"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sales_actions/test_card_renderer.py tests/unit/sales_actions/test_scheduler.py tests/integration/test_sales_action_scheduler.py
```

Expected: FAIL because scheduler/card modules do not exist.

- [ ] **Step 3: Implement card renderer**

Renderer outputs markdown for:

- due reminder;
- morning digest;
- evening digest;
- acknowledgement.

Include compact callback payload references, not raw sensitive context.

- [ ] **Step 4: Implement scheduler one-shot**

`run_sales_action_scheduler_once(session_factory, sender_factory, now)`:

1. claim due reminders;
2. render card;
3. send via `DingTalkCardSender.send_markdown_card()`;
4. record success/failure;
5. create morning/evening digest reminders when time window matches and idempotency key does not exist.

Use bounded backoff:

```python
def compute_sales_action_backoff_seconds(attempts: int) -> int:
    return min(300, 2 ** max(1, attempts))
```

- [ ] **Step 5: Add callback route**

`sales_action_callbacks.py` handles complete/snooze/cancel. It validates DingTalk signature if the callback endpoint receives signed requests; if card callbacks arrive through an existing DingTalk event type, normalize them into this handler. All actions call `SalesActionService` and return/update cards idempotently.

- [ ] **Step 6: Start worker**

In `worker_runner.py`, start scheduler if:

```python
settings.sales_actions.enabled and settings.sales_actions.scheduler_enabled
```

Cancel task on shutdown.

- [ ] **Step 7: Run scheduler tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/sales_actions/test_card_renderer.py tests/unit/sales_actions/test_scheduler.py tests/integration/test_sales_action_scheduler.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/services/sales_actions/card_renderer.py \
  src/sales_agent/services/sales_actions/scheduler.py \
  src/sales_agent/integrations/dingtalk/sales_action_callbacks.py \
  src/sales_agent/integrations/dingtalk/card_sender.py \
  src/sales_agent/main.py src/sales_agent/roles/worker_runner.py \
  tests/unit/sales_actions/test_card_renderer.py \
  tests/unit/sales_actions/test_scheduler.py \
  tests/integration/test_sales_action_scheduler.py
git commit -m "feat: deliver sales action reminders"
```

---

### Task 6: Add API and read-only operations console page

**Files:**
- Create: `src/sales_agent/api/routes/sales_actions.py`
- Modify: `src/sales_agent/main.py`
- Create: `tests/integration/test_sales_action_api.py`
- Create: `console/src/api/salesActions.ts`
- Create: `console/src/pages/Agents/AgentSalesActionsPage.tsx`
- Modify: `console/src/App.tsx`
- Modify: active Agent sidebar/menu file, likely `console/src/layout/AgentLayout.tsx` or `console/src/layout/Sidebar.tsx`

**Interfaces:**
- Produces read/list/detail APIs for actions, reminders, deliveries; mutation endpoints for tests/internal tooling; read-only UI.

- [ ] **Step 1: Write API tests**

Test:

- list actions filter by status;
- detail includes reminders, deliveries, events;
- complete/cancel/snooze endpoints require scoped agent and update state;
- reminders and deliveries list endpoints return failed/sent records.

- [ ] **Step 2: Run API tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_sales_action_api.py
```

Expected: FAIL because route does not exist.

- [ ] **Step 3: Implement FastAPI route**

Route prefix:

```text
/api/v1/agents/{agent_id}/sales-actions
```

Endpoints:

```text
POST /
GET  /
GET  /{action_id}
POST /{action_id}/complete
POST /{action_id}/cancel
POST /{action_id}/snooze
GET  /reminders
GET  /deliveries
```

Register in `main.py`.

- [ ] **Step 4: Implement console API**

Create `console/src/api/salesActions.ts` with typed wrappers:

```typescript
export function listSalesActions(agentId: string, filters: SalesActionFilters) {
  return apiGet<SalesActionListResponse>(`/api/v1/agents/${agentId}/sales-actions`, filters);
}
```

- [ ] **Step 5: Implement read-only page**

Create table with filters:

- status;
- action type;
- scheduled date range;
- user;
- reminder status.

Detail drawer shows action, reminders, deliveries, and events. No edit buttons.

- [ ] **Step 6: Wire route and menu**

Add route:

```tsx
<Route path="sales-actions" element={<AgentSalesActionsPage />} />
```

Add menu label: `销售任务`.

- [ ] **Step 7: Run API and console checks**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_sales_action_api.py
cd console && npm test -- --run
cd console && npm run build
```

Expected: PASS. If the console has no test script, run `npm run build`.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/api/routes/sales_actions.py src/sales_agent/main.py \
  tests/integration/test_sales_action_api.py \
  console/src/api/salesActions.ts console/src/pages/Agents/AgentSalesActionsPage.tsx \
  console/src/App.tsx console/src/layout/AgentLayout.tsx console/src/layout/Sidebar.tsx
git commit -m "feat: add sales action operations view"
```

---

### Task 7: Add eval dataset, gate script, and runbook

**Files:**
- Create: `eval/sales_actions/action_scenarios.jsonl`
- Create: `eval/run_sales_action_eval.py`
- Create: `tests/unit/eval/test_sales_action_eval.py`
- Modify: `tests/support/dingtalk_scenario.py`
- Create: `scripts/run_sales_action_gate.sh`
- Create: `docs/runbooks/sales-actions.md`
- Modify: `README.md`

**Interfaces:**
- Produces deterministic fixture eval and release gate for sales action cards.

- [ ] **Step 1: Write eval unit test**

```python
from eval.run_sales_action_eval import load_scenarios, run_fixture_eval


def test_sales_action_fixture_dataset_passes_required_tags():
    report = run_fixture_eval(load_scenarios("eval/sales_actions/action_scenarios.jsonl"))
    assert report.total_scenarios >= 12
    assert report.clear_create_accuracy == 1.0
    assert report.clarification_accuracy == 1.0
    assert report.duplicate_send_violations == 0
    assert report.memory_pollution_violations == 0
    assert report.thresholds_met is True
```

- [ ] **Step 2: Create JSONL scenarios**

Include 12 scenarios:

1. clear one-time reminder creation;
2. defaulted “明天上午”;
3. fuzzy “这两天” clarification;
4. missing action clarification;
5. suggested action confirmation;
6. due card delivery;
7. complete button;
8. snooze button;
9. cancel button;
10. morning digest idempotency;
11. evening digest idempotency;
12. task not in long-term memory/profile recall.

- [ ] **Step 3: Implement eval runner**

`run_sales_action_eval.py` fixture mode validates tags, budgets, idempotency expectations, and no memory pollution. Model/live mode can be left as a CLI stub pointing to integration tests; do not claim model mode is implemented if it is not.

- [ ] **Step 4: Add gate script**

Create `scripts/run_sales_action_gate.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}":.
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-sales-action-gate}"
rc=0

python3 -m pytest -q tests/unit/sales_actions tests/unit/eval/test_sales_action_eval.py || rc=1
python3 -m pytest -q \
  tests/integration/test_sales_action_repository.py \
  tests/integration/test_dingtalk_sales_actions.py \
  tests/integration/test_sales_action_scheduler.py \
  tests/integration/test_sales_action_api.py || rc=1
python3 eval/run_sales_action_eval.py \
  --mode fixture \
  --dataset eval/sales_actions/action_scenarios.jsonl \
  --output "${OUTPUT_DIR}/fixture" || rc=1

exit "$rc"
```

- [ ] **Step 5: Add runbook**

Runbook must cover:

- enabling `sales_actions.enabled`;
- scheduler loop and due reminder diagnosis;
- SQL to inspect action/reminder/delivery/event state;
- replaying failed reminders safely;
- DingTalk card callback troubleshooting;
- rollback by disabling feature flag;
- reminder duplicate prevention checks.

- [ ] **Step 6: Run full gate**

Run:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  bash scripts/run_sales_action_gate.sh
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add eval/sales_actions/action_scenarios.jsonl eval/run_sales_action_eval.py \
  tests/unit/eval/test_sales_action_eval.py tests/support/dingtalk_scenario.py \
  scripts/run_sales_action_gate.sh docs/runbooks/sales-actions.md README.md
git commit -m "test: add sales action release gate"
```

---

## Final Verification Checklist

- [ ] `git status --short` shows only intentional sales-action files before each commit.
- [ ] No task data is written to `agent_memories` or `user_memory_profiles`.
- [ ] Clear explicit reminders create an action and one one-time reminder.
- [ ] Creation reply includes concrete task and scheduled time.
- [ ] Fuzzy time or missing action asks clarification and creates no task.
- [ ] Agent-suggested task requires confirmation before creation.
- [ ] Due reminder delivery is exactly-once under concurrent workers.
- [ ] Card complete/snooze/cancel actions are idempotent.
- [ ] Morning digest is at most once per user per local date.
- [ ] Evening digest is at most once per user per local date.
- [ ] Operations page shows pending, reminded, done, cancelled, expired, failed, deliveries, and events.
- [ ] Failed reminders are retryable and visible.
- [ ] DingTalk integration tests replace only public outbound delivery.
- [ ] `TEST_DATABASE_URL` contains `test` before integration/gate runs.
- [ ] `bash scripts/run_sales_action_gate.sh` passes against an isolated test database.
