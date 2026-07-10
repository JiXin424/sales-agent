import pytest
import yaml

from sales_agent.llm.call_params import CallParams, load_call_params, get_call_params


def _write_yaml(tmp_path, data):
    p = tmp_path / "llm_call_defaults.yaml"
    p.write_text(yaml.safe_dump(data))
    return str(p)


def test_load_valid_yaml_and_get(tmp_path):
    path = _write_yaml(tmp_path, {"evidence_router": {"temperature": 0.0, "max_tokens": 500}})
    load_call_params(path)
    p = get_call_params("evidence_router")
    assert p == CallParams(temperature=0.0, max_tokens=500)


def test_missing_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, RuntimeError)):
        load_call_params(str(tmp_path / "nope.yaml"))


def test_missing_field_raises(tmp_path):
    path = _write_yaml(tmp_path, {"evidence_router": {"temperature": 0.0}})  # 缺 max_tokens
    with pytest.raises((ValueError, RuntimeError)):
        load_call_params(path)


def test_bad_temperature_range_raises(tmp_path):
    path = _write_yaml(tmp_path, {"x": {"temperature": 3.0, "max_tokens": 100}})
    with pytest.raises((ValueError, RuntimeError)):
        load_call_params(path)


def test_bad_max_tokens_raises(tmp_path):
    path = _write_yaml(tmp_path, {"x": {"temperature": 0.0, "max_tokens": 0}})
    with pytest.raises((ValueError, RuntimeError)):
        load_call_params(path)


def test_bad_type_raises(tmp_path):
    path = _write_yaml(tmp_path, {"x": {"temperature": "hot", "max_tokens": 100}})
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        load_call_params(path)


def test_get_before_load_raises():
    # 用一个全新 call_site 触发「未加载」分支（依赖模块状态，置于末尾）
    load_call_params.__wrapped__ if hasattr(load_call_params, "__wrapped__") else None
    import sales_agent.llm.call_params as m
    m._PARAMS = None
    with pytest.raises(RuntimeError):
        get_call_params("evidence_router")


def test_unknown_call_site_raises(tmp_path):
    path = _write_yaml(tmp_path, {"evidence_router": {"temperature": 0.0, "max_tokens": 500}})
    load_call_params(path)
    with pytest.raises(KeyError):
        get_call_params("nonexistent_call_site")


def test_load_real_defaults_file_all_keys():
    """加载真实 config/llm_call_defaults.yaml，验证全部 24 个 key 就位且值正确。"""
    from sales_agent.core.config import get_settings
    load_call_params(get_settings().llm_config_path)
    expected = {
        "evidence_router": (0.0, 500), "task_router": (0.1, 200),
        "agent_executor": (0.3, 2000), "context_resolver": (0.0, 500),
        "topic_manager": (0.0, 500), "topic_restore": (0.0, 500),
        "risk_checker": (0.1, 300), "memory_extractor": (0.0, 700),
        "md_optimizer": (0.3, 4096), "ontology_retrieval": (0.0, 100),
        "ontology_answer": (0.2, 1600), "ontology_entity_extraction": (0.1, 5000),
        "ontology_fact_extraction": (0.1, 6000), "ontology_graph": (0.0, 100),
        "web_fallback": (0.2, 800), "scenario_matcher": (0.0, 200),
        "coach_small_win": (0.4, 600), "coach_block_split": (0.2, 400),
        "coach_reframe": (0.4, 900), "daily_evaluator": (0.1, 2000),
        "prompt_preview": (0.3, 2000), "health_ping": (0.3, 10),
        "media_vision": (0.1, 800), "media_audio": (0.0, 800),
    }
    for key, (t, m) in expected.items():
        p = get_call_params(key)
        assert p.temperature == t, f"{key} temperature={p.temperature} != {t}"
        assert p.max_tokens == m, f"{key} max_tokens={p.max_tokens} != {m}"
