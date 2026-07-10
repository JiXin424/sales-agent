"""Test Plan: success_criteria + pursuit_goal in extraction and decision contracts."""

from datetime import datetime, timezone

from sales_agent.services.sales_actions.contracts import (
    SalesActionDecision,
    SalesActionExtraction,
)


def test_extraction_carries_success_criteria():
    ext = SalesActionExtraction(
        intent="create_action",
        title="发送案例给张总",
        success_criteria="张总确认技术负责人和可沟通时间",
        pursuit_goal="推进张总这单",
        confidence=0.95,
    )
    assert ext.success_criteria == "张总确认技术负责人和可沟通时间"
    assert ext.pursuit_goal == "推进张总这单"


def test_extraction_defaults_to_none():
    ext = SalesActionExtraction(intent="none", confidence=0.9)
    assert ext.success_criteria is None
    assert ext.pursuit_goal is None


def test_decision_carries_success_criteria():
    dec = SalesActionDecision(
        action="suggest",
        title="发送案例给张总",
        success_criteria="张总确认技术负责人和可沟通时间",
        pursuit_goal="推进张总这单",
    )
    assert dec.success_criteria == "张总确认技术负责人和可沟通时间"
    assert dec.pursuit_goal == "推进张总这单"


def test_decision_defaults_to_none():
    dec = SalesActionDecision(
        action="ignore",
        title="",
    )
    assert dec.success_criteria is None
    assert dec.pursuit_goal is None
