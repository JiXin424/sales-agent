"""coach_observe 与 coach_guidance 单元测试。"""

from __future__ import annotations

from sales_agent.coach.coach_guidance import (
    GUIDANCE_LEVEL_DIRECTIONAL,
    GUIDANCE_LEVEL_SPECIFIC,
    GUIDANCE_LEVEL_SUPPRESSED,
    generate_guidance,
)
from sales_agent.coach.coach_observe import observe


# ---------------------------------------------------------------------------
# observe
# ---------------------------------------------------------------------------


def test_observe_visit_preparation_from_keywords():
    r = observe(tenant_id="t", agent_id="a", user_id="u",
                message="明天要去拜访客户，帮我准备一下", task_type=None)
    assert r.scene_hint == "visit_preparation"
    assert r.confidence > 0
    assert "mention_visit" in r.observed_signals


def test_observe_pricing_scene():
    r = observe(tenant_id="t", agent_id="a", user_id="u",
                message="客户说太贵了怎么回", task_type=None)
    assert r.scene_hint == "closing_or_pricing"
    assert r.should_generate_guidance is True


def test_observe_task_type_boosts_confidence():
    r = observe(tenant_id="t", agent_id="a", user_id="u",
                message="帮我看看", task_type="visit_preparation")
    assert r.scene_hint == "visit_preparation"
    assert r.confidence >= 0.55


def test_observe_no_scene_returns_none():
    r = observe(tenant_id="t", agent_id="a", user_id="u",
                message="今天天气不错", task_type=None)
    assert r.scene_hint is None
    assert r.should_generate_guidance is False
    assert r.reason == "no_sales_scene_detected"


def test_observe_empty_message():
    r = observe(tenant_id="t", agent_id="a", user_id="u",
                message="   ", task_type=None)
    assert r.scene_hint is None
    assert r.reason == "empty_message"


def test_observe_history_adds_signals():
    r = observe(tenant_id="t", agent_id="a", user_id="u",
                message="好的", task_type=None,
                recent_history=[{"role": "user", "content": "客户一直不回我消息"}])
    assert r.scene_hint == "dormant_customer"


# ---------------------------------------------------------------------------
# guidance：分数带
# ---------------------------------------------------------------------------


def test_guidance_specific_band_for_low_score():
    r = generate_guidance(
        scene_hint="visit_preparation",
        scores={"customer_identification": 30},
        message="明天拜访客户", daily_guidance_used=0,
        observe_confidence=0.7,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SPECIFIC
    assert r.guidance_text
    assert "客户识别" in r.guidance_text
    assert r.dimension_focus == "customer_identification"


def test_guidance_directional_band_for_mid_score():
    r = generate_guidance(
        scene_hint="pain_point_discovery",
        scores={"needs_discovery": 55},
        message="客户提到预算紧张", daily_guidance_used=0,
        observe_confidence=0.7,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_DIRECTIONAL
    assert r.guidance_text


def test_guidance_suppressed_for_high_score():
    r = generate_guidance(
        scene_hint="closing_or_pricing",
        scores={"deal_advancement": 85},
        message="怎么推进成交", daily_guidance_used=0,
        observe_confidence=0.8,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SUPPRESSED
    assert r.suppressed_reason == "high_score_no_obvious_need"


# ---------------------------------------------------------------------------
# guidance：依赖规则
# ---------------------------------------------------------------------------


def test_guidance_dependency_rule_weak_needs_and_customer():
    # needs_discovery 与 customer_identification 都弱 → 聚焦 customer_identification
    r = generate_guidance(
        scene_hint="pain_point_discovery",
        scores={"needs_discovery": 35, "customer_identification": 38},
        message="多问几个问题", daily_guidance_used=0,
        observe_confidence=0.7,
    )
    assert r.dimension_focus == "customer_identification"


# ---------------------------------------------------------------------------
# guidance：抑制规则
# ---------------------------------------------------------------------------


def test_guidance_suppressed_on_coach_report_task():
    r = generate_guidance(
        scene_hint="visit_preparation", scores={"customer_identification": 30},
        message="我的评分", task_type="coach_report", daily_guidance_used=0,
        observe_confidence=0.9,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SUPPRESSED
    assert r.suppressed_reason == "coach_report_request"


def test_guidance_suppressed_on_command():
    r = generate_guidance(
        scene_hint="visit_preparation", scores={"customer_identification": 30},
        message="帮助", daily_guidance_used=0, observe_confidence=0.9,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SUPPRESSED


def test_guidance_suppressed_on_daily_limit():
    r = generate_guidance(
        scene_hint="visit_preparation", scores={"customer_identification": 30},
        message="拜访客户", daily_guidance_used=3, daily_guidance_limit=3,
        observe_confidence=0.9,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SUPPRESSED
    assert r.suppressed_reason == "daily_limit_exceeded"


def test_guidance_suppressed_on_pure_knowledge_qa():
    r = generate_guidance(
        scene_hint=None, scores={"customer_identification": 30},
        message="公司地址在哪", task_type="knowledge_qa",
        daily_guidance_used=0, observe_confidence=0.9,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SUPPRESSED
    assert r.suppressed_reason == "pure_knowledge_qa"


def test_guidance_suppressed_when_no_scene():
    r = generate_guidance(
        scene_hint=None, scores={"customer_identification": 30},
        message="随便聊聊", daily_guidance_used=0, observe_confidence=0.2,
    )
    assert r.guidance_level == GUIDANCE_LEVEL_SUPPRESSED
    assert r.suppressed_reason == "no_clear_scene"
