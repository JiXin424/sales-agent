from datetime import datetime, timezone

from sales_agent.services.memory.profile_repository import StoredUserMemoryProfile
from sales_agent.services.memory.transparency import (
    detect_transparency_command,
    render_memory_transparency,
)


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
