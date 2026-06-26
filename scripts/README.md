# Sales Agent — 多机子域名分发运维手册

## 架构概览

```
                       DNS: *.aijiaolian.com.cn → 47.120.50.181 (本机)
                              │
      ┌───────────────────────┼──────────────────────────┐
      ▼                       ▼                          ▼
  本机 (.181)              .219                       .235
  172.25.186.209         172.25.186.210            47.118.16.235
  同 VPC · 私网直连        同 VPC · 私网 0.2ms        跨 VPC · 公网 28ms
```

- **本机 (.181)**：唯一的公网入口，运行共享 Traefik（80/443），管理全局子域名路由表
- **.219 / .235**：运行租户容器，各自维护自己的 env 文件和租户列表
- **流量路径**：`https://{tenant}.aijiaolian.com.cn` → DNS → 本机 Traefik → 按 `Host` 头匹配 → 反代到租户所在机器的 `{host}:{api_port}`

### 关键设计决策

- `DINGTALK_PUBLIC_URL` **不手写**——由 render 脚本从 `domain` 字段自推导（`https://{domain}`）
- 本机 `deploy/tenants.json` 是**全局路由表**——含所有机器上所有租户（每个多加 `backend` 字段指向实际位置）
- 远端机器的 `deploy/tenants.json` 只管自己的容器部署，**不需要 `backend`**

---

## 运维场景

### 场景一：已有服务器上新建租户

以在 `.219` 上新建租户 `xinkehu`（API 端口 8005）为例。

#### 步骤

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **1** | 创建 env 文件 | **.219** | `vim /root/code/sales-agent/secrets/xinkehu.env` |

**env 文件模板**（`DINGTALK_PUBLIC_URL` **不写**，系统自推导）：

```bash
# ---- DingTalk ----
DINGTALK_ENABLED=true
DINGTALK_MESSAGE_MODE=stream
DINGTALK_AGENT_ID=<你的agent_id>
DINGTALK_CORP_ID=<你的corp_id>
DINGTALK_APP_KEY=<你的app_key>
DINGTALK_APP_SECRET=<你的app_secret>
DINGTALK_ROBOT_CODE=<你的robot_code>
DINGTALK_STREAMING_ENABLED=true
DINGTALK_ENCRYPT_TOKEN=
DINGTALK_AES_KEY=
# DINGTALK_PUBLIC_URL ← 不写！render 脚本从 domain 自推导

# ---- 快捷入口 ----
DINGTALK_REGISTER_QUICK_ENTRY=true
DINGTALK_QUICK_ENTRY_CLEAR_FIRST=true
DINGTALK_QUICK_ENTRY_ENTRIES=coach,small_win_appreciation,sales_block_breakthrough
DINGTALK_QUICK_ENTRY_NAME=教练模式

# ---- AI Model ----
MODEL_API_KEY=<你的API_KEY>
# ... 其他模型/知识库配置 ...
```

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **2** | 注册到目标机 | **.219** | 编辑 `/root/code/sales-agent/deploy/tenants.json`，在 `tenants` 数组追加： |

```json
{
    "id": "xinkehu",
    "name": "Xinkehu",
    "domain": "xinkehu.aijiaolian.com.cn",
    "api_port": 8005,
    "env_file": "secrets/xinkehu.env",
    "data_dir": "./data/xinkehu",
    "logs_dir": "./logs/xinkehu",
    "roles": ["api", "stream", "worker"]
}
```

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **3** | ⭐ **注册到本机路由表** | **本机 (.181)** | 编辑 `deploy/tenants.json`，追加同一条目，**额外加 `backend`**： |

```json
{
    "id": "xinkehu",
    "name": "Xinkehu (.219)",
    "domain": "xinkehu.aijiaolian.com.cn",
    "backend": "172.25.186.210:8005",
    "api_port": 8005,
    "env_file": "secrets/xinkehu.env",
    "data_dir": "./data/xinkehu",
    "logs_dir": "./logs/xinkehu",
    "roles": ["api", "stream", "worker"]
}
```

然后刷新 Traefik 路由：

```bash
cd /root/code/sales-agent
python scripts/render-multitenant-deploy.py --skip-validation deploy/tenants.json
# Traefik 自动热加载，无需重启
```

> ⚠️ **这一步最容易漏！** 不加本机路由表，Traefik 不知道把 `xinkehu.aijiaolian.com.cn` 的请求转发到哪。

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **4** | 重新渲染 + 部署 | **.219** | |

```bash
cd /root/code/sales-agent
export $(grep NEO4J_PASSWORD secrets/neo4j.env | xargs)
python scripts/render-multitenant-deploy.py deploy/tenants.json
docker compose -f docker-compose.generated.yml up -d
```

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **5** | 注册快捷入口 | **.219** | |

容器就绪后（`curl http://127.0.0.1:8005/health` 返回 ok）：

```bash
curl -X POST "http://127.0.0.1:8005/integrations/dingtalk/t/xinkehu/plugins/register?clear_first=true&name=教练模式&entries=coach,small_win_appreciation,sales_block_breakthrough"
```

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **6** | 钉钉 OAuth2 白名单 | **钉钉开放平台** | 在 xinkehu 对应的应用里，加回调域名： |

```
https://xinkehu.aijiaolian.com.cn/integrations/dingtalk/t/xinkehu/oauth2-callback
```

| # | 操作 | 位置 | 命令 / 内容 |
|---|------|------|-------------|
| **7** | 验证 | **任意** | |

```bash
# DNS 应解析到本机
dig +short xinkehu.aijiaolian.com.cn  # → 47.120.50.181

# 本机 Traefik 应能反代到远端
curl -sk -o /dev/null -w "HTTP %{http_code}" https://xinkehu.aijiaolian.com.cn/integrations/dingtalk/t/xinkehu/quick
# 期望：HTTP 200
```

---

### 场景二：新建一台服务器

假设新服务器 IP `47.120.70.100`，私网 `172.25.186.220`。

| # | 操作 | 位置 | 说明 |
|---|------|------|------|
| **1** | 环境初始化 | **新服务器** | 安装 Docker，导入 registry CA 证书，`docker login registry.internal:5000` |
| **2** | 部署代码框架 | **新服务器** | `git clone` 或 scp 必需的脚本目录（`scripts/`、`deploy/`）+ 创建 `secrets/`、`data/`、`logs/` |
| **3** | 部署第一个租户 | **新服务器** | 按场景一步骤 1→2→4，创建 env + tenants.json + 渲染 compose + 启动容器 |
| **4** | 注册到本机路由 | **本机** | 按场景一步骤 3，加租户条目（带 `backend`）→ 刷新 Traefik |
| **5** | 防火墙收敛 | **新服务器** | 见下方 |
| **6** | DNS | **阿里云** | 已有泛解析 `*.aijiaolian.com.cn → 47.120.50.181`，新子域名**自动生效**，零操作 |
| **7** | 快捷入口 + 钉钉白名单 | **新服务器 + 钉钉** | 按场景一步骤 5-6 |

#### 防火墙规则

**同 VPC（172.25.186.x）**——如 .219：
- API 端口建议收为**仅私网监听**，不暴露公网
- 在 `docker-compose.generated.yml` 里把 `ports` 从 `"8003:8000"` 改为 `"172.25.186.210:8003:8000"`

**跨 VPC**——如 .235：
- API 端口必须暴露公网，但**防火墙仅放行本机 IP**：

```bash
# iptables 示例
iptables -A INPUT -p tcp --dport 8103 -s 47.120.50.181 -j ACCEPT
iptables -A INPUT -p tcp --dport 8103 -j DROP
```

---

## 常用运维命令

### 健康检查

```bash
# 单个租户
curl -s http://127.0.0.1:{api_port}/health       # 容器存活
curl -s http://127.0.0.1:{api_port}/ready        # 模型/知识库就绪

# 子域名端到端链路（从任意机器）
curl -sk -o /dev/null -w "HTTP %{http_code}" https://{tenant}.aijiaolian.com.cn/integrations/dingtalk/t/{tenant}/quick

# 使用验证脚本（在本机）
./scripts/check-tenant-routing.sh {tenant}.aijiaolian.com.cn
```

### 重启注册快捷入口

```bash
# 在目标机上
curl -X POST "http://127.0.0.1:{api_port}/integrations/dingtalk/t/{tenant_id}/plugins/register?clear_first=true&name=教练模式&entries=coach,small_win_appreciation,sales_block_breakthrough"
```

### 刷新本机 Traefik 路由（新增/修改租户后）

```bash
cd /root/code/sales-agent
python scripts/render-multitenant-deploy.py --skip-validation deploy/tenants.json
# 无需重启 Traefik，自动热加载
```

### 查看当前路由表

```bash
cat /root/code/traefik/dynamic.d/generated-sales-agent.yml
# 或只看路由规则
grep -E "rule:|url:" /root/code/traefik/dynamic.d/generated-sales-agent.yml
```

---

## 租户端口分配

| 机器 | 租户 | api_port | backend (本机用) |
|------|------|----------|-------------------|
| 本机 (.181) | taishan | 8003 | (空，本地容器) |
| 本机 (.181) | taishankaifa2 | 8004 | (空，本地容器) |
| .219 | songbai | 8003 | `172.25.186.210:8003` |
| .219 | taishanyanshi | 8002 | `172.25.186.210:8002` |
| .235 | fuduoduo | 8103 | `47.118.16.235:8103` |

**注意**：由于本机与远程机器隔离，api_port 在不同机器间可以重复（如本机 taishan 和 .219 songbai 都用 8003），但**同一台机器上**必须唯一。本机 `tenants.json` 用 `--skip-validation` 跳过跨机端口重复检查。

---

## 常见问题

### 钉钉点击快捷入口显示"页面加载失败"

1. `dig +short {tenant}.aijiaolian.com.cn` — DNS 是否解析到 `47.120.50.181`？
2. `grep {tenant} /root/code/traefik/dynamic.d/generated-sales-agent.yml` — 本机 Traefik 有路由吗？
3. `curl -sk https://{tenant}.aijiaolian.com.cn/.../quick` — 从本机能访问通吗？

### 注册快捷入口报 500

1. 容器镜像太旧，拉最新：`docker pull registry.internal:5000/sales-agent:latest`
2. 丁丁应用权限：去钉钉开放平台检查是否开通 `Robot.SingleChat.ReadWrite`

### 注册快捷入口报 503 / Method Not Allowed

容器未完全启动。等 1-2 分钟让 alembic migration 跑完，`/ready` 返回 `"status":"ready"` 后再注册。
