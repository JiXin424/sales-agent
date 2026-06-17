# CI/CD:Gitea 自托管 + 私有 Registry(push 即部署多机)

push 到 `main` → 自动 build 镜像 → 推私有 registry → fan-out 部署到 `deploy/deploy-targets.json` 里每台目标机。

## 架构

```
git commit + push (main)
      ↓
┌──── 源服务器 47.120.55.219 ────────────────────┐
│ Gitea(:3001 web,git 走 HTTP) docker 容器        │
│ 私有 registry(172.25.186.210:5000,htpasswd)    │
│ act_runner(宿主机二进制, systemd, :host 标签) │
│ Gitea Action:                                   │
│   build-and-push → 推 <sha> + :latest           │
│   deploy-fanout  → 遍历目标机 SSH 部署           │
└────┬───────────────────────────────────────────┘
     ↓ pull 172.25.186.210:5000/sales-agent:<sha>
目标机(固定部署目录,secrets/data 永不覆盖)
```

源/目标同在 `cn-heyuan`、同 VPC,registry 走**私网 IP**,免费内网、不暴露公网。

## 组件

| 组件 | 位置 | 说明 |
|------|------|------|
| Gitea | 源 docker,`infra/cicd-compose.yml` 的 `gitea` 服务,web `:3001` | git 服务 + Actions。`:latest` 在阿里云源缓存了古早 1.15.9(不支持 Actions),所以**固定用 `gitea/gitea:1.24`** tag |
| Registry | 源 docker,`registry:2`,绑 `172.25.186.210:5000` | htpasswd basic auth。凭证在 `infra/htpasswd` + `infra/registry-password.txt`(gitignore) |
| act_runner | 源**宿主机**二进制 `/usr/local/bin/act_runner`,systemd `gitea-runner.service` | 用 `:host` 标签,job 直接在源宿主机跑 |
| deploy.yml | `.gitea/workflows/deploy.yml` | build-and-push + deploy-fanout 两个 job |
| deploy-targets.json | `deploy/deploy-targets.json` | fan-out 目标清单(主机信息,无密钥) |

启动/重启 CI 基础设施:

```bash
cd /root/code/sales-agent/infra
docker compose -f cicd-compose.yml up -d          # Gitea + registry
systemctl restart gitea-runner.service            # act_runner
```

## 为什么 act_runner 跑在宿主机(不是容器)

`:host` 模式下 job 在 **runner 进程所在环境**执行。放宿主机上,job 直接复用:
- 主机 docker daemon(`/root/.docker/config.json` 里的 registry 登录缓存)→ `docker build/push` 免登录;
- 主机 `~/.ssh` 免密 → SSH 到目标机免密。

**因此整个 CI 零 Gitea secret**。代价:能触发 workflow 的人等价于源机 root,靠 `main` 分支保护 + Gitea 私有仓库缓解。

## 目标机要求

1. **daemon.json** 加 `insecure-registries`(registry 走 HTTP):
   ```json
   { "insecure-registries": ["172.25.186.210:5000"] }
   ```
   然后 `systemctl reload docker`。
2. **docker login** 一次(缓存凭证,后续 pull 免登录):
   ```bash
   docker login 172.25.186.210:5000 -u salesagent   # 用 infra/registry-password.txt 的密码
   ```
3. **免密 SSH**:源机 `~/.ssh` 已能免密到目标(部署密钥或 root 免密)。
4. **部署目录**有脚本 + 配置(见下)。

## 目标机首次 bootstrap(每台做一次)

```bash
# 目标机上
cd /root/code/sales-agent
# 取部署脚本(可用 git clone,或从发布包解压;CI 后续只 pull 镜像,不动脚本)
cp deploy/tenants.example.json deploy/tenants.json   # 然后编辑
cp deploy/tenant.env.example secrets/<租户>.env && chmod 600 secrets/<租户>.env
```

编辑 `deploy/tenants.json`,关键项:

```jsonc
{
  "image": "172.25.186.210:5000/sales-agent:latest",   // registry ref(CI 会用 sha 覆盖)
  "database": { "expose_host_port": false },           // postgres 不映射宿主端口,避开冲突
  "traefik": { "enabled": false },                     // 不起自己的 traefik(用隔离端口时)
  "tenants": [{ "id": "taishan", "api_port": 8002, "roles": ["api","worker"], ... }]
}
```

`secrets/<租户>.env` 填真实凭证(不能有 `sk-example`/`example.com` 等占位符,否则 `deploy-release.sh` 校验失败)。

首次部署(手动验证一次,之后交给 CI):
```bash
REGISTRY_IMAGE=172.25.186.210:5000/sales-agent:latest scripts/deploy-release.sh --yes
```

## 日常迭代

```bash
# 源机上改代码
git add -A && git commit -m "fix: ..." && git push origin main
```

Gitea → 仓库 → Actions 看 `deploy` 跑绿即可。两个 job:
- `build-and-push`:build → tag `<short-sha>` + `:latest` → push。
- `deploy-fanout`:对 `deploy/deploy-targets.json` 每台目标 SSH 跑 `REGISTRY_IMAGE=.../sales-agent:<sha> scripts/deploy-release.sh --yes`(pull → 改 tag → up -d → 健康检查)。

每个 push 的 sha tag 都留存在 registry。

## 回滚(一行)

```bash
# 目标机上 pin 回某个旧 sha
REGISTRY_IMAGE=172.25.186.210:5000/sales-agent:<旧sha> scripts/deploy-release.sh --yes
```

或从源机远程触发:
```bash
ssh <target> "cd /root/code/sales-agent && REGISTRY_IMAGE=172.25.186.210:5000/sales-agent:<旧sha> scripts/deploy-release.sh --yes"
```

## 隔离端口(与目标机已有服务共存)

目标机若已占用 80/443/5432,在 `deploy/tenants.json`:
- `"traefik": { "enabled": false }` —— 不起 sales-agent 的 traefik,API 走 `api_port` 直连;
- `"database": { "expose_host_port": false }` —— postgres 不映射宿主端口(app 走容器内网连);
- 租户 `api_port` 选空闲端口(如 8002)。

## 关键文件

- `infra/cicd-compose.yml` — Gitea + registry
- `infra/htpasswd` / `infra/registry-password.txt` — registry 凭证(gitignore)
- `infra/gitea-admin-password.txt` — Gitea 管理员密码(gitignore)
- `/etc/systemd/system/gitea-runner.service` — act_runner 守护
- `.gitea/workflows/deploy.yml` — CI 流水线
- `deploy/deploy-targets.json` — fan-out 目标清单
- `scripts/render-multitenant-deploy.py` — compose 渲染(`OVERRIDE_IMAGE` / traefik 开关 / postgres 端口可配)
- `scripts/deploy-release.sh` — 目标机部署(`REGISTRY_IMAGE` pull 路径)
