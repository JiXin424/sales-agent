"""Test iteration event service: sequence allocation, redaction, replay."""

import json
import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.optimization import OptimizationIteration
from sales_agent.models.iteration_observability import IterationEvent
from sales_agent.models.base import generate_id


@pytest.fixture
def iteration() -> OptimizationIteration:
    """Return an in-memory iteration stub for unit tests (no DB needed)."""
    return OptimizationIteration(
        id=generate_id(),
        tenant_id="t1",
        agent_id="a1",
        iteration_no=1,
        status="running",
    )


class TestIterationEventService:
    """Unit tests that mock DB interactions."""

    def test_redacted_payload_keys(self):
        """Secret-like keys must be redacted before storage."""
        from sales_agent.optimization.event_service import _REDACT_KEYS, _redact_payload

        payload = {
            "token": "secret123",
            "api_key": "sk-abc",
            "password": "p@ss",
            "secret": "hidden",
            "credential": "cred",
            "candidate_id": "c1",
            "eval_run_id": "er1",
            "score": 0.95,
        }
        redacted = _redact_payload(payload)

        secret_keys_in_payload = _REDACT_KEYS & set(payload.keys())
        for key in secret_keys_in_payload:
            assert redacted.get(key) == "[REDACTED]", f"{key} not redacted"
        assert redacted["candidate_id"] == "c1"
        assert redacted["eval_run_id"] == "er1"
        assert redacted["score"] == 0.95

    def test_redact_nested_dicts(self):
        """Redaction recurses into nested dicts but not lists."""
        from sales_agent.optimization.event_service import _redact_payload

        payload = {
            "nested": {"token": "nested-secret", "ok": "keep"},
            "list": [{"password": "in-list"}, {"ok": "keep-list"}],
        }
        redacted = _redact_payload(payload)
        assert redacted["nested"]["token"] == "[REDACTED]"
        assert redacted["nested"]["ok"] == "keep"
        assert redacted["list"][0]["password"] == "in-list"  # not recursed into lists
        assert redacted["list"][1]["ok"] == "keep-list"


@pytest.mark.asyncio
class TestEventServiceAsync:
    """Tests that need the DB session."""

    async def test_append_allocates_monotonic_sequence(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """Two appends for the same iteration must receive seq 1 and 2."""
        from sales_agent.optimization.event_service import IterationEventService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=1,
            status="running",
            current_stage="diagnosing",
        )
        db_session.add(iteration)
        await db_session.flush()

        service = IterationEventService(db_session)
        first = await service.append(
            iteration, "stage.started", stage="diagnosing", status="running",
        )
        second = await service.append(
            iteration, "stage.completed", stage="diagnosing", status="completed",
        )
        assert first.sequence_no == 1
        assert second.sequence_no == 2
        assert first.event_type == "stage.started"
        assert second.event_type == "stage.completed"

    async def test_event_payload_redacts_secrets(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """Payload stored in the DB must redact secret-like keys."""
        from sales_agent.optimization.event_service import IterationEventService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=2,
            status="running",
        )
        db_session.add(iteration)
        await db_session.flush()

        service = IterationEventService(db_session)
        event = await service.append(
            iteration, "stage.progress",
            payload={"token": "secret", "candidate_id": "c1"},
        )
        payload = json.loads(event.payload_json)
        assert payload["token"] == "[REDACTED]"
        assert payload["candidate_id"] == "c1"

    async def test_list_after_respects_cursor(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """list_after with after_sequence=1 must return only later events."""
        from sales_agent.optimization.event_service import IterationEventService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=3,
            status="running",
        )
        db_session.add(iteration)
        await db_session.flush()

        service = IterationEventService(db_session)
        await service.append(iteration, "stage.started", stage="diagnosing")
        await service.append(iteration, "stage.progress", message="50%")
        await service.append(iteration, "stage.completed", stage="diagnosing")

        page = await service.list_after(
            tenant_id=sample_tenant,
            iteration_id=iteration.id,
            after_sequence=1,
            limit=10,
        )
        assert len(page) == 2
        assert page[0].sequence_no == 2
        assert page[1].sequence_no == 3

    async def test_list_after_empty_result(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """list_after with after_sequence beyond last event returns empty."""
        from sales_agent.optimization.event_service import IterationEventService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=4,
            status="running",
        )
        db_session.add(iteration)
        await db_session.flush()

        service = IterationEventService(db_session)
        await service.append(iteration, "stage.started")

        page = await service.list_after(
            tenant_id=sample_tenant,
            iteration_id=iteration.id,
            after_sequence=3,
            limit=10,
        )
        assert len(page) == 0

    async def test_wait_after_returns_on_new_events(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """wait_after returns immediately when events are already available."""
        from sales_agent.optimization.event_service import IterationEventService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=5,
            status="running",
        )
        db_session.add(iteration)
        await db_session.flush()

        service = IterationEventService(db_session)
        await service.append(iteration, "stage.started")
        await service.append(iteration, "stage.progress")

        result = await service.wait_after(
            tenant_id=sample_tenant,
            iteration_id=iteration.id,
            after_sequence=0,
            timeout_seconds=1,
            limit=10,
        )
        assert len(result.events) == 2
        assert result.next_sequence == 2
        assert result.terminal is False

    async def test_wait_after_returns_terminal_for_completed_iteration(
        self, db_session: AsyncSession, sample_tenant: str, active_agent,
    ):
        """wait_after marks terminal when iteration is in a completed state."""
        from sales_agent.optimization.event_service import IterationEventService

        iteration = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=6,
            status="completed",
        )
        db_session.add(iteration)
        await db_session.flush()

        service = IterationEventService(db_session)
        await service.append(iteration, "stage.started")
        await service.append(iteration, "stage.completed")

        result = await service.wait_after(
            tenant_id=sample_tenant,
            iteration_id=iteration.id,
            after_sequence=0,
            timeout_seconds=1,
        )
        assert result.terminal is True


@pytest.mark.asyncio
class TestEventServiceCrossTenant:
    """Verify tenant isolation in event queries."""

    async def test_events_isolated_by_tenant(
        self, db_session: AsyncSession, sample_tenant: str, active_agent, other_agent,
    ):
        """Events from tenant A must not be returned for tenant B queries."""
        from sales_agent.optimization.event_service import IterationEventService

        # Create iteration for tenant 1
        it1 = OptimizationIteration(
            id=generate_id(),
            tenant_id=sample_tenant,
            agent_id=active_agent.id,
            iteration_no=7,
            status="running",
        )
        db_session.add(it1)

        # Create iteration for tenant 2
        it2 = OptimizationIteration(
            id=generate_id(),
            tenant_id=other_agent.tenant_id,
            agent_id=other_agent.id,
            iteration_no=1,
            status="running",
        )
        db_session.add(it2)
        await db_session.flush()

        service = IterationEventService(db_session)
        await service.append(it1, "stage.started", stage="diagnosing")
        await service.append(it2, "stage.started", stage="diagnosing")

        # Query as tenant 1 — should only see tenant 1's events
        page = await service.list_after(
            tenant_id=sample_tenant,
            iteration_id=it1.id,
            after_sequence=0,
            limit=10,
        )
        assert len(page) == 1
        assert page[0].tenant_id == sample_tenant
