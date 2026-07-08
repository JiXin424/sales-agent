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
