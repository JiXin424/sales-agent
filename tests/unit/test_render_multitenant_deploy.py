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
    # render 的 neo4j 由 tenant knowledge_engine=ontology_neo4j 触发(非顶层 enabled),
    # 故给租户 env 显式启用 neo4j,使 neo4j 服务块被渲染。
    out = mod.render_compose(_inventory(
        tmp_path,
        {"enabled": True, "image": "registry.internal:5000/neo4j:5"},
        tenant_env_text="KNOWLEDGE_ENGINE=ontology_neo4j\n",
    ))
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
    # neo4j 由 tenant knowledge_engine=ontology_neo4j 触发,expose_ports 控制是否暴露端口。
    out = mod.render_compose(_inventory(
        tmp_path,
        {"enabled": True, "expose_ports": True},
        tenant_env_text="KNOWLEDGE_ENGINE=ontology_neo4j\n",
    ))
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


def _inv_traefik(tmp_path, domain="", backend="", tenant_id="", env_text=""):
    """Build inventory dict for render_traefik_routes tests.

    If *domain* is a multi-level domain and *tenant_id* is not given, the first
    subdomain component is used as the tenant ID (e.g. domain="songbai.x.y"
    → tenant_id="songbai").  This matches the expectation of the TDD test cases
    where router/service names embed the tenant ID.
    """
    if not tenant_id and domain:
        tenant_id = domain.split(".")[0]
    elif not tenant_id:
        tenant_id = "acme"
    env = tmp_path / f"{tenant_id}.env"
    env.write_text(env_text)
    tenant = {
        "id": tenant_id, "name": "ACME", "api_port": 8101,
        "env_file": str(env),
        "data_dir": f"./data/{tenant_id}", "logs_dir": f"./logs/{tenant_id}",
        "roles": ["api", "stream", "worker"],
    }
    if domain:
        tenant["domain"] = domain
    if backend:
        tenant["backend"] = backend
    return {"project_name": "sales-agent", "tenants": [tenant]}


def test_traefik_subdomain_remote_backend(tmp_path):
    """Tenant with domain + backend → subdomain route with remote URL, no catch-all."""
    mod = _load()
    data = _inv_traefik(tmp_path, domain="songbai.aijiaolian.com.cn",
                        backend="172.25.186.210:8003")
    out = mod.render_traefik_routes(data)

    # Subdomain dingtalk router exists
    assert "    sales-agent-songbai-sub-dingtalk:" in out
    assert 'Host(`songbai.aijiaolian.com.cn`) && PathPrefix(`/integrations/dingtalk/t/songbai/`)' in out
    assert "priority: 210" in out
    assert "certResolver: letsencrypt" in out

    # Remote backend URL in service
    assert "    sales-agent-songbai-sub-dingtalk-svc:" in out
    assert 'url: "http://172.25.186.210:8003"' in out

    # Catch-all Host→frontend MUST be absent for remote tenant
    assert "    sales-agent-songbai:\n" not in out
    assert "sales-agent-songbai-backend" not in out


def test_traefik_subdomain_local_no_backend(tmp_path):
    """Tenant with domain but no backend → catch-all preserved + subdomain route added."""
    mod = _load()
    data = _inv_traefik(tmp_path, domain="taishan.aijiaolian.com.cn")
    out = mod.render_traefik_routes(data)

    # Catch-all Host→frontend STILL exists (existing behavior for local tenant)
    assert "    sales-agent-taishan:" in out
    assert 'rule: "Host(`taishan.aijiaolian.com.cn`)"' in out
    assert "sales-agent-taishan-backend:" in out
    assert 'url: "http://sales-agent-taishan-frontend:80"' in out

    # Subdomain dingtalk route ALSO exists
    assert "    sales-agent-taishan-sub-dingtalk:" in out
    assert 'Host(`taishan.aijiaolian.com.cn`) && PathPrefix(`/integrations/dingtalk/t/taishan/`)' in out
    assert "priority: 210" in out

    # Local backend (container name) in subdomain service
    assert "    sales-agent-taishan-sub-dingtalk-svc:" in out
    assert 'url: "http://sales-agent-taishan-api:8000"' in out


def test_traefik_shared_pathprefix_unchanged(tmp_path):
    """Tenant with DINGTALK_PUBLIC_URL but no domain → shared PathPrefix unchanged."""
    mod = _load()
    data = _inv_traefik(tmp_path, env_text="DINGTALK_PUBLIC_URL=https://aijiaolian.com.cn\n")
    out = mod.render_traefik_routes(data)

    # Shared PathPrefix route exists (existing behavior unchanged)
    assert "    sales-agent-acme-dingtalk:" in out
    assert 'Host(`aijiaolian.com.cn`) && PathPrefix(`/integrations/dingtalk/t/acme/`)' in out
    assert "priority: 210" in out

    # NO subdomain route (no domain set)
    assert "    sales-agent-acme-sub-dingtalk:" not in out

    # NO catch-all (no domain set)
    assert "    sales-agent-acme:\n" not in out


def test_traefik_no_domain_no_backend(tmp_path):
    """Tenant with neither domain, backend, nor public_url → empty routes (no crash)."""
    mod = _load()
    data = _inv_traefik(tmp_path)
    out = mod.render_traefik_routes(data)

    # No routers generated at all (no domain, no public_url)
    assert "    sales-agent-acme-dingtalk:" not in out
    assert "    sales-agent-acme-sub-dingtalk:" not in out
    assert "    sales-agent-acme:\n" not in out


def test_traefik_two_tenants_with_subdomains(tmp_path):
    """Two tenants each with distinct domains → both get subdomain routes, no collision."""
    mod = _load()
    env1 = tmp_path / "songbai.env"
    env1.write_text("DINGTALK_PUBLIC_URL=https://aijiaolian.com.cn\n")
    env2 = tmp_path / "fuduoduo.env"
    env2.write_text("DINGTALK_PUBLIC_URL=https://aijiaolian.com.cn\n")
    data = {
        "project_name": "sales-agent",
        "tenants": [
            {"id": "songbai", "name": "Songbai", "api_port": 8101,
             "env_file": str(env1),
             "domain": "songbai.aijiaolian.com.cn",
             "backend": "172.25.186.210:8003",
             "data_dir": "./data/songbai", "logs_dir": "./logs/songbai",
             "roles": ["api", "stream", "worker"]},
            {"id": "fuduoduo", "name": "Fuduoduo", "api_port": 8102,
             "env_file": str(env2),
             "domain": "fuduoduo.aijiaolian.com.cn",
             "backend": "47.118.16.235:8103",
             "data_dir": "./data/fuduoduo", "logs_dir": "./logs/fuduoduo",
             "roles": ["api", "stream", "worker"]},
        ],
    }
    out = mod.render_traefik_routes(data)

    # Both tenants have sub-dingtalk routers
    assert "    sales-agent-songbai-sub-dingtalk:" in out
    assert "    sales-agent-fuduoduo-sub-dingtalk:" in out
    # Both have correct backend URLs
    assert 'url: "http://172.25.186.210:8003"' in out
    assert 'url: "http://47.118.16.235:8103"' in out
    # No catch-all for either (both are remote)
    assert "sales-agent-songbai-backend" not in out
    assert "sales-agent-fuduoduo-backend" not in out
    # No duplicate rule assertion — different PathPrefix (/t/songbai/ vs /t/fuduoduo/)
