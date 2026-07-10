# tests/unit/eval/test_memory_eval_dataset.py
from __future__ import annotations

import json

import pytest

from eval.memory_eval.dataset import (
    DatasetValidationError,
    load_scenarios,
    validate_dataset,
)
from eval.memory_eval.schema import MultiturnScenario, ScenarioTurn


def _write(tmp_path, rows):
    p = tmp_path / "ds.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(p)


def test_load_scenarios_roundtrip(tmp_path):
    path = _write(tmp_path, [
        {"id": "a", "version": 1, "tags": ["x"], "turns": [{"input": "hi", "expected": {}}]},
    ])
    scenarios = load_scenarios(path)
    assert len(scenarios) == 1
    assert isinstance(scenarios[0], MultiturnScenario)


def test_validate_rejects_secrets_and_identifiers(tmp_path):
    scenarios = [
        MultiturnScenario(
            id="s1",
            turns=[ScenarioTurn(input="记住我的密码是 abc123", expected={})],
        ),
        MultiturnScenario(
            id="s2",
            turns=[ScenarioTurn(input="用户 13800138000 的信息", expected={})],
        ),
        MultiturnScenario(
            id="s3",
            turns=[ScenarioTurn(input="身份证 110101199003071234", expected={})],
        ),
        MultiturnScenario(
            id="s4",
            turns=[ScenarioTurn(input="联系 a@b.com", expected={})],
        ),
    ]
    errors = validate_dataset(scenarios)
    assert any("secret" in e.lower() or "password" in e.lower() for e in errors)
    # Phone identifier (s2)
    assert any("direct identifier" in e.lower() and "phone" in e.lower() for e in errors)
    # ID-card identifier (s3)
    assert any("direct identifier" in e.lower() and "id card" in e.lower() for e in errors)
    # Email identifier (s4)
    assert any("direct identifier" in e.lower() and "email" in e.lower() for e in errors)


def test_validate_rejects_duplicate_ids(tmp_path):
    scenarios = [
        MultiturnScenario(id="dup", turns=[ScenarioTurn(input="hi", expected={})]),
        MultiturnScenario(id="dup", turns=[ScenarioTurn(input="yo", expected={})]),
    ]
    errors = validate_dataset(scenarios)
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_rejects_unreviewed_production_marker(tmp_path):
    scenarios = [
        MultiturnScenario(
            id="s1",
            tags=["unreviewed_production"],
            turns=[ScenarioTurn(input="hi", expected={})],
        ),
    ]
    errors = validate_dataset(scenarios)
    assert any("unreviewed" in e.lower() for e in errors)


def test_load_raises_on_bad_json(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError):
        load_scenarios(str(path))
