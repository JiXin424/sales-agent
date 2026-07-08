def test_atomic_memory_tables_registered():
    import sales_agent.models
    from sales_agent.core.database import Base

    sales_agent.models._import_dingtalk_models()

    table_names = set(Base.metadata.tables)
    assert "agent_memories" in table_names
    assert "memory_outbox" in table_names
    assert "memory_audit_events" in table_names


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
