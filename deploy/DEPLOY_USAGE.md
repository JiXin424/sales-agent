# Sales Agent 快速复制与部署使用方法

这份说明用于两种场景：

- 第一次把 Sales Agent 部署到一台新服务器。
- 后续更新版本后，在已有服务器上重新部署。

当前部署模型是：**一个 Agent 实例 = 一套 docker 服务栈 = 一个 API/前端入口端口**。
每个实例通常包含三个容器：

- `<id>-api`：后端 API + 前端控制台，同一个端口访问。
- `<id>-stream`：钉钉 Stream 长连接服务。
- `<id>-worker`：后台任务/HTTP worker。

## 一、在源服务器打包

在已有代码服务器上执行：

```bash
cd /root/code/sales-agent
scripts/package-deployment.sh
```

生成文件位置：

```text
dist/sales-agent-release-YYYYMMDD-HHMMSS.tar.gz
```

如果目标服务器不能稳定拉取 `pgvector` 或 `traefik` 镜像，可以打包基础镜像：

```bash
scripts/package-deployment.sh --include-base-images
```

打包内容包括：

- Sales Agent 应用镜像，已包含后端、前端、Agent、Worker、钉钉 Stream。
- `scripts/deploy-release.sh` 一键部署脚本。
- `deploy/tenants.example.json` 实例清单模板。
- `deploy/tenant.env.example` 单实例配置模板。
- compose 渲染脚本、健康检查脚本、Traefik 基础配置、DB 初始化 SQL。

不会打包真实凭证、数据、日志、TLS 私钥：

- `secrets/*.env`
- `data/`
- `logs/`
- `traefik/certs/`
- 数据库 volume

## 二、复制到目标服务器

把压缩包传到目标服务器后解压：

```bash
tar -xzf sales-agent-release-YYYYMMDD-HHMMSS.tar.gz
cd sales-agent-release-YYYYMMDD-HHMMSS
```

## 三、首次部署前配置

### 1. 创建实例清单

```bash
cp deploy/tenants.example.json deploy/tenants.json
vim deploy/tenants.json
```

必须检查并修改：

- `id`：实例 ID，必须唯一，例如 `huadong`。
- `name`：企业或 Agent 名称。
- `domain`：该实例域名；没有域名时也要先改掉 `example.com`。
- `api_port`：宿主机端口，例如 `8002`。
- `env_file`：对应配置文件路径，例如 `secrets/huadong.env`。
- `data_dir`：数据目录，例如 `./data/huadong`。
- `logs_dir`：日志目录，例如 `./logs/huadong`。

可选：

- `traefik.shared_network`：服务器级 Traefik 所在的 Docker 网络名（如 `taishan-network`）。
  设置后生成器会把每个实例的 `api` 容器同时挂到 `default` 和该外部网络，让 Traefik 能按容器名
  解析到 api（钉钉快捷入口等路由才不会 502）。**该挂载写进 compose，recreate 自动保留**；不设则
  仅用 `default` 网络（适用于本机自带 Traefik 或不经共享网关的部署）。

### 2. 创建实例配置文件

```bash
cp deploy/tenant.env.example secrets/huadong.env
chmod 600 secrets/huadong.env
vim secrets/huadong.env
```

必须检查并修改：

- `TENANT_ID`
- `TENANT_NAME`
- `MODEL_API_KEY`
- `MODEL_BASE_URL`
- `MODEL_CHAT_MODEL`
- `MODEL_EMBEDDING_MODEL`
- `VECTOR_COLLECTION`
- `DATA_DIR`
- `LOG_DIR`
- `DINGTALK_ENABLED`
- `DINGTALK_MESSAGE_MODE`
- `DINGTALK_CORP_ID`
- `DINGTALK_APP_KEY`
- `DINGTALK_APP_SECRET`
- `DINGTALK_ROBOT_CODE`
- `DINGTALK_PUBLIC_URL`
- `DINGTALK_REGISTER_QUICK_ENTRY`：部署时是否自动注册钉钉快捷入口，默认 `true`。
- `DINGTALK_QUICK_ENTRY_CLEAR_FIRST`：注册前是否清空旧快捷入口，默认 `true`。
- `DINGTALK_QUICK_ENTRY_ENTRIES`：默认 `coach,small_win_appreciation,sales_block_breakthrough`。
- `DINGTALK_QUICK_ENTRY_NAME`：默认教练入口名称，默认 `教练模式`。

注意：`TENANT_ID` 必须和 `deploy/tenants.json` 里的 `id` 一致。

## 四、一键部署

运行：

```bash
scripts/deploy-release.sh
```

脚本会先打印需要人工确认的配置文件路径，例如：

```text
inventory: /path/to/deploy/tenants.json
tenant env: /path/to/secrets/huadong.env
```

请打开这些文件确认是最新凭证和最新企业配置。确认无误后输入：

```text
DEPLOY
```

脚本随后会自动完成：

1. 加载 `images/*.tar` 中的 Docker 镜像。
2. 检查配置里是否还有示例凭证或 `example.com`。
3. 生成 `docker-compose.generated.yml`。
4. 启动/更新 `api`、`stream`、`worker`、`postgres`、`traefik`。
5. 对每个实例执行健康检查。
6. 如果 `DINGTALK_ENABLED=true` 且 `DINGTALK_REGISTER_QUICK_ENTRY` 不是 `false`，调用该实例的 `/integrations/dingtalk/t/<tenant_id>/plugins/register` 自动注册钉钉快捷入口（tenant_id 进 path 段，供共享域名下 Traefik 按租户分流）。

快捷入口注册链路在容器内完成：

```text
部署脚本 -> 本实例 API /integrations/dingtalk/t/<tenant_id>/plugins/register
  -> 钉钉 access_token
  -> 上传 coach_mode.png
  -> 官方 plugins/clear
  -> 官方 plugins/set
```

默认会注册三个入口：

- `教练模式`：打开 H5 页面，包含访前准备和访后复盘。
- `小赢欣赏`：打开触发页，回调 `/integrations/dingtalk/t/<tenant_id>/whoami` 后进入单聊多轮流程。
- `卡点破框`：打开触发页，回调 `/integrations/dingtalk/t/<tenant_id>/whoami` 后进入单聊多轮流程。

如果暂时不想自动注册快捷入口，在对应 `secrets/<tenant>.env` 设置：

```env
DINGTALK_REGISTER_QUICK_ENTRY=false
```

## 五、访问与验证

假设实例端口是 `8002`：

```bash
curl http://localhost:8002/health
curl http://localhost:8002/ready
curl http://localhost:8002/instance/agent
curl http://localhost:8002/integrations/dingtalk/t/<tenant_id>/plugins/query
curl http://localhost:8002/ | grep '<title>'
```

浏览器访问：

```text
http://服务器IP:8002
```

如果配置了域名和 Traefik：

```text
https://你的域名
```

前端会自动请求 `/instance/agent`，进入该实例自己的 Agent 控制台。

## 六、导入知识库

把文档放到实例数据目录，例如：

```text
data/huadong/documents/
```

执行：

```bash
docker compose -f docker-compose.generated.yml exec huadong-api \
  sales-agent ingest --tenant huadong --path /data/huadong/documents --rebuild
```

## 七、后续更新部署

在源服务器重新打包：

```bash
cd /root/code/sales-agent
scripts/package-deployment.sh
```

把新包复制到目标服务器并解压。更新时保留目标服务器已有配置：

- `deploy/tenants.json`
- `secrets/*.env`
- `data/`
- `logs/`
- 数据库 volume

然后在新包目录运行：

```bash
scripts/deploy-release.sh
```

脚本会再次要求确认配置文件路径和凭证。确认后会加载新镜像并重建服务。

## 八、常见注意事项

- 每个 Agent 实例只能运行一个 `stream` 容器，避免钉钉重复回复。
- `api`、`stream`、`worker` 要使用同一个镜像版本，更新时一起重建。
- 不要把真实 `secrets/*.env` 放进发布包或提交到仓库。
- 如果 `scripts/deploy-release.sh` 报 placeholder 错误，说明配置里还有示例值，按报错路径修改后再运行。
- 如果只用 IP + 端口访问，也要把 `deploy/tenants.json` 和 env 里的示例域名改成真实值或内部约定值，不能保留 `example.com`。
