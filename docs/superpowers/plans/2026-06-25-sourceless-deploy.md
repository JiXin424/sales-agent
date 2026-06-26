# 无源码目标机部署 — 实现计划

> **For agentic workers:** 用 superpowers:subagent-driven-development（推荐）或 executing-plans 逐任务实现。步骤用 `- [ ]` 跟踪。

**Goal:** 让杭州等无源码目标机通过「CI 构建 deploy 镜像（内含 rendered compose + 部署脚本）→ 目标机 `docker run` 执行」完成部署，不 scp、不 git、不放源码。

**Architecture:** CI build 时 render 各无源码目标的 `compose-<env>.yml`，和 `deploy-remote.sh` 一起打进 `sales-agent-deploy` 镜像推 registry；目标机 `docker run --rm -v docker.sock -v secrets:/secrets:ro sales-agent-deploy:<sha> <env>`，容器内 pull app/neo4j 镜像 + compose up + healthcheck。prod3 本地仍 `deploy-release`（有源码）。

**Tech Stack:** Docker（`docker:24-cli` base）、Bash、Python（render，纯标准库）、Gitea Actions。

## Global Constraints

- 无源码目标机**只有**：docker + docker compose、registry 登录、`/etc/docker/certs.d/registry.internal:5000/ca.crt`、`secrets/`（租户 env + `neo4j.env`）。无 src/scripts/.git。
- compose 与部署脚本**只通过 deploy 镜像走 registry**传输，不 scp、不 git pull。
- secrets **各机本地**从 `example.env`/`neo4j.env.example` 创建（gitignored，不进 CI）。
- deploy 镜像 base = `docker:24-cli`（含 docker cli + 装 docker-compose plugin）。
- 杭州 fuduoduo：`api_port=8103`，`env_file=secrets/fuduoduo.env`，`neo4j.enabled=true`，`traefik.enabled=false`。
- prod3（主控，`deploy-release` + `local:true`）、prod2（开发）**保留源码 + 现有部署方式不变**。
- 测试用 `.venv/bin/pytest`；shell 脚本用 `bash -n` + 内容断言冒烟。
- `registry.internal:5000`，镜像名 `sales-agent` / `sales-agent-deploy` / `neo4j:5`。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `scripts/render-multitenant-deploy.py` | compose 渲染 | 修改：加 `--skip-validation`（无源码目标 env 不在主控时用） |
| `tests/unit/test_render_multitenant_deploy.py` | 渲染器测试 | 扩展：`--skip-validation` 断言 |
| `deploy/Dockerfile` | deploy 镜像 | 新增 |
| `deploy/deploy-remote.sh` | deploy 镜像 entrypoint（目标机部署） | 新增 |
| `tests/unit/test_deploy_remote.sh` | deploy-remote 冒烟 | 新增 |
| `deploy/tenants.hangzhou.json` | 杭州 inventory | 新增 |
| `scripts/ci-fanout.sh` | fan-out 调度 | 修改：加 `image-deploy` method |
| `deploy/deploy-targets.json` | 目标清单 | 修改：杭州 method=`image-deploy` + `env` |
| `.gitea/workflows/deploy.yml` | CI 流水线 | 修改：render 多 compose + build deploy 镜像 |
| `docs/deploy/sourceless-target-setup.md` | 目标机初始化手册 | 新增 |
| `changelog/2026-06-25.md` | 升级日志 | 追加 |
| `docs/deploy/cicd-gitea.md` | CI/CD 文档 | 修订：无源码部署节 |

---

### Task 1: render 加 `--skip-validation` 选项

**背景**：`validate_inventory` 会在 `env_file` 不存在时 exit。无源码目标的 env 在目标机、不在主控，主控 render 时必须跳过该校验。

**Files:**
- Modify: `scripts/render-multitenant-deploy.py`（`main` argparse + validate 调用处）
- Test: `tests/unit/test_render_multitenant_deploy.py`

**Interfaces:**
- Produces: `render-multitenant-deploy.py` 支持 `--skip-validation` flag；设置时跳过 `validate_inventory`，直接 render。

- [ ] **Step 1: 写失败测试（追加到 `tests/unit/test_render_multitenant_deploy.py`）**

```python
def test_skip_validation_renders_without_env_file(tmp_path, monkeypatch):
    """--skip-validation 时 env_file 不存在也能 render（无源码目标场景）。"""
    import sys
    mod = _load()
    inv = tmp_path / "hangzhou.json"
    inv.write_text(json.dumps({
        "project_name": "sales-agent",
        "image": "sales-agent:latest",
        "tenants": [{
            "id": "fuduoduo", "name": "fuduoduo", "api_port": 8103,
            "env_file": "secrets/fuduoduo.env",   # 不存在
            "data_dir": "./data/fuduoduo", "logs_dir": "./logs/fuduoduo",
            "roles": ["api", "stream", "worker"],
        }],
    }))
    out = tmp_path / "compose.yml"
    # 模拟命令行调用 main(["--skip-validation", str(inv), "--compose-out", str(out)])
    rc = mod.main(["--skip-validation", str(inv), "--compose-out", str(out), "--traefik-out", str(tmp_path / "t.yml")])
    assert rc == 0
    assert "fuduoduo-api" in out.read_text()
```

> 文件顶部已 `import json`；若没有则补。

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/pytest tests/unit/test_render_multitenant_deploy.py::test_skip_validation_renders_without_env_file -v`
Expected: FAIL（`env_file not found` SystemExit）。

- [ ] **Step 3: 实现 — argparse 加 flag + 条件跳过 validate**

在 `main()` 的 argparse 块（`--traefik-out` 参数后）加：

```python
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="跳过 validate_inventory（用于无源码目标：env_file 在目标机不在主控）",
    )
```

把 `validate_inventory(data, inventory_path)` 调用（紧跟 `data = load_inventory(...)` 后）改为：

```python
    if not args.skip_validation:
        validate_inventory(data, inventory_path)
```

- [ ] **Step 4: 跑确认通过 + 全量回归**

Run: `.venv/bin/pytest tests/unit/test_render_multitenant_deploy.py -v`
Expected: 全 passed（含新测试）。

- [ ] **Step 5: Commit**

```bash
git add scripts/render-multitenant-deploy.py tests/unit/test_render_multitenant_deploy.py
git commit -m "feat(render): add --skip-validation for sourceless targets"
```

---

### Task 2: deploy 镜像 Dockerfile + deploy-remote.sh

**Files:**
- Create: `deploy/Dockerfile`
- Create: `deploy/deploy-remote.sh`
- Test: `tests/unit/test_deploy_remote.sh`（bash -n + 内容断言）

**Interfaces:**
- Consumes: 挂载的 `/secrets/neo4j.env`、`APP_IMAGE` env、参数 `<env>`、镜像内的 `/deploy/compose-<env>.yml`
- Produces: 目标机上 app/neo4j 容器 up + healthcheck

- [ ] **Step 1: 写 `deploy/deploy-remote.sh`**

```bash
#!/usr/bin/env bash
# 在无源码目标机上执行（由 deploy 镜像 docker run 触发，挂 docker.sock + secrets）。
# 用法（容器内 entrypoint）：deploy-remote.sh <env>   例：deploy-remote.sh hangzhou
set -euo pipefail

ENV="${1:?用法: deploy-remote.sh <env>}"
COMPOSE="/deploy/compose-${ENV}.yml"
APP_IMAGE="${APP_IMAGE:?需要 APP_IMAGE=registry.internal:5000/sales-agent:<sha>}"
COMPOSE_FILE_ENV="${COMPOSE_FILE_ENV:-/secrets/neo4j.env}"

[ -f "$COMPOSE" ] || { echo "ERROR: $COMPOSE 不在 deploy 镜像里（该 env 未 render?）" >&2; exit 1; }
[ -f "$COMPOSE_FILE_ENV" ] || { echo "ERROR: $COMPOSE_FILE_ENV 未挂载（secrets/neo4j.env 缺失?）" >&2; exit 1; }

echo "[deploy-remote] env=${ENV} app=${APP_IMAGE}"

# 1. pull app 镜像，retag 成 compose 引用的本地 tag sales-agent:latest
docker pull "$APP_IMAGE"
docker tag "$APP_IMAGE" sales-agent:latest

# 2. up（neo4j 密码经 --env-file 插值 ${NEO4J_PASSWORD}）
docker compose -f "$COMPOSE" --env-file "$COMPOSE_FILE_ENV" up -d

# 3. 等 api 容器 running（compose 里 api 无 healthcheck 字段，只校验 running；
#    业务健康靠 app /health + /ready，由人/ci-fanout 尾部复查）
echo "[deploy-remote] 等待 api running..."
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE" ps --status=running 2>/dev/null | grep -q -- "-api"; then
    echo "[deploy-remote] ok (after ${i}x2s)"
    exit 0
  fi
  sleep 2
done
echo "⚠️ [deploy-remote] api 未就绪，查 docker compose -f $COMPOSE logs" >&2
exit 1
```

- [ ] **Step 2: 写 `deploy/Dockerfile`**

```dockerfile
# deploy 镜像：装 docker cli + compose plugin，COPY 各目标 compose + 部署脚本。
# 目标机 docker run 它（挂 docker.sock + secrets）完成无源码部署。
FROM docker:24-cli
RUN apk add --no-cache docker-compose bash
WORKDIR /deploy
COPY deploy-remote.sh /deploy/deploy-remote.sh
COPY compose-*.yml /deploy/
RUN chmod +x /deploy/deploy-remote.sh
ENTRYPOINT ["/deploy/deploy-remote.sh"]
```

- [ ] **Step 3: 写冒烟测试 `tests/unit/test_deploy_remote.sh`**

```bash
#!/usr/bin/env bash
# deploy-remote.sh 冒烟：语法 + 关键逻辑断言。
set -e
SCRIPT="$(cd "$(dirname "$0")/../.." && pwd)/deploy/deploy-remote.sh"

bash -n "$SCRIPT"

grep -q 'compose-${ENV}' "$SCRIPT" || { echo "缺 compose 选择"; exit 1; }
grep -q -- '--env-file "$COMPOSE_FILE_ENV"' "$SCRIPT" || { echo "缺 --env-file 注入"; exit 1; }
grep -q 'docker pull "$APP_IMAGE"' "$SCRIPT" || { echo "缺 app pull"; exit 1; }
grep -q 'docker tag "$APP_IMAGE" sales-agent:latest' "$SCRIPT" || { echo "缺 retag"; exit 1; }
# migration 不在这里跑（靠 app 镜像 entrypoint）
test "$(grep -c 'alembic' "$SCRIPT")" -eq 0
echo "deploy-remote.sh 冒烟 OK"
```

- [ ] **Step 4: 跑冒烟**

Run: `bash tests/unit/test_deploy_remote.sh`
Expected: `deploy-remote.sh 冒烟 OK`。

- [ ] **Step 5: Commit**

```bash
git add deploy/Dockerfile deploy/deploy-remote.sh tests/unit/test_deploy_remote.sh
git commit -m "feat(deploy): add sourceless deploy image (Dockerfile + deploy-remote.sh)"
```

---

### Task 3: 杭州 inventory + render 出 compose-hangzhou.yml

**Files:**
- Create: `deploy/tenants.hangzhou.json`

**Interfaces:**
- Consumes: Task 1 的 `--skip-validation`（env_file=secrets/fuduoduo.env 不在主控）
- Produces: CI 能 `render-multitenant-deploy.py deploy/tenants.hangzhou.json --skip-validation` 生成 compose

- [ ] **Step 1: 写 `deploy/tenants.hangzhou.json`**

```json
{
  "_comment": "杭州 (47.118.16.235) 无源码目标租户清单。CI image-deploy 用。env_file 在目标机本地，主控 render 时用 --skip-validation。",
  "project_name": "sales-agent",
  "image": "registry.internal:5000/sales-agent:latest",
  "postgres_image": "registry.internal:5000/pgvector/pgvector:pg16",
  "database": {
    "name": "sales_agent",
    "user": "sales_agent",
    "password": "sales_agent_dev",
    "expose_host_port": false
  },
  "neo4j": {
    "enabled": true,
    "image": "registry.internal:5000/neo4j:5",
    "expose_ports": false
  },
  "traefik": { "enabled": false },
  "output": { "compose_file": "docker-compose.generated.yml" },
  "tenants": [
    {
      "id": "fuduoduo",
      "name": "fuduoduo (杭州)",
      "api_port": 8103,
      "env_file": "secrets/fuduoduo.env",
      "data_dir": "./data/fuduoduo",
      "logs_dir": "./logs/fuduoduo",
      "roles": ["api", "stream", "worker"]
    }
  ]
}
```

- [ ] **Step 2: 验证 JSON + render 出 compose-hangzhou.yml**

Run:
```bash
python3 -c "import json; json.load(open('deploy/tenants.hangzhou.json')); print('JSON OK')"
OVERRIDE_IMAGE=registry.internal:5000/sales-agent:test \
  python3 scripts/render-multitenant-deploy.py deploy/tenants.hangzhou.json \
  --skip-validation --compose-out /tmp/compose-hangzhou.yml --traefik-out /dev/null
grep -cE 'neo4j:|fuduoduo-api|NEO4J_URI' /tmp/compose-hangzhou.yml
```
Expected: JSON OK + render 成功 + grep 计数 ≥ 3。

- [ ] **Step 3: Commit**

```bash
git add deploy/tenants.hangzhou.json
git commit -m "feat(deploy): add tenants.hangzhou.json for sourceless target"
```

---

### Task 4: ci-fanout 加 image-deploy method + deploy-targets.json

**Files:**
- Modify: `scripts/ci-fanout.sh`（method case + SSH 分支）
- Modify: `deploy/deploy-targets.json`（杭州改 image-deploy + env）

**Interfaces:**
- Consumes: `deploy` 镜像 `registry.internal:5000/sales-agent-deploy:<sha>`、target 的 `env` 字段、`IMAGE`/`FRONTEND_IMAGE` env（ci-fanout 已有）
- Produces: 杭州 fan-out 执行 `docker run sales-agent-deploy:<sha> hangzhou`

- [ ] **Step 1: 改 `deploy/deploy-targets.json`，杭州改 image-deploy + env**

把 `test` 那条改为：

```json
    { "name": "杭州", "host": "47.118.16.235", "user": "root", "dir": "/root/code/sales-agent", "method": "image-deploy", "env": "hangzhou" }
```

（prod3 那条 `deploy-release` + `local:true` 不变。）

- [ ] **Step 2: ci-fanout.sh — case 加 image-deploy + python 解析加 env 字段**

在 method case（`self-deploy)` 后）加：

```bash
    image-deploy)   script=""; args="" ;;  # 不调本地脚本，docker run deploy 镜像
```

在 python 解析的 `print("|".join([...]))` 里加 `t.get("env", "")`（放在 `compose_file` 后，作为新最后一列），并相应在 `while read` 的变量列表末尾加 `env`。

- [ ] **Step 3: ci-fanout.sh — 加 image-deploy SSH 分支**

在远程执行的 `elif [ "$has_source" = "True" ]` 分支后、`else`（无源码脚本）前，加：

```bash
    elif [ "$method" = "image-deploy" ]; then
      DEPLOY_IMG="${REGISTRY_HOST:-registry.internal:5000}/sales-agent-deploy:${IMAGE##*:}"
      echo "[image-deploy] ${DEPLOY_IMG} env=${env:-?}"
      ssh -n -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$port" "${user}@${host}" \
        "docker pull '${DEPLOY_IMG}' && \
         docker run --rm \
           -v /var/run/docker.sock:/var/run/docker.sock \
           -v '${dir}/secrets:/secrets:ro' \
           -e APP_IMAGE='${IMAGE}' \
           '${DEPLOY_IMG}' '${env}'" \
        || echo "⚠️  [$name] image-deploy 失败，继续下一台" >&2
```

> `${IMAGE##*:}` 从 `IMAGE=registry.internal:5000/sales-agent:<sha>` 取 `<sha>`，拼 deploy 镜像 tag。

- [ ] **Step 4: 冒烟（bash -n + 关键串断言）**

Run:
```bash
bash -n scripts/ci-fanout.sh
grep -q 'image-deploy' scripts/ci-fanout.sh && grep -q 'sales-agent-deploy' scripts/ci-fanout.sh && echo "ci-fanout OK"
python3 -c "import json; json.load(open('deploy/deploy-targets.json')); print('targets OK')"
```
Expected: `ci-fanout OK` + `targets OK`。

- [ ] **Step 5: Commit**

```bash
git add scripts/ci-fanout.sh deploy/deploy-targets.json
git commit -m "feat(ci): add image-deploy fan-out method for sourceless targets"
```

---

### Task 5: deploy.yml render 多 compose + build deploy 镜像

**Files:**
- Modify: `.gitea/workflows/deploy.yml`（`build-and-push` job 末尾加 step）

**Interfaces:**
- Consumes: Task 1 的 `--skip-validation`、Task 3 的 `tenants.hangzhou.json`、`deploy/Dockerfile`
- Produces: registry 有 `sales-agent-deploy:<sha>`+`:latest`

- [ ] **Step 1: 在 `build-and-push` job 的 `Mirror neo4j image` step 后加新 step**

```yaml
      - name: Render per-env composes + build deploy image
        run: |
          SHA=$(git rev-parse --short HEAD)
          mkdir -p deploy
          for env in hangzhou; do
            OVERRIDE_IMAGE="${REGISTRY}/${IMAGE_NAME}:${SHA}" \
              python3 scripts/render-multitenant-deploy.py "deploy/tenants.${env}.json" \
              --skip-validation --compose-out "deploy/compose-${env}.yml" --traefik-out /dev/null
          done
          docker build -t ${REGISTRY}/sales-agent-deploy:${SHA} -t ${REGISTRY}/sales-agent-deploy:latest \
            -f deploy/Dockerfile deploy/
          docker push ${REGISTRY}/sales-agent-deploy:${SHA}
          docker push ${REGISTRY}/sales-agent-deploy:latest
          echo "::notice::pushed deploy image ${REGISTRY}/sales-agent-deploy:${SHA}"
```

- [ ] **Step 2: YAML 校验**

Run: `python3 -c "import yaml; d=yaml.safe_load(open('.gitea/workflows/deploy.yml')); print([s.get('name') for s in d['jobs']['build-and-push']['steps']])"`
Expected: step 列表含 `Render per-env composes + build deploy image`。

- [ ] **Step 3: Commit**

```bash
git add .gitea/workflows/deploy.yml
git commit -m "ci: build sourceless deploy image with per-env composes"
```

---

### Task 6: 文档（目标机初始化手册 + changelog + cicd-gitea）

**Files:**
- Create: `docs/deploy/sourceless-target-setup.md`
- Modify: `changelog/2026-06-25.md`、`docs/deploy/cicd-gitea.md`

- [ ] **Step 1: 写 `docs/deploy/sourceless-target-setup.md`**

内容：无源码目标机首次初始化步骤（装 docker + compose、docker login registry.internal:5000、放 CA 到 `/etc/docker/certs.d/registry.internal:5000/ca.crt`、`/etc/hosts` 加 registry.internal、创建 `secrets/`（从 example.env/neo4j.env.example）、若用 traefik 网关则 `docker network create <shared_network>`）、瘦身命令（删 src/scripts/.git 等，只留 secrets/）、日常部署说明（CI 自动 `docker run`，无需手动）。引用 spec `docs/superpowers/specs/2026-06-25-sourceless-deploy-design.md`。

- [ ] **Step 2: 追加 `changelog/2026-06-25.md`**

新增「无源码目标机部署架构」节：对象/类型/影响范围/改动明细（deploy 镜像 + deploy-remote.sh + ci-fanout image-deploy + render --skip-validation + tenants.hangzhou.json）/原因/回退。

- [ ] **Step 3: `docs/deploy/cicd-gitea.md` 加无源码部署节**

新增 §13「无源码目标机部署」：deploy 镜像机制、image-deploy method、目标机要求（指向 `sourceless-target-setup.md`）、与有源码 deploy-release 的区别。

- [ ] **Step 4: Commit**

```bash
git add docs/deploy/sourceless-target-setup.md changelog/2026-06-25.md docs/deploy/cicd-gitea.md
git commit -m "docs: sourceless target setup + changelog + cicd-gitea section"
```

---

### Task 7: 杭州端到端 rollout（运维操作，需 SSH + 你确认）

> 这一步**动杭州生产**，做之前跟你确认。代码 Task 1-6 合并 push 后才能做。

- [ ] **Step 1: 杭州 bootstrap（一次性）**——确认杭州已有：docker compose、`docker login registry.internal:5000`、CA 证书、`/etc/hosts` registry.internal、`secrets/fuduoduo.env` + `secrets/neo4j.env`（已有，前面验证过）。
- [ ] **Step 2: 杭州瘦身**——`mv /root/code/sales-agent/src /root/code/sales-agent/src.bak`（先备份不删，验证 OK 再删）；保留 `secrets/`、`data/`、`logs/`。
- [ ] **Step 3: 触发 CI**——push Task 1-6 的 commit 到 main，CI 自动 build deploy 镜像 + fan-out 杭州 `docker run`。
- [ ] **Step 4: 验证**——杭州 `docker ps` 看 app + neo4j 容器；`docker exec <fuduoduo-api> python -c "...urllib.../ready"` 看 `neo4j_ready:true`；确认杭州无源码也能部署。
- [ ] **Step 5: 清理**——验证稳定后删 `src.bak`、`.git`、`scripts/` 等。

---

## Self-Review 记录

- **Spec coverage**：spec §5.1→T2、§5.2→T2、§5.3→T1+T5、§5.4→T3、§5.5→T4、§5.6→T4、§5.7→T5、§6→T7、§12 文档→T6。全覆盖。
- **Placeholder**：无 TBD；所有 code step 给完整代码。
- **Type/命名一致**：`--skip-validation`、`image-deploy`、`deploy-remote.sh`、`compose-<env>.yml`、`sales-agent-deploy` 跨任务一致。
- **关键发现已纳入**：render `validate_inventory` env_file 校验 → T1 `--skip-validation`；杭州 `neo4j:None`/fuduoduo 8103 → T3 inventory；deploy-targets 杭州 `test`/deploy-release → T4 改 image-deploy。
- **依赖顺序**：T1（render 选项）→ T3（inventory 用它）→ T5（CI build 用它）；T2 独立；T4 依赖 T2（docker run deploy 镜像）；T6 文档最后；T7 rollout 最后。
