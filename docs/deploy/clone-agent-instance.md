# 克隆一个 Agent 实例（手动 docker compose 流程）

**产品模型**：一个 Agent = 一个 docker 实例 = 一个镜像（后端+控制台前端）= 一个端口。
克隆 Agent = 复制一套 docker 服务栈，注入新 tenant/模型/渠道配置，分配新端口。

所有实例共享：
- 同一个镜像 `sales-agent:latest`（已含后端 + 控制台前端）
- 同一个 Postgres 库（按 `tenant_id` 隔离，dedicated 模式每实例只看自己的 tenant）

每个实例独立：
- `TENANT_ID` / 模型 key / 钉钉绑定（各自的 `secrets/<name>.env`）
- 宿主机端口（`8xxx:8000`）
- 访问地址（`http://<server>:<port>` → 直进该 Agent 控制台，无列表无切换器）

---

## 前提

- 镜像已构建：`docker build -t sales-agent:latest .`
- `sales-agent-db` 容器在跑，库 `sales_agent` 已初始化（首个实例启动时自动建表）
- 已有一个可工作的实例作为克隆源（如 `taishan`）

## 克隆步骤（以新建 `huadong` Agent 为例）

### 1. 复制并修改 env

```bash
cd /root/code/sales-agent
cp secrets/taishan.env secrets/huadong.env
```

编辑 `secrets/huadong.env`，至少改：
- `TENANT_ID=huadong`（**必须唯一**，是该实例的隔离键）
- `TENANT_NAME=华东大客户销售`
- 模型配置（若不同）：`MODEL_API_KEY` / `MODEL_BASE_URL` / `MODEL_CHAT_MODEL` / `MODEL_EMBEDDING_MODEL`
- 钉钉（若该 Agent 接钉钉）：`DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` / `DINGTALK_CORP_ID` 等

### 2. 在 docker-compose.yml 加一套服务

复制 `taishan-api` / `taishan-stream` / `taishan-worker` 三块定义，改：
- `container_name`：`sales-agent-huadong-api` / `-stream` / `-worker`
- `profiles`：`["huadong-split"]`
- `ports`：换一个空闲宿主端口，如 `"8002:8000"`（这就是该 Agent 控制台的访问端口）
- `env_file`：`./secrets/huadong.env`
- `volumes`：`./data/huadong:/data/huadong`、`./logs/huadong:/logs/huadong`

（参考 `taishan-*` 的定义即可，结构完全一致，只改上述字段。）

### 3. 起栈

```bash
docker compose --profile huadong-split up -d
```

启动时该实例会：
- `init_db`：建表（已存在则跳过，幂等）
- `ensure_default_agents`：为 `huadong` 这个 tenant 自动创建一个默认 Agent（status=active）

### 4. 访问

浏览器打开 `http://<server>:8002` → 自动解析该实例的唯一 Agent，直进 overview。
- 无 Agent 列表、无切换器（一实例一 Agent）
- API 与前端同源（同端口），无 CORS 问题
- 后端 API：`http://<server>:8002/agents`、`/agent/chat`、`/instance/agent` 等

## 验证

```bash
# 该实例的 Agent
curl http://localhost:8002/instance/agent
# 前端首页
curl -s http://localhost:8002/ | grep -o '<title>.*</title>'
# 跑一句对话
curl -X POST http://localhost:8002/agent/chat -H 'Content-Type: application/json' \
  -d '{"tenant_id":"huadong","user_id":"u1","message":"你好","channel":"local"}'
```

## 隔离说明

- `tenant_id` 是主隔离键。dedicated 模式下 `check_tenant_match` 强制 huadong 实例只接受 `tenant_id=huadong` 的请求，taishan 实例只接受 `taishan`。
- 两实例共享同一个库但数据按 `tenant_id` 分开，互不可见。
- 任何实例停掉/重建不影响其他实例（除共享库的 schema 建表是全局的，幂等）。

## 备注

- 若要"在库内"复制一个 Agent 的配置（同 tenant 下做 A/B 变体，不起独立实例），用控制台的克隆向导（`/agents/{id}/clone`），那是另一种"配置级克隆"，不起新实例。
