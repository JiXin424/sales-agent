# Ontology / Neo4j 接入 CI 自动部署 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让手动触发的 `deploy` workflow 在 prod3/杭州自动起 Neo4j、跑 pg migration、以 `ontology_neo4j` 引擎拉起应用；prod2 dev 走同一生成器本地起。

**Architecture:** 共享单实例 Neo4j（每台一个容器，所有租户共用 `bolt://neo4j:7687`，靠图中属性隔离）。生成器 `render-multitenant-deploy.py` 按 inventory 的 `neo4j` 段条件渲染 Neo4j 服务并向 app 服务注入 `NEO4J_*` env；凭证 `NEO4J_PASSWORD` 由 `secrets/neo4j.env` 经 `docker compose --env-file` 插值 `${NEO4J_PASSWORD}`，同时供给 Neo4j 容器 `NEO4J_AUTH` 与 app 容器；entrypoint 在 api 角色启动前跑 `alembic upgrade head`。

**Tech Stack:** Python 3.10（生成器纯标准库）、Docker Compose、Gitea Actions、Alembic、Neo4j 5、Bash。

## Global Constraints

- Neo4j **共享单实例**：`container_name: sales-agent-neo4j`，`bolt://neo4j:7687`，单 database `neo4j`，租户靠图中 `tenant_id`/`agent_id` 属性隔离（不改代码的多 database 路由）。
- 凭证**不进 git、不进 generated compose 字面量**：compose 里只出现 `${NEO4J_PASSWORD}` 占位，值来自 `secrets/neo4j.env`（gitignore，600）。
- pg migration **仅 api 角色**在启动前跑 `alembic upgrade head`（幂等），失败 `exit 1`；stream/worker 不跑。
- Neo4j 镜像统一 `registry.internal:5000/neo4j:5`（CI mirror）；`docker.1ms.run/neo4j:5` 仅本地临时验证。
- **不改 CI 触发方式**（保持 `workflow_dispatch` 手动）。
- 依赖已就位：`alembic>=1.13.0`、`neo4j>=5.23.0`（pyproject.toml:25-26），镜像已装。
- 后端 Python；新增测试放 `tests/unit/`，pytest + pytest-asyncio。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `scripts/render-multitenant-deploy.py` | 生成多租户 compose + traefik | 修改：渲染 Neo4j 服务 + app `NEO4J_*` env 注入 |
| `tests/unit/test_render_multitenant_deploy.py` | 生成器单测 | 新增 |
| `scripts/docker-entrypoint.sh` | 按 PROCESS_ROLE 启动 | 修改：api 分支加 alembic upgrade |
| `tests/unit/test_entrypoint_migration.py` | entrypoint 冒烟测试 | 新增 |
| `scripts/deploy-release.sh` | 一键部署 | 修改：条件 `--env-file secrets/neo4j.env` |
| `deploy/tenants.example.json` | inventory 模板 | 修改：加 `neo4j` 段 |
| `deploy/tenants.prod2.json` / `prod3.json` / `test.json` | 各环境 inventory | 修改：加 `neo4j` 段 |
| `secrets/example.env` | 租户 env 模板 | 修改：补 `KNOWLEDGE_ENGINE` 段 |
| `secrets/neo4j.env.example` | 共享 Neo4j 凭证模板 | 新增 |
| `.gitea/workflows/deploy.yml` | CI 流水线 | 修改：mirror neo4j 镜像 step |
| `changelog/2026-06-25.md` | 升级日志 | 追加 |
| `docs/deploy/cicd-gitea.md` | CI/CD 权威文档 | 修订（补 neo4j + 迁移后状态） |

**职责边界**：生成器是唯一的 compose 真相来源；entrypoint 只管启动顺序；deploy 脚本只管编排（pull/up/healthcheck）；inventory 是声明式配置；secrets 是运行时凭证。互不越界。

---

### Task 1: 生成器渲染 Neo4j 服务并注入 app env

**Files:**
- Modify: `scripts/render-multitenant-deploy.py`（`render_compose` :149-198、`render_tenant_services` :201-295）
- Test: `tests/unit/test_render_multitenant_deploy.py`

**Interfaces:**
- Consumes: inventory `data["neo4j"]`（可选段：`enabled`/`image`/`expose_ports`）；租户 env 的 `KNOWLEDGE_ENGINE`（自动检测用）
- Produces: `render_compose(data)` 在 Neo4j 启用时输出额外服务与 volume；`render_tenant_services` 签名变为 `(tenant, image, database_url, neo4j_enabled=False)`

- [ ] **Step 1: 写失败测试（新增 `tests/unit/test_render_multitenant_deploy.py`）**

```python
"""生成器 render-multitenant-deploy.py 的单测。

脚本文件名含连字符无法直接 import，用 importlib 按路径加载。
"""
import importlib.util
import pathlib

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/code/sales-agent && python -m pytest tests/unit/test_render_multitenant_deploy.py -v`
Expected: FAIL（`render_tenant_services` 不接受 `neo4j_enabled` / 输出无 neo4j 块）。

- [ ] **Step 3: 实现 — 加 `_neo4j_enabled` 与渲染逻辑**

在 `render-multitenant-deploy.py` 的 `VALID_ROLES` 定义后、`def main` 前插入：

```python
def _neo4j_enabled(data: dict[str, Any]) -> bool:
    """inventory 显式开关优先；缺省时扫描租户 env 自动检测。"""
    cfg = data.get("neo4j") or {}
    if "enabled" in cfg:
        return bool(cfg["enabled"])
    for tenant in data.get("tenants", []):
        env_path = Path(tenant.get("env_file", "")).resolve()
        if _env_has(env_path, "KNOWLEDGE_ENGINE", "ontology_neo4j"):
            return True
    return False
```

> 注意：`_env_has` 定义在 :134，签名 `(env_path, key, value)`。放在其后或前均可（Python 函数体在调用时才解析名字）。

- [ ] **Step 4: 实现 — 改 `render_compose`**

把 `render_compose` 中这段（:193-198）：

```python
    database_url = f"postgresql+asyncpg://{db_user}:{db_password}@postgres:5432/{db_name}"
    for tenant in data["tenants"]:
        lines.extend(render_tenant_services(tenant, image, database_url))

    lines.extend(["volumes:", "  pgdata:", ""])
    return "\n".join(lines)
```

替换为：

```python
    database_url = f"postgresql+asyncpg://{db_user}:{db_password}@postgres:5432/{db_name}"
    neo4j_on = _neo4j_enabled(data)
    for tenant in data["tenants"]:
        lines.extend(render_tenant_services(tenant, image, database_url, neo4j_on))

    if neo4j_on:
        neo4j_cfg = data.get("neo4j") or {}
        neo4j_image = neo4j_cfg.get("image", "registry.internal:5000/neo4j:5")
        lines.extend([
            "  neo4j:",
            f"    image: {neo4j_image}",
            f"    container_name: {project_name}-neo4j",
            "    restart: unless-stopped",
            "    environment:",
            "      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}",
        ])
        if neo4j_cfg.get("expose_ports", False):
            lines += [
                "    ports:",
                '      - "7474:7474"',
                '      - "7687:7687"',
            ]
        lines += [
            "    volumes:",
            "      - neo4jdata:/data",
            "    healthcheck:",
            '      test: ["CMD-SHELL", "cypher-shell -u neo4j -p ${NEO4J_PASSWORD} \'RETURN 1\' || exit 1"]',
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 12",
            "      start_period: 30s",
            "",
        ]

    lines.extend(["volumes:", "  pgdata:"])
    if neo4j_on:
        lines.append("  neo4jdata:")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 5: 实现 — 改 `render_tenant_services` 签名与注入**

把签名（:201）改为：

```python
def render_tenant_services(tenant: dict[str, Any], image: str, database_url: str, neo4j_enabled: bool = False) -> list[str]:
```

在 `api` 块的 `environment` 列表里（:218-220，`DATABASE_URL` 那行之后）条件追加 Neo4j env；在 `depends_on`（:226-228）条件追加 neo4j。改后的 `api` 块关键部分：

```python
        lines.extend(
            [
                f"  {tenant_id}-api:",
                f"    image: {image}",
                f"    container_name: sales-agent-{tenant_id}-api",
                "    restart: unless-stopped",
                "    env_file:",
                f"      - ./{env_file}",
                "    environment:",
                "      PROCESS_ROLE: api",
                f"      DATABASE_URL: {database_url}",
            ]
        )
        if neo4j_enabled:
            lines += [
                "      NEO4J_URI: bolt://neo4j:7687",
                "      NEO4J_USER: neo4j",
                "      NEO4J_PASSWORD: ${NEO4J_PASSWORD}",
                "      NEO4J_DATABASE: neo4j",
            ]
        lines += [
            "    volumes:",
            f"      - {data_dir}:/data/{tenant_id}",
            f"      - {logs_dir}:/logs/{tenant_id}",
            "    ports:",
            f'      - "{tenant["api_port"]}:8000"',
            "    depends_on:",
            "      postgres:",
            "        condition: service_healthy",
        ]
        if neo4j_enabled:
            lines += [
                "      neo4j:",
                "        condition: service_healthy",
            ]
        lines.append("")
```

对 `stream` 块（:252-271）与 `worker` 块（:273-293）做**同样**的条件追加：
- `environment` 的 `DATABASE_URL` 行后加同样的 4 行 `NEO4J_*`（用 `if neo4j_enabled`）。
- `depends_on` 的 `postgres: condition: service_healthy` 后加同样的 2 行 `neo4j:`（用 `if neo4j_enabled`）。

> stream/worker 块原本的 `volumes`/`ports` 结构保持不变，仅在 environment 与 depends_on 两处插入条件块。前端 `{tenant_id}-frontend`（:236-250）**不加** Neo4j 配置。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /root/code/sales-agent && python -m pytest tests/unit/test_render_multitenant_deploy.py -v`
Expected: 4 passed。

- [ ] **Step 7: 冒烟 — 用真实 inventory 生成一次 compose**

Run: `cd /root/code/sales-agent && python3 scripts/render-multitenant-deploy.py deploy/tenants.prod3.json --compose-out /tmp/compose-prod3.yml && grep -E 'neo4j:|NEO4J_AUTH|neo4jdata' /tmp/compose-prod3.yml`
Expected: 看到 neo4j 服务块（prod3.json 改完前可能还没有 neo4j 段，此步在 Task 4 后再验；此处先确认脚本不报错）。

- [ ] **Step 8: Commit**

```bash
git add scripts/render-multitenant-deploy.py tests/unit/test_render_multitenant_deploy.py
git commit -m "feat(deploy): render neo4j service and inject NEO4J_* env in multi-tenant generator"
```

---

### Task 2: entrypoint 在 api 角色启动前跑 alembic upgrade

**Files:**
- Modify: `scripts/docker-entrypoint.sh`（api 分支 :29-33）
- Test: `tests/unit/test_entrypoint_migration.py`

**Interfaces:**
- Consumes: 容器内 `DATABASE_URL`（已由 compose 注入）、`alembic.ini`（/app，WORKDIR）
- Produces: api 容器启动前 pg schema 升至 head；`RUN_MIGRATIONS` 开关（默认 `1`）

- [ ] **Step 1: 写冒烟测试（新增 `tests/unit/test_entrypoint_migration.py`）**

```python
"""docker-entrypoint.sh 的冒烟测试：语法 + 关键逻辑断言。

shell 脚本不做完整 TDD；用 bash -n + 内容断言保证 migration 接入正确。
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts" / "docker-entrypoint.sh"


def test_bash_syntax_ok():
    r = subprocess.run(["bash", "-n", str(ENTRY)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_api_role_runs_migrations_with_gate():
    text = ENTRY.read_text(encoding="utf-8")
    assert "alembic upgrade head" in text
    assert "RUN_MIGRATIONS" in text
    # 必须在 api 分支内（exec sales-agent serve 之前）
    assert "alembic upgrade head" in text.split("sales-agent serve")[0]


def test_stream_worker_do_not_run_migrations():
    text = ENTRY.read_text(encoding="utf-8")
    # migration 调用只应出现一次（仅 api 分支）
    assert text.count("alembic upgrade head") == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/code/sales-agent && python -m pytest tests/unit/test_entrypoint_migration.py -v`
Expected: FAIL（`alembic upgrade head` 不在脚本里）。

- [ ] **Step 3: 实现 — 改 api 分支**

把 `scripts/docker-entrypoint.sh` 的 api 分支（:28-33）：

```bash
  api)
    exec sales-agent serve --host 0.0.0.0 --port "${PORT}"
    ;;
```

替换为：

```bash
  api)
    # 仅 api 角色跑 pg migration（幂等）；stream/worker 不跑，避免并发竞争。
    # RUN_MIGRATIONS=0 可跳过（调试用）。
    if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
      echo "Running alembic upgrade head..."
      alembic upgrade head || { echo "ERROR: alembic upgrade failed"; exit 1; }
    fi
    exec sales-agent serve --host 0.0.0.0 --port "${PORT}"
    ;;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/code/sales-agent && python -m pytest tests/unit/test_entrypoint_migration.py -v`
Expected: 3 passed。

- [ ] **Step 5: 本地集成验证（手动，确认镜像内 alembic 可用）**

Run: `cd /root/code/sales-agent && docker build -t sales-agent:neo4j-test . && docker run --rm -e PROCESS_ROLE=api -e DATABASE_URL=postgresql+asyncpg://x:x@db/x sales-agent:neo4j-test bash -lc 'alembic --version && echo OK'`
Expected: 打印 alembic 版本 + `OK`（确认镜像内 alembic 命令可用、`alembic.ini` 在 WORKDIR）。migration 实际执行在部署后由真实 DB 验证。

- [ ] **Step 6: Commit**

```bash
git add scripts/docker-entrypoint.sh tests/unit/test_entrypoint_migration.py
git commit -m "feat(entrypoint): run alembic upgrade head before api role starts"
```

---

### Task 3: deploy-release.sh 条件注入 neo4j env-file

**Files:**
- Modify: `scripts/deploy-release.sh`（第 9 步 :800-801）

**Interfaces:**
- Consumes: `secrets/neo4j.env`（存在则注入）
- Produces: `docker compose up -d` 携带 `--env-file secrets/neo4j.env`，使 compose 内 `${NEO4J_PASSWORD}` 插值生效

- [ ] **Step 1: 写冒烟测试（追加到 `tests/unit/test_entrypoint_migration.py` 同目录的新文件 `tests/unit/test_deploy_release_envfile.py`）**

```python
"""deploy-release.sh 冒烟测试：--env-file neo4j.env 注入。"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-release.sh"


def test_bash_syntax_ok():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_conditional_env_file_injection():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--env-file" in text
    assert "secrets/neo4j.env" in text
    # 必须是条件注入（文件存在才加），不能无条件硬加
    assert "-f" in text and "neo4j.env" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/code/sales-agent && python -m pytest tests/unit/test_deploy_release_envfile.py -v`
Expected: FAIL（`--env-file` 不在脚本里）。

- [ ] **Step 3: 实现 — 改第 9 步**

把 `scripts/deploy-release.sh` 的第 9 步（:800-801）：

```bash
echo "Starting services with $COMPOSE_FILE"
docker compose -f "$COMPOSE_FILE" up -d
```

替换为：

```bash
# 若存在共享 Neo4j 凭证文件，注入给 compose 的 ${NEO4J_PASSWORD} 插值。
ENV_FILE_ARGS=()
if [ -f "secrets/neo4j.env" ]; then
  ENV_FILE_ARGS+=(--env-file secrets/neo4j.env)
fi

echo "Starting services with $COMPOSE_FILE"
docker compose -f "$COMPOSE_FILE" "${ENV_FILE_ARGS[@]}" up -d
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/code/sales-agent && python -m pytest tests/unit/test_deploy_release_envfile.py -v`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy-release.sh tests/unit/test_deploy_release_envfile.py
git commit -m "feat(deploy): inject secrets/neo4j.env via --env-file when present"
```

---

### Task 4: inventory 与 secrets 模板补 Neo4j 配置

**Files:**
- Modify: `deploy/tenants.example.json`、`deploy/tenants.prod2.json`、`deploy/tenants.prod3.json`、`deploy/tenants.test.json`
- Modify: `secrets/example.env`
- Create: `secrets/neo4j.env.example`

**Interfaces:**
- Consumes: Task 1 的生成器（消费 `neo4j` 段）
- Produces: 三套环境 inventory 声明 `neo4j.enabled=true` + 镜像；租户模板声明 `KNOWLEDGE_ENGINE`；共享凭证模板

- [ ] **Step 1: 在 `deploy/tenants.example.json` 顶层加 neo4j 段**

在 `"database": {...}` 块之后、`"traefik"` 之前插入：

```json
  "neo4j": {
    "enabled": true,
    "image": "registry.internal:5000/neo4j:5",
    "expose_ports": false
  },
```

- [ ] **Step 2: 在 `deploy/tenants.prod2.json` 加 neo4j 段（enabled=true，同 VPC 用 registry）**

在 `"database": {...}` 块之后插入：

```json
  "neo4j": {
    "enabled": true,
    "image": "registry.internal:5000/neo4j:5",
    "expose_ports": false
  },
```

- [ ] **Step 3: 在 `deploy/tenants.prod3.json` 加同样的 neo4j 段**

（同 Step 2 内容，插入位置相同。）

- [ ] **Step 4: 在 `deploy/tenants.test.json` 加同样的 neo4j 段**

（同 Step 2 内容，插入位置相同。）

- [ ] **Step 5: 在 `secrets/example.env` 末尾补 KNOWLEDGE_ENGINE 段**

在文件末尾追加：

```env

# --- Knowledge engine / Ontology ---
# Knowledge engine: legacy_rag | ontology_neo4j
KNOWLEDGE_ENGINE=ontology_neo4j
ONTOLOGY_VECTOR_FALLBACK=conservative
# Neo4j 连接信息（URI/USER/PASSWORD/DATABASE）由生成器统一注入 compose，
# 共享密码来自 secrets/neo4j.env（${NEO4J_PASSWORD} 插值），不在此处重复。
```

- [ ] **Step 6: 新建 `secrets/neo4j.env.example`**

```env
# ============================================================
# ⚠️  EXAMPLE / TEMPLATE — DO NOT FILL WITH REAL SECRETS
# ============================================================
# 共享 Neo4j 凭证模板。每台部署机复制为 secrets/neo4j.env（gitignore, 600）。
# scripts/deploy-release.sh 通过 --env-file 注入，供 compose 的 ${NEO4J_PASSWORD}
# 插值，同时填给 neo4j 容器 NEO4J_AUTH 与 app 容器 NEO4J_PASSWORD。
#
#   cp secrets/neo4j.env.example secrets/neo4j.env
#   chmod 600 secrets/neo4j.env
#   # 然后把下面密码换成强密码
# ============================================================

NEO4J_PASSWORD=change-me-strong-password
```

- [ ] **Step 7: 验证 JSON 合法 + 生成器消费**

Run: `cd /root/code/sales-agent && for f in deploy/tenants.example.json deploy/tenants.prod2.json deploy/tenants.prod3.json deploy/tenants.test.json; do python3 -c "import json,sys; json.load(open('$f')); print('OK $f')"; done`
Expected: 4 行 `OK`。

Run: `cd /root/code/sales-agent && python3 scripts/render-multitenant-deploy.py deploy/tenants.prod3.json --compose-out /tmp/c.yml && grep -cE 'neo4j:|NEO4J_AUTH|NEO4J_URI' /tmp/c.yml`
Expected: 计数 ≥ 3（neo4j 服务块 + app env 都在）。

- [ ] **Step 8: 确认 .gitignore 覆盖 `secrets/neo4j.env`**

Run: `cd /root/code/sales-agent && grep -nE 'neo4j.env|secrets/\*' .gitignore || echo "NEEDS_ADD"`
Expected: 已有匹配；若输出 `NEEDS_ADD`，在 `.gitignore` 的 secrets 段加 `secrets/neo4j.env`（保留 `!secrets/neo4j.env.example` 与 `!secrets/example.env` 例外）。

- [ ] **Step 9: Commit**

```bash
git add deploy/tenants.example.json deploy/tenants.prod2.json deploy/tenants.prod3.json deploy/tenants.test.json secrets/example.env secrets/neo4j.env.example .gitignore
git commit -m "feat(deploy): add neo4j section to inventory templates and secrets templates"
```

---

### Task 5: CI workflow mirror neo4j 镜像到 registry

**Files:**
- Modify: `.gitea/workflows/deploy.yml`（`build-and-push` job :29-53 之间）

**Interfaces:**
- Consumes: `${REGISTRY}` env（:11，`registry.internal:5000`）
- Produces: registry 内有 `neo4j:5`，供杭州跨地域稳定拉取

- [ ] **Step 1: 在 `build-and-push` job 末尾加 mirror step**

在 `.gitea/workflows/deploy.yml` 的 `Push frontend image` step（:48-53）之后、`deploy-fanout` job（:55）之前插入：

```yaml
      - name: Mirror neo4j image to registry
        run: |
          docker pull docker.1ms.run/neo4j:5
          docker tag  docker.1ms.run/neo4j:5 ${REGISTRY}/neo4j:5
          docker push ${REGISTRY}/neo4j:5
          echo "::notice::mirrored ${REGISTRY}/neo4j:5"
```

- [ ] **Step 2: 验证 YAML 合法**

Run: `cd /root/code/sales-agent && python3 -c "import yaml; yaml.safe_load(open('.gitea/workflows/deploy.yml')); print('YAML OK')"`
Expected: `YAML OK`。

- [ ] **Step 3: Commit**

```bash
git add .gitea/workflows/deploy.yml
git commit -m "ci: mirror neo4j:5 base image to registry for cross-region pulls"
```

---

### Task 6: 文档 — changelog、cicd-gitea.md 修订、README

**Files:**
- Modify: `changelog/2026-06-25.md`（追加）、`docs/deploy/cicd-gitea.md`（修订）、`README.md`（如部署节涉及）

**Interfaces:**
- 无代码接口；记录本次改动 + 修正 doc 与代码漂移

- [ ] **Step 1: 追加 `changelog/2026-06-25.md`**

在文件末尾追加一节（若文件不存在则新建）：

```markdown
## ontology/neo4j 接入 CI 自动部署

- **对象**：`scripts/render-multitenant-deploy.py`、`scripts/docker-entrypoint.sh`、`scripts/deploy-release.sh`、`.gitea/workflows/deploy.yml`、`deploy/tenants.*.json`、`secrets/example.env`、`secrets/neo4j.env.example`
- **类型**：feat（部署链路）
- **影响范围**：prod3、杭州 test、prod2 dev 三台启用 ontology_neo4j 引擎
- **改动明细**：
  - 生成器按 inventory `neo4j` 段（或缺省时按租户 `KNOWLEDGE_ENGINE` 自动检测）渲染共享 neo4j 容器（`bolt://neo4j:7687`、持久 `neo4jdata` volume、cypher-shell healthcheck），并向每个 api/stream/worker 注入 `NEO4J_*` env + `depends_on: neo4j(service_healthy)`。
  - entrypoint 在 api 角色启动前跑 `alembic upgrade head`（`RUN_MIGRATIONS` 开关，默认开，失败 exit 1）；stream/worker 不跑。
  - `deploy-release.sh` 当 `secrets/neo4j.env` 存在时以 `--env-file` 注入，驱动 compose `${NEO4J_PASSWORD}` 插值，neo4j 容器 `NEO4J_AUTH` 与 app `NEO4J_PASSWORD` 共享同一来源。
  - CI `build-and-push` 新增 mirror step，把 `docker.1ms.run/neo4j:5` 推到 `registry.internal:5000/neo4j:5`，解决杭州跨地域拉取。
  - 三套 `tenants.*.json` 加 `neo4j.enabled=true`；`secrets/example.env` 补 `KNOWLEDGE_ENGINE=ontology_neo4j`；新增 `secrets/neo4j.env.example`。
- **原因**：ontology 代码已就绪但 CI 部署链路未接 neo4j（生成器无 neo4j 服务、entrypoint 不跑 migration、env 未启用引擎），push 后无法自动起 neo4j。
- **回退**：租户 env `KNOWLEDGE_ENGINE=legacy_rag` 即禁用引擎，neo4j 容器仍起但不被使用。
```

- [ ] **Step 2: 修订 `docs/deploy/cicd-gitea.md` 消除漂移 + 补 neo4j**

该文档当前描述旧拓扑（主控=47.120.50.181、push 自动触发、3 fan-out 含 image-retag）。按现状修订关键节：
- §1 概览图：主控改为 prod3 `47.120.55.219`；触发改为 `workflow_dispatch` 手动；fan-out 改为 prod3 + 杭州 2 台（prod2 手动管）。
- §6 workflow：触发改为仅 `workflow_dispatch`；job 列表补「mirror neo4j 镜像」step。
- 新增 §X「Neo4j / Ontology 部署」：说明 neo4j 共享单实例、`secrets/neo4j.env` 凭证、entrypoint 自动 migration、回退方式（`KNOWLEDGE_ENGINE=legacy_rag`）。

> 实操：逐节 Edit，先改 §1 概览图与角色表，再改 §6，最后追加 neo4j 节。改完通读确认无残留旧 IP（47.120.50.181 作为主控的描述）。

- [ ] **Step 3: 检查 `README.md`「产品文档对照」/部署节**

Run: `cd /root/code/sales-agent && grep -nE 'neo4j|ontology|部署|deploy|更新日志' README.md | head`
若 README 部署节提到「push 自动部署」或未提 neo4j，按现状更新（手动触发 + neo4j 已接入）。若无部署细节则跳过。

- [ ] **Step 4: Commit**

```bash
git add changelog/2026-06-25.md docs/deploy/cicd-gitea.md README.md
git commit -m "docs: changelog + cicd-gitea topology fix + neo4j deployment section"
```

---

## Self-Review 记录

- **Spec coverage**：spec §5.1→Task 1、§5.2→Task 2、§5.5→Task 3、§5.3+5.4→Task 4、§5.6→Task 5、§10 文档→Task 6。全覆盖。
- **Placeholder**：无 TBD/TODO；所有代码块完整。
- **Type consistency**：`_neo4j_enabled`、`neo4j_enabled` 参数名跨任务一致；`render_tenant_services` 新签名 `(tenant, image, database_url, neo4j_enabled=False)` 在 Task 1 定义并被 `render_compose` 调用。
- **依赖顺序**：Task 1（生成器）→ Task 4（inventory，靠生成器验证）；Task 2/3/5 独立；Task 6 最后。
