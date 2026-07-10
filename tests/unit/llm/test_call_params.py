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
