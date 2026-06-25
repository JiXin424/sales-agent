"""生成器 render-multitenant-deploy.py 的单测。

脚本文件名含连字符无法直接 import，用 importlib 按路径加载。
"""
import importlib.util
import pathlib

import json

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "render-multitenant-deploy.py"


def _load():
    spec = importlib.util.spec_from_file_location("render_multitenant", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inventory(tmp_path, neo4j_cfg=None, tenant_env_text=""):
    env = tmp_path / "acme.env"
    env.write_text(tenant_env_text)
    data = {
        "project_name": "sales-agent",
        "image": "sales-agent:latest",
        "tenants": [{
            "id": "acme", "name": "ACME", "api_port": 8101,
            "env_file": str(env),
            "data_dir": "./data/acme", "logs_dir": "./logs/acme",
            "roles": ["api", "stream", "worker"],
        }],
    }
    if neo4j_cfg is not None:
        data["neo4j"] = neo4j_cfg
    return data


def test_neo4j_rendered_when_enabled(tmp_path):
    mod = _load()
    out = mod.render_compose(_inventory(tmp_path, {"enabled": True, "image": "registry.internal:5000/neo4j:5"}))
    # neo4j 服务块
    assert "  neo4j:" in out
    assert "registry.internal:5000/neo4j:5" in out
    assert "container_name: sales-agent-neo4j" in out
    assert "NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}" in out
    # 持久 volume
    assert "neo4jdata:/data" in out
    assert "  neo4jdata:" in out
    # app 服务注入 NEO4J_* 并依赖 neo4j healthy
    assert "NEO4J_URI: bolt://neo4j:7687" in out
    assert "NEO4J_PASSWORD: ${NEO4J_PASSWORD}" in out
    assert "neo4j:" in out and "condition: service_healthy" in out


def test_neo4j_absent_when_disabled_and_no_tenant_uses_it(tmp_path):
    mod = _load()
    out = mod.render_compose(_inventory(tmp_path))  # 无 neo4j 段，env 无 KNOWLEDGE_ENGINE
    assert "  neo4j:" not in out
    assert "NEO4J_URI" not in out
    assert "neo4jdata:" not in out


def test_neo4j_auto_detected_from_tenant_env(tmp_path):
    mod = _load()
    env_text = "KNOWLEDGE_ENGINE=ontology_neo4j\n"
    out = mod.render_compose(_inventory(tmp_path, tenant_env_text=env_text))  # 无 neo4j 段但租户启用
    assert "  neo4j:" in out
    assert "NEO4J_URI: bolt://neo4j:7687" in out


def test_neo4j_expose_ports_optional(tmp_path):
    mod = _load()
    out = mod.render_compose(_inventory(tmp_path, {"enabled": True, "expose_ports": True}))
    assert '"7474:7474"' in out
    assert '"7687:7687"' in out


def test_skip_validation_renders_without_env_file(tmp_path):
    """--skip-validation 时 env_file 不存在也能 render（无源码目标：env 在目标机不在主控）。"""
    mod = _load()
    inv = tmp_path / "hangzhou.json"
    inv.write_text(json.dumps({
        "project_name": "sales-agent",
        "image": "sales-agent:latest",
        "traefik": {"enabled": False},
        "tenants": [{
            "id": "fuduoduo", "name": "fuduoduo", "api_port": 8103,
            "env_file": "secrets/fuduoduo.env",   # 不存在
            "data_dir": "./data/fuduoduo", "logs_dir": "./logs/fuduoduo",
            "roles": ["api", "stream", "worker"],
        }],
    }))
    out = tmp_path / "compose.yml"
    rc = mod.main(["--skip-validation", str(inv), "--compose-out", str(out)])
    assert rc == 0
    assert "fuduoduo-api" in out.read_text()
