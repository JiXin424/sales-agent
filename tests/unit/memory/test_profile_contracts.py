def test_user_memory_profile_tables_are_registered():
    import sales_agent.models
    from sales_agent.core.database import Base

    sales_agent.models._import_dingtalk_models()

    assert "user_memory_profiles" in Base.metadata.tables
    assert "user_profile_rebuild_jobs" in Base.metadata.tables


from sales_agent.services.memory.profile_contracts import EMPTY_PROFILE, UserMemoryProfileDocument


def test_empty_profile_has_only_approved_sections():
    profile = UserMemoryProfileDocument.model_validate(EMPTY_PROFILE)
    assert profile.work_context.sales_region is None
    assert profile.work_context.product_focus == []
    assert profile.response_preferences.format == []
    assert profile.development.coaching_goals == []
    assert "personality" not in profile.model_dump()
