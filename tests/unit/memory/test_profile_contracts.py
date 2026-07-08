def test_user_memory_profile_tables_are_registered():
    import sales_agent.models
    from sales_agent.core.database import Base

    sales_agent.models._import_dingtalk_models()

    assert "user_memory_profiles" in Base.metadata.tables
    assert "user_profile_rebuild_jobs" in Base.metadata.tables
