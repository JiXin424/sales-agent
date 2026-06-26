# 子域名多机分发架构 — 设计文档

- **日期**：2026-06-26
- **状态**：已批准方案 A（待 spec review）
- **分支**：待新建（基于 main）

## 1. 背景与动机

当前 sales-agent 是**多租户**部署，三台服务器各自跑了不同租户：

| 机器 | 公网 IP | 私网 IP | 80/443 | sales-agent 租户（API 端口） |
|---|---|---|---|---|
| 本机（主控） | 47.120.50.181 | 172.25.186.209 | 共享 Traefik | taishan(8003)、taishankaifa2(8004) |
| .219 | 47.120.55.219 | 172.25.186.210 | sales-agent-traefik | songbai(8003)、taishanyanshi(8002) |
| .235 | 47.118.16.235 | 172.18.128.88 | traefik + 宝塔(8888) | fuduoduo(8103) |

现有路由方式：**单一域名 `aijiaolian.com.cn` + 路径分发**（`/integrations/dingtalk/t/{tenant_id}/`），Traefik 后端 URL 只能写**本机** docker 容器名（`http://sales-agent-{id}-api:8000`），无法指向其他机器的服务。

**即将上线的「钉钉快捷入口 + 免登录」功能**需要：每台服务器上的租户，把免登录地址（`DINGTALK_PUBLIC_URL`）都配成**本机域名**，所有流量从本机 Traefik 进入，再按子域名分发到租户实际所在的服务器。当前架构做不到跨机分发。

### 网络实测结论（2026-06-26）

- 本机 ↔ .219：**同 VPC 同子网**（172.25.186.0/24），私网直连 0.2ms，TCP 通。
- 本机 ↔ .235：**不同 VPC**（172.18.128.88 私网 100% 丢包），只能走公网 47.118.16.235（28ms）。
- .219 / .235 各自已有自己的 Traefik 占着 80/443；.235 装了宝塔（占 8888，未占 80）。这些对方案 A 无影响——方案 A 只需租户 API 端口从本机可达，不需要远端 80/443。

## 2. 目标与非目标

### 目标
- 本机 Traefik 作为 `*.aijiaolian.com.cn` 的**唯一公网入口**，按子域名把请求反代到租户实际所在机器。
- 支持「快捷入口 + 免登录」全流程跨机工作（JSAPI 手机端 + OAuth2 PC 端两条路径）。
- **最小代码改动**：复用 `render-multitenant-deploy.py` 已有的 `domain` 字段，新增一个可选 `backend` 字段。
- 零停机迁移：新旧路由共存，逐租户切换。

### 非目标（YAGNI）
- **不做**每租户独立前端 SPA 入口（`Host(domain)` 根路径 → 前端 nginx）。快捷入口只需 `/integrations/dingtalk/t/{id}/` 路径，已覆盖。前端独立入口留作后续。
- **不引入** wildcard DNS-01 证书。保留现有 `tlsChallenge`，每子域名独立签发。租户爆发增长（>50/周）再演进。
- **不改** H5 代码、租户校验逻辑、OAuth2 换码逻辑。路径段 `/t/{tenant_id}/` 保留不变。
- **不做** .235 的 VPC peering / CEN（演进项）。.235 暂走公网 + 防火墙白名单。
- **不拆除** .219 / .235 上各自的 Traefik（它们仍服务各自机器上的 omni-agent / taishanxd / gitea 等）。

## 3. 关键设计决策（已与用户对齐）

| 决策点 | 选定 | 理由 |
|---|---|---|
| 分发维度 | **按租户**，每租户一子域名 | 用户明确；天然故障隔离 |
| 入口架构 | **方案 A：本机 Traefik 唯一入口，反代到远端** | 契合"免登录地址统一走本机域名"意图；改动最小；单证书库/单仪表盘 |
| 顶域 | `aijiaolian.com.cn` | 与现有 public-router 一致 |
| 子域名格式 | `{tenant_id}.aijiaolian.com.cn` | 直观，与 tenant_id 对齐 |
| 路径段 | **保留** `/integrations/dingtalk/t/{tenant_id}/` | H5/租户校验/OAuth2 零改动；最小风险 |
| DNS | 泛解析 `*.aijiaolian.com.cn → 47.120.50.181` | 一次性，新增租户零 DNS 操作 |
| TLS | 保留 `tlsChallenge`，每子域名独立 LE 证书 | 零 Traefik 改动；不引入 DNS provider 插件 |
| 后端传输 | .219 走私网 `172.25.186.210`；.235 走公网 `47.118.16.235` + 防火墙白名单本机 IP | .219 同 VPC 私网最优；.235 跨 VPC 只能公网 |
| 前端 SPA 子域名 | **不做**（YAGNI） | 核心需求是快捷入口，已覆盖 |
| 迁移期旧路由 | **保留共存** | 零停机，全切完再清理 |

## 4. 架构

```
                       DNS: *.aijiaolian.com.cn → 47.120.50.181 (本机)
                              │
钉钉快捷入口 / 浏览器 ──HTTPS──▶ 本机 Traefik (47.120.50.181)
                              │  按 Host(子域名) 路由
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
        本机租户          .219 租户            .235 租户
   (docker 容器名)    172.25.186.210:port   47.118.16.235:port
                      私网 VPC · 0.2ms       公网 · 28ms · 防火墙白名单
```

### 数据流（以 songbai 租户快捷入口为例）

```
1. 用户点钉钉快捷入口
   → https://songbai.aijiaolian.com.cn/integrations/dingtalk/t/songbai/quick
2. DNS 泛解析 → 47.120.50.181（本机 Traefik）
3. 本机 Traefik 匹配 Host(songbai.aijiaolian.com.cn) && PathPrefix(/integrations/dingtalk/t/songbai/)
   → service: songbai-sub-dingtalk-svc → http://172.25.186.210:8003（.219 私网）
4. .219 上 sales-agent-songbai-api 容器处理：
   - 渲染 H5（cocah.html / quick_trigger.html）
   - /whoami（JSAPI 换 userId）、/oauth2-callback（OAuth2 换 userId）
   - /jsapi-config、/static/*
5. H5 内 window.location.origin = songbai.aijiaolian.com.cn
   → OAuth2 redirect_uri 用子域名（须在钉钉后台白名单）
```

## 5. 核心代码改动（render 脚本）

**文件**：`scripts/render-multitenant-deploy.py`

### 5.1 tenants.json schema 扩展

每租户加 2 个可选字段：

```json
{
  "id": "songbai",
  "domain": "songbai.aijiaolian.com.cn",
  "backend": "172.25.186.210:8003",
  "api_port": 8003,
  "env_file": "secrets/songbai.env",
  "data_dir": "./data/songbai",
  "logs_dir": "./logs/songbai",
  "roles": ["api", "stream", "worker"]
}
```

- `domain`：子域名（**复用脚本已有字段**，原本就支持 Host 路由）。
- `backend`（**新增**）：远端 `host:port`。
  - 缺省 → `http://sales-agent-{id}-api:8000`（本机容器名，现状不变）。
  - 有值 → `http://{backend}`（远端机器，走私网或公网 IP）。

### 5.2 `render_traefik_routes` 生成逻辑

对每个**有 `domain` 的租户**，额外生成一条「子域名 + 钉钉路径」路由：

```yaml
http:
  routers:
    songbai-sub-dingtalk:
      rule: "Host(`songbai.aijiaolian.com.cn`) && PathPrefix(`/integrations/dingtalk/t/songbai/`)"
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt
      service: songbai-sub-dingtalk-svc
      priority: 210
  services:
    songbai-sub-dingtalk-svc:
      loadBalancer:
        servers:
          - url: "http://172.25.186.210:8003"   # backend 字段值；缺省则为本机容器名
```

要点：
- **现有共享域名 PathPrefix 路由保留**（迁移期双通）。
- backend URL 解析：`tenant.get("backend")` or `f"http://sales-agent-{id}-api:8000"`。
- 已有的「重复 rule 断言」（render 脚本 L515-522）天然覆盖新路由，防止子域名重复。
- priority 210 与现有 dingtalk PathPrefix 路由一致，确保子域名下钉钉路径优先于 catch-all。

### 5.3 与现有 `Host(domain)` catch-all 路由的交互（关键，消除歧义）

现有 render 逻辑（L459-478）：租户有 `domain` 时，会生成一条 `Host(domain)` catch-all → **本机前端 nginx 容器**。这条 catch-all 对**远端租户**会出问题——本机没有它的前端容器，访问子域名根路径会 502。

规则：
- **远端租户**（`backend` 有值）：**只生成** `Host(domain) && PathPrefix(/integrations/dingtalk/t/{id}/)` → api backend 这一条；**跳过** catch-all（避免 502）。子域名根路径暂不服务（非目标，YAGNI）。
- **本机租户**（`backend` 空，且有本地 frontend 容器）：catch-all → 本机前端 nginx **保留**，PathPrefix → api 也生成。两条共存。
- 判定依据：`backend` 是否为空 + 是否存在本地 frontend 容器名。

> 注：即使误生了 catch-all，因 PathPrefix 路由 priority 210 高于 catch-all，快捷入口用到的 `/integrations/dingtalk/t/{id}/` 路径仍正确打到 api，catch-all 502 只影响子域名根路径（当前不用）。上述规则是为了干净，不是功能必需。

### 5.3 本机租户的处理

本机租户（taishan/taishankaifa2）`backend` 留空 → 回落本机容器名 → 行为完全同现状。可选：也给本机租户配 `domain` 走子域名入口（统一体验）。

## 6. 每租户配置变更（3 处）

1. **`secrets/{tenant}.env`**：
   ```
   DINGTALK_PUBLIC_URL=https://{tenant}.aijiaolian.com.cn
   ```
2. **钉钉后台 OAuth2 白名单**：加
   `https://{tenant}.aijiaolian.com.cn/integrations/dingtalk/t/{tenant}/oauth2-callback`
3. **重注册快捷入口**：跑 `scripts/dingtalk/register-quick-entries.sh`，用新 URL 重注册按钮。

## 7. 安全收敛（远端 API 端口）

- **.219**：本机走私网 `172.25.186.210`。建议把 .219 上 `0.0.0.0:8002/8003` 收成 `172.25.186.210:8002/8003`（仅私网监听），或防火墙挡掉公网访问。
- **.235**：跨 VPC 只能公网 `47.118.16.235:8103`。**必须**在 .235（宝塔安全组 / iptables）加规则：`8103` 仅放行源 IP `47.120.50.181`。
- **演进项**：.235 通过阿里云 VPC peering / CEN 拉进同 VPC → 走私网（消除公网暴露，本次不做）。

## 8. 零停机迁移

1. **不动**现有共享域名 PathPrefix 路由。
2. 部署 render 脚本改动 → 本机 Traefik 热加载出新子域名路由。
3. **单租户试点**（建议 `songbai`，在 .219 上）：
   - 配 `DINGTALK_PUBLIC_URL` + 钉钉 OAuth2 白名单 + 重注册快捷入口。
   - 验证 `https://songbai.aijiaolian.com.cn/integrations/dingtalk/t/songbai/quick` 全链路。
4. 试点通过 → 逐租户切换（taishanyanshi、fuduoduo、本机租户…）。
5. 全部切完 → 可选清理旧共享域名 PathPrefix 路由。

## 9. 可观测 / 故障隔离

- **故障隔离红利**：每租户独立 service，单租户后端挂了**不影响**其他租户（优于共享 PathPrefix）。
- Traefik access.log 已开启，按 `Host` 头即可定位租户。
- 新增 `scripts/check-tenant-routing.sh {子域名}`：curl `/health` 验证「本机 Traefik → 远端租户 API」链路通断。

## 10. 测试

- **单元**（`tests/`）：
  - render 对新 schema 生成正确 YAML：有 `backend` → 远端 URL；无 `backend` → 本机容器名回落。
  - 有 `domain` → 生成子域名 Host 路由；无 `domain` → 不生成。
  - 重复 rule 断言仍生效（两个相同子域名 → 报错退出）。
- **集成**：本机 Traefik 加载生成的 dynamic 配置无报错（`docker logs sales-agent-traefik`）。
- **端到端**：试点租户快捷入口免登录全流程——
  - JSAPI 路径（钉钉容器内 requestAuthCode → /whoami）。
  - OAuth2 路径（PC 浏览器 → login.dingtalk.com → /oauth2-callback）。
  - TLS 证书自动签发成功（首次访问 subdomain）。

## 11. 涉及文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `scripts/render-multitenant-deploy.py` | **改** | 核心：`backend` 字段解析 + 子域名 Host 路由生成 |
| `deploy/tenants.example.json` | 改 | 文档化 `domain` + `backend` 新字段 |
| `deploy/tenant.env.example` | 改 | 文档化 `DINGTALK_PUBLIC_URL` 子域名用法 |
| `scripts/check-tenant-routing.sh` | **新增** | 子域名链路验证脚本 |
| `tests/unit/test_render_multitenant.py`（或同名） | **新增/改** | render 新逻辑单元测试 |
| `docs/deploy/subdomain-routing.md` | **新增** | 运维手册（DNS/白名单/防火墙/迁移步骤） |

### 运维操作（非代码）
- 阿里云 DNS：加泛解析 `*.aijiaolian.com.cn → 47.120.50.181`。
- 钉钉后台：每租户 OAuth2 redirect_uri 白名单加子域名。
- .235 防火墙：`8103` 仅放行 `47.120.50.181`。
- .219（可选）：API 端口收敛为仅私网监听。

## 12. 演进项（本次不做）

- wildcard DNS-01 证书（租户数 >50/周时）。
- .235 拉进同 VPC（VPC peering / CEN），消除公网暴露。
- 每租户独立前端 SPA 子域名入口。
- 清理旧共享域名 PathPrefix 路由（全量迁移完成后）。
