def test_atomic_memory_tables_registered():
    import sales_agent.models
    from sales_agent.core.database import Base

    sales_agent.models._import_dingtalk_models()

    table_names = set(Base.metadata.tables)
    assert "agent_memories" in table_names
    assert "memory_outbox" in table_names
    assert "memory_audit_events" in table_names
