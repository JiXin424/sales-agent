"""Tests for the scenario markdown loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from sales_agent.scenarios.loader import parse_scenario_md, get_scenario_registry
from sales_agent.scenarios.models import ScenarioMatchDecision

_DATA_PATH = Path(__file__).resolve().parents[2] / "src" / "sales_agent" / "scenarios" / "data" / "销冠智慧教练手册.md"


def test_parses_real_manual():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    assert reg.is_available()
    # 6 scenarios, 11 questions
    assert len(reg.scenarios) == 6
    questions = reg.list_questions()
    assert len(questions) == 11
    assert [q["id"] for q in questions] == [f"Q{i:02d}" for i in range(1, 12)]


def test_source_name_extracted():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    assert reg.source_name == "销冠智慧教练手册·2026年4月版"


def test_question_fields_populated():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    q01 = reg.get_question("Q01")
    assert q01 is not None
    assert q01.text  # non-empty representative question
    assert q01.tag  # "需判断 · 先判断再应对"
    assert len(q01.answer_sections) >= 1
    assert q01.answer_summary == q01.text  # summary is the representative question, not the tag label
    assert "需判断" not in q01.answer_summary  # not the generic tag label
    # 需判断-type Q01 has #### subsections like "价值还没立住"
    titles = [s.title for s in q01.answer_sections]
    assert any("价值还没立住" in t for t in titles)
    # every section has title + content
    for s in q01.answer_sections:
        assert s.title
        assert s.content


def test_flow_type_question_has_step_sections():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    q07 = reg.get_question("Q07")  # 流程型
    assert q07 is not None
    titles = [s.title for s in q07.answer_sections]
    assert any(t.startswith("步骤 1") for t in titles)


def test_has_question_and_get():
    md = _DATA_PATH.read_text(encoding="utf-8")
    reg = parse_scenario_md(md)
    assert reg.has_question("Q11") is True
    assert reg.has_question("Q99") is False
    assert reg.get_question("Q99") is None


def test_malformed_md_raises():
    with pytest.raises(ValueError):
        parse_scenario_md("# not a scenario manual\n\nnothing here")
    with pytest.raises(ValueError):
        parse_scenario_md("")


def test_get_registry_singleton_available():
    reg = get_scenario_registry()
    assert reg.is_available()
    assert len(reg.scenarios) == 6


def test_decision_model_defaults():
    d = ScenarioMatchDecision()
    assert d.matched_question_id is None
    assert d.confidence == 0.0
    assert d.reason_code == "unknown"
