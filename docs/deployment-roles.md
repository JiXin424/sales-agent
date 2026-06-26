# 部署角色说明

## 概述

Sales Agent 支持将单个全功能进程拆分为多个独立角色，以适应不同的部署需求。

| 角色 | 说明 | 端口 | 扩展性 |
|------|------|------|--------|
| `all` | 全功能模式（默认） | 8000 | 单实例 |
| `api` | 仅 HTTP API 服务 | 8000 | 可水平扩展 |
| `stream` | 钉钉 Stream 长连接 | 无 | **单实例** |
| `worker` | 后台任务处理 | 无 | 可水平扩展 |

## 环境变量

```bash
PROCESS_ROLE=all    # 可选值: all, api, stream, worker
```

未设置时默认为 `all`，行为与拆分前完全一致。

## 角色职责

### `all` — 全功能模式

启动所有组件：HTTP API + 钉钉 Stream/HTTP Worker。

适用于：开发、测试、小规模部署。

```bash
uvicorn sales_agent.main:app --host 0.0.0.0 --port 8000
```

### `api` — HTTP API 服务

仅启动 FastAPI 路由，**不启动**钉钉 Stream 长连接或 HTTP 后台队列 Worker。

适用于：需要独立扩缩容的 API 服务。

```bash
sales-agent serve --host 0.0.0.0 --port 8000
# 或
PROCESS_ROLE=api uvicorn sales_agent.main:app --host 0.0.0.0 --port 8000
```

健康检查：
- `GET /health` — 进程存活
- `GET /ready` — 就绪检查（模型配置、向量库）

### `stream` — 钉钉 Stream 长连接

仅启动钉钉 Stream WebSocket 长连接，通过 `ChatPipeline` 处理单聊消息。

适用于：钉钉消息处理的专用进程。

```bash
sales-agent stream
```

⚠️ **重要：每个租户只应运行一个 Stream 实例。** 多实例会导致重复消费和重复回复。

健康检查：基于进程存活（无 HTTP 端点），建议使用容器级别的 health check。

### `worker` — 后台任务处理

启动后台 Worker 进程。当前实现包含：
- 钉钉 HTTP 模式事件队列 Worker（如启用）

未来可扩展：
- 知识库导入任务
- 评估集运行
- 告警评估
- 报告生成

```bash
sales-agent worker
```

## Docker Compose 部署

### 角色拆分模式（推荐生产部署）

```bash
# 泰山兄弟：API + Stream + Worker
docker compose --profile taishan-split up -d
```

拆分模式下的服务：

| 服务 | 端口映射 | 说明 |
|------|----------|------|
| `taishan-api` | 8101→8000 | HTTP API |
| `taishan-stream` | 无 | 钉钉 Stream |
| `taishan-worker` | 无 | 后台任务 |

## Stream 单例约束

### 为什么必须单实例？

钉钉 Stream 模式使用 WebSocket 长连接，SDK 在连接建立后以 fan-out 方式推送消息。
如果同一租户运行多个 Stream 实例：

1. 消息可能被多个实例同时接收
2. 导致重复处理和重复回复
3. 用户会收到多条相同消息

### 推荐做法

```yaml
# docker-compose.yml 中 stream 服务
deploy:
  replicas: 1
  restart_policy:
    condition: on-failure
```

- 设置 `replicas: 1`
- 使用 `restart_policy: on-failure`（自动重启，但不增加副本）
- 在 Kubernetes 中使用 `replicas: 1` + PodDisruptionBudget

## 钉钉 HTTP 模式兼容性

| 部署模式 | HTTP 回调路由 | 事件处理 |
|----------|:---:|---|
| `all` | ✅ 可用 | 进程内队列 |
| `api` | ✅ 可用 | 需配合 worker 角色处理 |
| `api + worker` | ✅ 可用 | worker 内队列 |

在 `api` 角色中，HTTP 回调路由 `/integrations/dingtalk/events` 仍然可用，
但异步事件处理需要 `worker` 角色运行（或使用 `all` 模式）。

## 健康检查

| 角色 | 检查方式 | 说明 |
|------|----------|------|
| `all` | `GET /health` | HTTP 端点 |
| `api` | `GET /health` | HTTP 端点 |
| `stream` | 进程存活 | 容器级别 health check |
| `worker` | 进程存活 | 容器级别 health check |

对于 Stream 和 Worker 角色，建议使用 Docker 健康检查：

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
  interval: 30s
  timeout: 10s
  retries: 3
```
