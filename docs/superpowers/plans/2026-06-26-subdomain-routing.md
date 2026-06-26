# 子域名多机分发 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `render-multitenant-deploy.py` so that tenants with a `domain` + optional `backend` generate a subdomain-based Traefik route (`Host(domain) && PathPrefix(/integrations/dingtalk/t/{id}/)` → `http://{backend or local container}:8000`), enabling the central Traefik on 本机 to reverse-proxy DingTalk quick-entry traffic to tenant APIs on remote machines (.219/.235).

**Architecture:** A single new `backend` field in tenants.json tells the render script where the tenant API actually lives (local docker container name by default, or a remote `host:port`). When `domain` is set, a new Traefik `Host`+`PathPrefix` router is always generated pointing to the API backend. The existing catch-all `Host(domain)`→frontend router is skipped for remote tenants (no local frontend container → would 502).

**Tech Stack:** Python 3.12 (stdlib only — no new deps), Traefik v3.3 YAML dynamic config, pytest

## Global Constraints

- 后端 CommonJS（`require`），前端 ES Modules — 本次改动全是 Python，不影响。
- 数据库变更必须走 Alembic migration — 本次无 DB 变更。
- 每次功能升级后自动更新 README — 本次改动不涉及用户可见功能，skip。
- 所有非琐碎改动必须记录到升级日志 — 实现合并时记录；本次改动兼容旧 inventory（`backend` 缺省=本机），现有租户配置无需修改。

---

### Task 1: Write failing tests for `render_traefik_routes` with `backend` + subdomain support

**Files:**
- Modify: `tests/unit/test_render_multitenant_deploy.py` (append at end)

**Interfaces:**
- Consumes: `render_traefik_routes(data: dict)` from `scripts/render-multitenant-deploy.py`
- Produces: 5 new test functions + helper `_inv_traefik`

- [ ] **Step 1: Add test helper and 5 tests**

Append to `tests/unit/test_render_multitenant_deploy.py`:

```python
def _inv_traefik(tmp_path, domain="", backend="", tenant_id="acme", env_text=""):
    """Build inventory dict for render_traefik_routes tests."""
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


def test_traefik_duplicate_domain_raises(tmp_path):
    """Two tenants with same domain → duplicate rule assertion fires."""
    mod = _load()
    env1 = tmp_path / "a.env"
    env1.write_text("DINGTALK_PUBLIC_URL=https://dup.aijiaolian.com.cn\n")
    env2 = tmp_path / "b.env"
    env2.write_text("DINGTALK_PUBLIC_URL=https://dup.aijiaolian.com.cn\n")
    data = {
        "project_name": "sales-agent",
        "tenants": [
            {"id": "one", "name": "One", "api_port": 8101,
             "env_file": str(env1),
             "domain": "dup.aijiaolian.com.cn",
             "data_dir": "./data/one", "logs_dir": "./logs/one",
             "roles": ["api", "stream", "worker"]},
            {"id": "two", "name": "Two", "api_port": 8102,
             "env_file": str(env2),
             "domain": "dup.aijiaolian.com.cn",
             "data_dir": "./data/two", "logs_dir": "./logs/two",
             "roles": ["api", "stream", "worker"]},
        ],
    }
    import subprocess, sys
    import json
    inv_path = tmp_path / "dup.json"
    inv_path.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(inv_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0, f"Expected non-zero exit, got {result.returncode}"
    assert "重复" in (result.stderr + result.stdout)
```

- [ ] **Step 2: Run tests — expect ALL 5 FAIL**

```bash
cd /root/code/sales-agent && .venv/bin/python -m pytest tests/unit/test_render_multitenant_deploy.py::test_traefik_subdomain_remote_backend tests/unit/test_render_multitenant_deploy.py::test_traefik_subdomain_local_no_backend tests/unit/test_render_multitenant_deploy.py::test_traefik_shared_pathprefix_unchanged tests/unit/test_render_multitenant_deploy.py::test_traefik_no_domain_no_backend tests/unit/test_render_multitenant_deploy.py::test_traefik_duplicate_domain_raises -v
```

Expected: 5 FAILED — subdomain routes + backend logic not yet implemented.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/unit/test_render_multitenant_deploy.py
git commit -m "test(render): add failing tests for subdomain+backend Traefik route generation

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Implement `render_traefik_routes` — backend resolution + subdomain dingtalk route

**Files:**
- Modify: `scripts/render-multitenant-deploy.py` (lines 452-508 → replace with new logic)

**Interfaces:**
- Consumes: `data["tenants"][*]["backend"]` (new optional field), `data["tenants"][*]["domain"]` (existing)
- Produces: `render_traefik_routes()` returns YAML string with new `{tenant}-sub-dingtalk` routers + services

- [ ] **Step 1: Read current `render_traefik_routes` body (lines 452-508) to verify edit context**

```bash
sed -n '452,508p' scripts/render-multitenant-deploy.py
```

- [ ] **Step 2: Replace lines 452-508 with the new implementation**

In `scripts/render-multitenant-deploy.py`, replace from line 452:

```
    for tenant in data["tenants"]:
        tenant_id = tenant["id"]
        domain = tenant.get("domain", "")
        env_path = Path(tenant["env_file"]).resolve()
        api_container = f"sales-agent-{tenant_id}-api"
        frontend_container = f"sales-agent-{tenant_id}-frontend"

        if domain:
            # Host-based route — 用户域名 → 前端 nginx 容器（SPA + API 代理）
            router_name = f"sales-agent-{tenant_id}"
            service_name = f"sales-agent-{tenant_id}-backend"
            lines.extend([
                f"    {router_name}:",
                f'      rule: "Host(`{domain}`)"',
                "      entryPoints:",
                "        - websecure",
                "      tls:",
                "        certResolver: letsencrypt",
                f"      service: {service_name}",
            ])
            if service_name not in seen_services:
                seen_services.add(service_name)
                service_lines.extend([
                    f"    {service_name}:",
                    "      loadBalancer:",
                    "        servers:",
                    f'          - url: "http://{frontend_container}:80"',
                ])

        # PathPrefix route for DingTalk integration (shared domain).
```

with:

```
    for tenant in data["tenants"]:
        tenant_id = tenant["id"]
        domain = tenant.get("domain", "")
        backend = tenant.get("backend", "")  # NEW: optional remote host:port
        env_path = Path(tenant["env_file"]).resolve()
        api_container = f"sales-agent-{tenant_id}-api"
        frontend_container = f"sales-agent-{tenant_id}-frontend"

        # Resolve dingtalk backend URL: remote or local container
        if backend:
            dingtalk_backend_url = f"http://{backend}"
        else:
            dingtalk_backend_url = f"http://{api_container}:8000"

        if domain and not backend:
            # Host-based route → 前端 nginx 容器（仅本机租户有前端容器）
            # 远端租户 (backend 有值) 跳过此 catch-all，避免 502
            router_name = f"sales-agent-{tenant_id}"
            service_name = f"sales-agent-{tenant_id}-backend"
            lines.extend([
                f"    {router_name}:",
                f'      rule: "Host(`{domain}`)"',
                "      entryPoints:",
                "        - websecure",
                "      tls:",
                "        certResolver: letsencrypt",
                f"      service: {service_name}",
            ])
            if service_name not in seen_services:
                seen_services.add(service_name)
                service_lines.extend([
                    f"    {service_name}:",
                    "      loadBalancer:",
                    "        servers:",
                    f'          - url: "http://{frontend_container}:80"',
                ])

        if domain:
            # NEW: 子域名 + DingTalk PathPrefix → 租户 API（本机或远端）
            # 这是快捷入口/免登录的核心路由：Host(subdomain) &&
            # PathPrefix(/integrations/dingtalk/t/{tenant_id}/) → api backend
            router_name = f"sales-agent-{tenant_id}-sub-dingtalk"
            service_name = f"sales-agent-{tenant_id}-sub-dingtalk-svc"
            lines.extend([
                f"    {router_name}:",
                f'      rule: "Host(`{domain}`) && PathPrefix(`/integrations/dingtalk/t/{tenant_id}/`)"',
                "      entryPoints:",
                "        - websecure",
                "      tls:",
                "        certResolver: letsencrypt",
                f"      service: {service_name}",
                "      priority: 210",
            ])
            if service_name not in seen_services:
                seen_services.add(service_name)
                service_lines.extend([
                    f"    {service_name}:",
                    "      loadBalancer:",
                    "        servers:",
                    f'          - url: "{dingtalk_backend_url}"',
                ])

        # PathPrefix route for DingTalk integration (shared domain).
```

- [ ] **Step 3: Verify edit — read back the changed function**

```bash
sed -n '452,540p' scripts/render-multitenant-deploy.py
```
Confirm: (a) `backend = tenant.get("backend", "")` present, (b) `dingtalk_backend_url` resolution present, (c) `if domain and not backend:` guards the catch-all, (d) `if domain:` block adds subdomain dingtalk route with `{tenant_id}-sub-dingtalk` naming, (e) shared PathPrefix block unchanged after new code.

- [ ] **Step 4: Run the 5 new tests — expect ALL PASS**

```bash
cd /root/code/sales-agent && .venv/bin/python -m pytest tests/unit/test_render_multitenant_deploy.py::test_traefik_subdomain_remote_backend tests/unit/test_render_multitenant_deploy.py::test_traefik_subdomain_local_no_backend tests/unit/test_render_multitenant_deploy.py::test_traefik_shared_pathprefix_unchanged tests/unit/test_render_multitenant_deploy.py::test_traefik_no_domain_no_backend tests/unit/test_render_multitenant_deploy.py::test_traefik_duplicate_domain_raises -v
```

Expected: 5 PASSED

- [ ] **Step 5: Run ALL existing render tests — expect zero regressions**

```bash
cd /root/code/sales-agent && .venv/bin/python -m pytest tests/unit/test_render_multitenant_deploy.py -v
```

Expected: ALL PASS (existing tests unchanged — no changes to `render_compose`, shared PathPrefix logic untouched)

- [ ] **Step 6: Commit**

```bash
git add scripts/render-multitenant-deploy.py
git commit -m "feat(render): subdomain + backend support for cross-machine tenant routing

Add optional 'backend' field per tenant (host:port of remote API). When domain
is set, generate Host(domain) && PathPrefix(/integrations/dingtalk/t/{id}/)
route pointing to the resolved backend (remote IP or local container).
Skip the catch-all Host(domain)→frontend route for remote tenants
(no local frontend container, would 502).

Backward-compatible: backend field defaults to empty, falling back to
local container name (existing behavior unchanged).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Document schema in example files

**Files:**
- Modify: `deploy/tenants.example.json`
- Modify: `deploy/tenant.env.example`

- [ ] **Step 1: Add `backend` field example to tenants.example.json**

In `deploy/tenants.example.json`, replace the `acme` tenant block (line 24-32) to include `domain` + `backend` documentation:

```json
    {
      "id": "acme",
      "name": "ACME Enterprise",
      "domain": "acme-agent.example.com",
      "backend": "",
      "_domain_comment": "子域名。有 domain 时自动生成 Host(domain) 路由。若本机有前端容器则另加 catch-all。",
      "_backend_comment": "可选。远端 API 的 host:port（如 172.25.186.210:8003）。缺省=本机 sales-agent-{id}-api:8000。",
      "api_port": 8101,
      "env_file": "secrets/acme.env",
      "data_dir": "./data/acme",
      "logs_dir": "./logs/acme",
      "roles": ["api", "stream", "worker"]
    },
```

- [ ] **Step 2: Document subdomain value in tenant.env.example**

In `deploy/tenant.env.example`, change line 51 from:

```
DINGTALK_PUBLIC_URL=https://acme-agent.example.com
```

to:

```
# 免登录/快捷入口的公开 URL。若分配了子域名，使用 https://{tenant}.aijiaolian.com.cn
# （需配合 tenants.json 的 domain 字段 + 钉钉 OAuth2 redirect_uri 白名单）。
DINGTALK_PUBLIC_URL=https://acme-agent.example.com
```

- [ ] **Step 3: Commit**

```bash
git add deploy/tenants.example.json deploy/tenant.env.example
git commit -m "docs(deploy): document domain + backend fields for subdomain routing

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Create `check-tenant-routing.sh` verification script

**Files:**
- Create: `scripts/check-tenant-routing.sh`

- [ ] **Step 1: Write the script**

```bash
#!/bin/bash
# Verify subdomain → backend routing health for a tenant.
# Usage: check-tenant-routing.sh <subdomain> [tenant_id]
#   subdomain:  full subdomain, e.g. songbai.aijiaolian.com.cn
#   tenant_id:  optional; defaults to first label of subdomain
#
# Exit 0 if backend reachable (any HTTP response except 502/000),
# exit 1 if unreachable.
set -euo pipefail

SUBDOMAIN="${1:?usage: check-tenant-routing.sh <subdomain> [tenant_id]}"
TENANT_ID="${2:-${SUBDOMAIN%%.*}}"
URL="https://${SUBDOMAIN}/integrations/dingtalk/t/${TENANT_ID}/quick"

echo "→ probing ${URL} ..."
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 "${URL}" 2>&1) || {
    echo "FAIL: curl error reaching ${SUBDOMAIN}"
    exit 1
}

if [ "$HTTP_CODE" = "502" ] || [ "$HTTP_CODE" = "000" ]; then
    echo "FAIL: ${SUBDOMAIN} → backend unreachable (HTTP ${HTTP_CODE})"
    exit 1
fi

echo "OK: ${SUBDOMAIN} → backend reachable (HTTP ${HTTP_CODE})"
```

- [ ] **Step 2: Make executable and test locally (expect 000 — no DNS yet)**

```bash
chmod +x scripts/check-tenant-routing.sh
./scripts/check-tenant-routing.sh songbai.aijiaolian.com.cn || echo "(expected failure — DNS not configured yet)"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/check-tenant-routing.sh
git commit -m "feat(ops): add check-tenant-routing.sh for subdomain health verification

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Integration test — Traefik loads generated config without error

**Files:**
- No code changes; verify end-to-end.

- [ ] **Step 1: Run the render script against actual tenants.json and check Traefik**

```bash
cd /root/code/sales-agent
# Re-render with current tenants.json
.venv/bin/python scripts/render-multitenant-deploy.py --traefik-out /tmp/test-subdomain-routes.yml deploy/tenants.json

# Show generated content
cat /tmp/test-subdomain-routes.yml

# Check Traefik can parse it (docker exec into traefik container)
docker exec sales-agent-traefik traefik validate --configDir=/etc/traefik 2>&1 | tail -5
```

Expected: `traefik validate` exits 0 (configuration is valid). If tenants.json doesn't have `domain` set on current tenants, generated output will be minimal — that's fine, the new code doesn't break existing inventories.

- [ ] **Step 2: Verify with a synthetic inventory that includes backend**

```bash
cat > /tmp/test-backend-inventory.json << 'EOF'
{
  "project_name": "sales-agent",
  "traefik": {"enabled": true, "dynamic_output": "/tmp/test-backend-routes.yml"},
  "tenants": [
    {
      "id": "songbai",
      "name": "Songbai",
      "domain": "songbai.aijiaolian.com.cn",
      "backend": "172.25.186.210:8003",
      "api_port": 8003,
      "env_file": "secrets/songbai.env",
      "data_dir": "./data/songbai",
      "logs_dir": "./logs/songbai",
      "roles": ["api", "stream", "worker"]
    }
  ]
}
EOF

cd /root/code/sales-agent
.venv/bin/python scripts/render-multitenant-deploy.py --traefik-out /tmp/test-backend-routes.yml --skip-validation /tmp/test-backend-inventory.json
cat /tmp/test-backend-routes.yml

# Verify key content
grep -q 'songbai-sub-dingtalk' /tmp/test-backend-routes.yml && echo "✓ subdomain router found"
grep -q '172.25.186.210:8003' /tmp/test-backend-routes.yml && echo "✓ remote backend URL found"
grep -q 'sales-agent-songbai-backend' /tmp/test-backend-routes.yml && echo "✗ catch-all should be absent (BUG)" || echo "✓ catch-all correctly absent"
```

Expected: all three ✓ checks pass.

- [ ] **Step 3: Cleanup temp files, commit**

```bash
rm -f /tmp/test-subdomain-routes.yml /tmp/test-backend-routes.yml /tmp/test-backend-inventory.json
echo "Integration test passed — no commit needed"
```

---

### Task 6: Final — run full test suite + commit

- [ ] **Step 1: Run full unit test suite**

```bash
cd /root/code/sales-agent && .venv/bin/python -m pytest tests/unit/ -v --timeout=60 2>&1 | tail -30
```

Expected: ALL PASS. If any unexpected failures, investigate before committing.

- [ ] **Step 2: Verify git status is clean**

```bash
cd /root/code/sales-agent && git status
```

Expected: only the committed changes on current branch, no uncommitted files.

- [ ] **Step 3: (If applicable) Push branch for PR**

```bash
git log --oneline -8
```

---

## Self-Review Checklist (for plan author)

- [x] Spec coverage: §5 (back-end resolution + route gen) → Tasks 1-2; §5.3 (catch-all guard) → Task 2; §6 (DINGTALK_PUBLIC_URL) → Task 3; §9 (verification script) → Task 4; §10 (testing) → Tasks 1+5+6
- [x] No placeholders — all steps have complete code, exact commands, expected output
- [x] Type consistency — `backend` field is `str` throughout; `dingtalk_backend_url` computed once per tenant; router naming `{tenant_id}-sub-dingtalk`/`{tenant_id}-sub-dingtalk-svc` consistent
- [x] Backward compatibility — `backend` defaults to `""`, falls back to `api_container:8000`; `backend` unset → exact same behavior as before; `validate_inventory` unchanged (no new mandatory field)
