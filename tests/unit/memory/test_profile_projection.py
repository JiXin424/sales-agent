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
