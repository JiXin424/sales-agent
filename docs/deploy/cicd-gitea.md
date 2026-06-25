# CI/CD 完整配置(交接文档)

> 本文件是 CI/CD 系统的**唯一权威说明**,供接手的 agent / 运维查阅。最后更新对应仓库 HEAD。

## 1. 概览

> ⚠️ **2026-06-25 迁移后状态**（下方旧 ASCII 图描述的是迁移前的 prod2 主控拓扑，保留作历史参考）：
> - **主控 = prod3 `47.120.55.219`**（私网 172.25.186.210）：Gitea(:3002) + registry(:5000) + act_runner，**兼**生产运行时（taishan api:8001, qiyelongxia.com.cn）。
> - **触发改为手动** `workflow_dispatch`（不再 push 自动触发；见 `.gitea/workflows/deploy.yml`）。
> - **fan-out 2 台**：prod3(本地) + 杭州 `47.118.16.235`（deploy-release）；**prod2 `47.120.50.181` 降级为开发机**，手动 `deploy-release.sh` 管理，不在 fan-out。
> - **Neo4j 已接入**：每台起共享 neo4j 容器，entrypoint 自动跑 alembic migration（见 §12）。

`workflow_dispatch` 手动触发 → 主控 Gitea Action → build 镜像 → 推 registry → fan-out 部署。开发在 prod2 改代码,push 到 prod3 Gitea 后在 Web UI 手动点触发。

```
你(主控 47.120.50.181)/root/code/sales-agent  git push
        │ code
        ▼
┌── 主控 47.120.50.181(河源 cn-prod2,私网 172.25.186.209)──┐
│ Gitea(web :3002)+ 公网 registry(:5000,自签 TLS)          │
│ act_runner(宿主机二进制 v0.6.1,systemd gitea-runner)      │
│ Action: build → 推 registry.internal → fan-out 三台         │
└──┬────────────────┬───────────────────┬────────────────────┘
   ↓ 私网(本地)     ↓ SSH + image-retag   ↓ SSH(公网,跨地域)
 主控自己           本机 47.120.55.219     杭州 47.118.16.235(cn-test)
 deploy-release     image-retag           deploy-release
 api:8002           sales-agent:latest    api:8002
                    (traefik 不动)        DingTalk 关
                    qiyelongxia.com.cn
```

**3 台角色:**
- **主控 47.120.50.181**:控制平面(Gitea+registry+runner)+ 也跑 sales-agent(deploy-release,隔离端口 8002)+ **开发 checkout**(`/root/code/sales-agent` 完整源码)。
- **本机 47.120.55.219(生产)**:跑 sales-agent 生产(image-retag,保留 `docker-compose.yml` + traefik 路由 qiyelongxia.com.cn,api:8001)。**纯运行时,无源码**(开发已移到主控)。
- **杭州 47.118.16.235(跨地域)**:跑 sales-agent(deploy-release,隔离端口 8002,DingTalk 关)。纯运行时。

## 2. 核心设计:`registry.internal` 分流

Docker tag 内嵌 registry 主机名;杭州跨地域只能走公网、其余走私网,**主机名不同无法共用 tag**。解法:用名字 `registry.internal`,各机 `/etc/hosts` 解析到自己能到的 IP:

| 机器 | /etc/hosts 里 registry.internal → |
|------|----------------------------------|
| 主控 | `127.0.0.1` |
| 本机(同 VPC) | `172.25.186.209`(主控私网) |
| 杭州(跨地域) | `47.120.50.181`(主控公网) |

自签证书 SAN 覆盖 `registry.internal` + `172.25.186.209` + `47.120.50.181` + `127.0.0.1`,各机连哪个都匹配。CA 证书在各机 `/etc/docker/certs.d/registry.internal:5000/ca.crt`(正规 TLS,**无需 insecure-registries**)。

## 3. 网络 & 防火墙(关键)

- 主控 47.120.50.181:私网 172.25.186.209,公网 47.120.50.181,cn-prod2。
- 本机 47.120.55.219:私网 172.25.186.210,cn-prod2,**与主控同 VPC**(私网互通)。
- 杭州 47.118.16.235:私网 172.18.128.88,cn-**test**,**跨地域,私网不通主控**,只能走公网。
- **杭州拉镜像要主控两层防火墙都放行 TCP:5000 来源 47.118.16.235**:
  1. 阿里云**安全组**(控制台):入方向 TCP:5000 ← 47.118.16.235。
  2. 主控本地 **ufw**(默认 INPUT DROP):`ufw allow from 47.118.16.235 to any port 5000 proto tcp`。
  - 只开一个不够(实测)。
- 各机 `docker login registry.internal:5000 -u salesagent`(密码见凭证)。

## 4. 凭证位置(都 gitignore,勿提交)

**主控 `/root/code/sales-agent/infra/`:**
- `htpasswd` / `registry-password.txt` —— registry 用户 `salesagent` 的密码。
- `domain.crt` / `domain.key` —— registry TLS 证书+私钥。
- `registry-ca.crt` —— registry CA(分发到各消费者)。
- `gitea-admin-password.txt` —— Gitea 管理员 `gitea-admin` 密码。

**主控 `/root/cicd-certs/`(生成证书时留的):** `ca.key`(CA 私钥)、`master-gitea-admin.txt`、`master-gitea-push-pat.txt`(本机 push 主控用的 PAT)。

**各目标机 `/root/code/sales-agent/secrets/taishan.env`:** 真实凭证(MODEL_API_KEY、DingTalk 等)。**DingTalk 在主控/杭州关闭**(避免与生产 stream 重复回复),只有本机生产开着。

## 5. 部署方法 & 脚本

`deploy/deploy-targets.json` 每个目标带 `method` 字段:

| method | 脚本 | 用于 | 干什么 |
|--------|------|------|--------|
| `deploy-release` | `scripts/deploy-release.sh --yes` | 主控、杭州 | `REGISTRY_IMAGE` pull → 渲染 compose(`scripts/render-multitenant-deploy.py`,image 走 `OVERRIDE_IMAGE` env 注入 sha)→ `docker compose up -d` → 健康检查 |
| `image-retag` | `scripts/deploy-image-retag.sh --yes` | 本机生产 | pull registry 镜像 → retag 成 `sales-agent:latest` → `docker compose -f docker-compose.yml --profile taishan-split up -d`。**不动 compose/traefik**,最低风险 |

`local: true` 的目标(主控)在主控本地执行(不 SSH);其余 SSH。

**fan-out 调度:`scripts/ci-fanout.sh`** —— 读 `deploy/deploy-targets.json`,按 method 调对应脚本。

## 6. Gitea Action workflow(`.gitea/workflows/deploy.yml`)

触发:**仅手动 `workflow_dispatch`**（2026-06-25 起不再 push 自动触发）。jobs:
1. `build-and-push`:从本地 Gitea(`127.0.0.1:3002`)fetch 精确 SHA → `docker build` tag `<sha>`+`latest` → push `registry.internal:5000/sales-agent`；同样构建前端镜像；**mirror `docker.1ms.run/neo4j:5` → `registry.internal:5000/neo4j:5`**（跨地域稳定拉取）。
2. `deploy-fanout`:`IMAGE=...:<sha> scripts/ci-fanout.sh` 部署 **2 台**（prod3 本地 + 杭州）。
3. `sync-code`:`scripts/sync-code.sh` 把代码 git pull 到"源码镜像"服务器(清单空则 no-op,为未来开发机准备)。
4. `diag`:SSH 连通性 + 远端 deploy 冒烟。

runner 跑在主控宿主机(`:host` 标签),复用主机 docker(已登录 registry)+ 免密 ssh 到各目标 → **零 Gitea secret**。

## 7. ⚠️ 已踩过的坑(务必记住)

1. **fan-out 的 `ssh` 必须 `ssh -n`**(`scripts/ci-fanout.sh`、`scripts/sync-code.sh`)。否则 `while read` 循环里的 ssh 吸走清单文件的 stdin,循环提前结束、**跳过第一个 SSH 之后的目标**(曾导致杭州被跳过、一直不更新)。
2. **Gitea 镜像 tag 必须用具体版本 `1.24`**。阿里云源对 `:latest`/`:1` 缓存了古早 1.15.9(不支持 Actions)。
3. **pgvector 基础镜像从 `docker.1ms.run` 拉会失败**(缺 in-toto attestation manifest)。已改推到自家 registry(`registry.internal:5000/pgvector/pgvector:pg16`),`tenants.json` 的 `postgres_image` 指向它。
4. **杭州跨地域要 SG + ufw 两层都开 :5000**(见 §3)。
5. **workflow 不用 `actions/checkout@v4`**(国内拉 github.com 失败 `unexpected EOF`),改为从本地 Gitea `git fetch` 精确 SHA(用自动 `GITHUB_TOKEN`,`oauth2:` 鉴权)。
6. **credential.helper store 不可靠**(git 把端口 `:3001` 存成 `%3a3001` 导致认证失败)。push 认证用 **PAT 内嵌进 origin 的 push URL**(本机)。
7. 本机 image-retag:生产 stream/worker 若跑旧镜像,首次部署会重建它们(几秒 DingTalk 中断),属正常。

## 8. 日常操作

**改代码部署(在主控):**
```bash
cd /root/code/sales-agent
git add -A && git commit -m "..." && git push   # 触发 CI,三台自动更新
# 看 run:http://47.120.50.181:3002/gitea-admin/sales-agent/actions
```

**回滚(某台):**
```bash
# deploy-release 目标(主控/杭州)
REGISTRY_IMAGE=registry.internal:5000/sales-agent:<旧sha> scripts/deploy-release.sh --yes
# image-retag 目标(本机)
REGISTRY_IMAGE=registry.internal:5000/sales-agent:<旧sha> scripts/deploy-image-retag.sh --yes
```
每个 push 的 sha tag 都留存在 registry,可随时回滚。

**重启主控 CI 基础设施:**
```bash
cd /root/code/sales-agent/infra && docker compose -f cicd-compose.yml up -d   # Gitea + registry
systemctl restart gitea-runner.service                                          # act_runner
```

## 9. 扩展(加机器)

**加部署目标:** 在目标机 bootstrap(脚本+tenants.json+secrets+CA+login,见历史 commit),然后 `deploy/deploy-targets.json` 加一条(带 `method`)。push 即生效。

**加"源码同步"开发机:** 该机 `git clone` 主控 Gitea 仓库 + 配 pull 认证,然后 `deploy/code-sync-targets.json` 加一条。push 后 `sync-code` job 自动 `git pull` 到它。

**加全新项目(多项目):** 在主控 Gitea 建新仓库 + 写该项目 `.gitea/workflows/*.y`。当前 runner 是 repo 级(只服务 sales-agent);多项目时给每个 repo 注册 runner,或升级成实例级(注意 Gitea 这版 `/admin/actions/runners/registration-token` 是 404,实例级 token 要从 web 管理页取)。

## 10. 关键文件清单(仓库内)

- `infra/cicd-compose.yml`(主控)—— Gitea + registry
- `.gitea/workflows/deploy.yml` —— CI 流水线
- `deploy/deploy-targets.json` —— 部署目标清单(+method)
- `deploy/code-sync-targets.json` —— 源码镜像清单(未来)
- `scripts/ci-fanout.sh` —— fan-out 调度(⚠️ ssh -n)
- `scripts/deploy-release.sh` / `scripts/deploy-image-retag.sh` —— 两种部署
- `scripts/render-multitenant-deploy.py` —— compose 渲染(`OVERRIDE_IMAGE`/traefik 开关/pg 端口可配)
- `scripts/sync-code.sh` —— 源码同步(⚠️ ssh -n)
- `Dockerfile` —— 多阶段构建(已优化:依赖层与源码层分离,纯代码改动增量构建 ~6s)

## 11. 验证 checklist

- 三台 `docker inspect sales-agent-<id>-api --format '{{.Config.Image}}'` == 最新 sha(主控/杭州)或 sales-agent:latest(本机)。
- 三台 `/health` 200、`/ready` 200。
- 本机 `curl -k https://qiyelongxia.com.cn/` 200(生产 traefik 未动)。
- 杭州 `docker pull registry.internal:5000/sales-agent:smoke` 成功(跨地域公网+TLS+CA+SG+ufw 全对)。
- registry tags 含每个历史 sha(`curl -sk -u salesagent:<pass> https://registry.internal:5000/v2/sales-agent/tags/list`)。

## 12. Neo4j / Ontology 部署（2026-06-25 接入）

ontology_neo4j 引擎依赖 Neo4j。CI 部署链路已自动接入：

- **共享单实例**：每台机器一个 `sales-agent-neo4j` 容器（`bolt://neo4j:7687`，持久 `neo4jdata` volume，cypher-shell healthcheck），所有租户共用，靠图中 `tenant_id`/`agent_id` 属性隔离。
- **凭证**：`secrets/neo4j.env`（gitignore，600）放 `NEO4J_PASSWORD`；`deploy-release.sh` 用 `--env-file secrets/neo4j.env` 注入，compose 的 `${NEO4J_PASSWORD}` 同时填给 neo4j 容器 `NEO4J_AUTH` 与 app `NEO4J_PASSWORD`。
- **渲染开关**：inventory 顶层 `neo4j.enabled`（三套 `tenants.*.json` 已设 true）；缺省时生成器按租户 env 的 `KNOWLEDGE_ENGINE=ontology_neo4j` 自动检测。
- **migration**：`docker-entrypoint.sh` 在 api 角色启动前跑 `alembic upgrade head`（`RUN_MIGRATIONS` 开关默认开，失败 exit 1）；stream/worker 不跑。
- **镜像**：CI `build-and-push` mirror `docker.1ms.run/neo4j:5` → `registry.internal:5000/neo4j:5`（杭州跨地域稳定拉取）。
- **每台运维手动步骤**：
  1. `cp secrets/neo4j.env.example secrets/neo4j.env && chmod 600 secrets/neo4j.env`，设强密码。
  2. 租户 `secrets/taishan.env` 设 `KNOWLEDGE_ENGINE=ontology_neo4j`（否则 neo4j 起但 app 走 legacy_rag）。
- **回退**：租户 env `KNOWLEDGE_ENGINE=legacy_rag` 即禁用引擎，neo4j 容器仍起但不被使用。
- **验证**：`docker inspect sales-agent-neo4j` healthy；app `/ready` 返回 ontology 就绪；`ingestion_jobs` 含 0003 新增列。

## 13. 无源码目标机部署（2026-06-25）

杭州及未来不给源码的目标机用 **deploy 镜像** 部署（不 git sync、不放源码）：

- **deploy 镜像**（`registry.internal:5000/sales-agent-deploy:<sha>`）：CI `build-and-push` 末尾 render 各无源码目标的 `compose-<env>.yml`（`render-multitenant-deploy.py --skip-validation`），和 `deploy-remote.sh` 一起打进镜像推 registry。compose 内 image = `registry.internal:5000/sales-agent:latest`（retag 自精确 sha）。
- **fan-out `image-deploy` method**：`ci-fanout.sh` SSH 目标机执行
  `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v <dir>/secrets:/secrets:ro -e APP_IMAGE=... sales-agent-deploy:<sha> <env>`。
- **deploy 镜像内**：pull app 镜像 → retag → `compose up`（`--env-file /secrets/neo4j.env`）→ 等 api running。migration 仍由 app 镜像 entrypoint 跑。
- **目标机要求**：只有 docker + compose、registry 登录、CA、`secrets/`。无 src/scripts/.git。初始化见 [`sourceless-target-setup.md`](sourceless-target-setup.md)。
- **与有源码 `deploy-release` 区别**：有源码目标 fan-out 时 git sync + 现场 render compose；无源码目标用 CI 预 render 进 deploy 镜像。
- **杭州**：`deploy-targets.json` method=`image-deploy` env=`hangzhou`；`tenants.hangzhou.json` 含 fuduoduo（8103）。
