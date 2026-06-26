# Sales Agent 部署指南

> 部署架构的权威说明见 [`cicd-gitea.md`](cicd-gitea.md)；无源码目标机初始化细节见
> [`sourceless-target-setup.md`](sourceless-target-setup.md)。本文件是**操作手册**：
> 新服务器怎么接入、老服务器怎么加租户。

## 架构一分钟

```
prod2(开发,有源码) ──push main──► prod3(主控,Gitea+registry+runner,有源码)
                                         │ CI: build app 镜像 + deploy 镜像
                                         │ deploy 镜像内含 rendered compose + deploy-remote.sh
                                         ▼ fan-out
                                   ┌─────┴─────────┐
                                   ▼               ▼
                              prod3(本地)      test/未来(无源码)
                              deploy-release   image-deploy
                              (git sync+render) (docker run deploy 镜像)
```

- **有源码目标**（prod3、prod2）：fan-out 时 `git sync` + 现场 `render` compose + `deploy-release.sh`。
- **无源码目标**（test、未来不给源码的）：CI 预 render compose 打进 `sales-agent-deploy` 镜像；目标机 `docker run` 它（挂 docker.sock + secrets + workspace）部署。

## 日常部署（最常用）

```bash
# 在 prod2 改完代码
git add -A && git commit -m "..." && git push origin main
# → prod3 Gitea 自动触发 CI → build → fan-out prod3 + test
# 看 run: http://47.120.55.219:3002/admin/sales-agent/actions
```

docs/changelog 改动不触发 CI（paths-ignore）。

---

## 一、新服务器怎么部署（无源码目标机）

### 1. 目标机准备（一次性，见 [`sourceless-target-setup.md`](sourceless-target-setup.md) 全文）

```bash
# 装 docker + compose plugin（略）

# 允许 HTTP registry（走 HTTP + htpasswd，无 TLS）+ 登录
#   在 /etc/docker/daemon.json 加 { "insecure-registries": ["registry.internal:5000"] }（已有则合并）
systemctl restart docker
docker login registry.internal:5000 -u salesagent   # 凭证问主控运维

# /etc/hosts 解析 registry.internal → 主控可达 IP（跨地域走公网，同 VPC 走私网）
echo "<主控IP> registry.internal" >> /etc/hosts

# 建 workspace 目录 + secrets（从 example 模板，本地填真实凭证，绝不进 git）
mkdir -p /root/code/sales-agent/secrets
# 拿模板：secrets/example.env、secrets/neo4j.env.example（从仓库或主控）
cp example.env <tenant>.env && $EDITOR <tenant>.env   # 填 MODEL_API_KEY/DINGTALK/KNOWLEDGE_ENGINE 等
cp neo4j.env.example neo4j.env && $EDITOR neo4j.env   # 设 NEO4J_PASSWORD（三台一致）
chmod 600 /root/code/sales-agent/secrets/*.env
```

**跨地域机器**（如test）拉镜像要主控**两层防火墙**放行 TCP:5000：阿里云安全组 + 主控 ufw（见 cicd-gitea.md §3）。

### 2. 主控登记目标（在 prod2 改，push）

**(a) 加 inventory** `deploy/tenants.<env>.json`（新建，照 `tenants.test.json` 抄）：

```json
{
  "project_name": "sales-agent",
  "image": "registry.internal:5000/sales-agent:latest",
  "postgres_image": "registry.internal:5000/pgvector/pgvector:pg16",
  "database": {"name":"sales_agent","user":"sales_agent","password":"sales_agent_dev","expose_host_port":false},
  "neo4j": {"enabled": true, "image": "registry.internal:5000/neo4j:5", "expose_ports": false},
  "traefik": {"enabled": false},
  "tenants": [
    {"id":"<tenant>","name":"<租户名>","api_port":<端口>,"env_file":"secrets/<tenant>.env",
     "data_dir":"./data/<tenant>","logs_dir":"./logs/<tenant>","roles":["api","stream","worker"]}
  ]
}
```

> `env_file` 在目标机本地，主控 render 时用 `--skip-validation`（env 不在主控）。

**(b) 加 fan-out 目标** `deploy/deploy-targets.json`：

```json
{ "name": "<机器名>", "host": "<IP>", "user": "root", "dir": "/root/code/sales-agent",
  "method": "image-deploy", "env": "<env>" }
```

**(c) 让 CI 给这个 env build deploy 镜像** — 编辑 `.gitea/workflows/deploy.yml` 的 `for env in test`，加上你的 env：

```yaml
for env in test <新env>; do
```

### 3. push → 自动部署

```bash
git push origin main
# CI build deploy 镜像（含新目标的 compose-<env>.yml）→ fan-out image-deploy 到新机器
```

### 4. 首次 bootstrap（建租户 + 默认 Agent）

```bash
# 等容器起来后，调 api 注册租户（deploy-release/image-deploy 不自动建 Agent）
curl -X POST http://<机器>:<api_port>/tenants \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"<tenant>","name":"<租户名>"}'
# 注册后重启 api 容器，启动钩子会建默认 Agent（见 lessons #9）
docker restart sales-agent-<tenant>-api
```

### 5. 验证

```bash
docker ps | grep <tenant>                          # api/stream/worker/frontend + neo4j healthy
curl localhost:<api_port>/ready | grep neo4j_ready # neo4j_ready:true
```

---

## 二、老服务器怎么开多租户（dedicated mode：一租户一组容器）

### 有源码服务器（prod3、prod2）

```bash
# 1. inventory 加租户条目（prod3 用 deploy/tenants.prod3.json，dev 用 deploy/tenants.json）
#    tenants 数组加：
#    {"id":"<新租户>","name":"...","api_port":<新端口>,"env_file":"secrets/<新租户>.env",
#     "data_dir":"./data/<新租户>","logs_dir":"./logs/<新租户>","roles":["api","stream","worker"]}

# 2. 建租户凭证
cp secrets/example.env secrets/<新租户>.env && $EDITOR secrets/<新租户>.env
chmod 600 secrets/<新租户>.env

# 3. 部署（本地）
scripts/deploy-release.sh --env <新租户>.env
# 或 CI push：git push（fan-out 时 deploy-release 会发现新 secrets 并渲染进 compose）

# 4. bootstrap 建租户 + 默认 Agent（同上 §1.4）
curl -X POST http://localhost:<新端口>/tenants -H "Content-Type: application/json" \
  -d '{"tenant_id":"<新租户>","name":"..."}'
docker restart sales-agent-<新租户>-api
```

**端口**：新租户用未占用的端口（dev 机注意别和别的项目撞，见 lessons #7）。

### 无源码服务器（test等）

无源码目标机**不能本地改 inventory**（没源码），租户配置走主控 + CI：

```bash
# 在 prod2（主控源码）：
# 1. deploy/tenants.<env>.json 的 tenants 数组加新租户条目（同上）
# 2. push → CI render 新 compose（含新租户）打进 deploy 镜像
git push origin main

# 3. 目标机（test）补新租户的 secrets（CI 拉不到 gitignored 文件）
ssh root@<目标机>
cp /root/code/sales-agent/secrets/example.env /root/code/sales-agent/secrets/<新租户>.env
$EDITOR /root/code/sales-agent/secrets/<新租户>.env   # 填凭证
chmod 600 /root/code/sales-agent/secrets/<新租户>.env
# 等 CI image-deploy 跑完（docker run deploy 镜像会带上新租户服务）

# 4. bootstrap 建租户 + 默认 Agent（同上）
```

> image-deploy 的 compose 由主控 render（含新租户），目标机只需补 `secrets/<新租户>.env`。
> `data/<租户>` `logs/<租户>` 目录 docker 会自动建。

---

## 三、验证 / 故障排查

| 现象 | 查 |
|---|---|
| 容器没起来 | `docker ps -a`、`docker compose logs <svc>` |
| `/ready` neo4j_ready:false | neo4j 容器 healthy? `secrets/neo4j.env` 密码和 neo4j 容器 `NEO4J_AUTH` 一致? |
| 502（traefik 网关）| 跨 network：compose 要含 `shared_network` external（lessons #10）；rule 冲突（lessons #12）|
| image-deploy 失败 | CI 没 build deploy 镜像?（检查 `for env` 列表）；目标机缺 `secrets/neo4j.env`? |
| 新租户 `/instance/agent` 500 | 没建默认 Agent——POST /tenants 后**重启 api**（lessons #9）|
| test pull 镜像超时 | 主控两层防火墙 :5000 没放行（cicd-gitea.md §3）|

**关键命令**：
```bash
docker ps --format '{{.Names}} {{.Status}}'                          # 容器状态
docker exec <api> python -c "import urllib.request,json;print(json.loads(urllib.request.urlopen('http://localhost:8000/ready').read()))"  # /ready
docker inspect sales-agent-<tenant>-api --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -iE 'NEO4J|KNOWLEDGE'  # env 注入
```

## 相关文档

- [`cicd-gitea.md`](cicd-gitea.md) — CI/CD 完整配置（主控、registry、防火墙、fan-out、坑）
- [`sourceless-target-setup.md`](sourceless-target-setup.md) — 无源码目标机初始化手册
- [`deployment-roles.md`](../deployment-roles.md) — 进程角色（api/stream/worker/all）
- [`multitenant-deployment.md`](multitenant-deployment.md) — 多租户部署模式
- [`docs/superpowers/specs/2026-06-25-sourceless-deploy-design.md`](../superpowers/specs/2026-06-25-sourceless-deploy-design.md) — 无源码架构设计
