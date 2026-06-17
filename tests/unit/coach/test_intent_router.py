"""CoachIntentRouter 单元测试 —— 关键是不得"偷走"既有 conversation_scoring。"""

from __future__ import annotations

import pytest

from sales_agent.coach.intent_router import match_coach_intent


@pytest.mark.parametrize(
    "message,expected",
    [
        # scores
        ("我的评分", "scores"),
        ("我的评分怎么样", "scores"),
        ("我的能力", "scores"),
        ("能力报告", "scores"),
        ("评分", "scores"),
        # level
        ("我的等级", "level"),
        ("我的段位", "level"),
        ("段位", "level"),
        # milestones
        ("里程碑", "milestones"),
        ("我的里程碑", "milestones"),
        # rewards
        ("奖励", "rewards"),
        ("徽章", "rewards"),
        ("红包", "rewards"),
        # iceberg
        ("冰山", "iceberg"),
        ("我卡在哪", "iceberg"),
        ("卡在哪", "iceberg"),
        ("深层问题", "iceberg"),
        ("哪里需要提升", "iceberg"),
        # full
        ("教练报告", "full"),
        ("完整报告", "full"),
    ],
)
def test_coach_triggers_match(message, expected):
    assert match_coach_intent(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "给我打分",      # 既有对话评分
        "帮我打分",
        "对话评分",
        "通话评分",
        "沟通评分",
        "销售评分",
        "评分标准是什么",
        "帮我评分这段对话",
        # 非报告意图
        "客户说太贵了怎么回",
        "帮我写一段跟进话术",
        "我们产品的优势是什么",
        # help/reset（即便穿过 fast 命令层也应被排除）
        "帮助",
        "新话题",
    ],
)
def test_non_coach_messages_return_none(message):
    assert match_coach_intent(message) is None


def test_full_takes_precedence_over_scores_report_phrase():
    # "教练报告" 含 "报告" 但应命中 full 而非 scores
    assert match_coach_intent("教练报告") == "full"


def test_empty_or_whitespace_returns_none():
    assert match_coach_intent("") is None
    assert match_coach_intent("   ") is None
