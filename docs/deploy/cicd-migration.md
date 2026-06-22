# CI/CD 迁移操作手册

> 将 CI/CD 主控从 47.120.50.181 迁移到 47.120.55.219 (benji)
>
> 最后更新: 2026-06-22

## 概览

```
迁移前:  47.120.50.181 = Gitea + Registry + Runner + 开发
迁移后:  47.120.55.219 = Gitea + Registry + Runner + 源码 (NEW 主控)
         47.120.50.181 = 纯开发机 + 部署目标
         47.118.16.235 = 部署目标 (多租户)
```

## 执行步骤

### Step 1: 在新主控 (47.120.55.219) 上执行 bootstrap

```bash
# SSH 到新主控
ssh root@47.120.55.219

# 确保 Docker 可用
docker info

# 创建目录并准备 bootstrap 脚本
# 方式 A: 从本仓库 scp
# scp scripts/bootstrap-new-master.sh root@47.120.55.219:/root/

# 方式 B: 直接在服务器上创建脚本 (复制 scripts/bootstrap-new-master.sh 内容)

# 执行 bootstrap
bash /root/bootstrap-new-master.sh
```

**预期结果:**
- Gitea Web: http://47.120.55.219:3002/
- Registry: registry.internal:5000 (仅本机可访问)
- Runner 运行中: `systemctl status gitea-runner`
- 凭证保存在 `/root/code/sales-agent/infra/`:
  - `gitea-admin-token.txt` — Gitea API Token
  - `gitea-admin-password.txt` — Gitea 管理员密码
  - `registry-password.txt` — Registry 密码
  - `registry-ca.crt` — Registry CA 证书

### Step 2: 推代码到新主控 Gitea

在新主控上，源码已从旧 Gitea 克隆，但需要确保最新的 CI/CD 配置：

```bash
# 在新主控上
cd /root/code/sales-agent

# 如果源码还没包含最新配置，从旧 Gitea 拉取
git fetch origin
git reset --hard origin/main

# 或者直接推 (如果本地有更新)
git push origin main
```

### Step 3: 配置新主控的 tenants.json

```bash
# 在新主控上
cd /root/code/sales-agent

# 使用 benji 专用配置
cp deploy/tenants.benji.json deploy/tenants.json

# 确保 secrets/taishan.env 存在并正确配置
# (如果已有，跳过此步)
```

### Step 4: 更新目标服务器

#### 4a: 47.120.50.181 (heyuan dev 机)

```bash
# SSH 到 heyuan
ssh root@47.120.50.181

# 如果脚本在本地，先拉取最新代码
cd /root/code/sales-agent
git fetch origin-old 2>/dev/null && git reset --hard origin-old/main || true

# 运行迁移脚本
bash scripts/migration-update-target.sh --target heyuan

# 创建本机 tenants.json
cp deploy/tenants.heyuan.json deploy/tenants.json
```

**注意:** 如果无法 SSH 免密到新主控，会提示手动操作:
1. 从新主控复制 `registry-ca.crt` 到 `/etc/docker/certs.d/registry.internal:5000/ca.crt`
2. 从新主控获取 registry 密码 (`infra/registry-password.txt`)
3. 手动执行 `docker login registry.internal:5000`

#### 4b: 47.118.16.235 (hangzhou)

```bash
# SSH 到 hangzhou
ssh root@47.118.16.235

cd /root/code/sales-agent
bash scripts/migration-update-target.sh --target hangzhou

# 创建本机多租户 tenants.json
cp deploy/tenants.hangzhou.json deploy/tenants.json
```

### Step 5: 首次 CI/CD 验证

在新主控上:

```bash
cd /root/code/sales-agent

# 做一个小改动来触发 CI (例如更新 README 或一个无害文件)
echo "# CI/CD Migration Verified - $(date)" >> CI_MIGRATION_VERIFIED.md
git add CI_MIGRATION_VERIFIED.md deploy/ deploy-targets.json infra/cicd-compose.yml scripts/bootstrap-new-master.sh scripts/migration-update-target.sh docs/deploy/cicd-migration.md
git commit -m "feat(infra): CI/CD 主控迁移到 47.120.55.219

- 新主控 bootstrap 脚本
- 目标服务器迁移脚本
- 各服务器独立 tenants.json 模板
- deploy-targets 更新为新拓扑

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin main
```

观察 CI: http://47.120.55.219:3002/gitea-admin/sales-agent/actions

**预期 CI 过程:**
1. `build-and-push`: 构建镜像并推送到本地 registry
2. `deploy-fanout`: 
   - 本地 (benji) 通过 deploy-release 部署
   - heyuan 通过 SSH + deploy-release 部署
   - hangzhou 通过 SSH + deploy-release 部署
3. `sync-code`: no-op (清单为空)

### Step 6: 验证各目标

```bash
# 检查新主控
curl http://127.0.0.1:8001/health

# 检查 heyuan dev 机
ssh root@47.120.50.181 'curl -s http://127.0.0.1:8002/health'

# 检查 hangzhou
ssh root@47.118.16.235 'curl -s http://127.0.0.1:8002/health'
```

### Step 7: 配置开发机 git remote (47.120.50.181)

```bash
# 在 47.120.50.181 开发机上
cd /root/code/sales-agent

# 旧 origin 已保存为 origin-old (指向本地 Gitea)
# 新 origin 指向 benji Gitea
git remote set-url origin http://47.120.55.219:3002/gitea-admin/sales-agent.git

# 开发流程: git push origin main → 触发 benji CI
```

---

## 回滚步骤

如果迁移出现问题，逐台回滚:

### 1. 恢复 /etc/hosts
```bash
# 在所有三台上
# 47.120.55.219: 不需要改 (删除 registry.internal 条目)
# 47.120.50.181: registry.internal → 127.0.0.1 (指向自己/旧master)
# 47.118.16.235: registry.internal → 47.120.50.181 (指向旧master公网)
```

### 2. 恢复 deploy-targets.json
```bash
# 改回旧拓扑 (master 为 127.0.0.1 local)
```

### 3. 重启旧 master CI 基础设施
```bash
# 在 47.120.50.181 上
cd /root/code/sales-agent/infra
docker compose -f cicd-compose.yml up -d
systemctl restart gitea-runner
```

---

## 多租户扩展

在任一服务器上添加新租户 (同服务器多容器，不同凭证):

1. 复制 env 模板:
```bash
cp deploy/tenant.env.example secrets/new-tenant.env
chmod 600 secrets/new-tenant.env
# 编辑 secrets/new-tenant.env — 设置 TENANT_ID、凭证等
```

2. 在 `deploy/tenants.json` 的 `tenants` 数组添加:
```json
{
  "id": "new-tenant",
  "name": "New Enterprise",
  "api_port": 8103,
  "env_file": "secrets/new-tenant.env",
  "data_dir": "./data/new-tenant",
  "logs_dir": "./logs/new-tenant",
  "roles": ["api", "stream", "worker"]
}
```

3. 部署:
```bash
REGISTRY_IMAGE=registry.internal:5000/sales-agent:latest scripts/deploy-release.sh --yes
```

每租户使用独立端口 + 独立 env_file → 隔离的凭证/模型配置/DingTalk 配置。
