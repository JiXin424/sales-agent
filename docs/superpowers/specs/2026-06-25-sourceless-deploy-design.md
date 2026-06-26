# 无源码目标机部署架构 — 设计文档

- **日期**：2026-06-25
- **状态**：已批准方案 A（待 spec review）
- **分支**：待新建（基于 main）

## 1. 背景与动机

当前部署模型要求**每台目标机都有完整 git 仓库 + 源码**：fan-out 时 `git fetch + reset --hard` 同步代码，再本地跑 `render-multitenant-deploy.py` 现场渲染 compose，最后 `deploy-release.sh`。这有三个问题：

1. **目标机上直接改代码会分叉**：`ca58360`（松柏租户）就是在 prod3 上直接 commit、没走 git，fan-out 的 `reset --hard` 差点冲掉，被迫手动整合。
2. **未来有不给源码的服务器**：某些环境（客户内网、受控机器）不能放源码，现行模型无法部署。
3. **目标机维护成本高**：每台都装 git 仓库、Python、render 依赖，只为生成一份 compose。

## 2. 目标与非目标

### 目标
- 目标机**无源码**：只有 `docker` + `registry 登录` + `CA 证书` + `secrets/`。
- compose 与部署逻辑通过 **CI/CD 镜像通道**传输（搭 app 镜像的便车走 registry），不 scp、不 git。
- prod3（主控+build+render）、prod2（开发）**保留源码**；杭州 + 未来不给源码的机器转无源码。
- 复用现有 fan-out SSH 调度，改动可控。

### 非目标（YAGNI）
- 不改 prod3/prod2 的部署方式（仍是本地 `deploy-release.sh`）。
- 不做 secrets 加密同步（各机本地从 example 创建）。
- 不做 deploy 镜像签名/验证（registry 已 TLS + 登录，后续可加）。
- 不改 app 镜像本身（entrypoint alembic migration 等不变）。

## 3. 关键设计决策（已与用户对齐）

| 决策点 | 选定 | 理由 |
|---|---|---|
| compose 传输 | **打进 deploy 镜像**，走 registry | 复用镜像通道，不 scp；目标机零文件维护 |
| 部署执行 | **方案 A：deploy 镜像当部署 agent**（`docker run` 挂 docker socket + secrets）| 目标机一条命令完成，逻辑全在镜像 |
| secrets | **各机本地从 example 模板创建** | 敏感凭证不进 CI/git；首次部署手动放 |
| 范围 | **杭州 + 未来不给源码的**；prod3/prod2 保留源码 | 开发/主控需要源码；运行时不需要 |

## 4. 架构

```
┌─ prod3 主控（有源码）─────────────────────────────────────┐
│ CI build-and-push:                                        │
│   1. build app 镜像 sales-agent:<sha>                     │
│   2. 对每个无源码目标，render compose-<env>.yml           │
│      （tenants.<env>.json + OVERRIDE_IMAGE=<sha>）         │
│   3. build deploy 镜像 sales-agent-deploy:<sha>           │
│      （COPY 各 compose-<env>.yml + deploy-remote.sh）     │
│   4. push app + deploy 镜像到 registry                    │
│                                                            │
│ deploy-fanout:                                             │
│   • 有源码目标（prod3 本地）→ deploy-release.sh（不变）   │
│   • 无源码目标（杭州）→ SSH 执行 docker run deploy 镜像   │
└────────────────────────────────────────────────────────────┘
                          │ SSH
                          ▼
┌─ 杭州（无源码）──────────────────────────────────────────┐
│ 仅有：docker + registry 登录 + CA + secrets/              │
│ 执行：docker run --rm \                                    │
│        -v /var/run/docker.sock:/var/run/docker.sock \      │
│        -v /root/code/sales-agent/secrets:/secrets:ro \     │
│        registry.internal:5000/sales-agent-deploy:<sha> hangzhou │
│                                                            │
│ deploy 容器内 deploy-remote.sh hangzhou:                  │
│   • 选 compose-hangzhou.yml                                │
│   • docker pull app 镜像 + neo4j 镜像                     │
│   • docker compose -f compose-hangzhou.yml \              │
│       --env-file /secrets/neo4j.env up -d                 │
│   • healthcheck                                            │
└────────────────────────────────────────────────────────────┘
```

## 5. 组件设计

### 5.1 `deploy/Dockerfile`（deploy 镜像，新增）

轻量 base + docker cli + compose plugin + 部署脚本 + 各目标 compose。

```dockerfile
FROM docker:24-cli
RUN apk add --no-cache docker-compose bash
WORKDIR /deploy
COPY deploy-remote.sh /deploy/deploy-remote.sh
COPY compose-*.yml /deploy/
ENTRYPOINT ["/deploy/deploy-remote.sh"]
```

> 用 `docker:24-cli`（含 docker cli），装 docker-compose plugin。镜像小、启动快。

### 5.2 `deploy/deploy-remote.sh`（部署脚本，新增）

deploy 镜像的 entrypoint，参数为目标 env 名：

```bash
#!/usr/bin/env bash
# 在无源码目标机上执行（由 deploy 镜像 docker run 触发）。
# 用法：deploy-remote.sh <env>   例如 deploy-remote.sh hangzhou
set -euo pipefail

ENV="${1:?用法: deploy-remote.sh <env>}"
COMPOSE="/deploy/compose-${ENV}.yml"
REGISTRY="${REGISTRY:-registry.internal:5000}"
APP_IMAGE="${APP_IMAGE:?需要 APP_IMAGE=registry.internal:5000/sales-agent:<sha>}"

[ -f "$COMPOSE" ] || { echo "ERROR: $COMPOSE 不在 deploy 镜像里" >&2; exit 1; }
[ -f /secrets/neo4j.env ] || { echo "ERROR: /secrets/neo4j.env 未挂载" >&2; exit 1; }

echo "[deploy-remote] env=${ENV} app=${APP_IMAGE}"

# 1. pull app 镜像，retag 成 compose 引用的本地 tag
docker pull "$APP_IMAGE"
docker tag "$APP_IMAGE" sales-agent:latest

# 2. compose up（neo4j 密码从挂载的 secrets 插值）
docker compose -f "$COMPOSE" --env-file /secrets/neo4j.env up -d

# 3. healthcheck：确认 api 容器 running（compose 里 api 无 healthcheck 字段，
#    故只校验 running；真正业务健康靠 app 自身 /health + /ready，由人/ci-fanout 尾部复查）
echo "[deploy-remote] 等待 api running..."
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE" ps --status=running | grep -q api; then
    echo "[deploy-remote] ok (after ${i}x2s)"; exit 0
  fi
  sleep 2
done
echo "⚠️ [deploy-remote] api 未就绪，请查 docker logs" >&2; exit 1
```

### 5.3 render 多目标 compose（build 时）

CI build deploy 镜像前，对每个**无源码目标** render 一份 compose：

```bash
# 在 build-and-push job 里
for env in hangzhou; do
  OVERRIDE_IMAGE="${REGISTRY}/${IMAGE_NAME}:${SHA}" \
    python3 scripts/render-multitenant-deploy.py deploy/tenants.${env}.json \
    --compose-out "deploy/compose-${env}.yml" \
    --traefik-out /dev/null   # 无源码目标不走共享 traefik 动态配置（如需要另行处理）
done
```

- compose 里 image = `registry.internal:5000/sales-agent:<sha>`（精确）。
- 若该目标 inventory 有 `traefik.shared_network`，render 已把 api 挂到 external network（你新加的逻辑），compose 自带 `networks.<name>.external: true` 声明 —— **根治 lessons #10 的跨 network 502**。
- neo4j 段按 inventory 渲染（已实现）。

### 5.4 `deploy/tenants.hangzhou.json`（杭州 inventory，新增/调整）

基于现状（杭州跑 `fuduoduo` 租户）。含 `neo4j.enabled` + 必要时 `traefik.shared_network`。

### 5.5 `scripts/ci-fanout.sh` 新增 `image-deploy` method

```bash
case "$method" in
  deploy-release) ...   # 既有
  image-retag)    ...   # 既有
  self-deploy)    ...   # 既有（保留向后兼容）
  image-deploy)   # 新：无源码，docker run deploy 镜像
    ssh -n ... "${user}@${host}" \
      "docker pull ${REGISTRY}/sales-agent-deploy:${SHA} && \
       docker run --rm \
         -v /var/run/docker.sock:/var/run/docker.sock \
         -v ${dir}/secrets:/secrets:ro \
         -e APP_IMAGE='${REGISTRY}/${IMAGE_NAME}:${SHA}' \
         ${REGISTRY}/sales-agent-deploy:${SHA} ${env}" ;;
esac
```

- 读 target 的 `env` 字段（如 `hangzhou`）告诉 deploy 镜像用哪份 compose。
- secrets 以只读挂载，不进镜像。

### 5.6 `deploy/deploy-targets.json` 更新

杭州从 `deploy-release` 改 `image-deploy`，加 `env` 字段：

```json
{ "name": "杭州", "host": "47.118.16.235", "user": "root",
  "dir": "/root/code/sales-agent", "method": "image-deploy", "env": "hangzhou" }
```

prod3 仍 `deploy-release` + `local: true`。

### 5.7 `.gitea/workflows/deploy.yml` 加 build deploy 镜像 step

`build-and-push` job 里，build app/frontend/mirror neo4j 之后，加：

```yaml
- name: Render per-env composes + build deploy image
  env:
    SHA: ${{ ... }}
  run: |
    for env in hangzhou; do
      OVERRIDE_IMAGE="${REGISTRY}/${IMAGE_NAME}:${SHA}" \
        python3 scripts/render-multitenant-deploy.py deploy/tenants.${env}.json \
        --compose-out "deploy/compose-${env}.yml" --traefik-out /dev/null
    done
    docker build -t ${REGISTRY}/sales-agent-deploy:${SHA} -t ${REGISTRY}/sales-agent-deploy:latest \
      -f deploy/Dockerfile deploy/
    docker push ${REGISTRY}/sales-agent-deploy:${SHA}
    docker push ${REGISTRY}/sales-agent-deploy:latest
```

## 6. 目标机要求与瘦身

### 首次初始化（杭州，一次性）
1. 装 docker + compose plugin。
2. `docker login registry.internal:5000`（凭证见主控 infra）。
3. 放 CA：`/etc/docker/certs.d/registry.internal:5000/ca.crt`。
4. `/etc/hosts` 加 `registry.internal → 主控可达 IP`。
5. 创建 `secrets/`：
   ```bash
   mkdir -p /root/code/sales-agent/secrets
   # 从主控 scp example.env / neo4j.env.example，或手动建：
   # secrets/fuduoduo.env（租户凭证）+ secrets/neo4j.env（NEO4J_PASSWORD）
   chmod 600 secrets/*.env
   ```
6. 若用 traefik 网关：建 external network `docker network create <shared_network>`。

### 瘦身（删源码）
删 `src/ scripts/ tests/ docs/ console/ eval/ config/ .git/ *.md pyproject.toml alembic.ini Dockerfile ontology-toolkit.zip` 等，**只留 `secrets/`**（+ 可选 `data/` `logs/` 持久卷目录）。

## 7. 数据流

```
push main → CI build → app 镜像 + deploy 镜像(含 compose-<env>.yml) → push registry
   → fan-out SSH 杭州 → docker run deploy 镜像
   → deploy 容器内 pull app/neo4j 镜像 + compose up（secrets 只读挂载）
   → app 容器起，entrypoint 跑 alembic，连 neo4j
```

secrets 全程不出目标机；compose 全程在 deploy 镜像里（registry 传输）；app 镜像从 registry pull。

## 8. 错误处理与回退

| 场景 | 处理 |
|---|---|
| deploy 镜像里缺 `compose-<env>.yml` | `deploy-remote.sh` 启动即 exit 1（`[ -f ]` 校验）|
| `/secrets/neo4j.env` 未挂载 | exit 1（校验）|
| app 镜像 pull 失败 | `docker pull` 失败 → 脚本 exit（set -e）|
| api 未在 60s 内就绪 | healthcheck 超时 exit 1，ci-fanout 报警继续下一台 |
| **回退** | fan-out 指定旧 sha 的 deploy 镜像（registry 留历史 sha）：`sales-agent-deploy:<旧sha>` |
| 杭州 secrets 缺失/错 | 本地修 `secrets/`，重跑 `docker run` |

## 9. 安全考量

- **docker socket 挂载**：deploy 容器能控制目标机 docker。目标机由 root 通过 fan-out SSH 执行，部署场景可接受。deploy 镜像来自自有 registry（TLS + 登录）。
- **secrets 只读挂载**（`:ro`），不进 deploy 镜像、不进 registry。
- **registry 凭证**：目标机本地 docker login，不进 git。

## 10. 测试与验证

- **deploy-remote.sh 单测/冒烟**：`bash -n` 语法 + 关键逻辑断言（compose 存在检查、--env-file 注入）。
- **render 多目标**：扩展现有 `test_render_multitenant_deploy.py`，断言 `compose-hangzhou.yml` 含 neo4j + shared_network external。
- **deploy 镜像本地构建**：`docker build -f deploy/Dockerfile deploy/` 成功 + 启动校验 compose 列表。
- **杭州端到端**：首次按 §6 初始化后，触发 CI，确认杭州 `docker run deploy 镜像` 拉起 app + neo4j，`/ready` 通。

## 11. Rollout

1. 先在 prod2 本地 build deploy 镜像 + 跑 `deploy-remote.sh` 冒烟（用临时容器模拟目标机）。
2. 杭州按 §6 初始化（装 docker/login/CA/secrets/network）+ 瘦身（删源码）。
3. 改 `deploy-targets.json` 杭州 method=`image-deploy` + 加 `env`，push 触发 CI。
4. 观察 fan-out 杭州 `docker run deploy 镜像` 成功 + `/ready` 通。
5. 稳定后，未来新机器按 §6 模板加入。

## 12. 受影响文件

| 文件 | 改动 |
|---|---|
| `deploy/Dockerfile` | 新增（deploy 镜像）|
| `deploy/deploy-remote.sh` | 新增（部署脚本）|
| `deploy/tenants.hangzhou.json` | 新增/调整（杭州 inventory）|
| `deploy/deploy-targets.json` | 修改（杭州 method=image-deploy + env）|
| `scripts/ci-fanout.sh` | 修改（加 image-deploy method）|
| `.gitea/workflows/deploy.yml` | 修改（build deploy 镜像 + render 多 compose）|
| `tests/unit/test_render_multitenant_deploy.py` | 扩展（多目标 compose 断言）|
| `tests/unit/test_deploy_remote.sh` 或 py 冒烟 | 新增 |
| `changelog/2026-06-25.md` | 追加 |
| `docs/deploy/cicd-gitea.md` | 修订（无源码部署节）|
| `docs/deploy/sourceless-target-setup.md` | 新增（目标机初始化手册）|

## 13. 风险

- **docker socket 暴露面**：deploy 容器 = 目标机的 docker root。缓解：deploy 镜像自有 registry、只读挂载 secrets、目标机本就是受控部署机。未来可加签名验证。
- **compose 与 inventory 漂移**：compose 在 build 时 render，inventory 改了要重新 build deploy 镜像才生效（不像源码现场 render 那样实时）。可接受（CI 每次 push 都 rebuild）。
- **杭州 traefik/shared_network**：若杭州有反代网关，compose 必须含 `shared_network` external，否则重蹈 lessons #10 的 502。inventory 里设好即可（render 已支持）。
