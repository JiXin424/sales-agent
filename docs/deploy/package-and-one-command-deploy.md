# 打包与一键部署说明

这份文档说明如何把 Sales Agent 从当前服务器打包，迁移到另一台服务器，或者在已有服务器上做版本更新。

## 发布包包含什么

发布包会包含：

- `images/sales-agent-image.tar`：Sales Agent 应用镜像，镜像内已经包含后端 API、Agent 运行时、Worker 入口、钉钉 Stream 长连接入口，以及构建后的前端控制台。
- `scripts/deploy-release.sh`：目标服务器上的一键部署脚本。它会加载镜像、生成 compose、启动服务、执行健康检查，并在启用时自动注册钉钉快捷入口。
- `scripts/render-multitenant-deploy.py`：根据 `deploy/tenants.json` 生成 `docker-compose.generated.yml`。
- `scripts/check-tenant.sh` 和 `scripts/check-all-tenants.sh`：部署后的健康检查脚本。
- `scripts/init-db.sql`：PostgreSQL 初始化 SQL。
- `deploy/tenants.example.json`：Agent 实例清单模板。
- `deploy/tenant.env.example`：单个 Agent 实例的配置模板。
- `deploy/DEPLOY_USAGE.md`：更具体的操作步骤说明。
- `traefik/traefik.yml`：Traefik 静态配置。
- 部署相关文档。

发布包不会包含：

- 真实的 `secrets/*.env` 凭证文件。
- 租户数据目录 `data/`。
- 日志目录 `logs/`。
- TLS 证书私钥。
- 数据库 volume 或数据库数据文件。

## 在源服务器打包

在源服务器执行：

```bash
cd /root/code/sales-agent
scripts/package-deployment.sh
```

默认会构建并打包 `sales-agent:latest` 镜像，输出文件位于：

```text
dist/sales-agent-release-YYYYMMDD-HHMMSS.tar.gz
```

如果目标服务器不能稳定拉取基础镜像，可以把本机已有的 `pgvector` 和 `traefik` 镜像也放进发布包：

```bash
scripts/package-deployment.sh --include-base-images
```

常用参数：

```bash
# 指定应用镜像名
scripts/package-deployment.sh --image sales-agent:v0.3.2

# 不重新 docker build，直接打包本地已有镜像
scripts/package-deployment.sh --skip-build

# 指定输出目录
scripts/package-deployment.sh --output-dir /tmp/releases
```

## 首次部署到目标服务器

把压缩包复制到目标服务器后解压：

```bash
tar -xzf sales-agent-release-YYYYMMDD-HHMMSS.tar.gz
cd sales-agent-release-YYYYMMDD-HHMMSS
```

创建并编辑实例清单：

```bash
cp deploy/tenants.example.json deploy/tenants.json
vim deploy/tenants.json
```

需要确认每个 Agent 实例的：

- `id`：实例 ID，必须唯一，例如 `huadong`。
- `name`：实例名称或企业名称。
- `domain`：实例域名。没有域名时也不要保留 `example.com`。
- `api_port`：宿主机端口，例如 `8002`。
- `env_file`：实例配置文件路径，例如 `secrets/huadong.env`。
- `data_dir`：实例数据目录，例如 `./data/huadong`。
- `logs_dir`：实例日志目录，例如 `./logs/huadong`。

创建并编辑实例 env：

```bash
cp deploy/tenant.env.example secrets/huadong.env
chmod 600 secrets/huadong.env
vim secrets/huadong.env
```

必须替换：

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

如果要让部署脚本自动注册钉钉快捷入口，保持：

```env
DINGTALK_REGISTER_QUICK_ENTRY=true
DINGTALK_QUICK_ENTRY_CLEAR_FIRST=true
DINGTALK_QUICK_ENTRY_ENTRIES=coach,small_win_appreciation,sales_block_breakthrough
DINGTALK_QUICK_ENTRY_NAME=教练模式
```

如果暂时不注册快捷入口：

```env
DINGTALK_REGISTER_QUICK_ENTRY=false
```

运行一键部署：

```bash
scripts/deploy-release.sh
```

脚本会打印它将使用的配置文件路径，例如：

```text
inventory: /path/to/deploy/tenants.json
tenant env: /path/to/secrets/huadong.env
```

确认这些文件里的凭证和配置是最新的，然后输入：

```text
DEPLOY
```

之后脚本会自动：

1. 加载 `images/*.tar` 中的 Docker 镜像。
2. 检查配置里是否还有示例凭证或 `example.com`。
3. 生成 `docker-compose.generated.yml`。
4. 启动或更新 `postgres`、`traefik`、每个实例的 `api`、`stream`、`worker`。
5. 对每个实例执行 `/health` 和 `/ready` 检查。
6. 如果启用了钉钉快捷入口注册，调用实例 API 自动注册快捷入口。

## 更新已有服务器

更新时不要覆盖目标服务器已有的真实配置和数据：

- 保留 `deploy/tenants.json`。
- 保留 `secrets/*.env`。
- 保留 `data/`、`logs/`。
- 保留数据库 volume。

推荐流程：

```bash
tar -xzf sales-agent-release-YYYYMMDD-HHMMSS.tar.gz
cd sales-agent-release-YYYYMMDD-HHMMSS
# 把旧部署目录中的 deploy/tenants.json 和 secrets/*.env 复制到新目录
scripts/deploy-release.sh
```

脚本会重新加载新镜像，重新生成 compose，重建服务，并再次做健康检查和快捷入口注册。

## 运行时结构

每个 Agent 实例会生成三类容器：

- `<id>-api`：后端 API + 前端控制台，一个端口访问。
- `<id>-stream`：钉钉 Stream 长连接服务。每个实例只能跑一个，避免重复回复。
- `<id>-worker`：后台任务和 HTTP 模式钉钉 worker。

多个实例可以共享同一个 PostgreSQL 容器。隔离依赖 dedicated `TENANT_ID` 和应用层 tenant 校验。

## 钉钉快捷入口

部署脚本会在服务健康检查通过后，对每个启用快捷入口的实例调用：

```text
POST /integrations/dingtalk/plugins/register?tenant_id=<id>&clear_first=true
```

该接口内部会调用钉钉官方 API：

```text
获取 access_token
上传 coach_mode.png
POST /v1.0/robot/plugins/clear
POST /v1.0/robot/plugins/set
```

默认注册三个入口：

- `教练模式`：打开 H5 页面，包含访前准备和访后复盘。
- `小赢欣赏`：打开触发页，回调 `/integrations/dingtalk/whoami` 后进入单聊多轮流程。
- `卡点破框`：打开触发页，回调 `/integrations/dingtalk/whoami` 后进入单聊多轮流程。

相关配置位于每个实例的 `secrets/<tenant>.env`：

```env
DINGTALK_PUBLIC_URL=https://your-domain.example
DINGTALK_REGISTER_QUICK_ENTRY=true
DINGTALK_QUICK_ENTRY_CLEAR_FIRST=true
DINGTALK_QUICK_ENTRY_ENTRIES=coach,small_win_appreciation,sales_block_breakthrough
DINGTALK_QUICK_ENTRY_NAME=教练模式
```

如果设置：

```env
DINGTALK_REGISTER_QUICK_ENTRY=false
```

部署脚本会跳过快捷入口注册。

## 验证命令

假设实例端口是 `8002`：

```bash
curl http://localhost:8002/health
curl http://localhost:8002/ready
curl http://localhost:8002/instance/agent
curl http://localhost:8002/integrations/dingtalk/plugins/query
curl http://localhost:8002/ | grep '<title>'
```

浏览器访问：

```text
http://服务器IP:8002
```

或配置域名后访问：

```text
https://你的域名
```
