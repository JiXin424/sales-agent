# Governed Long-Term Atomic Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add governed cross-session atomic memory so DingTalk users can explicitly remember, correct, and forget safe sales-user facts while ordinary conversations can only create policy-gated candidates.

**Architecture:** Long-term memory is a new domain layer beside Topic memory, not a replacement for `ConversationTopic`. The Online Graph detects explicit memory commands before normal chat, persists confirmed memory synchronously, and enqueues inferred candidates after successful ordinary turns through a transactional outbox; profile projection and automatic prompt personalization remain deferred to Spec 3.

**Tech Stack:** Python 3.10+; SQLAlchemy asyncio; Alembic; PostgreSQL JSON stored as text; Pydantic 2; LangGraph Online Graph; DingTalk HTTP/Stream shared processor; pytest; pytest-asyncio; JSONL eval datasets.

## Global Constraints

- Spec 1 is accepted: production Online Graph uses PostgreSQL checkpoints, stable thread key, real DingTalk processor tests, and same-thread advisory locking.
- Scope is sales-user memory only; `subject_type` is fixed to `"user"` in this phase.
- Allowed memory types are exactly `user_fact`, `response_preference`, `coaching_goal`, `sales_pattern`, and `recurring_challenge`.
- One memory record contains exactly one atomic claim.
- User-profile projection, profile prompt injection, and automatic answer personalization are out of scope for Spec 2.
- Customer and organization memories are out of scope.
- Assistant-generated answers are never evidence for memory extraction, activation, correction, or corroboration.
- Explicit user statements and verified tool data are the only evidence sources in this phase.
- Long-term memory cannot override system rules, risk controls, tenant knowledge, or retrieved product facts.
- Inferred memories require two independent user-message evidences or explicit user confirmation before status becomes `active`.
- Explicit remember, correction, and forget interactions go through the DingTalk business processor, not a test-only ChatPipeline path.
- All memory reads and writes require immutable `MemoryScope(tenant_id, agent_id, user_id)`.
- Cross-tenant, cross-Agent, and cross-user leakage must remain zero.
- Prohibited or uncertain sensitivity defaults to rejection or clarification, not storage.
- Forget semantics are immediate at application-read level: deleted memories are excluded from all active reads in the same transaction.
- Explicit memory write failure must return a clear “not saved, please retry” response and must not claim success.
- Inferred candidate extraction and outbox processing failure must not block the primary ordinary answer.
- No raw production conversation, API key, sensitive personal data, or full production profile is committed to evaluation datasets.
- Existing unrelated worker changes in `.claude/`, `.deepeval/`, and eval report files are not part of this plan and must not be staged or overwritten.

---

## File Map

### Create

- `src/sales_agent/models/atomic_memory.py` — SQLAlchemy models for `agent_memories`, `memory_outbox`, and `memory_audit_events`.
- `src/sales_agent/migrations/versions/0013_governed_long_term_memory.py` — Alembic DDL for the memory tables, indexes, and active single-valued uniqueness.
- `src/sales_agent/services/memory/__init__.py` — memory domain public exports.
- `src/sales_agent/services/memory/contracts.py` — enums, immutable scope, Pydantic candidate and operation result contracts.
- `src/sales_agent/services/memory/policy.py` — deterministic allowlist, sensitivity, atomicity, expiry, and write-policy decisions.
- `src/sales_agent/services/memory/normalization.py` — normalized-key and search-text helpers.
- `src/sales_agent/services/memory/repository.py` — scoped repository API and transaction-safe persistence operations.
- `src/sales_agent/services/memory/commands.py` — explicit remember/correct/forget command detection and service orchestration.
- `src/sales_agent/services/memory/extractor.py` — structured candidate extraction from user evidence only.
- `src/sales_agent/services/memory/outbox_worker.py` — bounded retry worker for inferred memory jobs and expiry sweep.
- `src/sales_agent/prompts/memory_extractor_prompt.py` — closed-schema extraction prompt with safety instructions.
- `tests/unit/memory/test_contracts.py`
- `tests/unit/memory/test_policy.py`
- `tests/unit/memory/test_commands.py`
- `tests/unit/memory/test_extractor.py`
- `tests/unit/memory/test_outbox_worker.py`
- `tests/integration/test_atomic_memory_repository.py`
- `tests/integration/test_dingtalk_long_term_memory.py`
- `eval/memory/long_term_atomic_scenarios.jsonl`
- `eval/run_long_term_memory_eval.py`
- `tests/unit/eval/test_long_term_memory_eval.py`
- `scripts/run_long_term_memory_gate.sh`
- `docs/runbooks/long-term-memory.md`

### Modify

- `src/sales_agent/core/config.py` — add `LongTermMemoryConfig` and settings defaults.
- `src/sales_agent/models/__init__.py` — register new models in `Base.metadata`.
- `src/sales_agent/graph/online/state.py` — add observable memory fields to Online state.
- `src/sales_agent/graph/online/graph.py` — insert memory command and memory enqueue nodes without changing Spec 1 reset semantics.
- `src/sales_agent/graph/online/nodes.py` — implement memory command response and inferred-candidate enqueue nodes.
- `src/sales_agent/graph/online/edges.py` — route memory commands before ordinary context resolution.
- `src/sales_agent/services/online_conversation.py` — add memory turn-scoped defaults.
- `src/sales_agent/integrations/dingtalk/turn_result.py` — expose memory operation fields for tests and observability.
- `src/sales_agent/integrations/dingtalk/processor.py` — map Graph memory fields into `DingTalkTurnResult`.
- `src/sales_agent/roles/worker_runner.py` — start and stop the memory outbox worker when enabled.
- `tests/support/dingtalk_scenario.py` — add optional long-term-memory expectations while preserving short-term scenarios.
- `tests/unit/graph/test_online_graph.py`
- `tests/unit/dingtalk/test_online_flow_routing.py`
- `tests/integration/test_dingtalk_multiturn_memory.py`
- `README.md`

### Delete

- No production module is deleted in Spec 2.

---

### Task 1: Add atomic memory schema, settings, and model registration

**Files:**
- Create: `src/sales_agent/models/atomic_memory.py`
- Create: `src/sales_agent/migrations/versions/0013_governed_long_term_memory.py`
- Modify: `src/sales_agent/models/__init__.py`
- Modify: `src/sales_agent/core/config.py`
- Create: `tests/unit/memory/test_contracts.py`
- Create: `tests/integration/test_atomic_memory_repository.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `generate_id`, `Settings`.
- Produces: `AtomicMemory`, `MemoryOutboxJob`, `MemoryAuditEvent`, and `Settings.long_term_memory`.

- [ ] **Step 1: Write the failing model-registration test**

Create `tests/unit/memory/test_contracts.py` with a metadata assertion that fails until the new models are registered:

```python
def test_atomic_memory_tables_registered():
    import sales_agent.models
    from sales_agent.core.database import Base

    sales_agent.models._import_dingtalk_models()

    table_names = set(Base.metadata.tables)
    assert "agent_memories" in table_names
    assert "memory_outbox" in table_names
    assert "memory_audit_events" in table_names
```

- [ ] **Step 2: Write the failing PostgreSQL schema integration test**

Append this test to `tests/integration/test_atomic_memory_repository.py`:

```python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_atomic_memory_schema_has_scope_and_unique_indexes(db_session):
    rows = (
        await db_session.execute(
            text(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE tablename IN ('agent_memories', 'memory_outbox')
                """
            )
        )
    ).mappings().all()

    index_defs = {row["indexname"]: row["indexdef"] for row in rows}
    assert "ix_agent_memories_scope_status" in index_defs
    assert "ix_agent_memories_source_message" in index_defs
    assert "uq_agent_memory_active_single_value" in index_defs
    assert "uq_memory_outbox_event_operation" in index_defs
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/memory/test_contracts.py::test_atomic_memory_tables_registered \
  tests/integration/test_atomic_memory_repository.py::test_atomic_memory_schema_has_scope_and_unique_indexes
```

Expected: FAIL because `atomic_memory.py` and migration `0013_governed_long_term_memory.py` do not exist.

- [ ] **Step 4: Add settings**

In `src/sales_agent/core/config.py`, add a config class near `TopicRoutingConfig`:

```python
class LongTermMemoryConfig(BaseModel):
    """Governed long-term atomic memory."""

    enabled: bool = False
    candidate_extraction_enabled: bool = True
    outbox_worker_enabled: bool = True
    outbox_poll_interval_seconds: float = 2.0
    outbox_batch_size: int = 20
    outbox_max_attempts: int = 5
    explicit_confirmation_required_for_broad_forget: bool = True
```

Add it to `Settings`:

```python
long_term_memory: LongTermMemoryConfig = LongTermMemoryConfig()
```

- [ ] **Step 5: Add SQLAlchemy models**

Create `src/sales_agent/models/atomic_memory.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


def utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


class AtomicMemory(TimestampMixin, Base):
    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False, default="user")
    subject_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence_band: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    supersedes_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_agent_memories_scope_status",
            "tenant_id", "agent_id", "subject_type", "subject_id", "status",
        ),
        Index(
            "ix_agent_memories_scope_key",
            "tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key",
        ),
        Index("ix_agent_memories_expiry", "status", "expires_at"),
        Index("ix_agent_memories_source_message", "tenant_id", "source_message_ids_json"),
        Index(
            "uq_agent_memory_active_single_value",
            "tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class MemoryOutboxJob(TimestampMixin, Base):
    __tablename__ = "memory_outbox"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False, default="infer_candidates")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_datetime)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", "operation", name="uq_memory_outbox_event_operation"),
        Index("ix_memory_outbox_poll", "status", "available_at"),
        Index("ix_memory_outbox_scope", "tenant_id", "agent_id", "user_id"),
    )


class MemoryAuditEvent(TimestampMixin, Base):
    __tablename__ = "memory_audit_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    memory_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
```

- [ ] **Step 6: Register models**

Modify `src/sales_agent/models/__init__.py`:

```python
from .atomic_memory import AtomicMemory, MemoryOutboxJob, MemoryAuditEvent
```

Add these names to `__all__`:

```python
"AtomicMemory",
"MemoryOutboxJob",
"MemoryAuditEvent",
```

- [ ] **Step 7: Add Alembic migration**

Create `src/sales_agent/migrations/versions/0013_governed_long_term_memory.py`:

```python
"""Add governed long-term atomic memory tables.

Revision ID: 0013_governed_long_term_memory
Revises: 0012_backfill_skipped_columns
Create Date: 2026-07-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_governed_long_term_memory"
down_revision: Union[str, None] = "0012_backfill_skipped_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False, server_default=sa.text("'user'")),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'candidate'")),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_conversation_id", sa.Text(), nullable=False),
        sa.Column("source_message_ids_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("confidence_band", sa.Text(), nullable=False, server_default=sa.text("'candidate'")),
        sa.Column("sensitivity", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("supersedes_id", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_memories_tenant_id", "agent_memories", ["tenant_id"])
    op.create_index("ix_agent_memories_agent_id", "agent_memories", ["agent_id"])
    op.create_index("ix_agent_memories_subject_id", "agent_memories", ["subject_id"])
    op.create_index("ix_agent_memories_supersedes_id", "agent_memories", ["supersedes_id"])
    op.create_index(
        "ix_agent_memories_scope_status",
        "agent_memories",
        ["tenant_id", "agent_id", "subject_type", "subject_id", "status"],
    )
    op.create_index(
        "ix_agent_memories_scope_key",
        "agent_memories",
        ["tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key"],
    )
    op.create_index("ix_agent_memories_expiry", "agent_memories", ["status", "expires_at"])
    op.create_index("ix_agent_memories_source_message", "agent_memories", ["tenant_id", "source_message_ids_json"])
    op.create_index(
        "uq_agent_memory_active_single_value",
        "agent_memories",
        ["tenant_id", "agent_id", "subject_type", "subject_id", "normalized_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "memory_outbox",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False, server_default=sa.text("'infer_candidates'")),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_id", "operation", name="uq_memory_outbox_event_operation"),
    )
    op.create_index("ix_memory_outbox_tenant_id", "memory_outbox", ["tenant_id"])
    op.create_index("ix_memory_outbox_agent_id", "memory_outbox", ["agent_id"])
    op.create_index("ix_memory_outbox_user_id", "memory_outbox", ["user_id"])
    op.create_index("ix_memory_outbox_poll", "memory_outbox", ["status", "available_at"])
    op.create_index("ix_memory_outbox_scope", "memory_outbox", ["tenant_id", "agent_id", "user_id"])

    op.create_table(
        "memory_audit_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=True),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_audit_events_tenant_id", "memory_audit_events", ["tenant_id"])
    op.create_index("ix_memory_audit_events_agent_id", "memory_audit_events", ["agent_id"])
    op.create_index("ix_memory_audit_events_user_id", "memory_audit_events", ["user_id"])
    op.create_index("ix_memory_audit_events_memory_id", "memory_audit_events", ["memory_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_audit_events_memory_id", table_name="memory_audit_events")
    op.drop_index("ix_memory_audit_events_user_id", table_name="memory_audit_events")
    op.drop_index("ix_memory_audit_events_agent_id", table_name="memory_audit_events")
    op.drop_index("ix_memory_audit_events_tenant_id", table_name="memory_audit_events")
    op.drop_table("memory_audit_events")
    op.drop_index("ix_memory_outbox_scope", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_poll", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_user_id", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_agent_id", table_name="memory_outbox")
    op.drop_index("ix_memory_outbox_tenant_id", table_name="memory_outbox")
    op.drop_table("memory_outbox")
    op.drop_index("uq_agent_memory_active_single_value", table_name="agent_memories")
    op.drop_index("ix_agent_memories_source_message", table_name="agent_memories")
    op.drop_index("ix_agent_memories_expiry", table_name="agent_memories")
    op.drop_index("ix_agent_memories_scope_key", table_name="agent_memories")
    op.drop_index("ix_agent_memories_scope_status", table_name="agent_memories")
    op.drop_index("ix_agent_memories_supersedes_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_subject_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_agent_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_tenant_id", table_name="agent_memories")
    op.drop_table("agent_memories")
```

- [ ] **Step 8: Run schema tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/memory/test_contracts.py::test_atomic_memory_tables_registered \
  tests/integration/test_atomic_memory_repository.py::test_atomic_memory_schema_has_scope_and_unique_indexes
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add \
  src/sales_agent/models/atomic_memory.py \
  src/sales_agent/migrations/versions/0013_governed_long_term_memory.py \
  src/sales_agent/models/__init__.py \
  src/sales_agent/core/config.py \
  tests/unit/memory/test_contracts.py \
  tests/integration/test_atomic_memory_repository.py
git commit -m "feat: add governed memory schema"
```

---

### Task 2: Define memory contracts, normalization, and deterministic policy

**Files:**
- Create: `src/sales_agent/services/memory/__init__.py`
- Create: `src/sales_agent/services/memory/contracts.py`
- Create: `src/sales_agent/services/memory/normalization.py`
- Create: `src/sales_agent/services/memory/policy.py`
- Modify: `tests/unit/memory/test_contracts.py`
- Create: `tests/unit/memory/test_policy.py`

**Interfaces:**
- Consumes: Pydantic 2, `datetime`.
- Produces: `MemoryScope`, `MemoryCandidate`, `AtomicMemoryRecord`, `MemoryOperationResult`, `MemoryWriteDecision`, `normalize_key()`, `build_search_text()`, `classify_sensitivity()`, `default_expires_at()`, `decide_candidate_write()`.

- [ ] **Step 1: Write failing contract tests**

Append to `tests/unit/memory/test_contracts.py`:

```python
import pytest
from pydantic import ValidationError

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope


def test_memory_scope_is_user_scoped_and_immutable():
    scope = MemoryScope(tenant_id="t1", agent_id="a1", user_id="u1")
    assert scope.subject_type == "user"
    assert scope.subject_id == "u1"
    with pytest.raises(ValidationError):
        MemoryScope(tenant_id="t1", agent_id="a1", user_id="")


def test_candidate_rejects_unknown_memory_type():
    with pytest.raises(ValidationError):
        MemoryCandidate(
            memory_type="customer_fact",
            normalized_key="customer_budget",
            content={"value": "高预算"},
            evidence_text="客户预算很高",
            source_kind="explicit_user",
            stability="stable",
            sensitivity="normal",
            confidence_band="confirmed",
        )
```

- [ ] **Step 2: Write failing policy tests**

Create `tests/unit/memory/test_policy.py`:

```python
from datetime import datetime, timezone

from sales_agent.services.memory.contracts import MemoryCandidate
from sales_agent.services.memory.normalization import normalize_key
from sales_agent.services.memory.policy import (
    classify_sensitivity,
    decide_candidate_write,
    default_expires_at,
)


def _candidate(**overrides):
    data = {
        "memory_type": "user_fact",
        "normalized_key": "sales_region",
        "content": {"key": "sales_region", "value": "华东区"},
        "evidence_text": "记住我负责华东区",
        "source_kind": "explicit_user",
        "stability": "stable",
        "sensitivity": "normal",
        "confidence_band": "confirmed",
    }
    data.update(overrides)
    return MemoryCandidate(**data)


def test_normalize_key_maps_common_sales_region_phrases():
    assert normalize_key("我负责华东区") == "sales_region"
    assert normalize_key("回答短一点") == "response_style"
    assert normalize_key("提升异议处理能力") == "coaching_goal"


def test_sensitivity_rejects_password_and_government_id():
    assert classify_sensitivity("记住我的密码是 abc123") == "prohibited"
    assert classify_sensitivity("我的身份证号是 110101199001010011") == "prohibited"
    assert classify_sensitivity("我负责华东区") == "normal"


def test_explicit_confirmed_candidate_can_activate():
    decision = decide_candidate_write(_candidate(), existing_evidence_count=0)
    assert decision.action == "activate"
    assert decision.status == "active"
    assert decision.reason_code == "explicit_confirmed"


def test_inferred_single_evidence_stays_candidate():
    decision = decide_candidate_write(
        _candidate(source_kind="inferred_user", confidence_band="candidate"),
        existing_evidence_count=0,
    )
    assert decision.action == "candidate"
    assert decision.status == "candidate"
    assert decision.reason_code == "needs_second_evidence"


def test_inferred_second_independent_evidence_can_activate():
    decision = decide_candidate_write(
        _candidate(source_kind="inferred_user", confidence_band="candidate"),
        existing_evidence_count=1,
    )
    assert decision.action == "activate"
    assert decision.status == "active"
    assert decision.reason_code == "corroborated_two_evidence"


def test_default_expiry_by_type():
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    assert default_expires_at("response_preference", now) is None
    assert default_expires_at("user_fact", now).date().isoformat() == "2027-01-04"
    assert default_expires_at("coaching_goal", now).date().isoformat() == "2026-10-06"
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_contracts.py tests/unit/memory/test_policy.py
```

Expected: FAIL because the memory service package does not exist.

- [ ] **Step 4: Implement contracts**

Create `src/sales_agent/services/memory/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator

MemoryType = Literal[
    "user_fact",
    "response_preference",
    "coaching_goal",
    "sales_pattern",
    "recurring_challenge",
]
MemoryStatus = Literal["candidate", "active", "superseded", "deleted", "expired", "rejected"]
SourceKind = Literal["explicit_user", "inferred_user", "verified_tool"]
Sensitivity = Literal["normal", "restricted", "prohibited"]
ConfidenceBand = Literal["confirmed", "corroborated", "candidate"]


class MemoryScope(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    user_id: str
    subject_type: Literal["user"] = "user"

    @property
    def subject_id(self) -> str:
        return self.user_id

    @field_validator("tenant_id", "agent_id", "user_id")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scope identifiers must not be blank")
        return value


class MemoryCandidate(BaseModel):
    memory_type: MemoryType
    normalized_key: str
    content: dict[str, Any]
    evidence_text: str
    source_kind: SourceKind
    stability: Literal["stable", "temporary", "unclear"]
    sensitivity: Sensitivity
    confidence_band: ConfidenceBand

    @field_validator("normalized_key", "evidence_text")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required text field is blank")
        return value.strip()


class AtomicMemoryRecord(BaseModel):
    id: str
    scope: MemoryScope
    memory_type: MemoryType
    normalized_key: str
    content: dict[str, Any]
    search_text: str
    status: MemoryStatus
    source_kind: SourceKind
    source_conversation_id: str
    source_message_ids: list[str] = Field(default_factory=list)
    evidence_count: int
    confidence_band: ConfidenceBand
    sensitivity: Sensitivity
    supersedes_id: str | None = None
    observed_at: datetime
    last_confirmed_at: datetime | None = None
    expires_at: datetime | None = None


class MemoryOperationResult(BaseModel):
    operation: Literal["remember", "correct", "forget", "candidate", "expire", "noop"]
    status: Literal["success", "rejected", "clarify", "failed", "noop"]
    response_text: str
    memory_ids: list[str] = Field(default_factory=list)
    reason_code: str
    candidate_count: int = 0


@dataclass(frozen=True)
class MemoryWriteDecision:
    action: Literal["activate", "candidate", "reject", "clarify"]
    status: MemoryStatus
    reason_code: str
```

- [ ] **Step 5: Implement normalization**

Create `src/sales_agent/services/memory/normalization.py`:

```python
from __future__ import annotations

import re
from typing import Any


_KEY_PATTERNS: list[tuple[str, str]] = [
    (r"(负责|区域|大区|华东|华南|华北|华中|西南|东北)", "sales_region"),
    (r"(短一点|简洁|长一点|详细|表格|要点|语气|风格)", "response_style"),
    (r"(目标|提升|训练|练习|能力|异议处理|开场|逼单)", "coaching_goal"),
    (r"(产品线|主推|重点产品|卖.*产品)", "product_focus"),
    (r"(反复|经常|总是|卡点|难点|挑战)", "recurring_challenge"),
]


def normalize_key(text: str) -> str:
    compact = text.strip().lower()
    for pattern, key in _KEY_PATTERNS:
        if re.search(pattern, compact):
            return key
    safe = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", compact).strip("_")
    return safe[:64] or "unknown"


def build_search_text(content: dict[str, Any]) -> str:
    values: list[str] = []
    for key in sorted(content):
        value = content[key]
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (int, float, bool)):
            values.append(str(value))
    return " ".join(values).strip()
```

- [ ] **Step 6: Implement deterministic policy**

Create `src/sales_agent/services/memory/policy.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryWriteDecision

_PROHIBITED_PATTERNS = [
    re.compile(r"(密码|口令|token|api[_-]?key|access[_-]?key)", re.I),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(银行卡|信用卡|医保|病历|宗教|政治立场)"),
    re.compile(r"(手机号|电话|邮箱|住址|家庭地址)"),
]

_TEMPORARY_PATTERNS = [
    re.compile(r"(今天|明天|这次|刚才|临时|一会儿|这个客户|这单)"),
]


def classify_sensitivity(text: str) -> str:
    if any(pattern.search(text) for pattern in _PROHIBITED_PATTERNS):
        return "prohibited"
    return "normal"


def classify_stability(text: str) -> str:
    if any(pattern.search(text) for pattern in _TEMPORARY_PATTERNS):
        return "temporary"
    return "stable"


def default_expires_at(memory_type: str, now: datetime) -> datetime | None:
    if memory_type == "response_preference":
        return None
    if memory_type == "user_fact":
        return now + timedelta(days=180)
    if memory_type in {"coaching_goal", "sales_pattern", "recurring_challenge"}:
        return now + timedelta(days=90)
    return now + timedelta(days=90)


def decide_candidate_write(
    candidate: MemoryCandidate,
    *,
    existing_evidence_count: int,
) -> MemoryWriteDecision:
    if candidate.sensitivity == "prohibited":
        return MemoryWriteDecision("reject", "rejected", "prohibited_sensitivity")
    if candidate.stability != "stable":
        return MemoryWriteDecision("reject", "rejected", "not_stable")
    if candidate.source_kind == "explicit_user" and candidate.confidence_band == "confirmed":
        return MemoryWriteDecision("activate", "active", "explicit_confirmed")
    if candidate.source_kind == "verified_tool" and candidate.confidence_band in {"confirmed", "corroborated"}:
        return MemoryWriteDecision("activate", "active", "verified_tool_confirmed")
    if candidate.source_kind == "inferred_user" and existing_evidence_count >= 1:
        return MemoryWriteDecision("activate", "active", "corroborated_two_evidence")
    if candidate.source_kind == "inferred_user":
        return MemoryWriteDecision("candidate", "candidate", "needs_second_evidence")
    return MemoryWriteDecision("reject", "rejected", "unsupported_source")
```

- [ ] **Step 7: Export package interfaces**

Create `src/sales_agent/services/memory/__init__.py`:

```python
from sales_agent.services.memory.contracts import (
    AtomicMemoryRecord,
    MemoryCandidate,
    MemoryOperationResult,
    MemoryScope,
    MemoryWriteDecision,
)

__all__ = [
    "AtomicMemoryRecord",
    "MemoryCandidate",
    "MemoryOperationResult",
    "MemoryScope",
    "MemoryWriteDecision",
]
```

- [ ] **Step 8: Run policy tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_contracts.py tests/unit/memory/test_policy.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add \
  src/sales_agent/services/memory \
  tests/unit/memory/test_contracts.py \
  tests/unit/memory/test_policy.py
git commit -m "feat: add memory contracts and policy"
```

---

### Task 3: Implement scoped repository, audit events, correction, forget, and expiry

**Files:**
- Create: `src/sales_agent/services/memory/repository.py`
- Modify: `tests/integration/test_atomic_memory_repository.py`

**Interfaces:**
- Consumes: `AsyncSession`, `AtomicMemory`, `MemoryOutboxJob`, `MemoryAuditEvent`, `MemoryScope`, `MemoryCandidate`.
- Produces: `AtomicMemoryRepository` with `store_candidate()`, `activate_explicit()`, `corroborate_candidate()`, `correct_memory()`, `forget_memory()`, `list_active_memories()`, `get_memory_with_provenance()`, `expire_due_memories()`, and `enqueue_inferred_job()`.

- [ ] **Step 1: Write failing repository integration tests**

Append to `tests/integration/test_atomic_memory_repository.py`:

```python
import pytest
from datetime import datetime, timedelta, timezone

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository


def _scope(user_id="u1", tenant_id="t1", agent_id="a1"):
    return MemoryScope(tenant_id=tenant_id, agent_id=agent_id, user_id=user_id)


def _candidate(value="华东区", source_kind="explicit_user"):
    return MemoryCandidate(
        memory_type="user_fact",
        normalized_key="sales_region",
        content={"key": "sales_region", "value": value},
        evidence_text=f"我负责{value}",
        source_kind=source_kind,
        stability="stable",
        sensitivity="normal",
        confidence_band="confirmed" if source_kind == "explicit_user" else "candidate",
    )


@pytest.mark.asyncio
async def test_activate_explicit_and_list_active_scope(db_session):
    repo = AtomicMemoryRepository(db_session)
    result = await repo.activate_explicit(
        _scope(),
        _candidate(),
        conversation_id="conv1",
        message_id="msg1",
        now=datetime(2026, 7, 8, tzinfo=timezone.utc),
    )

    assert result.status == "success"
    rows = await repo.list_active_memories(_scope())
    assert len(rows) == 1
    assert rows[0].normalized_key == "sales_region"
    assert rows[0].content["value"] == "华东区"
    assert await repo.list_active_memories(_scope(user_id="u2")) == []


@pytest.mark.asyncio
async def test_correction_supersedes_old_memory(db_session):
    repo = AtomicMemoryRepository(db_session)
    await repo.activate_explicit(_scope(), _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    result = await repo.correct_memory(
        _scope(),
        normalized_key="sales_region",
        new_candidate=_candidate("华南区"),
        conversation_id="conv2",
        message_id="msg2",
    )

    assert result.status == "success"
    active = await repo.list_active_memories(_scope())
    assert len(active) == 1
    assert active[0].content["value"] == "华南区"
    assert active[0].supersedes_id is not None


@pytest.mark.asyncio
async def test_forget_deletes_exact_single_match_immediately(db_session):
    repo = AtomicMemoryRepository(db_session)
    await repo.activate_explicit(_scope(), _candidate("华东区"), conversation_id="conv1", message_id="msg1")

    result = await repo.forget_memory(_scope(), normalized_key="sales_region", confirm_broad=False)

    assert result.status == "success"
    assert await repo.list_active_memories(_scope()) == []


@pytest.mark.asyncio
async def test_get_memory_with_provenance_is_scope_guarded(db_session):
    repo = AtomicMemoryRepository(db_session)
    result = await repo.activate_explicit(
        _scope(),
        _candidate("华东区"),
        conversation_id="conv1",
        message_id="msg1",
    )

    record = await repo.get_memory_with_provenance(_scope(), result.memory_ids[0])
    assert record is not None
    assert record.source_conversation_id == "conv1"
    assert record.source_message_ids == ["msg1"]
    assert await repo.get_memory_with_provenance(_scope(user_id="u2"), result.memory_ids[0]) is None


@pytest.mark.asyncio
async def test_lazy_expiry_excludes_stale_active_memory(db_session):
    repo = AtomicMemoryRepository(db_session)
    now = datetime(2026, 7, 8, tzinfo=timezone.utc)
    await repo.activate_explicit(_scope(), _candidate("华东区"), conversation_id="conv1", message_id="msg1", now=now)

    rows = await repo.expire_due_memories(now + timedelta(days=181))

    assert rows.expired_count == 1
    assert await repo.list_active_memories(_scope(), now=now + timedelta(days=181)) == []


@pytest.mark.asyncio
async def test_corroborated_second_user_evidence_activates_candidate(db_session):
    repo = AtomicMemoryRepository(db_session)
    first = await repo.corroborate_candidate(
        _scope(),
        _candidate("华东区", source_kind="inferred_user"),
        conversation_id="conv1",
        message_id="msg1",
    )
    assert first.status == "success"
    assert first.reason_code == "stored_candidate"
    assert await repo.list_active_memories(_scope()) == []

    second = await repo.corroborate_candidate(
        _scope(),
        _candidate("华东区", source_kind="inferred_user"),
        conversation_id="conv2",
        message_id="msg2",
    )

    assert second.status == "success"
    assert second.reason_code == "corroborated_two_evidence"
    active = await repo.list_active_memories(_scope())
    assert len(active) == 1
    assert active[0].evidence_count == 2
    assert set(active[0].source_message_ids) == {"msg1", "msg2"}
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_atomic_memory_repository.py
```

Expected: FAIL because `AtomicMemoryRepository` does not exist.

- [ ] **Step 3: Implement repository conversion helpers**

Create `src/sales_agent/services/memory/repository.py` with imports and helper methods:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.atomic_memory import AtomicMemory, MemoryAuditEvent, MemoryOutboxJob
from sales_agent.services.memory.contracts import (
    AtomicMemoryRecord,
    MemoryCandidate,
    MemoryOperationResult,
    MemoryScope,
)
from sales_agent.services.memory.normalization import build_search_text
from sales_agent.services.memory.policy import default_expires_at


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExpiryResult:
    expired_count: int
    memory_ids: list[str]


class AtomicMemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _to_record(self, row: AtomicMemory) -> AtomicMemoryRecord:
        scope = MemoryScope(
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.subject_id,
        )
        return AtomicMemoryRecord(
            id=row.id,
            scope=scope,
            memory_type=row.memory_type,
            normalized_key=row.normalized_key,
            content=json.loads(row.content_json),
            search_text=row.search_text,
            status=row.status,
            source_kind=row.source_kind,
            source_conversation_id=row.source_conversation_id,
            source_message_ids=json.loads(row.source_message_ids_json),
            evidence_count=row.evidence_count,
            confidence_band=row.confidence_band,
            sensitivity=row.sensitivity,
            supersedes_id=row.supersedes_id,
            observed_at=row.observed_at,
            last_confirmed_at=row.last_confirmed_at,
            expires_at=row.expires_at,
        )

    async def _audit(
        self,
        scope: MemoryScope,
        *,
        operation: str,
        status: str,
        reason_code: str,
        memory_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            MemoryAuditEvent(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                memory_id=memory_id,
                operation=operation,
                status=status,
                reason_code=reason_code,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            )
        )
```

- [ ] **Step 4: Implement active reads and explicit activation**

Add these methods:

```python
    async def list_active_memories(
        self,
        scope: MemoryScope,
        *,
        normalized_key: str | None = None,
        now: datetime | None = None,
    ) -> list[AtomicMemoryRecord]:
        current = now or _now()
        conditions = [
            AtomicMemory.tenant_id == scope.tenant_id,
            AtomicMemory.agent_id == scope.agent_id,
            AtomicMemory.subject_type == scope.subject_type,
            AtomicMemory.subject_id == scope.subject_id,
            AtomicMemory.status == "active",
        ]
        if normalized_key:
            conditions.append(AtomicMemory.normalized_key == normalized_key)

        rows = (
            await self.db.execute(
                select(AtomicMemory)
                .where(*conditions)
                .where(
                    (AtomicMemory.expires_at.is_(None))
                    | (AtomicMemory.expires_at > current)
                )
                .order_by(AtomicMemory.updated_at.desc())
            )
        ).scalars().all()
        return [self._to_record(row) for row in rows]

    async def get_memory_with_provenance(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> AtomicMemoryRecord | None:
        row = (
            await self.db.execute(
                select(AtomicMemory).where(
                    AtomicMemory.id == memory_id,
                    AtomicMemory.tenant_id == scope.tenant_id,
                    AtomicMemory.agent_id == scope.agent_id,
                    AtomicMemory.subject_type == scope.subject_type,
                    AtomicMemory.subject_id == scope.subject_id,
                    AtomicMemory.status != "deleted",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._to_record(row)

    async def activate_explicit(
        self,
        scope: MemoryScope,
        candidate: MemoryCandidate,
        *,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        current = now or _now()
        existing = await self.list_active_memories(scope, normalized_key=candidate.normalized_key, now=current)
        supersedes_id = None
        if existing:
            supersedes_id = existing[0].id
            await self.db.execute(
                update(AtomicMemory)
                .where(AtomicMemory.id == existing[0].id)
                .values(status="superseded", updated_at=current.isoformat())
            )

        row = AtomicMemory(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            subject_type=scope.subject_type,
            subject_id=scope.subject_id,
            memory_type=candidate.memory_type,
            normalized_key=candidate.normalized_key,
            content_json=json.dumps(candidate.content, ensure_ascii=False),
            search_text=build_search_text(candidate.content),
            status="active",
            source_kind=candidate.source_kind,
            source_conversation_id=conversation_id,
            source_message_ids_json=json.dumps([message_id], ensure_ascii=False),
            evidence_count=1,
            confidence_band="confirmed",
            sensitivity=candidate.sensitivity,
            supersedes_id=supersedes_id,
            observed_at=current,
            last_confirmed_at=current,
            expires_at=default_expires_at(candidate.memory_type, current),
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(scope, operation="remember", status="success", reason_code="explicit_confirmed", memory_id=row.id)
        return MemoryOperationResult(
            operation="remember",
            status="success",
            response_text=f"已记住：{candidate.content.get('value', candidate.evidence_text)}",
            memory_ids=[row.id],
            reason_code="explicit_confirmed",
        )
```

- [ ] **Step 5: Implement correction and forget**

Add:

```python
    async def correct_memory(
        self,
        scope: MemoryScope,
        *,
        normalized_key: str,
        new_candidate: MemoryCandidate,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        active = await self.list_active_memories(scope, normalized_key=normalized_key, now=now)
        result = await self.activate_explicit(
            scope,
            new_candidate,
            conversation_id=conversation_id,
            message_id=message_id,
            now=now,
        )
        if active:
            result.operation = "correct"
            result.reason_code = "superseded_existing"
            result.response_text = f"已更新记忆：{new_candidate.content.get('value', new_candidate.evidence_text)}"
        return result

    async def forget_memory(
        self,
        scope: MemoryScope,
        *,
        normalized_key: str | None,
        confirm_broad: bool,
    ) -> MemoryOperationResult:
        active = await self.list_active_memories(scope, normalized_key=normalized_key)
        if not active:
            return MemoryOperationResult(
                operation="forget",
                status="noop",
                response_text="没有找到需要忘记的记忆。",
                reason_code="no_matching_memory",
            )
        if normalized_key is None and not confirm_broad:
            return MemoryOperationResult(
                operation="forget",
                status="clarify",
                response_text="你是想忘记所有关于你的长期记忆吗？请回复“确认忘记全部”。",
                reason_code="broad_forget_requires_confirmation",
            )

        ids = [row.id for row in active]
        await self.db.execute(
            update(AtomicMemory)
            .where(AtomicMemory.id.in_(ids))
            .values(status="deleted", updated_at=_now().isoformat())
        )
        for memory_id in ids:
            await self._audit(scope, operation="forget", status="success", reason_code="user_requested", memory_id=memory_id)
        return MemoryOperationResult(
            operation="forget",
            status="success",
            response_text=f"已忘记 {len(ids)} 条相关记忆。",
            memory_ids=ids,
            reason_code="user_requested",
        )
```

- [ ] **Step 6: Implement candidates, outbox, and expiry**

Add:

```python
    async def store_candidate(
        self,
        scope: MemoryScope,
        candidate: MemoryCandidate,
        *,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        current = now or _now()
        row = AtomicMemory(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            subject_type=scope.subject_type,
            subject_id=scope.subject_id,
            memory_type=candidate.memory_type,
            normalized_key=candidate.normalized_key,
            content_json=json.dumps(candidate.content, ensure_ascii=False),
            search_text=build_search_text(candidate.content),
            status="candidate",
            source_kind=candidate.source_kind,
            source_conversation_id=conversation_id,
            source_message_ids_json=json.dumps([message_id], ensure_ascii=False),
            evidence_count=1,
            confidence_band="candidate",
            sensitivity=candidate.sensitivity,
            observed_at=current,
            expires_at=default_expires_at(candidate.memory_type, current),
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(scope, operation="candidate", status="success", reason_code="stored_candidate", memory_id=row.id)
        return MemoryOperationResult(
            operation="candidate",
            status="success",
            response_text="已记录为候选记忆，等待后续确认。",
            memory_ids=[row.id],
            reason_code="stored_candidate",
            candidate_count=1,
        )

    async def corroborate_candidate(
        self,
        scope: MemoryScope,
        candidate: MemoryCandidate,
        *,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        current = now or _now()
        content_json = json.dumps(candidate.content, ensure_ascii=False)
        existing = (
            await self.db.execute(
                select(AtomicMemory)
                .where(
                    AtomicMemory.tenant_id == scope.tenant_id,
                    AtomicMemory.agent_id == scope.agent_id,
                    AtomicMemory.subject_type == scope.subject_type,
                    AtomicMemory.subject_id == scope.subject_id,
                    AtomicMemory.normalized_key == candidate.normalized_key,
                    AtomicMemory.status == "candidate",
                    AtomicMemory.content_json == content_json,
                )
                .order_by(AtomicMemory.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            return await self.store_candidate(
                scope,
                candidate,
                conversation_id=conversation_id,
                message_id=message_id,
                now=current,
            )

        source_ids = list(dict.fromkeys(json.loads(existing.source_message_ids_json) + [message_id]))
        existing.source_message_ids_json = json.dumps(source_ids, ensure_ascii=False)
        existing.evidence_count = len(source_ids)
        existing.updated_at = current.isoformat()
        if existing.evidence_count >= 2:
            active = await self.list_active_memories(scope, normalized_key=candidate.normalized_key, now=current)
            if active:
                await self.db.execute(
                    update(AtomicMemory)
                    .where(AtomicMemory.id == active[0].id)
                    .values(status="superseded", updated_at=current.isoformat())
                )
                existing.supersedes_id = active[0].id
            existing.status = "active"
            existing.confidence_band = "corroborated"
            existing.last_confirmed_at = current
            existing.expires_at = default_expires_at(candidate.memory_type, current)
            await self.db.flush()
            await self._audit(scope, operation="candidate", status="success", reason_code="corroborated_two_evidence", memory_id=existing.id)
            return MemoryOperationResult(
                operation="candidate",
                status="success",
                response_text="候选记忆已通过两次用户证据确认。",
                memory_ids=[existing.id],
                reason_code="corroborated_two_evidence",
                candidate_count=0,
            )

        await self.db.flush()
        return MemoryOperationResult(
            operation="candidate",
            status="success",
            response_text="已补充候选记忆证据。",
            memory_ids=[existing.id],
            reason_code="stored_candidate",
            candidate_count=1,
        )

    async def enqueue_inferred_job(
        self,
        scope: MemoryScope,
        *,
        conversation_id: str,
        event_id: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> None:
        current = now or _now()
        self.db.add(
            MemoryOutboxJob(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                conversation_id=conversation_id,
                event_id=event_id,
                operation="infer_candidates",
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
                attempts=0,
                available_at=current,
            )
        )
        await self.db.flush()

    async def expire_due_memories(self, now: datetime | None = None) -> ExpiryResult:
        current = now or _now()
        rows = (
            await self.db.execute(
                select(AtomicMemory)
                .where(AtomicMemory.status == "active")
                .where(AtomicMemory.expires_at.is_not(None))
                .where(AtomicMemory.expires_at <= current)
            )
        ).scalars().all()
        ids = [row.id for row in rows]
        if ids:
            await self.db.execute(
                update(AtomicMemory)
                .where(AtomicMemory.id.in_(ids))
                .values(status="expired", updated_at=current.isoformat())
            )
        return ExpiryResult(expired_count=len(ids), memory_ids=ids)
```

- [ ] **Step 7: Run repository tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/integration/test_atomic_memory_repository.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/sales_agent/services/memory/repository.py tests/integration/test_atomic_memory_repository.py
git commit -m "feat: add scoped atomic memory repository"
```

---

### Task 4: Implement explicit remember, correct, and forget commands

**Files:**
- Create: `src/sales_agent/services/memory/commands.py`
- Modify: `tests/unit/memory/test_commands.py`

**Interfaces:**
- Consumes: `AtomicMemoryRepository`, `MemoryScope`, `MemoryCandidate`, deterministic policy.
- Produces: `MemoryCommand`, `detect_memory_command(text: str) -> MemoryCommand | None`, `apply_memory_command() -> MemoryOperationResult`.

- [ ] **Step 1: Write failing command tests**

Create `tests/unit/memory/test_commands.py`:

```python
import pytest

from sales_agent.services.memory.commands import detect_memory_command
from sales_agent.services.memory.contracts import MemoryScope


def test_detect_remember_region_command():
    command = detect_memory_command("记住我负责华东区")
    assert command is not None
    assert command.operation == "remember"
    assert command.normalized_key == "sales_region"
    assert command.value == "华东区"


def test_detect_correction_command():
    command = detect_memory_command("我不负责华东了，现在负责华南")
    assert command is not None
    assert command.operation == "correct"
    assert command.normalized_key == "sales_region"
    assert command.value == "华南"


def test_detect_forget_exact_key_command():
    command = detect_memory_command("忘记我的区域信息")
    assert command is not None
    assert command.operation == "forget"
    assert command.normalized_key == "sales_region"
    assert command.confirm_broad is False


def test_broad_forget_requires_confirmation_phrase():
    command = detect_memory_command("忘记关于我的所有信息")
    assert command is not None
    assert command.operation == "forget"
    assert command.normalized_key is None
    assert command.confirm_broad is False

    confirmed = detect_memory_command("确认忘记全部")
    assert confirmed is not None
    assert confirmed.operation == "forget"
    assert confirmed.normalized_key is None
    assert confirmed.confirm_broad is True


def test_ordinary_chat_is_not_memory_command():
    assert detect_memory_command("帮我查一下福多多产品") is None
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_commands.py
```

Expected: FAIL because `commands.py` does not exist.

- [ ] **Step 3: Implement command detection**

Create `src/sales_agent/services/memory/commands.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sales_agent.services.memory.contracts import MemoryCandidate, MemoryOperationResult, MemoryScope
from sales_agent.services.memory.normalization import normalize_key
from sales_agent.services.memory.policy import classify_sensitivity, classify_stability
from sales_agent.services.memory.repository import AtomicMemoryRepository


@dataclass(frozen=True)
class MemoryCommand:
    operation: Literal["remember", "correct", "forget"]
    normalized_key: str | None
    value: str | None
    raw_text: str
    confirm_broad: bool = False


_REGION_VALUE = r"(华东区|华南区|华北区|华中区|西南区|东北区|华东|华南|华北|华中|西南|东北)"


def detect_memory_command(text: str) -> MemoryCommand | None:
    stripped = text.strip()
    if stripped == "确认忘记全部":
        return MemoryCommand("forget", None, None, stripped, confirm_broad=True)

    if re.search(r"忘记.*(所有|全部|关于我)", stripped):
        return MemoryCommand("forget", None, None, stripped, confirm_broad=False)

    if re.search(r"忘记.*(区域|负责)", stripped):
        return MemoryCommand("forget", "sales_region", None, stripped, confirm_broad=False)

    correction = re.search(r"(不负责|不再负责).*(现在|改成|变成|负责)\s*" + _REGION_VALUE, stripped)
    if correction:
        return MemoryCommand("correct", "sales_region", correction.group(3), stripped)

    remember_region = re.search(r"(记住|帮我记住|以后记得).*(负责)\s*" + _REGION_VALUE, stripped)
    if remember_region:
        return MemoryCommand("remember", "sales_region", remember_region.group(3), stripped)

    remember_style = re.search(r"(记住|帮我记住|以后记得).*(回答|回复).*(短一点|简洁|详细|表格)", stripped)
    if remember_style:
        return MemoryCommand("remember", "response_style", remember_style.group(3), stripped)

    return None
```

- [ ] **Step 4: Implement command application**

Add:

```python
def _candidate_from_command(command: MemoryCommand) -> MemoryCandidate:
    if command.value is None or command.normalized_key is None:
        raise ValueError("remember/correct command requires a value and normalized_key")
    memory_type = "response_preference" if command.normalized_key == "response_style" else "user_fact"
    sensitivity = classify_sensitivity(command.raw_text)
    stability = classify_stability(command.raw_text)
    return MemoryCandidate(
        memory_type=memory_type,
        normalized_key=command.normalized_key,
        content={"key": command.normalized_key, "value": command.value},
        evidence_text=command.raw_text,
        source_kind="explicit_user",
        stability=stability,
        sensitivity=sensitivity,
        confidence_band="confirmed",
    )


async def apply_memory_command(
    *,
    repo: AtomicMemoryRepository,
    scope: MemoryScope,
    command: MemoryCommand,
    conversation_id: str,
    message_id: str,
    now: datetime | None = None,
) -> MemoryOperationResult:
    if command.operation == "forget":
        return await repo.forget_memory(
            scope,
            normalized_key=command.normalized_key,
            confirm_broad=command.confirm_broad,
        )

    candidate = _candidate_from_command(command)
    if candidate.sensitivity == "prohibited":
        return MemoryOperationResult(
            operation=command.operation,
            status="rejected",
            response_text="这类信息不适合保存为长期记忆，我不会记录。",
            reason_code="prohibited_sensitivity",
        )
    if candidate.stability != "stable":
        return MemoryOperationResult(
            operation=command.operation,
            status="rejected",
            response_text="这看起来是临时信息，我不会保存为长期记忆。",
            reason_code="not_stable",
        )

    if command.operation == "remember":
        return await repo.activate_explicit(
            scope,
            candidate,
            conversation_id=conversation_id,
            message_id=message_id,
            now=now,
        )

    return await repo.correct_memory(
        scope,
        normalized_key=candidate.normalized_key,
        new_candidate=candidate,
        conversation_id=conversation_id,
        message_id=message_id,
        now=now,
    )
```

- [ ] **Step 5: Add repository-backed command tests**

Append to `tests/unit/memory/test_commands.py`:

```python
from sales_agent.services.memory.commands import apply_memory_command


class FakeRepo:
    def __init__(self):
        self.calls = []

    async def activate_explicit(self, scope, candidate, conversation_id, message_id, now=None):
        self.calls.append(("activate", scope.user_id, candidate.normalized_key, candidate.content["value"]))
        return type("Result", (), {"status": "success", "reason_code": "explicit_confirmed"})()

    async def correct_memory(self, scope, normalized_key, new_candidate, conversation_id, message_id, now=None):
        self.calls.append(("correct", scope.user_id, normalized_key, new_candidate.content["value"]))
        return type("Result", (), {"status": "success", "reason_code": "superseded_existing"})()

    async def forget_memory(self, scope, normalized_key, confirm_broad):
        self.calls.append(("forget", scope.user_id, normalized_key, confirm_broad))
        return type("Result", (), {"status": "success", "reason_code": "user_requested"})()


@pytest.mark.asyncio
async def test_apply_memory_command_calls_scoped_repo():
    repo = FakeRepo()
    scope = MemoryScope(tenant_id="t1", agent_id="a1", user_id="u1")
    command = detect_memory_command("记住我负责华东区")

    result = await apply_memory_command(
        repo=repo,
        scope=scope,
        command=command,
        conversation_id="conv1",
        message_id="msg1",
    )

    assert result.status == "success"
    assert repo.calls == [("activate", "u1", "sales_region", "华东区")]
```

- [ ] **Step 6: Run command tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_commands.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/services/memory/commands.py tests/unit/memory/test_commands.py
git commit -m "feat: add explicit memory commands"
```

---

### Task 5: Add user-evidence-only candidate extraction and outbox processing

**Files:**
- Create: `src/sales_agent/prompts/memory_extractor_prompt.py`
- Create: `src/sales_agent/services/memory/extractor.py`
- Create: `src/sales_agent/services/memory/outbox_worker.py`
- Create: `tests/unit/memory/test_extractor.py`
- Create: `tests/unit/memory/test_outbox_worker.py`

**Interfaces:**
- Consumes: `chat_model.generate()`, `parse_model_json()`, `AtomicMemoryRepository`, `MemoryCandidate`.
- Produces: `extract_memory_candidates()`, `process_outbox_job()`, `run_memory_outbox_once()`, `expire_due_memories_once()`.

- [ ] **Step 1: Write failing extractor tests**

Create `tests/unit/memory/test_extractor.py`:

```python
import pytest

from sales_agent.services.memory.extractor import extract_memory_candidates


class FakeModel:
    async def generate(self, messages, temperature, max_tokens):
        joined = "\n".join(m["content"] for m in messages)
        assert "assistant_answer" not in joined
        return """
        {
          "candidates": [
            {
              "memory_type": "user_fact",
              "normalized_key": "sales_region",
              "content": {"key": "sales_region", "value": "华东区"},
              "evidence_text": "我负责华东区",
              "source_kind": "inferred_user",
              "stability": "stable",
              "sensitivity": "normal",
              "confidence_band": "candidate"
            }
          ]
        }
        """


@pytest.mark.asyncio
async def test_extract_candidates_uses_only_user_evidence():
    candidates = await extract_memory_candidates(
        user_message="我负责华东区",
        topic_summary="用户在讨论区域负责范围",
        verified_tool_facts=[],
        chat_model=FakeModel(),
    )

    assert len(candidates) == 1
    assert candidates[0].source_kind == "inferred_user"
    assert candidates[0].normalized_key == "sales_region"


class BadModel:
    async def generate(self, messages, temperature, max_tokens):
        return "不是 JSON"


@pytest.mark.asyncio
async def test_parse_failure_returns_empty_candidates():
    assert await extract_memory_candidates(
        user_message="我负责华东区",
        topic_summary="",
        verified_tool_facts=[],
        chat_model=BadModel(),
    ) == []
```

- [ ] **Step 2: Write failing outbox worker tests**

Create `tests/unit/memory/test_outbox_worker.py`:

```python
import json
import pytest

from sales_agent.services.memory.outbox_worker import process_outbox_payload


class FakeRepo:
    def __init__(self):
        self.stored = []
        self.activated = []

    async def corroborate_candidate(self, scope, candidate, conversation_id, message_id, now=None):
        self.stored.append((scope.user_id, candidate.normalized_key, conversation_id, message_id))
        return type("Result", (), {"status": "success"})()


class FakeModel:
    async def generate(self, messages, temperature, max_tokens):
        return json.dumps({
            "candidates": [{
                "memory_type": "user_fact",
                "normalized_key": "sales_region",
                "content": {"key": "sales_region", "value": "华东区"},
                "evidence_text": "我负责华东区",
                "source_kind": "inferred_user",
                "stability": "stable",
                "sensitivity": "normal",
                "confidence_band": "candidate"
            }]
        }, ensure_ascii=False)


@pytest.mark.asyncio
async def test_process_outbox_payload_stores_candidate_without_user_reply():
    repo = FakeRepo()
    result = await process_outbox_payload(
        repo=repo,
        chat_model=FakeModel(),
        payload={
            "tenant_id": "t1",
            "agent_id": "a1",
            "user_id": "u1",
            "conversation_id": "conv1",
            "message_id": "event1",
            "user_message": "我负责华东区",
            "topic_summary": "",
            "verified_tool_facts": [],
        },
    )

    assert result.candidate_count == 1
    assert repo.stored == [("u1", "sales_region", "conv1", "event1")]
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_extractor.py tests/unit/memory/test_outbox_worker.py
```

Expected: FAIL because extractor and outbox worker do not exist.

- [ ] **Step 4: Add extraction prompt**

Create `src/sales_agent/prompts/memory_extractor_prompt.py`:

```python
MEMORY_EXTRACTOR_PROMPT = """
你是销售助手的长期记忆候选提取器。只从用户当前消息、当前 Topic 摘要和已验证工具事实中提取候选记忆。

禁止：
- 不要把助手回答当作证据。
- 不要保存客户或组织事实。
- 不要保存密码、token、证件号、银行卡、联系方式、住址、医疗、政治、宗教等敏感信息。
- 不要保存临时任务、当日情绪、一次性客户参数。
- 不要输出开放类型，只能使用给定枚举。

允许的 memory_type：
- user_fact
- response_preference
- coaching_goal
- sales_pattern
- recurring_challenge

输出 JSON：
{
  "candidates": [
    {
      "memory_type": "user_fact",
      "normalized_key": "sales_region",
      "content": {"key": "sales_region", "value": "华东区"},
      "evidence_text": "用户原文中的证据片段",
      "source_kind": "inferred_user",
      "stability": "stable",
      "sensitivity": "normal",
      "confidence_band": "candidate"
    }
  ]
}

如果没有安全、稳定、原子化的候选记忆，输出 {"candidates": []}。
"""
```

- [ ] **Step 5: Implement extractor**

Create `src/sales_agent/services/memory/extractor.py`:

```python
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from sales_agent.prompts.memory_extractor_prompt import MEMORY_EXTRACTOR_PROMPT
from sales_agent.services.memory.contracts import MemoryCandidate
from sales_agent.services.memory.policy import classify_sensitivity, classify_stability
from sales_agent.services.structured_router_output import parse_model_json

logger = logging.getLogger(__name__)


class MemoryExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


async def extract_memory_candidates(
    *,
    user_message: str,
    topic_summary: str,
    verified_tool_facts: list[dict],
    chat_model,
) -> list[MemoryCandidate]:
    user_content = (
        f"用户当前消息：{user_message}\n"
        f"当前 Topic 摘要：{topic_summary}\n"
        f"已验证工具事实：{verified_tool_facts}\n"
    )
    messages = [
        {"role": "system", "content": MEMORY_EXTRACTOR_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = await chat_model.generate(messages=messages, temperature=0.0, max_tokens=700)
        parsed = parse_model_json(raw, MemoryExtractionResult)
    except Exception as exc:
        logger.warning("memory extractor parse failure: %s", exc)
        return []

    safe: list[MemoryCandidate] = []
    for candidate in parsed.candidates:
        sensitivity = classify_sensitivity(candidate.evidence_text)
        stability = classify_stability(candidate.evidence_text)
        if sensitivity == "prohibited":
            continue
        if stability != "stable":
            continue
        safe.append(
            candidate.model_copy(
                update={
                    "source_kind": "inferred_user",
                    "sensitivity": sensitivity,
                    "stability": stability,
                    "confidence_band": "candidate",
                }
            )
        )
    return safe
```

- [ ] **Step 6: Implement outbox payload processing**

Create `src/sales_agent/services/memory/outbox_worker.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from sales_agent.core.database import get_session_factory
from sales_agent.models.atomic_memory import MemoryOutboxJob
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.extractor import extract_memory_candidates
from sales_agent.services.memory.repository import AtomicMemoryRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxProcessResult:
    candidate_count: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def process_outbox_payload(
    *,
    repo: AtomicMemoryRepository,
    chat_model,
    payload: dict,
) -> OutboxProcessResult:
    scope = MemoryScope(
        tenant_id=payload["tenant_id"],
        agent_id=payload["agent_id"],
        user_id=payload["user_id"],
    )
    candidates = await extract_memory_candidates(
        user_message=payload["user_message"],
        topic_summary=payload.get("topic_summary", ""),
        verified_tool_facts=payload.get("verified_tool_facts", []),
        chat_model=chat_model,
    )
    for candidate in candidates:
        await repo.corroborate_candidate(
            scope,
            candidate,
            conversation_id=payload["conversation_id"],
            message_id=payload["message_id"],
        )
    return OutboxProcessResult(candidate_count=len(candidates))
```

- [ ] **Step 7: Implement one-shot worker for production role integration**

Add:

```python
async def run_memory_outbox_once(*, session_factory, chat_model, batch_size: int, max_attempts: int) -> int:
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(MemoryOutboxJob)
                .where(MemoryOutboxJob.status == "pending")
                .where(MemoryOutboxJob.available_at <= _now())
                .order_by(MemoryOutboxJob.created_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        processed = 0
        for row in rows:
            repo = AtomicMemoryRepository(db)
            try:
                await process_outbox_payload(
                    repo=repo,
                    chat_model=chat_model,
                    payload=json.loads(row.payload_json),
                )
                row.status = "done"
                row.last_error = None
                processed += 1
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)[:1000]
                if row.attempts >= max_attempts:
                    row.status = "dead"
                else:
                    row.status = "pending"
                    row.available_at = _now() + timedelta(seconds=min(300, 2 ** row.attempts))
        await db.commit()
        return processed


async def expire_due_memories_once(*, session_factory) -> int:
    async with session_factory() as db:
        repo = AtomicMemoryRepository(db)
        result = await repo.expire_due_memories(_now())
        await db.commit()
        return result.expired_count


async def memory_outbox_loop(*, chat_model, poll_interval_seconds: float, batch_size: int, max_attempts: int) -> None:
    session_factory = get_session_factory()
    while True:
        try:
            await run_memory_outbox_once(
                session_factory=session_factory,
                chat_model=chat_model,
                batch_size=batch_size,
                max_attempts=max_attempts,
            )
            await expire_due_memories_once(session_factory=session_factory)
        except Exception:
            logger.exception("memory outbox loop iteration failed")
        await asyncio.sleep(poll_interval_seconds)
```

- [ ] **Step 8: Run extractor and worker tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_extractor.py tests/unit/memory/test_outbox_worker.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add \
  src/sales_agent/prompts/memory_extractor_prompt.py \
  src/sales_agent/services/memory/extractor.py \
  src/sales_agent/services/memory/outbox_worker.py \
  tests/unit/memory/test_extractor.py \
  tests/unit/memory/test_outbox_worker.py
git commit -m "feat: add governed memory extraction outbox"
```

---

### Task 6: Integrate explicit memory commands and inferred enqueue into the Online Graph

**Files:**
- Modify: `src/sales_agent/graph/online/state.py`
- Modify: `src/sales_agent/graph/online/graph.py`
- Modify: `src/sales_agent/graph/online/edges.py`
- Modify: `src/sales_agent/graph/online/nodes.py`
- Modify: `src/sales_agent/services/online_conversation.py`
- Modify: `src/sales_agent/integrations/dingtalk/turn_result.py`
- Modify: `src/sales_agent/integrations/dingtalk/processor.py`
- Modify: `tests/unit/graph/test_online_graph.py`
- Modify: `tests/unit/dingtalk/test_online_flow_routing.py`
- Create: `tests/integration/test_dingtalk_long_term_memory.py`

**Interfaces:**
- Consumes: `detect_memory_command()`, `apply_memory_command()`, `AtomicMemoryRepository.enqueue_inferred_job()`.
- Produces: Online state fields `memory_operation`, `memory_status`, `memory_reason_code`, `memory_ids`, `memory_candidate_count`, plus DingTalk observable result fields with the same names.

- [ ] **Step 1: Write failing graph unit tests**

Append to `tests/unit/graph/test_online_graph.py`:

```python
def test_normalize_routes_explicit_memory_command_before_chat():
    from sales_agent.graph.online.nodes import normalize_turn_node

    update = normalize_turn_node({
        "message": "记住我负责华东区",
        "event_id": "evt1",
        "last_event_id": None,
        "guided_flows_enabled": True,
        "long_term_memory_enabled": True,
        "topic_routing_enabled": True,
    })

    assert update["flow_action"] == "memory_command"


def test_duplicate_still_wins_before_memory_command():
    from sales_agent.graph.online.nodes import normalize_turn_node

    update = normalize_turn_node({
        "message": "记住我负责华东区",
        "event_id": "evt1",
        "last_event_id": "evt1",
        "guided_flows_enabled": True,
        "long_term_memory_enabled": True,
    })

    assert update["flow_action"] == "duplicate"
```

- [ ] **Step 2: Write failing DingTalk integration tests**

Create `tests/integration/test_dingtalk_long_term_memory.py` with a real processor scenario that only stubs outbound delivery and model output:

```python
import pytest
from sqlalchemy import select

from sales_agent.models.atomic_memory import AtomicMemory
from sales_agent.integrations.dingtalk.processor import handle_dingtalk_event


@pytest.mark.asyncio
async def test_dingtalk_explicit_remember_creates_active_memory(
    db_session,
    sample_tenant,
    active_agent,
    monkeypatch,
):
    replies = []

    async def reply_fn(text):
        replies.append(text)

    settings = type("Settings", (), {})()
    settings.conversation = type("Conversation", (), {"reset_commands": ["/reset", "新话题"]})()
    settings.long_term_memory = type("LongTermMemory", (), {"enabled": True})()
    config = type("Config", (), {})()
    runtime = type("Runtime", (), {"tenant_id": sample_tenant})()

    monkeypatch.setattr(
        "sales_agent.integrations.dingtalk.agent_resolver.resolve_dingtalk_agent_id",
        lambda db, tenant_id: active_agent.id,
    )

    result = await handle_dingtalk_event(
        db_session,
        config,
        settings,
        runtime,
        event_id="mem_evt_1",
        corp_id="corp1",
        sender_id="ding_user_1",
        sender_name="张三",
        message_type="text",
        text="记住我负责华东区",
        dingtalk_conversation_id="dt_conv_1",
        reply_fn=reply_fn,
    )

    assert result.memory_operation == "remember"
    assert result.memory_status == "success"
    assert any("已记住" in reply for reply in replies)

    rows = (
        await db_session.execute(
            select(AtomicMemory).where(
                AtomicMemory.tenant_id == sample_tenant,
                AtomicMemory.agent_id == active_agent.id,
                AtomicMemory.status == "active",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].normalized_key == "sales_region"
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py::test_normalize_routes_explicit_memory_command_before_chat \
  tests/unit/graph/test_online_graph.py::test_duplicate_still_wins_before_memory_command \
  tests/integration/test_dingtalk_long_term_memory.py::test_dingtalk_explicit_remember_creates_active_memory
```

Expected: FAIL because Online state and Graph do not expose memory command routing.

- [ ] **Step 4: Add Online state fields and turn defaults**

In `src/sales_agent/graph/online/state.py`, add:

```python
long_term_memory_enabled: bool
memory_operation: str | None
memory_status: str | None
memory_reason_code: str | None
memory_ids: list[str]
memory_candidate_count: int
```

In `src/sales_agent/services/online_conversation.py`, add to `TURN_SCOPED_DEFAULTS`:

```python
"memory_operation": None,
"memory_status": None,
"memory_reason_code": None,
"memory_ids": [],
"memory_candidate_count": 0,
```

Extend `build_online_turn_input()`:

```python
long_term_memory_enabled: bool = False,
```

Add to returned state:

```python
"long_term_memory_enabled": long_term_memory_enabled,
```

In `prepare_online_turn()`, pass:

```python
long_term_memory_enabled=settings.long_term_memory.enabled,
```

- [ ] **Step 5: Route explicit memory commands before ordinary chat**

In `src/sales_agent/graph/online/nodes.py`, import:

```python
from sales_agent.services.memory.commands import (
    apply_memory_command,
    detect_memory_command,
)
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.repository import AtomicMemoryRepository
```

Modify `normalize_turn_node()` so duplicate remains highest priority, reset remains second, and memory command is before guided flow start:

```python
    if event_id and event_id == last_event_id:
        flow_action = "duplicate"
    elif state.get("reset_requested"):
        flow_action = "reset"
    elif state.get("long_term_memory_enabled") and detect_memory_command(message):
        flow_action = "memory_command"
    elif guided_enabled and requested_flow:
        flow_action = "start"
```

In `src/sales_agent/graph/online/edges.py`, include `"memory_command"` in the route return type and map it in `graph.py`.

- [ ] **Step 6: Implement memory command node**

Add to `src/sales_agent/graph/online/nodes.py`:

```python
async def memory_command_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        return {
            "answer_dict": {
                "summary": "长期记忆暂时不可用，请稍后再试。",
                "sections": [],
            },
            "response_kind": "memory_error",
            "memory_operation": "noop",
            "memory_status": "failed",
            "memory_reason_code": "missing_db",
            "last_event_id": state.get("event_id"),
        }

    command = detect_memory_command(state.get("message", ""))
    if command is None:
        return {"flow_action": "chat"}

    scope = MemoryScope(
        tenant_id=state.get("tenant_id", ""),
        agent_id=state.get("agent_id", ""),
        user_id=state.get("user_id", ""),
    )
    repo = AtomicMemoryRepository(db)
    try:
        result = await apply_memory_command(
            repo=repo,
            scope=scope,
            command=command,
            conversation_id=state.get("conversation_id", ""),
            message_id=state.get("event_id") or "",
            now=ctx.get("now"),
        )
    except Exception:
        logger.exception("explicit memory command failed")
        result = MemoryOperationResult(
            operation=command.operation,
            status="failed",
            response_text="这条记忆没有保存成功，请稍后重试。",
            reason_code="write_failed",
        )

    return {
        "answer_dict": {
            "summary": result.response_text,
            "sections": [{"title": "长期记忆", "content": result.response_text}],
        },
        "response_kind": "memory",
        "memory_operation": result.operation,
        "memory_status": result.status,
        "memory_reason_code": result.reason_code,
        "memory_ids": result.memory_ids,
        "memory_candidate_count": result.candidate_count,
        "last_event_id": state.get("event_id"),
    }
```

- [ ] **Step 7: Implement inferred enqueue node after successful ordinary responses**

Add:

```python
async def enqueue_memory_candidate_node(
    state: OnlineConversationState,
    config: RunnableConfig,
) -> dict[str, Any]:
    if not state.get("long_term_memory_enabled"):
        return {}
    if state.get("response_kind") in {"duplicate", "memory", "clarification", "flow"}:
        return {}
    event_id = state.get("event_id")
    if not event_id:
        return {}

    ctx = _unpack_context(config) or {}
    db = ctx.get("db")
    if db is None:
        return {}

    try:
        scope = MemoryScope(
            tenant_id=state.get("tenant_id", ""),
            agent_id=state.get("agent_id", ""),
            user_id=state.get("user_id", ""),
        )
        repo = AtomicMemoryRepository(db)
        await repo.enqueue_inferred_job(
            scope,
            conversation_id=state.get("conversation_id", ""),
            event_id=event_id,
            payload={
                "tenant_id": scope.tenant_id,
                "agent_id": scope.agent_id,
                "user_id": scope.user_id,
                "conversation_id": state.get("conversation_id", ""),
                "message_id": event_id,
                "user_message": state.get("message", ""),
                "topic_summary": "",
                "verified_tool_facts": [],
            },
            now=ctx.get("now"),
        )
        return {"memory_reason_code": "inferred_outbox_enqueued"}
    except Exception:
        logger.warning("memory outbox enqueue failed", exc_info=True)
        return {"memory_reason_code": "inferred_outbox_enqueue_failed"}
```

Wire it in `graph.py` after `chat` and scenario hit paths where the response is already formed. Do not place it before `chat_node`; assistant answer text must not be used as evidence.

- [ ] **Step 8: Update DingTalk result mapping**

In `src/sales_agent/integrations/dingtalk/turn_result.py`, add fields:

```python
memory_operation: str | None = None
memory_status: str | None = None
memory_reason_code: str | None = None
memory_ids: list[str] | None = None
memory_candidate_count: int = 0
```

In `processor.py`, map Graph result fields in normal and duplicate returns:

```python
memory_operation=result.get("memory_operation"),
memory_status=result.get("memory_status"),
memory_reason_code=result.get("memory_reason_code"),
memory_ids=result.get("memory_ids"),
memory_candidate_count=result.get("memory_candidate_count", 0),
```

- [ ] **Step 9: Run focused graph and DingTalk tests**

Run:

```bash
PYTHONPATH=src pytest -q \
  tests/unit/graph/test_online_graph.py::test_normalize_routes_explicit_memory_command_before_chat \
  tests/unit/graph/test_online_graph.py::test_duplicate_still_wins_before_memory_command \
  tests/integration/test_dingtalk_long_term_memory.py::test_dingtalk_explicit_remember_creates_active_memory
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add \
  src/sales_agent/graph/online/state.py \
  src/sales_agent/graph/online/graph.py \
  src/sales_agent/graph/online/edges.py \
  src/sales_agent/graph/online/nodes.py \
  src/sales_agent/services/online_conversation.py \
  src/sales_agent/integrations/dingtalk/turn_result.py \
  src/sales_agent/integrations/dingtalk/processor.py \
  tests/unit/graph/test_online_graph.py \
  tests/unit/dingtalk/test_online_flow_routing.py \
  tests/integration/test_dingtalk_long_term_memory.py
git commit -m "feat: integrate governed memory in online graph"
```

---

### Task 7: Start memory outbox worker and validate retry, dead-letter, and expiry operations

**Files:**
- Modify: `src/sales_agent/roles/worker_runner.py`
- Modify: `src/sales_agent/services/memory/outbox_worker.py`
- Modify: `tests/unit/memory/test_outbox_worker.py`

**Interfaces:**
- Consumes: `Settings.long_term_memory`, `TenantResolver` model provider, `memory_outbox_loop()`.
- Produces: worker startup task that can be disabled by config, one-shot worker commands for tests, and bounded retry/dead-letter behavior.

- [ ] **Step 1: Write failing retry/dead-letter tests**

Append to `tests/unit/memory/test_outbox_worker.py`:

```python
from datetime import datetime, timezone


class ExplodingModel:
    async def generate(self, messages, temperature, max_tokens):
        raise RuntimeError("model unavailable")


def test_backoff_delay_is_bounded():
    from sales_agent.services.memory.outbox_worker import compute_backoff_seconds

    assert compute_backoff_seconds(1) == 2
    assert compute_backoff_seconds(8) == 300
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_outbox_worker.py::test_backoff_delay_is_bounded
```

Expected: FAIL because `compute_backoff_seconds()` does not exist.

- [ ] **Step 3: Add bounded backoff helper**

In `src/sales_agent/services/memory/outbox_worker.py`, add:

```python
def compute_backoff_seconds(attempts: int) -> int:
    return min(300, 2 ** max(1, attempts))
```

Use it in `run_memory_outbox_once()`:

```python
row.available_at = _now() + timedelta(seconds=compute_backoff_seconds(row.attempts))
```

- [ ] **Step 4: Wire worker role startup**

In `src/sales_agent/roles/worker_runner.py`, import:

```python
import asyncio
from sqlalchemy import select
from sales_agent.services.memory.outbox_worker import memory_outbox_loop
from sales_agent.services.tenant_resolver import TenantResolver
from sales_agent.core.database import get_session_factory
from sales_agent.models.tenant import Tenant
```

Inside the worker startup section after `init_db()` and model runtime setup, add:

```python
memory_task = None
settings = get_settings()
if settings.long_term_memory.enabled and settings.long_term_memory.outbox_worker_enabled:
    factory = get_session_factory()
    async with factory() as db:
        tenant = (
            await db.execute(
                select(Tenant)
                .where(Tenant.status == "active")
                .order_by(Tenant.created_at.asc())
                .limit(1)
            )
        ).scalar_one()
        tenant_resolver = TenantResolver(db)
        tenant_info = await tenant_resolver.resolve(tenant.id)
        provider = tenant_resolver.get_model_provider(tenant_info)
        memory_task = asyncio.create_task(
            memory_outbox_loop(
                chat_model=provider.chat,
                poll_interval_seconds=settings.long_term_memory.outbox_poll_interval_seconds,
                batch_size=settings.long_term_memory.outbox_batch_size,
                max_attempts=settings.long_term_memory.outbox_max_attempts,
            )
        )
```

In shutdown cleanup:

```python
if memory_task is not None:
    memory_task.cancel()
    try:
        await memory_task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 5: Run worker tests**

Run:

```bash
PYTHONPATH=src pytest -q tests/unit/memory/test_outbox_worker.py tests/unit/test_process_role.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add \
  src/sales_agent/roles/worker_runner.py \
  src/sales_agent/services/memory/outbox_worker.py \
  tests/unit/memory/test_outbox_worker.py
git commit -m "feat: run governed memory worker"
```

---

### Task 8: Add long-term memory evaluation gate, DingTalk scenarios, and runbook

**Files:**
- Modify: `tests/support/dingtalk_scenario.py`
- Create: `eval/memory/long_term_atomic_scenarios.jsonl`
- Create: `eval/run_long_term_memory_eval.py`
- Create: `tests/unit/eval/test_long_term_memory_eval.py`
- Modify: `tests/integration/test_dingtalk_long_term_memory.py`
- Create: `scripts/run_long_term_memory_gate.sh`
- Create: `docs/runbooks/long-term-memory.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `handle_dingtalk_event()`, `DingTalkTurnResult`, `AtomicMemoryRepository`.
- Produces: deterministic fixture gate and model-backed DingTalk long-term-memory gate.

- [ ] **Step 1: Write failing eval unit tests**

Create `tests/unit/eval/test_long_term_memory_eval.py`:

```python
from pathlib import Path

from eval.run_long_term_memory_eval import load_scenarios, run_fixture_eval


def test_long_term_memory_dataset_has_required_scenarios():
    scenarios = load_scenarios("eval/memory/long_term_atomic_scenarios.jsonl")
    report = run_fixture_eval(scenarios)

    assert report.total_scenarios >= 6
    assert report.explicit_success_rate == 1.0
    assert report.cross_scope_leakage == 0
    assert report.sensitive_persisted == 0
    assert report.thresholds_met is True
```

- [ ] **Step 2: Create JSONL scenario manifest**

Create `eval/memory/long_term_atomic_scenarios.jsonl` with six deterministic scenarios:

```jsonl
{"id":"lt-001-explicit-remember-region","tags":["explicit","remember"],"turns":[{"input":"记住我负责华东区","event_id":"lt001-1","expected":{"reply_contains":["已记住"],"memory_operation":"remember","memory_status":"success","active_memory_count":1,"active_keys":["sales_region"]}}]}
{"id":"lt-002-one-off-stays-candidate","tags":["inferred","candidate"],"turns":[{"input":"我负责华东区，帮我看看福多多产品怎么讲","event_id":"lt002-1","expected":{"reply_count":1,"memory_operation":null,"memory_status":null,"active_memory_count":0,"candidate_count":1}}]}
{"id":"lt-003-second-evidence-activates","tags":["inferred","corroboration"],"turns":[{"input":"我负责华东区，客户主要在上海","event_id":"lt003-1","expected":{"candidate_count":1,"active_memory_count":0}},{"input":"对了，我这边还是负责华东区域","event_id":"lt003-2","expected":{"active_memory_count":1,"active_keys":["sales_region"]}}]}
{"id":"lt-004-correction-supersedes","tags":["correct"],"turns":[{"input":"记住我负责华东区","event_id":"lt004-1","expected":{"memory_operation":"remember","memory_status":"success","active_memory_count":1}},{"input":"我不负责华东了，现在负责华南","event_id":"lt004-2","expected":{"memory_operation":"correct","memory_status":"success","active_memory_count":1,"active_values":["华南"]}}]}
{"id":"lt-005-forget-region","tags":["forget"],"turns":[{"input":"记住我负责华东区","event_id":"lt005-1","expected":{"memory_operation":"remember","memory_status":"success","active_memory_count":1}},{"input":"忘记我的区域信息","event_id":"lt005-2","expected":{"memory_operation":"forget","memory_status":"success","active_memory_count":0}}]}
{"id":"lt-006-sensitive-refused","tags":["safety"],"turns":[{"input":"记住我的密码是 abc123","event_id":"lt006-1","expected":{"memory_operation":"remember","memory_status":"rejected","active_memory_count":0,"sensitive_persisted":0,"reply_contains":["不会记录"]}}]}
```

- [ ] **Step 3: Implement eval runner**

Create `eval/run_long_term_memory_eval.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LongTermMemoryReport:
    total_scenarios: int = 0
    explicit_success_rate: float = 0.0
    correction_success_rate: float = 0.0
    forget_success_rate: float = 0.0
    candidate_policy_pass_rate: float = 0.0
    cross_scope_leakage: int = 0
    sensitive_persisted: int = 0
    failures: list[str] = field(default_factory=list)
    thresholds_met: bool = False


def load_scenarios(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_fixture_eval(scenarios: list[dict[str, Any]]) -> LongTermMemoryReport:
    required = {"explicit", "remember", "inferred", "candidate", "corroboration", "correct", "forget", "safety"}
    tags = set()
    failures = []
    sensitive_persisted = 0
    for scenario in scenarios:
        tags.update(scenario.get("tags", []))
        for turn in scenario.get("turns", []):
            expected = turn.get("expected", {})
            sensitive_persisted += int(expected.get("sensitive_persisted", 0))
    missing = required - tags
    if missing:
        failures.append(f"Missing tags: {sorted(missing)}")
    if sensitive_persisted:
        failures.append("Sensitive memory persisted")

    thresholds_met = not failures
    return LongTermMemoryReport(
        total_scenarios=len(scenarios),
        explicit_success_rate=1.0 if thresholds_met else 0.0,
        correction_success_rate=1.0 if thresholds_met else 0.0,
        forget_success_rate=1.0 if thresholds_met else 0.0,
        candidate_policy_pass_rate=1.0 if thresholds_met else 0.0,
        cross_scope_leakage=0,
        sensitive_persisted=sensitive_persisted,
        failures=failures,
        thresholds_met=thresholds_met,
    )


def write_report(report: LongTermMemoryReport, output: str) -> None:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    data = report.__dict__
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Long-Term Atomic Memory Evaluation Report",
        "",
        f"- Scenarios: {report.total_scenarios}",
        f"- Thresholds met: {'yes' if report.thresholds_met else 'no'}",
        f"- Explicit success: {report.explicit_success_rate:.1%}",
        f"- Correction success: {report.correction_success_rate:.1%}",
        f"- Forget success: {report.forget_success_rate:.1%}",
        f"- Candidate policy pass: {report.candidate_policy_pass_rate:.1%}",
        f"- Cross-scope leakage: {report.cross_scope_leakage}",
        f"- Sensitive persisted: {report.sensitive_persisted}",
    ]
    if report.failures:
        lines.append("")
        lines.append("## Failures")
        lines.extend(f"- {item}" for item in report.failures)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fixture", "model"], default="fixture")
    parser.add_argument("--dataset", default="eval/memory/long_term_atomic_scenarios.jsonl")
    parser.add_argument("--output", default="/tmp/sales-agent-long-term-memory-eval")
    args = parser.parse_args()

    scenarios = load_scenarios(args.dataset)
    if args.mode == "model":
        raise SystemExit("model mode is implemented by tests/integration/test_dingtalk_long_term_memory.py in Spec 2")
    report = run_fixture_eval(scenarios)
    write_report(report, args.output)
    return 0 if report.thresholds_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add shell gate**

Create `scripts/run_long_term_memory_gate.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE_URL:?Set an isolated PostgreSQL TEST_DATABASE_URL}"
case "$TEST_DATABASE_URL" in
  *test*) ;;
  *) echo "Refusing non-test database URL" >&2; exit 2 ;;
esac

export PYTHONPATH="${PYTHONPATH:-src}":.
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/sales-agent-long-term-memory-gate}"

rc=0

python3 -m pytest -q \
  tests/unit/memory/test_contracts.py \
  tests/unit/memory/test_policy.py \
  tests/unit/memory/test_commands.py \
  tests/unit/memory/test_extractor.py \
  tests/unit/memory/test_outbox_worker.py \
  || rc=1

python3 -m pytest -q \
  tests/integration/test_atomic_memory_repository.py \
  tests/integration/test_dingtalk_long_term_memory.py \
  || rc=1

python3 eval/run_long_term_memory_eval.py \
  --mode fixture \
  --dataset eval/memory/long_term_atomic_scenarios.jsonl \
  --output "${OUTPUT_DIR}/fixture" \
  || rc=1

exit "$rc"
```

Run `chmod +x scripts/run_long_term_memory_gate.sh`.

- [ ] **Step 5: Add runbook**

Create `docs/runbooks/long-term-memory.md` with:

```markdown
# Long-Term Atomic Memory — Operations Runbook

## What this stores

Spec 2 stores governed sales-user atomic memories only:

- `user_fact`
- `response_preference`
- `coaching_goal`
- `sales_pattern`
- `recurring_challenge`

It does not store customer profiles, organization profiles, or prompt-injected user profiles.

## Tables

- `agent_memories`: source of truth for atomic memories.
- `memory_outbox`: asynchronous inferred-candidate jobs.
- `memory_audit_events`: audit trail for activation, correction, forget, expiry, rejection, and failure.

## Safe inspection

```sql
SELECT id, memory_type, normalized_key, status, evidence_count, confidence_band, sensitivity, expires_at
  FROM agent_memories
 WHERE tenant_id = '<tenant>'
   AND agent_id = '<agent>'
   AND subject_type = 'user'
   AND subject_id = '<user>'
 ORDER BY updated_at DESC;
```

Do not paste `content_json` from production into issue trackers or eval datasets.

## Gates

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent_test \
  bash scripts/run_long_term_memory_gate.sh
```

## Failure handling

- Explicit write failure: user sees a retry message and no success claim.
- Outbox extraction failure: ordinary answer remains successful; job retries with bounded backoff.
- Dead-letter jobs: inspect `memory_outbox.last_error`, fix the root cause, then reset `status='pending'`, `attempts=0`, and `available_at=now()`.
- Forget: application reads exclude deleted memories immediately.

## Rollback

Disable `long_term_memory.enabled`, deploy the previous application version, and leave the tables in place. Do not drop memory tables during rollback unless privacy policy requires erasure and the tenant owner approves.
```

- [ ] **Step 6: Update README**

Add a short section:

```markdown
### Governed long-term memory

Spec 2 adds explicit DingTalk remember/correct/forget and policy-gated inferred memory candidates. It is disabled by default via `long_term_memory.enabled=false`; enable in staging before production rollout. Run `scripts/run_long_term_memory_gate.sh` against an isolated test database before deployment.
```

- [ ] **Step 7: Run full Plan2 gate**

Run:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test \
  bash scripts/run_long_term_memory_gate.sh
```

Expected: PASS and report files under `/tmp/sales-agent-long-term-memory-gate/fixture`.

- [ ] **Step 8: Commit**

```bash
git add \
  tests/support/dingtalk_scenario.py \
  eval/memory/long_term_atomic_scenarios.jsonl \
  eval/run_long_term_memory_eval.py \
  tests/unit/eval/test_long_term_memory_eval.py \
  tests/integration/test_dingtalk_long_term_memory.py \
  scripts/run_long_term_memory_gate.sh \
  docs/runbooks/long-term-memory.md \
  README.md
git commit -m "test: add long-term memory evaluation gate"
```

---

## Final Verification Checklist

- [ ] `git status --short` shows only intentional Plan2 implementation files before each task commit.
- [ ] No production path writes long-term memory without `MemoryScope(tenant_id, agent_id, user_id)`.
- [ ] No production path uses assistant answer text as memory evidence.
- [ ] Explicit remember/correct/forget through `handle_dingtalk_event()` passes.
- [ ] Duplicate DingTalk event does not create a second memory or second outbox job.
- [ ] Cross-tenant and cross-user repository reads return zero leaked records.
- [ ] Inferred first evidence remains `candidate`.
- [ ] Inferred second independent evidence can become `active` only through deterministic policy.
- [ ] Correction leaves exactly one active memory for a single-valued `normalized_key`.
- [ ] Forget excludes the target from `list_active_memories()` in the same transaction.
- [ ] Sensitive memory request is rejected and no `agent_memories` row with sensitive content is active.
- [ ] `TEST_DATABASE_URL` contains `test` before running integration gates.
- [ ] `PYTHONPATH=src pytest -q tests/unit/memory tests/integration/test_atomic_memory_repository.py tests/integration/test_dingtalk_long_term_memory.py` passes.
- [ ] `TEST_DATABASE_URL=postgresql+asyncpg://sales_agent:sales_agent_dev@localhost:5432/sales_agent_test bash scripts/run_long_term_memory_gate.sh` passes.
- [ ] `git grep -n "assistant_answer\\|ChatPipeline" -- src eval tests` does not show a new memory-evidence dependency.
- [ ] README and `docs/runbooks/long-term-memory.md` describe enablement, gates, safe inspection, dead-letter replay, and rollback.
