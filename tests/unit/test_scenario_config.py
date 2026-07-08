"""Tests for ScenarioCoachConfig env overrides."""

from __future__ import annotations

import importlib

import pytest


def _reload_settings(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from sales_agent.core import config as config_mod
    importlib.reload(config_mod)
    return config_mod.get_settings()


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """Ensure a fresh settings singleton per test and clean env."""
    for k in ("SCENARIO_COACH_ENABLED", "SCENARIO_COACH_CONFIDENCE_THRESHOLD"):
        monkeypatch.delenv(k, raising=False)
    from sales_agent.core import config as config_mod
    importlib.reload(config_mod)
    yield
    importlib.reload(config_mod)


def test_defaults_off():
    from sales_agent.core.config import get_settings
    s = get_settings()
    assert s.scenario_coach.enabled is False
    assert s.scenario_coach.confidence_threshold == 0.8


def test_env_enables(monkeypatch):
    s = _reload_settings(monkeypatch, {"SCENARIO_COACH_ENABLED": "true"})
    assert s.scenario_coach.enabled is True


def test_env_disabled_variants(monkeypatch):
    for val in ("0", "false", "no", "off", "False", "anything"):
        s = _reload_settings(monkeypatch, {"SCENARIO_COACH_ENABLED": val})
        assert s.scenario_coach.enabled is False


def test_env_threshold_float(monkeypatch):
    s = _reload_settings(monkeypatch, {"SCENARIO_COACH_CONFIDENCE_THRESHOLD": "0.65"})
    assert s.scenario_coach.confidence_threshold == 0.65


def test_env_threshold_invalid_ignored(monkeypatch):
    s = _reload_settings(monkeypatch, {"SCENARIO_COACH_CONFIDENCE_THRESHOLD": "not-a-number"})
    assert s.scenario_coach.confidence_threshold == 0.8
