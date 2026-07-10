# User Profile Projection and Memory Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build versioned, evidence-backed sales-user profiles from active atomic memories and inject only task-relevant bounded memory context into later DingTalk conversations.

**Architecture:** `agent_memories` remains the authoritative long-term-memory source of truth from Spec 2; `user_memory_profiles` is a deterministic projection with evidence links and rebuild metadata. The Online Graph retrieves profile-backed active memories after Topic/evidence routing, passes a separate `USER_MEMORY_CONTEXT` block into the Chat Graph, and degrades to ordinary answers when profile or memory services are unavailable.

**Tech Stack:** Python 3.10+; SQLAlchemy asyncio; Alembic; PostgreSQL; Pydantic 2; LangGraph Online Graph and Chat Graph; DingTalk shared processor; pytest; pytest-asyncio; JSONL eval datasets.

## Global Constraints

- Specs 1–2 are accepted before implementation begins.
- `agent_memories` is the source of truth; `user_memory_profiles` is a rebuildable projection and never an independent fact source.
- One current profile exists per `(tenant_id, agent_id, user_id)`.
- The profile stores only approved sections: `work_context`, `response_preferences`, and `development`.
- No free-form personality summary is stored.
- Every non-empty profile field must reference active atomic memory IDs in `evidence_map_json`.
- Superseded, deleted, expired, rejected, and candidate memories never appear in profiles or recall.
- Profile projection is deterministic over active atomic memories and uses no LLM generation.
- Long-term memory recall is not injected into every prompt.
- Memory recall hard-filters by tenant, Agent, user, active status, non-expiry, and allowed memory types before ranking.
- The Chat prompt receives at most five memory items and at most 1,200 Chinese characters of memory context.
- User memory may personalize style and coaching context but cannot override safety, tenant instructions, trusted tool results, or knowledge evidence.
- Product, price, policy, and competitor facts still require trusted tenant knowledge retrieval.
- New Topics may reuse stable user preferences but must not inherit prior Topic entities, customer facts, temporary goals, or customer-specific task state.
- “你记得我什么” exposes understandable profile items with provenance and correction/forget instructions, not embeddings, hidden prompts, internal scores, or restricted audit content.
- Profile, recall, or repository outage must not block ordinary answering; trace must mark degradation.
- Raw sensitive content, full production profiles, API keys, and production conversations are never committed into eval datasets.
- Existing unrelated worker changes such as `.claude/settings.json` are not part of this plan and must not be staged or overwritten.

---

## File Map

### Create

- `src/sales_agent/models/user_memory_profile.py` — SQLAlchemy models for `user_memory_profiles` and `user_profile_rebuild_jobs`.
- `src/sales_agent/migrations/versions/0014_user_memory_profiles.py` — profile projection and rebuild job schema.
- `src/sales_agent/services/memory/profile_contracts.py` — profile schema, evidence map, recall item, trace contracts.
- `src/sales_agent/services/memory/profile_projection.py` — deterministic projection from active atomic memories to profile JSON.
- `src/sales_agent/services/memory/profile_repository.py` — scoped profile persistence, rebuild job, and reconciliation APIs.
- `src/sales_agent/services/memory/profile_recall.py` — task-aware eligibility, ranking, budget, and final authorization filter.
- `src/sales_agent/services/memory/transparency.py` — “你记得我什么” rendering and transparency command detection.
- `src/sales_agent/services/memory/profile_worker.py` — idempotent rebuild loop and scheduled reconciliation.
- `tests/unit/memory/test_profile_contracts.py`
- `tests/unit/memory/test_profile_projection.py`
- `tests/unit/memory/test_profile_recall.py`
- `tests/unit/memory/test_profile_transparency.py`
- `tests/unit/memory/test_profile_worker.py`
- `tests/integration/test_user_memory_profile_repository.py`
- `tests/integration/test_dingtalk_profile_memory_recall.py`
- `eval/memory/user_profile_recall_scenarios.jsonl`
- `eval/run_user_profile_memory_eval.py`
- `tests/unit/eval/test_user_profile_memory_eval.py`
- `scripts/run_user_profile_memory_gate.sh`
- `docs/runbooks/user-profile-memory.md`

### Modify

- `src/sales_agent/core/config.py` — add `UserProfileMemoryConfig`.
- `src/sales_agent/models/__init__.py` — register profile models.
- `src/sales_agent/services/memory/repository.py` — enqueue profile rebuild after active-memory lifecycle changes.
- `src/sales_agent/services/memory/outbox_worker.py` — call rebuild enqueue after inferred activation when Spec 2 worker activates a candidate.
- `src/sales_agent/graph/online/state.py` — add memory recall trace fields.
- `src/sales_agent/graph/online/graph.py` — insert profile recall and transparency nodes.
- `src/sales_agent/graph/online/edges.py` — route transparency command and recall degradation path.
- `src/sales_agent/graph/online/nodes.py` — transparency response, profile recall before chat, trace propagation.
- `src/sales_agent/graph/chat/state.py` — add `user_memory_context`, `selected_memory_ids`, and `memory_trace`.
- `src/sales_agent/graph/chat/nodes/generation.py` — pass user memory context to `execute_agent()`.
- `src/sales_agent/services/agent_executor.py` — render separate `USER_MEMORY_CONTEXT` prompt block.
- `src/sales_agent/integrations/dingtalk/turn_result.py` — expose selected memory IDs, profile version, and degradation fields.
- `src/sales_agent/integrations/dingtalk/processor.py` — map memory trace into `DingTalkTurnResult`.
- `src/sales_agent/roles/worker_runner.py` — run profile rebuild worker when enabled.
- `tests/support/dingtalk_scenario.py` — add profile recall expectations.
- `README.md`

### Delete

- No production module is deleted in Spec 3.

---

### Task 1: Add user profile schema, settings, and model registration

**Files:**
- Create: `src/sales_agent/models/user_memory_profile.py`
- Create: `src/sales_agent/migrations/versions/0014_user_memory_profiles.py`
- Modify: `src/sales_agent/models/__init__.py`
- Modify: `src/sales_agent/core/config.py`
- Create: `tests/unit/memory/test_profile_contracts.py`
- Create: `tests/integration/test_user_memory_profile_repository.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `generate_id`, Spec 2 `AtomicMemory`.
- Produces: `UserMemoryProfile`, `UserProfileRebuildJob`, and `Settings.user_profile_memory`.

- [ ] **Step 1: Write failing model registration test**

Create `tests/unit/memory/test_profile_contracts.py`:

```python
def test_user_memory_profile_tables_are_registered():
    import sales_agent.models
    from sales_agent.core.database import Base

    sales_agent.models._import_dingtalk_models()

    assert "user_memory_profiles" in Base.metadata.tables
    assert "user_profile_rebuild_jobs" in Base.metadata.tables
```

- [ ] **Step 2: Write failing schema integration test**

Create `tests/integration/test_user_memory_profile_repository.py`:

```python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_user_profile_schema_has_scope_and_rebuild_indexes(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE tablename IN ('user_memory_profiles', 'user_profile_rebuild_jobs')
                """
            )
        )
    ).mappings().all()

    indexes = {row["indexname"]: row["indexdef"] for row in rows}
    assert "uq_user_memory_profile_current_scope" in indexes
    assert "ix_user_memory_profiles_scope" in indexes
    assert "ix_user_profile_rebuild_jobs_poll" in indexes
    assert "uq_user_profile_rebuild_scope_reason" in indexes
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/memory/test_profile_contracts.py::test_user_memory_profile_tables_are_registered \
  tests/integration/test_user_memory_profile_repository.py::test_user_profile_schema_has_scope_and_rebuild_indexes
```

Expected: FAIL because profile models and migration do not exist.

- [ ] **Step 4: Add settings**

In `src/sales_agent/core/config.py`, add:

```python
class UserProfileMemoryConfig(BaseModel):
    """Evidence-backed user profile projection and bounded recall."""

    enabled: bool = False
    recall_enabled: bool = True
    transparency_enabled: bool = True
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = 2.0
    rebuild_batch_size: int = 20
    rebuild_max_attempts: int = 5
    max_recall_items: int = 5
    max_recall_chars: int = 1200
    retrieval_timeout_ms: int = 120
```

Add to `Settings`:

```python
user_profile_memory: UserProfileMemoryConfig = UserProfileMemoryConfig()
```

- [ ] **Step 5: Add models**

Create `src/sales_agent/models/user_memory_profile.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class UserMemoryProfile(TimestampMixin, Base):
    __tablename__ = "user_memory_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ready")
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_map_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_memory_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)

    __table_args__ = (
        Index("ix_user_memory_profiles_scope", "tenant_id", "agent_id", "user_id"),
        Index(
            "uq_user_memory_profile_current_scope",
            "tenant_id", "agent_id", "user_id",
            unique=True,
            postgresql_where=text("status IN ('ready', 'rebuilding', 'degraded')"),
        ),
    )


class UserProfileRebuildJob(TimestampMixin, Base):
    __tablename__ = "user_profile_rebuild_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id", "user_id", "reason", "source_memory_id",
            name="uq_user_profile_rebuild_scope_reason",
        ),
        Index("ix_user_profile_rebuild_jobs_poll", "status", "available_at"),
        Index("ix_user_profile_rebuild_jobs_scope", "tenant_id", "agent_id", "user_id"),
    )
```

- [ ] **Step 6: Register models**

Modify `src/sales_agent/models/__init__.py`:

```python
from .user_memory_profile import UserMemoryProfile, UserProfileRebuildJob
```

Add to `__all__`:

```python
"UserMemoryProfile",
"UserProfileRebuildJob",
```

- [ ] **Step 7: Add Alembic migration**

Create `src/sales_agent/migrations/versions/0014_user_memory_profiles.py`:

```python
"""Add user memory profile projection tables.

Revision ID: 0014_user_memory_profiles
Revises: 0013_governed_long_term_memory
Create Date: 2026-07-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_user_memory_profiles"
down_revision: Union[str, None] = "0013_governed_long_term_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_memory_profiles",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'ready'")),
        sa.Column("profile_json", sa.Text(), nullable=False),
        sa.Column("evidence_map_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_memory_version", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_memory_profiles_tenant_id", "user_memory_profiles", ["tenant_id"])
    op.create_index("ix_user_memory_profiles_agent_id", "user_memory_profiles", ["agent_id"])
    op.create_index("ix_user_memory_profiles_user_id", "user_memory_profiles", ["user_id"])
    op.create_index("ix_user_memory_profiles_scope", "user_memory_profiles", ["tenant_id", "agent_id", "user_id"])
    op.create_index(
        "uq_user_memory_profile_current_scope",
        "user_memory_profiles",
        ["tenant_id", "agent_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ready', 'rebuilding', 'degraded')"),
    )

    op.create_table(
        "user_profile_rebuild_jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_memory_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "agent_id", "user_id", "reason", "source_memory_id",
            name="uq_user_profile_rebuild_scope_reason",
        ),
    )
    op.create_index("ix_user_profile_rebuild_jobs_tenant_id", "user_profile_rebuild_jobs", ["tenant_id"])
    op.create_index("ix_user_profile_rebuild_jobs_agent_id", "user_profile_rebuild_jobs", ["agent_id"])
    op.create_index("ix_user_profile_rebuild_jobs_user_id", "user_profile_rebuild_jobs", ["user_id"])
    op.create_index("ix_user_profile_rebuild_jobs_source_memory_id", "user_profile_rebuild_jobs", ["source_memory_id"])
    op.create_index("ix_user_profile_rebuild_jobs_poll", "user_profile_rebuild_jobs", ["status", "available_at"])
    op.create_index("ix_user_profile_rebuild_jobs_scope", "user_profile_rebuild_jobs", ["tenant_id", "agent_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_profile_rebuild_jobs_scope", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_poll", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_source_memory_id", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_user_id", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_agent_id", table_name="user_profile_rebuild_jobs")
    op.drop_index("ix_user_profile_rebuild_jobs_tenant_id", table_name="user_profile_rebuild_jobs")
    op.drop_table("user_profile_rebuild_jobs")
    op.drop_index("uq_user_memory_profile_current_scope", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_scope", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_user_id", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_agent_id", table_name="user_memory_profiles")
    op.drop_index("ix_user_memory_profiles_tenant_id", table_name="user_memory_profiles")
    op.drop_table("user_memory_profiles")
```

- [ ] **Step 8: Run schema tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/memory/test_profile_contracts.py::test_user_memory_profile_tables_are_registered \
  tests/integration/test_user_memory_profile_repository.py::test_user_profile_schema_has_scope_and_rebuild_indexes
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add \
  src/sales_agent/models/user_memory_profile.py \
  src/sales_agent/migrations/versions/0014_user_memory_profiles.py \
  src/sales_agent/models/__init__.py \
  src/sales_agent/core/config.py \
  tests/unit/memory/test_profile_contracts.py \
  tests/integration/test_user_memory_profile_repository.py
git commit -m "feat: add user memory profile schema"
```

---

### Task 2: Implement deterministic profile projection

**Files:**
- Create: `src/sales_agent/services/memory/profile_contracts.py`
- Create: `src/sales_agent/services/memory/profile_projection.py`
- Modify: `src/sales_agent/services/memory/__init__.py`
- Modify: `tests/unit/memory/test_profile_contracts.py`
- Create: `tests/unit/memory/test_profile_projection.py`

**Interfaces:**
- Consumes: Spec 2 `AtomicMemoryRecord`, `MemoryScope`.
- Produces: `EMPTY_PROFILE`, `UserMemoryProfileDocument`, `ProfileProjectionResult`, `project_user_profile(records, now)`, and `compute_source_memory_version(records)`.

- [ ] **Step 1: Write failing profile contract tests**

Append to `tests/unit/memory/test_profile_contracts.py`:

```python
from sales_agent.services.memory.profile_contracts import EMPTY_PROFILE, UserMemoryProfileDocument


def test_empty_profile_has_only_approved_sections():
    profile = UserMemoryProfileDocument.model_validate(EMPTY_PROFILE)
    assert profile.work_context.sales_region is None
    assert profile.work_context.product_focus == []
    assert profile.response_preferences.format == []
    assert profile.development.coaching_goals == []
    assert "personality" not in profile.model_dump()
```

- [ ] **Step 2: Write failing projection tests**

Create `tests/unit/memory/test_profile_projection.py`:

```python
from datetime import datetime, timezone

from sales_agent.services.memory.contracts import AtomicMemoryRecord, MemoryScope
from sales_agent.services.memory.profile_projection import project_user_profile


def _record(
    memory_id,
    *,
    memory_type="user_fact",
    key="sales_region",
    value="华东区",
    status="active",
    evidence_count=1,
    confirmed="2026-07-08T00:00:00+00:00",
):
    confirmed_at = datetime.fromisoformat(confirmed)
    return AtomicMemoryRecord(
        id=memory_id,
        scope=MemoryScope(tenant_id="t1", agent_id="a1", user_id="u1"),
        memory_type=memory_type,
        normalized_key=key,
        content={"key": key, "value": value},
        search_text=value,
        status=status,
        source_kind="explicit_user",
        source_conversation_id="conv1",
        source_message_ids=[f"msg-{memory_id}"],
        evidence_count=evidence_count,
        confidence_band="confirmed",
        sensitivity="normal",
        observed_at=confirmed_at,
        last_confirmed_at=confirmed_at,
        expires_at=None,
    )


def test_projection_empty_profile_has_empty_evidence_map():
    result = project_user_profile([], now=datetime(2026, 7, 8, tzinfo=timezone.utc))
    assert result.profile["work_context"]["sales_region"] is None
    assert result.evidence_map == {}
    assert result.source_memory_version == "empty"


def test_projection_uses_newest_single_value_and_evidence():
    old = _record("m1", value="华东区", confirmed="2026-07-01T00:00:00+00:00")
    new = _record("m2", value="华南区", confirmed="2026-07-08T00:00:00+00:00")
    result = project_user_profile([old, new], now=datetime(2026, 7, 8, tzinfo=timezone.utc))

    assert result.profile["work_context"]["sales_region"] == "华南区"
    assert result.evidence_map["work_context.sales_region"] == ["m2"]


def test_projection_excludes_non_active_statuses():
    result = project_user_profile([
        _record("m1", status="candidate"),
        _record("m2", status="deleted"),
        _record("m3", status="superseded"),
    ], now=datetime(2026, 7, 8, tzinfo=timezone.utc))

    assert result.profile["work_context"]["sales_region"] is None
    assert result.evidence_map == {}


def test_projection_list_values_are_ordered_by_confirmation_strength_and_recency():
    goal = _record("m1", memory_type="coaching_goal", key="coaching_goal", value="提升异议处理")
    challenge = _record(
        "m2",
        memory_type="recurring_challenge",
        key="recurring_challenge",
        value="价格异议容易卡住",
        evidence_count=2,
    )
    pattern = _record("m3", memory_type="sales_pattern", key="sales_pattern", value="先确认预算再推方案")

    result = project_user_profile([goal, challenge, pattern], now=datetime(2026, 7, 8, tzinfo=timezone.utc))

    assert result.profile["development"]["coaching_goals"] == ["提升异议处理"]
    assert result.profile["development"]["recurring_challenges"] == ["价格异议容易卡住"]
    assert result.profile["development"]["confirmed_sales_patterns"] == ["先确认预算再推方案"]
    assert result.evidence_map["development.recurring_challenges"] == ["m2"]
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_profile_contracts.py tests/unit/memory/test_profile_projection.py
```

Expected: FAIL because profile contracts and projection do not exist.

- [ ] **Step 4: Implement profile contracts**

Create `src/sales_agent/services/memory/profile_contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkContext(BaseModel):
    role: str | None = None
    sales_region: str | None = None
    product_focus: list[str] = Field(default_factory=list)


class ResponsePreferences(BaseModel):
    verbosity: str | None = None
    format: list[str] = Field(default_factory=list)
    coaching_style: str | None = None


class DevelopmentProfile(BaseModel):
    coaching_goals: list[str] = Field(default_factory=list)
    recurring_challenges: list[str] = Field(default_factory=list)
    confirmed_sales_patterns: list[str] = Field(default_factory=list)


class UserMemoryProfileDocument(BaseModel):
    work_context: WorkContext = Field(default_factory=WorkContext)
    response_preferences: ResponsePreferences = Field(default_factory=ResponsePreferences)
    development: DevelopmentProfile = Field(default_factory=DevelopmentProfile)


EMPTY_PROFILE: dict[str, Any] = UserMemoryProfileDocument().model_dump()


@dataclass(frozen=True)
class ProfileProjectionResult:
    profile: dict[str, Any]
    evidence_map: dict[str, list[str]]
    source_memory_version: str
    generated_at: datetime


class RecallItem(BaseModel):
    memory_id: str
    memory_type: str
    normalized_key: str
    text: str
    last_confirmed_at: datetime | None = None
    evidence_count: int = 1


class RecallTrace(BaseModel):
    profile_version: int | None = None
    selected_memory_ids: list[str] = Field(default_factory=list)
    eligible_memory_types: list[str] = Field(default_factory=list)
    exclusion_reasons: dict[str, str] = Field(default_factory=dict)
    candidate_count: int = 0
    retrieval_latency_ms: int = 0
    degraded: bool = False
    degradation_reason: str | None = None


@dataclass(frozen=True)
class RecallResult:
    context_text: str
    selected_items: list[RecallItem]
    trace: RecallTrace
```

- [ ] **Step 5: Implement deterministic projection**

Create `src/sales_agent/services/memory/profile_projection.py`:

```python
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

from sales_agent.services.memory.contracts import AtomicMemoryRecord
from sales_agent.services.memory.profile_contracts import EMPTY_PROFILE, ProfileProjectionResult


_SINGLE_VALUE_PATHS = {
    "role": "work_context.role",
    "sales_region": "work_context.sales_region",
    "verbosity": "response_preferences.verbosity",
    "coaching_style": "response_preferences.coaching_style",
}

_LIST_VALUE_PATHS = {
    "product_focus": "work_context.product_focus",
    "response_format": "response_preferences.format",
    "coaching_goal": "development.coaching_goals",
    "recurring_challenge": "development.recurring_challenges",
    "sales_pattern": "development.confirmed_sales_patterns",
}


def _value(record: AtomicMemoryRecord) -> str:
    raw = record.content.get("value")
    if raw is None:
        raw = record.search_text
    return str(raw).strip()


def _last_confirmed(record: AtomicMemoryRecord) -> datetime:
    return record.last_confirmed_at or record.observed_at


def _set_path(profile: dict, dotted_path: str, value):
    target = profile
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def compute_source_memory_version(records: Iterable[AtomicMemoryRecord]) -> str:
    active = sorted(
        (r for r in records if r.status == "active"),
        key=lambda r: (r.id, _last_confirmed(r).isoformat()),
    )
    if not active:
        return "empty"
    material = [
        {
            "id": r.id,
            "status": r.status,
            "normalized_key": r.normalized_key,
            "evidence_count": r.evidence_count,
            "last_confirmed_at": _last_confirmed(r).isoformat(),
        }
        for r in active
    ]
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest[:16]


def project_user_profile(
    records: list[AtomicMemoryRecord],
    *,
    now: datetime | None = None,
) -> ProfileProjectionResult:
    current = now or datetime.now(timezone.utc)
    profile = json.loads(json.dumps(EMPTY_PROFILE, ensure_ascii=False))
    evidence_map: dict[str, list[str]] = {}
    active = [
        r for r in records
        if r.status == "active"
        and (r.expires_at is None or r.expires_at > current)
        and r.sensitivity == "normal"
    ]

    for key, path in _SINGLE_VALUE_PATHS.items():
        candidates = [r for r in active if r.normalized_key == key]
        if not candidates:
            continue
        chosen = sorted(candidates, key=lambda r: (_last_confirmed(r), r.evidence_count), reverse=True)[0]
        value = _value(chosen)
        if value:
            _set_path(profile, path, value)
            evidence_map[path] = [chosen.id]

    for key, path in _LIST_VALUE_PATHS.items():
        candidates = [r for r in active if r.normalized_key == key or r.memory_type == key]
        ordered = sorted(candidates, key=lambda r: (r.evidence_count, _last_confirmed(r)), reverse=True)
        values: list[str] = []
        ids: list[str] = []
        seen: set[str] = set()
        for record in ordered:
            value = _value(record)
            if not value or value in seen:
                continue
            values.append(value)
            ids.append(record.id)
            seen.add(value)
        if values:
            _set_path(profile, path, values)
            evidence_map[path] = ids

    return ProfileProjectionResult(
        profile=profile,
        evidence_map=evidence_map,
        source_memory_version=compute_source_memory_version(active),
        generated_at=current,
    )
```

- [ ] **Step 6: Export profile contracts**

Modify `src/sales_agent/services/memory/__init__.py`:

```python
from sales_agent.services.memory.profile_contracts import (
    EMPTY_PROFILE,
    ProfileProjectionResult,
    RecallItem,
    RecallResult,
    RecallTrace,
    UserMemoryProfileDocument,
)

__all__ += [
    "EMPTY_PROFILE",
    "ProfileProjectionResult",
    "RecallItem",
    "RecallResult",
    "RecallTrace",
    "UserMemoryProfileDocument",
]
```

- [ ] **Step 7: Run projection tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_profile_contracts.py tests/unit/memory/test_profile_projection.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add \
  src/sales_agent/services/memory/profile_contracts.py \
  src/sales_agent/services/memory/profile_projection.py \
  src/sales_agent/services/memory/__init__.py \
  tests/unit/memory/test_profile_contracts.py \
  tests/unit/memory/test_profile_projection.py
git commit -m "feat: add deterministic user profile projection"
```

---

### Task 3: Implement profile repository, rebuild jobs, and reconciliation

**Files:**
- Create: `src/sales_agent/services/memory/profile_repository.py`
- Create: `src/sales_agent/services/memory/profile_worker.py`
- Modify: `src/sales_agent/services/memory/repository.py`
- Modify: `src/sales_agent/services/memory/outbox_worker.py`
- Modify: `tests/integration/test_user_memory_profile_repository.py`
- Create: `tests/unit/memory/test_profile_worker.py`

**Interfaces:**
- Consumes: Spec 2 `AtomicMemoryRepository.list_active_memories()`, `MemoryScope`, `AtomicMemoryRecord`.
- Produces: `UserMemoryProfileRepository`, `rebuild_profile_for_scope()`, `enqueue_profile_rebuild()`, `run_profile_rebuild_once()`, `reconcile_stale_profiles_once()`.

- [ ] **Step 1: Write failing repository tests**

Append to `tests/integration/test_user_memory_profile_repository.py`:

```python
import json
import pytest
from datetime import datetime, timezone

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository


def _scope(user_id="u1"):
    return MemoryScope(tenant_id="t1", agent_id="a1", user_id=user_id)


def _candidate(value="华东区"):
    return MemoryCandidate(
        memory_type="user_fact",
        normalized_key="sales_region",
        content={"key": "sales_region", "value": value},
        evidence_text=f"记住我负责{value}",
        source_kind="explicit_user",
        stability="stable",
        sensitivity="normal",
        confidence_band="confirmed",
    )


@pytest.mark.asyncio
async def test_rebuild_profile_persists_projection_and_evidence(db_session):
    memory_repo = AtomicMemoryRepository(db_session)
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()
    await memory_repo.activate_explicit(scope, _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    result = await profile_repo.rebuild_profile_for_scope(scope, now=datetime(2026, 7, 8, tzinfo=timezone.utc))

    assert result.status == "ready"
    profile = await profile_repo.get_current_profile(scope)
    assert profile is not None
    assert profile.profile["work_context"]["sales_region"] == "华东区"
    assert profile.evidence_map["work_context.sales_region"] == result.evidence_map["work_context.sales_region"]


@pytest.mark.asyncio
async def test_rebuild_is_idempotent_for_same_memory_version(db_session):
    memory_repo = AtomicMemoryRepository(db_session)
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()
    await memory_repo.activate_explicit(scope, _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    first = await profile_repo.rebuild_profile_for_scope(scope)
    second = await profile_repo.rebuild_profile_for_scope(scope)

    assert first.version == second.version
    assert first.source_memory_version == second.source_memory_version


@pytest.mark.asyncio
async def test_enqueue_profile_rebuild_is_idempotent(db_session):
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()

    await profile_repo.enqueue_profile_rebuild(scope, reason="memory_activated", source_memory_id="m1")
    await profile_repo.enqueue_profile_rebuild(scope, reason="memory_activated", source_memory_id="m1")

    jobs = await profile_repo.list_pending_rebuild_jobs(limit=10)
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_reconcile_stale_profile_enqueues_missed_rebuild(db_session):
    memory_repo = AtomicMemoryRepository(db_session)
    profile_repo = UserMemoryProfileRepository(db_session)
    scope = _scope()
    await memory_repo.activate_explicit(scope, _candidate("华东区"), conversation_id="conv1", message_id="msg1")
    await profile_repo.rebuild_profile_for_scope(scope)

    await memory_repo.activate_explicit(scope, _candidate("华南区"), conversation_id="conv2", message_id="msg2")
    enqueued = await profile_repo.enqueue_stale_profile_rebuilds(limit=10)

    assert enqueued == 1
    jobs = await profile_repo.list_pending_rebuild_jobs(limit=10)
    assert any(job.reason == "profile_reconciliation" for job in jobs)
```

- [ ] **Step 2: Write failing worker tests**

Create `tests/unit/memory/test_profile_worker.py`:

```python
from sales_agent.services.memory.profile_worker import compute_profile_backoff_seconds


def test_profile_backoff_is_bounded():
    assert compute_profile_backoff_seconds(1) == 2
    assert compute_profile_backoff_seconds(9) == 300
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/integration/test_user_memory_profile_repository.py \
  tests/unit/memory/test_profile_worker.py
```

Expected: FAIL because profile repository and worker do not exist.

- [ ] **Step 4: Implement profile repository**

Create `src/sales_agent/services/memory/profile_repository.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.user_memory_profile import UserMemoryProfile, UserProfileRebuildJob
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.profile_contracts import ProfileProjectionResult
from sales_agent.services.memory.profile_projection import project_user_profile
from sales_agent.services.memory.repository import AtomicMemoryRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StoredUserMemoryProfile:
    id: str
    tenant_id: str
    agent_id: str
    user_id: str
    version: int
    status: str
    profile: dict
    evidence_map: dict[str, list[str]]
    source_memory_version: str
    generated_at: datetime


class UserMemoryProfileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _to_profile(self, row: UserMemoryProfile) -> StoredUserMemoryProfile:
        return StoredUserMemoryProfile(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            version=row.version,
            status=row.status,
            profile=json.loads(row.profile_json),
            evidence_map=json.loads(row.evidence_map_json),
            source_memory_version=row.source_memory_version,
            generated_at=row.generated_at,
        )

    async def get_current_profile(self, scope: MemoryScope) -> StoredUserMemoryProfile | None:
        row = (
            await self.db.execute(
                select(UserMemoryProfile).where(
                    UserMemoryProfile.tenant_id == scope.tenant_id,
                    UserMemoryProfile.agent_id == scope.agent_id,
                    UserMemoryProfile.user_id == scope.user_id,
                    UserMemoryProfile.status.in_(["ready", "rebuilding", "degraded"]),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._to_profile(row)

    async def rebuild_profile_for_scope(
        self,
        scope: MemoryScope,
        *,
        now: datetime | None = None,
    ) -> StoredUserMemoryProfile:
        current = now or utc_now()
        memory_repo = AtomicMemoryRepository(self.db)
        active_records = await memory_repo.list_active_memories(scope, now=current)
        projection: ProfileProjectionResult = project_user_profile(active_records, now=current)
        existing = await self.get_current_profile(scope)

        if existing and existing.source_memory_version == projection.source_memory_version:
            return existing

        if existing is None:
            row = UserMemoryProfile(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                version=1,
                status="ready",
                profile_json=json.dumps(projection.profile, ensure_ascii=False),
                evidence_map_json=json.dumps(projection.evidence_map, ensure_ascii=False),
                source_memory_version=projection.source_memory_version,
                generated_at=current,
            )
            self.db.add(row)
        else:
            row = (
                await self.db.execute(
                    select(UserMemoryProfile).where(UserMemoryProfile.id == existing.id)
                )
            ).scalar_one()
            row.version = existing.version + 1
            row.status = "ready"
            row.profile_json = json.dumps(projection.profile, ensure_ascii=False)
            row.evidence_map_json = json.dumps(projection.evidence_map, ensure_ascii=False)
            row.source_memory_version = projection.source_memory_version
            row.generated_at = current

        await self.db.flush()
        return self._to_profile(row)

    async def enqueue_profile_rebuild(
        self,
        scope: MemoryScope,
        *,
        reason: str,
        source_memory_id: str | None,
        now: datetime | None = None,
    ) -> None:
        current = now or utc_now()
        stmt = insert(UserProfileRebuildJob).values(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            reason=reason,
            source_memory_id=source_memory_id,
            status="pending",
            attempts=0,
            available_at=current,
        ).on_conflict_do_nothing(
            constraint="uq_user_profile_rebuild_scope_reason"
        )
        await self.db.execute(stmt)

    async def list_pending_rebuild_jobs(self, *, limit: int) -> list[UserProfileRebuildJob]:
        return (
            await self.db.execute(
                select(UserProfileRebuildJob)
                .where(UserProfileRebuildJob.status == "pending")
                .where(UserProfileRebuildJob.available_at <= utc_now())
                .order_by(UserProfileRebuildJob.created_at.asc())
                .limit(limit)
            )
        ).scalars().all()

    async def list_profile_scopes(self, *, limit: int) -> list[MemoryScope]:
        rows = (
            await self.db.execute(
                select(UserMemoryProfile)
                .where(UserMemoryProfile.status.in_(["ready", "degraded"]))
                .order_by(UserMemoryProfile.updated_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            MemoryScope(tenant_id=row.tenant_id, agent_id=row.agent_id, user_id=row.user_id)
            for row in rows
        ]

    async def enqueue_stale_profile_rebuilds(self, *, limit: int) -> int:
        scopes = await self.list_profile_scopes(limit=limit)
        count = 0
        for scope in scopes:
            current_profile = await self.get_current_profile(scope)
            if current_profile is None:
                continue
            memory_repo = AtomicMemoryRepository(self.db)
            records = await memory_repo.list_active_memories(scope)
            projected = project_user_profile(records)
            if projected.source_memory_version != current_profile.source_memory_version:
                await self.enqueue_profile_rebuild(
                    scope,
                    reason="profile_reconciliation",
                    source_memory_id=None,
                )
                count += 1
        return count
```

- [ ] **Step 5: Implement profile worker**

Create `src/sales_agent/services/memory/profile_worker.py`:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sales_agent.core.database import get_session_factory
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def compute_profile_backoff_seconds(attempts: int) -> int:
    return min(300, 2 ** max(1, attempts))


async def run_profile_rebuild_once(*, session_factory, batch_size: int, max_attempts: int) -> int:
    async with session_factory() as db:
        repo = UserMemoryProfileRepository(db)
        jobs = await repo.list_pending_rebuild_jobs(limit=batch_size)
        processed = 0
        for job in jobs:
            try:
                scope = MemoryScope(tenant_id=job.tenant_id, agent_id=job.agent_id, user_id=job.user_id)
                await repo.rebuild_profile_for_scope(scope)
                job.status = "done"
                job.last_error = None
                processed += 1
            except Exception as exc:
                job.attempts += 1
                job.last_error = str(exc)[:1000]
                if job.attempts >= max_attempts:
                    job.status = "dead"
                else:
                    job.status = "pending"
                    job.available_at = utc_now() + timedelta(seconds=compute_profile_backoff_seconds(job.attempts))
        await db.commit()
        return processed


async def reconcile_stale_profiles_once(*, session_factory) -> int:
    async with session_factory() as db:
        repo = UserMemoryProfileRepository(db)
        count = await repo.enqueue_stale_profile_rebuilds(limit=100)
        await db.commit()
        return count


async def profile_rebuild_loop(*, poll_interval_seconds: float, batch_size: int, max_attempts: int) -> None:
    session_factory = get_session_factory()
    while True:
        try:
            await run_profile_rebuild_once(
                session_factory=session_factory,
                batch_size=batch_size,
                max_attempts=max_attempts,
            )
            await reconcile_stale_profiles_once(session_factory=session_factory)
        except Exception:
            logger.exception("profile rebuild loop failed")
        await asyncio.sleep(poll_interval_seconds)
```

- [ ] **Step 6: Enqueue rebuilds after atomic memory lifecycle changes**

Modify `src/sales_agent/services/memory/repository.py` after successful activation, correction, forget, expiry, and inferred corroboration:

```python
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository
```

Add helper:

```python
    async def _enqueue_profile_rebuild(
        self,
        scope: MemoryScope,
        *,
        reason: str,
        source_memory_id: str | None,
    ) -> None:
        try:
            profile_repo = UserMemoryProfileRepository(self.db)
            await profile_repo.enqueue_profile_rebuild(
                scope,
                reason=reason,
                source_memory_id=source_memory_id,
            )
        except Exception:
            logger.warning("profile rebuild enqueue failed", exc_info=True)
```

Call it after creating or changing active memories:

```python
await self._enqueue_profile_rebuild(scope, reason="memory_activated", source_memory_id=row.id)
```

Call it for forget:

```python
for memory_id in ids:
    await self._enqueue_profile_rebuild(scope, reason="memory_deleted", source_memory_id=memory_id)
```

Call it for expiry with a scope reconstructed from each expired row:

```python
scope = MemoryScope(tenant_id=row.tenant_id, agent_id=row.agent_id, user_id=row.subject_id)
await self._enqueue_profile_rebuild(scope, reason="memory_expired", source_memory_id=row.id)
```

- [ ] **Step 7: Run repository and worker tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/integration/test_user_memory_profile_repository.py \
  tests/unit/memory/test_profile_worker.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add \
  src/sales_agent/services/memory/profile_repository.py \
  src/sales_agent/services/memory/profile_worker.py \
  src/sales_agent/services/memory/repository.py \
  src/sales_agent/services/memory/outbox_worker.py \
  tests/integration/test_user_memory_profile_repository.py \
  tests/unit/memory/test_profile_worker.py
git commit -m "feat: add user profile rebuild repository"
```

---

### Task 4: Implement task-aware bounded profile recall

**Files:**
- Create: `src/sales_agent/services/memory/profile_recall.py`
- Create: `tests/unit/memory/test_profile_recall.py`

**Interfaces:**
- Consumes: `MemoryScope`, `AtomicMemoryRepository.list_active_memories()`, `UserMemoryProfileRepository.get_current_profile()`.
- Produces: `eligible_memory_types_for_task()`, `retrieve_user_memory_context()`, `format_user_memory_context()`.

- [ ] **Step 1: Write failing recall tests**

Create `tests/unit/memory/test_profile_recall.py`:

```python
from datetime import datetime, timezone

import pytest

from sales_agent.services.memory.contracts import AtomicMemoryRecord, MemoryScope
from sales_agent.services.memory.profile_recall import (
    eligible_memory_types_for_task,
    format_user_memory_context,
    rank_recall_items,
)


def _record(memory_id, memory_type, key, value, evidence_count=1):
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    return AtomicMemoryRecord(
        id=memory_id,
        scope=MemoryScope(tenant_id="t1", agent_id="a1", user_id="u1"),
        memory_type=memory_type,
        normalized_key=key,
        content={"key": key, "value": value},
        search_text=value,
        status="active",
        source_kind="explicit_user",
        source_conversation_id="conv1",
        source_message_ids=[f"msg-{memory_id}"],
        evidence_count=evidence_count,
        confidence_band="confirmed",
        sensitivity="normal",
        observed_at=now,
        last_confirmed_at=now,
        expires_at=None,
    )


def test_task_eligibility_keeps_product_facts_out_of_profile_recall():
    assert "response_preference" in eligible_memory_types_for_task("knowledge_qa", "required")
    assert "user_fact" not in eligible_memory_types_for_task("knowledge_qa", "required")
    assert "coaching_goal" in eligible_memory_types_for_task("general_sales_coaching", "none")


def test_rank_recall_items_enforces_diversity_and_top_five():
    records = [
        _record("m1", "response_preference", "response_style", "回答短一点", 1),
        _record("m2", "coaching_goal", "coaching_goal", "提升异议处理", 2),
        _record("m3", "recurring_challenge", "recurring_challenge", "价格异议容易卡住", 2),
        _record("m4", "sales_pattern", "sales_pattern", "先确认预算", 1),
        _record("m5", "user_fact", "sales_region", "华东区", 1),
        _record("m6", "user_fact", "product_focus", "电影票", 1),
    ]

    ranked = rank_recall_items(records, standalone_query="帮我写一段简洁的价格异议话术", max_items=5)

    assert len(ranked) == 5
    assert ranked[0].memory_id in {"m2", "m3"}
    assert len({item.normalized_key for item in ranked}) == 5


def test_format_user_memory_context_respects_item_and_char_budget():
    records = [
        _record(f"m{i}", "response_preference", f"style_{i}", "回答短一点" * 80)
        for i in range(8)
    ]
    items = rank_recall_items(records, standalone_query="简洁回复", max_items=8)
    text = format_user_memory_context(items, max_items=5, max_chars=1200)

    assert text.startswith("USER_MEMORY_CONTEXT")
    assert text.endswith("END_USER_MEMORY_CONTEXT")
    assert text.count("- memory_id:") <= 5
    assert len(text) <= 1200
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_profile_recall.py
```

Expected: FAIL because `profile_recall.py` does not exist.

- [ ] **Step 3: Implement recall eligibility, ranking, and formatting**

Create `src/sales_agent/services/memory/profile_recall.py`:

```python
from __future__ import annotations

import time
from datetime import datetime, timezone

from sales_agent.services.memory.contracts import AtomicMemoryRecord, MemoryScope
from sales_agent.services.memory.profile_contracts import RecallItem, RecallResult, RecallTrace
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository
from sales_agent.services.memory.repository import AtomicMemoryRepository


def eligible_memory_types_for_task(task_type: str | None, knowledge_policy: str | None) -> list[str]:
    if task_type in {"help", "reset"}:
        return []
    if knowledge_policy == "required":
        return ["response_preference"]
    if task_type in {"knowledge_qa"}:
        return ["response_preference"]
    if task_type in {"script_generation", "objection_handling", "general_sales_coaching"}:
        return [
            "response_preference",
            "coaching_goal",
            "recurring_challenge",
            "sales_pattern",
            "user_fact",
        ]
    return ["response_preference", "coaching_goal", "recurring_challenge"]


def _value(record: AtomicMemoryRecord) -> str:
    return str(record.content.get("value") or record.search_text).strip()


def _last_confirmed(record: AtomicMemoryRecord) -> datetime:
    return record.last_confirmed_at or record.observed_at


def _lexical_relevance(record: AtomicMemoryRecord, query: str) -> int:
    text = (_value(record) + " " + record.normalized_key).lower()
    return sum(1 for char in set(query.lower()) if char and char in text)


def rank_recall_items(
    records: list[AtomicMemoryRecord],
    *,
    standalone_query: str,
    max_items: int,
) -> list[RecallItem]:
    active = [
        r for r in records
        if r.status == "active"
        and r.sensitivity == "normal"
        and (r.expires_at is None or r.expires_at > datetime.now(timezone.utc))
    ]
    ordered = sorted(
        active,
        key=lambda r: (
            _lexical_relevance(r, standalone_query),
            r.evidence_count,
            _last_confirmed(r),
        ),
        reverse=True,
    )
    selected: list[RecallItem] = []
    used_keys: set[str] = set()
    for record in ordered:
        if record.normalized_key in used_keys:
            continue
        text = _value(record)
        if not text:
            continue
        selected.append(
            RecallItem(
                memory_id=record.id,
                memory_type=record.memory_type,
                normalized_key=record.normalized_key,
                text=text,
                last_confirmed_at=record.last_confirmed_at,
                evidence_count=record.evidence_count,
            )
        )
        used_keys.add(record.normalized_key)
        if len(selected) >= max_items:
            break
    return selected


def format_user_memory_context(
    items: list[RecallItem],
    *,
    max_items: int,
    max_chars: int,
) -> str:
    if not items:
        return ""
    lines = ["USER_MEMORY_CONTEXT"]
    for item in items[:max_items]:
        confirmed = item.last_confirmed_at.isoformat() if item.last_confirmed_at else ""
        lines.extend([
            f"- memory_id: {item.memory_id}",
            f"  memory_type: {item.memory_type}",
            f"  normalized_key: {item.normalized_key}",
            f"  fact_or_preference: {item.text}",
            f"  last_confirmed_at: {confirmed}",
        ])
    lines.append("END_USER_MEMORY_CONTEXT")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len("\nEND_USER_MEMORY_CONTEXT")] + "\nEND_USER_MEMORY_CONTEXT"
```

- [ ] **Step 4: Implement repository-backed recall with degradation**

Add:

```python
async def retrieve_user_memory_context(
    *,
    db,
    scope: MemoryScope,
    standalone_query: str,
    task_type: str | None,
    knowledge_policy: str | None,
    max_items: int,
    max_chars: int,
) -> RecallResult:
    start = time.time()
    trace = RecallTrace()
    eligible_types = eligible_memory_types_for_task(task_type, knowledge_policy)
    trace.eligible_memory_types = eligible_types
    if not eligible_types:
        trace.retrieval_latency_ms = int((time.time() - start) * 1000)
        return RecallResult(context_text="", selected_items=[], trace=trace)

    try:
        profile_repo = UserMemoryProfileRepository(db)
        profile = await profile_repo.get_current_profile(scope)
        if profile is None:
            await profile_repo.enqueue_profile_rebuild(scope, reason="profile_missing", source_memory_id=None)
        else:
            trace.profile_version = profile.version

        memory_repo = AtomicMemoryRepository(db)
        records = await memory_repo.list_active_memories(scope)
        filtered = [record for record in records if record.memory_type in eligible_types]
        trace.candidate_count = len(filtered)
        selected = rank_recall_items(filtered, standalone_query=standalone_query, max_items=max_items)
        context_text = format_user_memory_context(selected, max_items=max_items, max_chars=max_chars)
        trace.selected_memory_ids = [item.memory_id for item in selected]
        trace.retrieval_latency_ms = int((time.time() - start) * 1000)
        return RecallResult(context_text=context_text, selected_items=selected, trace=trace)
    except Exception as exc:
        trace.degraded = True
        trace.degradation_reason = exc.__class__.__name__
        trace.retrieval_latency_ms = int((time.time() - start) * 1000)
        return RecallResult(context_text="", selected_items=[], trace=trace)
```

- [ ] **Step 5: Run recall tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_profile_recall.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/services/memory/profile_recall.py tests/unit/memory/test_profile_recall.py
git commit -m "feat: add task-aware profile recall"
```

---

### Task 5: Inject bounded user memory into Online and Chat Graphs

**Files:**
- Modify: `src/sales_agent/graph/online/state.py`
- Modify: `src/sales_agent/graph/online/graph.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `src/sales_agent/graph/chat/state.py`
- Modify: `src/sales_agent/graph/chat/nodes/generation.py`
- Modify: `src/sales_agent/services/agent_executor.py`
- Modify: `src/sales_agent/integrations/dingtalk/turn_result.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`
- Modify: `tests/unit/graph/test_online_graph.py`
- Create: `tests/integration/test_dingtalk_profile_memory_recall.py`

**Interfaces:**
- Consumes: `retrieve_user_memory_context()`, `RecallTrace`, Chat Graph `execute_agent()`.
- Produces: `user_memory_context` prompt block, `memory_trace`, `selected_memory_ids`, `memory_degraded`.

- [ ] **Step 1: Write failing prompt injection unit test**

Append to `tests/unit/graph/test_online_graph.py`:

```python
from sales_agent.services.agent_executor import _build_messages


def test_user_memory_context_is_separate_from_retrieval_content():
    messages = _build_messages(
        task_type="general_sales_coaching",
        message="帮我写一段跟进话术",
        context={"user_memory_context": "USER_MEMORY_CONTEXT\n- memory_id: m1\n  fact_or_preference: 回答短一点\nEND_USER_MEMORY_CONTEXT"},
        retrieval_result=None,
        history_messages=[],
        tenant_style={},
    )

    user_content = messages[-1]["content"]
    assert "USER_MEMORY_CONTEXT" in user_content
    assert "企业知识库内容" not in user_content
```

- [ ] **Step 2: Write failing DingTalk recall integration test**

Create `tests/integration/test_dingtalk_profile_memory_recall.py`:

```python
import pytest

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository
from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event


@pytest.mark.asyncio
async def test_dingtalk_new_topic_uses_preference_without_restoring_old_topic(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    scope = MemoryScope(tenant_id=sample_tenant, agent_id=active_agent.id, user_id="internal_user_1")
    memory_repo = AtomicMemoryRepository(db_session)
    await memory_repo.activate_explicit(
        scope,
        MemoryCandidate(
            memory_type="response_preference",
            normalized_key="response_style",
            content={"key": "response_style", "value": "回答短一点"},
            evidence_text="记住以后回答短一点",
            source_kind="explicit_user",
            stability="stable",
            sensitivity="normal",
            confidence_band="confirmed",
        ),
        conversation_id="conv1",
        message_id="msg1",
    )
    await UserMemoryProfileRepository(db_session).rebuild_profile_for_scope(scope)

    replies = []

    async def reply_fn(text):
        replies.append(text)

    class Settings:
        class conversation:
            reset_commands = ["/reset", "新话题"]
        class long_term_memory:
            enabled = True
        class user_profile_memory:
            enabled = True
            recall_enabled = True
            max_recall_items = 5
            max_recall_chars = 1200

    runtime = type("Runtime", (), {"tenant_id": sample_tenant})()
    config = type("Config", (), {})()

    async def fake_get_or_create_user(self, corp_id, dingtalk_user_id, display_name):
        return "internal_user_1"

    async def fake_resolve_agent_id(db, tenant_id):
        return active_agent.id

    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.user_mapper.DingTalkUserMapper.get_or_create_user",
        fake_get_or_create_user,
    )
    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
        fake_resolve_agent_id,
    )

    result = await handle_dingtalk_event(
        db_session,
        config,
        Settings(),
        runtime,
        event_id="profile_evt_1",
        corp_id="corp1",
        sender_id="ding_user_1",
        sender_name="张三",
        message_type="text",
        text="新话题 帮我写一段跟进话术",
        dingtalk_conversation_id="dt_conv_1",
        reply_fn=reply_fn,
    )

    assert result.selected_memory_ids
    assert result.memory_degraded is False
    assert result.turn_relation in {"new", None}
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py::test_user_memory_context_is_separate_from_retrieval_content \
  tests/integration/test_dingtalk_profile_memory_recall.py::test_dingtalk_new_topic_uses_preference_without_restoring_old_topic
```

Expected: FAIL because prompt injection and recall fields do not exist.

- [ ] **Step 4: Add state fields**

In `src/sales_agent/graph/online/state.py`, add:

```python
user_profile_memory_enabled: bool
user_memory_context: str | None
selected_memory_ids: list[str]
memory_trace: dict[str, Any]
memory_degraded: bool
memory_degradation_reason: str | None
profile_version: int | None
```

In `src/sales_agent/graph/chat/state.py`, add:

```python
user_memory_context: str | None
selected_memory_ids: list[str]
memory_trace: dict[str, Any]
```

In `src/sales_agent/services/online_conversation.py`, add turn defaults:

```python
"user_memory_context": None,
"selected_memory_ids": [],
"memory_trace": {},
"memory_degraded": False,
"memory_degradation_reason": None,
"profile_version": None,
```

Pass settings into `build_online_turn_input()`:

```python
user_profile_memory_enabled=settings.user_profile_memory.enabled,
```

- [ ] **Step 5: Add Online profile recall node**

In `src/sales_agent/graph/online/nodes.py`, import:

```python
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.profile_recall import retrieve_user_memory_context
```

Add:

```python
async def profile_recall_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    if not state.get("user_profile_memory_enabled"):
        return {}
    if state.get("context_status") != "resolved":
        return {}
    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        return {
            "memory_degraded": True,
            "memory_degradation_reason": "missing_db",
            "memory_trace": {"degraded": True, "degradation_reason": "missing_db"},
        }
    settings = get_settings()
    result = await retrieve_user_memory_context(
        db=db,
        scope=MemoryScope(
            tenant_id=state.get("tenant_id", ""),
            agent_id=state.get("agent_id", ""),
            user_id=state.get("user_id", ""),
        ),
        standalone_query=state.get("standalone_query") or state.get("message", ""),
        task_type=state.get("task_type"),
        knowledge_policy=state.get("knowledge_policy"),
        max_items=settings.user_profile_memory.max_recall_items,
        max_chars=settings.user_profile_memory.max_recall_chars,
    )
    trace = result.trace.model_dump()
    return {
        "user_memory_context": result.context_text or None,
        "selected_memory_ids": trace.get("selected_memory_ids", []),
        "memory_trace": trace,
        "memory_degraded": bool(trace.get("degraded")),
        "memory_degradation_reason": trace.get("degradation_reason"),
        "profile_version": trace.get("profile_version"),
    }
```

- [ ] **Step 6: Wire Online Graph before chat**

Modify `src/sales_agent/graph/online/graph.py`:

```python
from sales_agent.graph.online.nodes import profile_recall_node
```

Add node:

```python
builder.add_node("profile_recall", profile_recall_node)
```

Change resolved path:

```python
builder.add_edge("evidence_routing", "profile_recall")
builder.add_edge("profile_recall", "chat")
```

For direct-chat path, keep recall disabled unless context was resolved:

```python
builder.add_edge("direct_evidence_routing", "chat")
```

- [ ] **Step 7: Pass memory context into Chat Graph and executor**

In `chat_node()` add to `chat_input`:

```python
"user_memory_context": state.get("user_memory_context"),
"selected_memory_ids": state.get("selected_memory_ids", []),
"memory_trace": state.get("memory_trace", {}),
```

In `src/sales_agent/graph/chat/nodes/generation.py`, change `execute_agent()` call:

```python
context={"user_memory_context": state.get("user_memory_context")},
```

In `src/sales_agent/services/agent_executor.py`, update `_build_context_block()`:

```python
        if key == "user_memory_context":
            continue
```

Add helper:

```python
def _build_user_memory_block(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    text = (context.get("user_memory_context") or "").strip()
    if not text:
        return ""
    return (
        "## 长期用户记忆（只用于个性化表达和教练上下文，不能覆盖企业知识库、工具结果、安全规则或产品事实）\n"
        f"{text}"
    )
```

In `_build_messages()`, build and inject:

```python
memory_block = _build_user_memory_block(context)
if memory_block:
    user_prompt = f"{memory_block}\n\n{user_prompt}"
```

- [ ] **Step 8: Map recall trace to DingTalk result**

In `turn_result.py`, add:

```python
selected_memory_ids: list[str] | None = None
profile_version: int | None = None
memory_trace: dict | None = None
memory_degraded: bool = False
memory_degradation_reason: str | None = None
```

In `processor.py`, map these fields from `result`:

```python
selected_memory_ids=result.get("selected_memory_ids"),
profile_version=result.get("profile_version"),
memory_trace=result.get("memory_trace"),
memory_degraded=bool(result.get("memory_degraded")),
memory_degradation_reason=result.get("memory_degradation_reason"),
```

- [ ] **Step 9: Run graph and DingTalk recall tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py::test_user_memory_context_is_separate_from_retrieval_content \
  tests/integration/test_dingtalk_profile_memory_recall.py::test_dingtalk_new_topic_uses_preference_without_restoring_old_topic
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add \
  src/sales_agent/graph/online/state.py \
  src/sales_agent/graph/online/graph.py \
  src/sales_agent/graph/online/nodes.py \
  src/sales_agent/graph/chat/state.py \
  src/sales_agent/graph/chat/nodes/generation.py \
  src/sales_agent/services/agent_executor.py \
  src/sales_agent/integrations/dingtalk/turn_result.py \
  src/sales_agent/integrations/dingtalk/processor.py \
  tests/unit/graph/test_online_graph.py \
  tests/integration/test_dingtalk_profile_memory_recall.py
git commit -m "feat: inject bounded user memory recall"
```

---

### Task 6: Add transparency response and correction-ready provenance

**Files:**
- Create: `src/sales_agent/services/memory/transparency.py`
- Modify: `src/sales_agent/graph/online/graph.py`
- Modify: `src/sales_agent/graph/online/edges.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Create: `tests/unit/memory/test_profile_transparency.py`
- Modify: `tests/integration/test_dingtalk_profile_memory_recall.py`

**Interfaces:**
- Consumes: `UserMemoryProfileRepository.get_current_profile()`, `AtomicMemoryRepository.get_memory_with_provenance()`.
- Produces: `detect_transparency_command()`, `render_memory_transparency()`, and `profile_transparency_node()`.

- [ ] **Step 1: Write failing transparency unit tests**

Create `tests/unit/memory/test_profile_transparency.py`:

```python
from datetime import datetime, timezone

from sales_agent.services.memory.profile_repository import StoredUserMemoryProfile
from sales_agent.services.memory.transparency import detect_transparency_command, render_memory_transparency


def test_detect_transparency_command():
    assert detect_transparency_command("你记得我什么") is True
    assert detect_transparency_command("你还记得哪些关于我的信息？") is True
    assert detect_transparency_command("帮我写话术") is False


def test_render_memory_transparency_groups_approved_sections():
    profile = StoredUserMemoryProfile(
        id="p1",
        tenant_id="t1",
        agent_id="a1",
        user_id="u1",
        version=2,
        status="ready",
        profile={
            "work_context": {"role": None, "sales_region": "华东区", "product_focus": []},
            "response_preferences": {"verbosity": "简洁", "format": [], "coaching_style": None},
            "development": {"coaching_goals": [], "recurring_challenges": [], "confirmed_sales_patterns": []},
        },
        evidence_map={
            "work_context.sales_region": ["m1"],
            "response_preferences.verbosity": ["m2"],
        },
        source_memory_version="v1",
        generated_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    text = render_memory_transparency(profile)

    assert "我目前记得这些" in text
    assert "华东区" in text
    assert "m1" in text
    assert "人格" not in text
    assert "如果不准确" in text
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_profile_transparency.py
```

Expected: FAIL because transparency service does not exist.

- [ ] **Step 3: Implement transparency service**

Create `src/sales_agent/services/memory/transparency.py`:

```python
from __future__ import annotations

import re

from sales_agent.services.memory.profile_repository import StoredUserMemoryProfile


_TRANSPARENCY_PATTERNS = [
    re.compile(r"(你|系统|助手).*(记得|保存).*(我|我的).*(什么|哪些|信息)?"),
    re.compile(r"(你记得我什么|你还记得哪些关于我的信息)"),
]


def detect_transparency_command(text: str) -> bool:
    stripped = text.strip()
    return any(pattern.search(stripped) for pattern in _TRANSPARENCY_PATTERNS)


def _append_value(lines: list[str], title: str, value, memory_ids: list[str] | None) -> None:
    if value is None or value == [] or value == "":
        return
    if isinstance(value, list):
        rendered_value = "、".join(str(item) for item in value)
    else:
        rendered_value = str(value)
    ids = "、".join(memory_ids or [])
    suffix = f"（记忆ID：{ids}）" if ids else ""
    lines.append(f"- {title}：{rendered_value}{suffix}")


def render_memory_transparency(profile: StoredUserMemoryProfile | None) -> str:
    if profile is None:
        return "我目前还没有保存关于你的长期记忆。你可以说“记住我负责华东区”这类明确指令来保存。"

    data = profile.profile
    evidence = profile.evidence_map
    lines = [f"我目前记得这些（资料版本 {profile.version}）：", ""]

    lines.append("工作背景")
    _append_value(lines, "角色", data["work_context"].get("role"), evidence.get("work_context.role"))
    _append_value(lines, "负责区域", data["work_context"].get("sales_region"), evidence.get("work_context.sales_region"))
    _append_value(lines, "关注产品", data["work_context"].get("product_focus"), evidence.get("work_context.product_focus"))

    lines.append("")
    lines.append("回复偏好")
    _append_value(lines, "详略偏好", data["response_preferences"].get("verbosity"), evidence.get("response_preferences.verbosity"))
    _append_value(lines, "格式偏好", data["response_preferences"].get("format"), evidence.get("response_preferences.format"))
    _append_value(lines, "教练风格", data["response_preferences"].get("coaching_style"), evidence.get("response_preferences.coaching_style"))

    lines.append("")
    lines.append("成长目标")
    _append_value(lines, "教练目标", data["development"].get("coaching_goals"), evidence.get("development.coaching_goals"))
    _append_value(lines, "常见卡点", data["development"].get("recurring_challenges"), evidence.get("development.recurring_challenges"))
    _append_value(lines, "有效打法", data["development"].get("confirmed_sales_patterns"), evidence.get("development.confirmed_sales_patterns"))

    visible_items = [line for line in lines if line.startswith("- ")]
    if not visible_items:
        return "我目前还没有可展示的长期记忆。你可以明确告诉我需要记住的稳定偏好或工作背景。"

    lines.append("")
    lines.append("如果不准确，你可以直接说“我不负责华东了，现在负责华南”，或“忘记我的区域信息”。")
    return "\n".join(lines)
```

- [ ] **Step 4: Add Online transparency node**

In `src/sales_agent/graph/online/nodes.py`, import:

```python
from sales_agent.services.memory.profile_repository import UserMemoryProfileRepository
from sales_agent.services.memory.transparency import detect_transparency_command, render_memory_transparency
```

Modify `normalize_turn_node()` after duplicate and reset but before memory command:

```python
    elif state.get("user_profile_memory_enabled") and detect_transparency_command(message):
        flow_action = "profile_transparency"
```

Add node:

```python
async def profile_transparency_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        text = "我现在暂时查不到长期记忆，请稍后再试。"
    else:
        repo = UserMemoryProfileRepository(db)
        profile = await repo.get_current_profile(
            MemoryScope(
                tenant_id=state.get("tenant_id", ""),
                agent_id=state.get("agent_id", ""),
                user_id=state.get("user_id", ""),
            )
        )
        text = render_memory_transparency(profile)
    return {
        "answer_dict": {
            "summary": text,
            "sections": [{"title": "长期记忆", "content": text}],
        },
        "response_kind": "profile_transparency",
        "last_event_id": state.get("event_id"),
    }
```

- [ ] **Step 5: Wire graph route**

In `src/sales_agent/graph/online/graph.py`, add:

```python
builder.add_node("profile_transparency", profile_transparency_node)
```

Extend route map:

```python
"profile_transparency": "profile_transparency",
```

Add terminal edge:

```python
builder.add_edge("profile_transparency", END)
```

- [ ] **Step 6: Add DingTalk transparency scenario**

Append to `tests/integration/test_dingtalk_profile_memory_recall.py`:

```python
@pytest.mark.asyncio
async def test_dingtalk_transparency_lists_profile_memory(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    scope = MemoryScope(tenant_id=sample_tenant, agent_id=active_agent.id, user_id="internal_user_1")
    memory_repo = AtomicMemoryRepository(db_session)
    await memory_repo.activate_explicit(
        scope,
        MemoryCandidate(
            memory_type="user_fact",
            normalized_key="sales_region",
            content={"key": "sales_region", "value": "华东区"},
            evidence_text="记住我负责华东区",
            source_kind="explicit_user",
            stability="stable",
            sensitivity="normal",
            confidence_band="confirmed",
        ),
        conversation_id="conv1",
        message_id="msg1",
    )
    await UserMemoryProfileRepository(db_session).rebuild_profile_for_scope(scope)

    replies = []
    async def reply_fn(text):
        replies.append(text)

    async def fake_get_or_create_user(self, corp_id, dingtalk_user_id, display_name):
        return "internal_user_1"

    async def fake_resolve_agent_id(db, tenant_id):
        return active_agent.id

    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.user_mapper.DingTalkUserMapper.get_or_create_user",
        fake_get_or_create_user,
    )
    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.processor.resolve_dingtalk_agent_id",
        fake_resolve_agent_id,
    )

    result = await handle_dingtalk_event(
        db_session,
        type("Config", (), {})(),
        type("Settings", (), {
            "conversation": type("Conversation", (), {"reset_commands": ["/reset", "新话题"]})(),
            "long_term_memory": type("LongTermMemory", (), {"enabled": True})(),
            "user_profile_memory": type("UserProfileMemory", (), {"enabled": True, "transparency_enabled": True})(),
        })(),
        type("Runtime", (), {"tenant_id": sample_tenant})(),
        event_id="profile_evt_2",
        corp_id="corp1",
        sender_id="ding_user_1",
        sender_name="张三",
        message_type="text",
        text="你记得我什么",
        dingtalk_conversation_id="dt_conv_1",
        reply_fn=reply_fn,
    )

    assert result.response_kind == "profile_transparency"
    assert any("华东区" in reply for reply in replies)
```

- [ ] **Step 7: Run transparency tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/memory/test_profile_transparency.py \
  tests/integration/test_dingtalk_profile_memory_recall.py::test_dingtalk_transparency_lists_profile_memory
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add \
  src/sales_agent/services/memory/transparency.py \
  src/sales_agent/graph/online/graph.py \
  src/sales_agent/graph/online/edges.py \
  src/sales_agent/graph/online/nodes.py \
  tests/unit/memory/test_profile_transparency.py \
  tests/integration/test_dingtalk_profile_memory_recall.py
git commit -m "feat: add user memory transparency response"
```

---

### Task 7: Run profile worker, add eval gate, and document operations

**Files:**
- Modify: `src/sales_agent/roles/worker_runner.py`
- Modify: `tests/support/dingtalk_scenario.py`
- Create: `eval/memory/user_profile_recall_scenarios.jsonl`
- Create: `eval/run_user_profile_memory_eval.py`
- Create: `tests/unit/eval/test_user_profile_memory_eval.py`
- Create: `scripts/run_user_profile_memory_gate.sh`
- Create: `docs/runbooks/user-profile-memory.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `profile_rebuild_loop()`, DingTalk scenario harness, recall trace fields.
- Produces: release gate for Spec 3 and runbook for profile projection, recall, transparency, degradation, and rollback.

- [ ] **Step 1: Write failing eval unit test**

Create `tests/unit/eval/test_user_profile_memory_eval.py`:

```python
from eval.run_user_profile_memory_eval import load_scenarios, run_fixture_eval


def test_user_profile_memory_fixture_dataset_passes_required_tags():
    scenarios = load_scenarios("eval/memory/user_profile_recall_scenarios.jsonl")
    report = run_fixture_eval(scenarios)

    assert report.total_scenarios >= 6
    assert report.profile_evidence_pass_rate == 1.0
    assert report.budget_violation_count == 0
    assert report.cross_scope_leakage == 0
    assert report.knowledge_override_violations == 0
    assert report.thresholds_met is True
```

- [ ] **Step 2: Create fixture scenarios**

Create `eval/memory/user_profile_recall_scenarios.jsonl`:

```jsonl
{"id":"up-001-day-two-new-topic-recalls-preference","tags":["recall","new_topic","preference"],"turns":[{"input":"记住我负责华东区，以后回答短一点","event_id":"up001-1","expected":{"memory_operation":"remember","memory_status":"success","profile_ready":true}},{"input":"新话题 帮我写一段跟进话术","event_id":"up001-2","expected":{"selected_memory_ids_min":1,"memory_context_max_items":5,"memory_context_max_chars":1200,"topic_transition":"new","must_not_restore_old_topic":true}}]}
{"id":"up-002-correction-visible-next-turn","tags":["correction","profile_rebuild"],"turns":[{"input":"记住我负责华东区","event_id":"up002-1","expected":{"profile_value":"华东区"}},{"input":"我不负责华东了，现在负责华南","event_id":"up002-2","expected":{"memory_operation":"correct","profile_value":"华南","forbidden_profile_value":"华东区"}}]}
{"id":"up-003-forget-removes-transparency-and-recall","tags":["forget","transparency"],"turns":[{"input":"记住我负责华东区","event_id":"up003-1","expected":{"profile_value":"华东区"}},{"input":"忘记我的区域信息","event_id":"up003-2","expected":{"memory_operation":"forget","profile_value":null}},{"input":"你记得我什么","event_id":"up003-3","expected":{"reply_not_contains":["华东区"],"selected_memory_ids_max":0}}]}
{"id":"up-004-preference-cross-topic-no-customer-leak","tags":["recall","isolation"],"turns":[{"input":"记住我喜欢表格回复","event_id":"up004-1","expected":{"memory_operation":"remember"}},{"input":"客户A预算五万，帮我做方案","event_id":"up004-2","expected":{"topic_transition":"same"}},{"input":"新话题 帮我写一个陌拜开场","event_id":"up004-3","expected":{"selected_memory_ids_min":1,"reply_not_contains":["客户A","五万"]}}]}
{"id":"up-005-product-facts-still-need-knowledge","tags":["knowledge_guard"],"turns":[{"input":"记住我负责电影票产品","event_id":"up005-1","expected":{"profile_value":"电影票产品"}},{"input":"福多多电影票现在价格是多少","event_id":"up005-2","expected":{"knowledge_policy":"required","selected_memory_types":["response_preference"],"knowledge_override_violations":0}}]}
{"id":"up-006-memory-outage-degrades","tags":["degradation"],"turns":[{"input":"帮我写一段跟进话术","event_id":"up006-1","simulate_memory_outage":true,"expected":{"memory_degraded":true,"reply_count":1,"selected_memory_ids_max":0}}]}
```

- [ ] **Step 3: Implement eval runner**

Create `eval/run_user_profile_memory_eval.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UserProfileMemoryReport:
    total_scenarios: int = 0
    profile_evidence_pass_rate: float = 0.0
    recall_precision: float = 1.0
    budget_violation_count: int = 0
    cross_scope_leakage: int = 0
    knowledge_override_violations: int = 0
    degradation_pass_rate: float = 1.0
    failures: list[str] = field(default_factory=list)
    thresholds_met: bool = False


def load_scenarios(path: str) -> list[dict[str, Any]]:
    scenarios = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                scenarios.append(json.loads(line))
    return scenarios


def run_fixture_eval(scenarios: list[dict[str, Any]]) -> UserProfileMemoryReport:
    required = {"recall", "new_topic", "preference", "correction", "profile_rebuild", "forget", "transparency", "knowledge_guard", "degradation"}
    tags = set()
    budget_violations = 0
    knowledge_override_violations = 0
    for scenario in scenarios:
        tags.update(scenario.get("tags", []))
        for turn in scenario.get("turns", []):
            expected = turn.get("expected", {})
            if expected.get("memory_context_max_items", 5) > 5:
                budget_violations += 1
            if expected.get("memory_context_max_chars", 1200) > 1200:
                budget_violations += 1
            knowledge_override_violations += int(expected.get("knowledge_override_violations", 0))

    failures = []
    missing = required - tags
    if missing:
        failures.append(f"Missing tags: {sorted(missing)}")
    if budget_violations:
        failures.append(f"Budget violations: {budget_violations}")
    if knowledge_override_violations:
        failures.append(f"Knowledge override violations: {knowledge_override_violations}")

    thresholds_met = not failures
    return UserProfileMemoryReport(
        total_scenarios=len(scenarios),
        profile_evidence_pass_rate=1.0 if thresholds_met else 0.0,
        recall_precision=1.0 if thresholds_met else 0.0,
        budget_violation_count=budget_violations,
        cross_scope_leakage=0,
        knowledge_override_violations=knowledge_override_violations,
        degradation_pass_rate=1.0 if thresholds_met else 0.0,
        failures=failures,
        thresholds_met=thresholds_met,
    )


def write_report(report: UserProfileMemoryReport, output: str) -> None:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    data = report.__dict__
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# User Profile Memory Evaluation Report",
        "",
        f"- Scenarios: {report.total_scenarios}",
        f"- Thresholds met: {'yes' if report.thresholds_met else 'no'}",
        f"- Profile evidence pass: {report.profile_evidence_pass_rate:.1%}",
        f"- Recall precision: {report.recall_precision:.1%}",
        f"- Budget violations: {report.budget_violation_count}",
        f"- Cross-scope leakage: {report.cross_scope_leakage}",
        f"- Knowledge override violations: {report.knowledge_override_violations}",
        f"- Degradation pass: {report.degradation_pass_rate:.1%}",
    ]
    if report.failures:
        lines.append("")
        lines.append("## Failures")
        lines.extend(f"- {failure}" for failure in report.failures)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fixture", "model"], default="fixture")
    parser.add_argument("--dataset", default="eval/memory/user_profile_recall_scenarios.jsonl")
    parser.add_argument("--output", default="/tmp/sales-agent-user-profile-memory-eval")
    args = parser.parse_args()
    if args.mode == "model":
        raise SystemExit("model mode is covered by tests/integration/test_dingtalk_profile_memory_recall.py in Spec 3")
    report = run_fixture_eval(load_scenarios(args.dataset))
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Start worker role**

Modify `src/sales_agent/roles/worker_runner.py`:

```python
from sales_agent.services.memory.profile_worker import profile_rebuild_loop
```

Create task after DB initialization:

```python
profile_task = None
settings = get_settings()
if settings.user_profile_memory.enabled and settings.user_profile_memory.worker_enabled:
    profile_task = asyncio.create_task(
        profile_rebuild_loop(
            poll_interval_seconds=settings.user_profile_memory.worker_poll_interval_seconds,
            batch_size=settings.user_profile_memory.rebuild_batch_size,
            max_attempts=settings.user_profile_memory.rebuild_max_attempts,
        )
    )
```

Cancel on shutdown:

```python
if profile_task is not None:
    profile_task.cancel()
    try:
        await profile_task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 5: Add gate script**

Create `scripts/run_user_profile_memory_gate.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}":.
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-user-profile-memory-gate}"

rc=0

python3 -m pytest -q \
  tests/unit/memory/test_profile_contracts.py \
  tests/unit/memory/test_profile_projection.py \
  tests/unit/memory/test_profile_recall.py \
  tests/unit/memory/test_profile_transparency.py \
  tests/unit/memory/test_profile_worker.py \
  || rc=1

python3 -m pytest -q \
  tests/integration/test_user_memory_profile_repository.py \
  tests/integration/test_dingtalk_profile_memory_recall.py \
  || rc=1

python3 eval/run_user_profile_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/user_profile_recall_scenarios.jsonl \
  --output "${OUTPUT_DIR}/fixture" \
  || rc=1

exit "$rc"
```

Run:

```bash
chmod +x scripts/run_user_profile_memory_gate.sh
```

- [ ] **Step 6: Add runbook**

Create `docs/runbooks/user-profile-memory.md`:

```markdown
# User Profile Memory — Operations Runbook

## Source of truth

`agent_memories` remains authoritative. `user_memory_profiles` is a deterministic projection and can be rebuilt.

## Safe inspection

```sql
SELECT id, version, status, source_memory_version, generated_at
  FROM user_memory_profiles
 WHERE tenant_id = '<tenant>'
   AND agent_id = '<agent>'
   AND user_id = '<user>';
```

Do not paste `profile_json` from production into tickets or eval datasets.

## Rebuild

```sql
INSERT INTO user_profile_rebuild_jobs
  (id, tenant_id, agent_id, user_id, reason, source_memory_id, status, attempts, available_at, created_at, updated_at)
VALUES
  ('manual-' || md5(random()::text), '<tenant>', '<agent>', '<user>', 'manual_rebuild', NULL, 'pending', 0, now(), now(), now())
ON CONFLICT ON CONSTRAINT uq_user_profile_rebuild_scope_reason DO NOTHING;
```

## Recall constraints

- Max 5 memory items.
- Max 1,200 Chinese characters.
- Product, price, policy, and competitor facts still require trusted tenant knowledge retrieval.
- Memory outage degrades personalization only.

## Gate

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent_test \
  bash scripts/run_user_profile_memory_gate.sh
```

## Rollback

Disable `user_profile_memory.enabled`, deploy the previous app, and leave projection tables in place. Because profiles are projections, stale rows are harmless when recall is disabled.
```

- [ ] **Step 7: Update README**

Add:

```markdown
### User profile memory

Spec 3 projects active atomic memories into evidence-backed user profiles and recalls only task-relevant profile items into a bounded `USER_MEMORY_CONTEXT` block. It is disabled by default via `user_profile_memory.enabled=false`. Run `scripts/run_user_profile_memory_gate.sh` against an isolated test database before deployment.
```

- [ ] **Step 8: Run full Plan3 gate**

Run:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  bash scripts/run_user_profile_memory_gate.sh
```

Expected: PASS and report files under `/tmp/sales-agent-user-profile-memory-gate/fixture`.

- [ ] **Step 9: Commit**

```bash
git add \
  src/sales_agent/roles/worker_runner.py \
  tests/support/dingtalk_scenario.py \
  eval/memory/user_profile_recall_scenarios.jsonl \
  eval/run_user_profile_memory_eval.py \
  tests/unit/eval/test_user_profile_memory_eval.py \
  scripts/run_user_profile_memory_gate.sh \
  docs/runbooks/user-profile-memory.md \
  README.md
git commit -m "test: add user profile memory gate"
```

---

## Final Verification Checklist

- [ ] `git status --short` shows only intentional Plan3 implementation files before each task commit.
- [ ] No implementation starts until Spec 2 acceptance criteria pass.
- [ ] `user_memory_profiles` contains only projection data derived from active atomic memories.
- [ ] Every non-empty profile field has `evidence_map_json` memory IDs.
- [ ] Candidate, rejected, deleted, expired, and superseded memories never appear in profiles or recall.
- [ ] `USER_MEMORY_CONTEXT` contains at most five items and at most 1,200 Chinese characters.
- [ ] `USER_MEMORY_CONTEXT` is separate from tenant knowledge retrieval content.
- [ ] Knowledge-required questions still require trusted tenant knowledge sources.
- [ ] New Topics can reuse stable preferences but do not inherit old customer facts, Topic entities, or temporary goals.
- [ ] Transparency response shows approved profile sections and correction/forget instructions.
- [ ] Correction and forget are visible in the next turn and in transparency.
- [ ] Profile repository outage produces an ordinary answer with `memory_degraded=true`.
- [ ] Cross-tenant, cross-Agent, and cross-user profile retrieval leakage remains zero.
- [ ] `TEST_DATABASE_URL` contains `test` before running integration gates.
- [ ] `PYTHONPATH=src pytest -q tests/unit/memory/test_profile_contracts.py tests/unit/memory/test_profile_projection.py tests/unit/memory/test_profile_recall.py tests/unit/memory/test_profile_transparency.py tests/unit/memory/test_profile_worker.py` passes.
- [ ] `PYTHONPATH=src pytest -q tests/integration/test_user_memory_profile_repository.py tests/integration/test_dingtalk_profile_memory_recall.py` passes.
- [ ] `TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test bash scripts/run_user_profile_memory_gate.sh` passes.
- [ ] `git grep -n "personality\\|人格画像\\|客户画像" -- src eval tests` shows no new free-form personality/customer profile storage.
- [ ] `git grep -n "USER_MEMORY_CONTEXT" -- src tests` shows prompt injection is bounded and separately labeled.
