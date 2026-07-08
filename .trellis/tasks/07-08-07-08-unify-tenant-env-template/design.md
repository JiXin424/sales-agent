# Design — 统一租户 env 模板并随 CI/CD 发布

## 1. 现状与约束（已核实）

### CI/CD 到目标机的文件通道
| 目标机类型 | method | 文件如何到达 | 拿到 env 模板吗 |
|---|---|---|---|
| prod2（有源码） | deploy-release | `git reset --hard origin/main` 同步整仓 | ✅ 随仓库（含 3 份模板） |
| test / prod3（无源码） | image-deploy | `docker run sales-agent-deploy`（镜像 COPY 自 `deploy/`） + ci-fanout `tar\|ssh` 推少量运维脚本 | ❌ 当前不带模板 |

- deploy 镜像构建：`.gitea/workflows/deploy.yml:76-77` → `docker build -f deploy/Dockerfile deploy/`。**build context = `deploy/`**，只有 `deploy/` 下文件能进镜像 → 权威模板必须放 `deploy/`。
- `deploy-remote.sh` 在目标机容器内跑，`/workspace`（= 目标机 `${dir}`，含 `secrets/`）是**可写**挂载（`-v "${dir}:/workspace"`）。
- `deploy-release.sh:83` 用 `find secrets/ -type f -name "*.env" ! -name "example.env"` 发现租户；`-type f` 不匹配符号链接，`! -name` 再排除，软链双重安全。

### 三份模板受众
- `deploy/tenant.env.example` + `secrets/example.env`：租户部署，同受众 → 合并为一。
- `.env.example`（根）：本机单进程 dev（`cp .env.example .env`）。软链后 `cp` 解引用复制真实内容，dev 不受影响。

## 2. 方案

### 2.1 单一真源 + 软链
```
deploy/tenant.env.example        # 权威真源（唯一手工维护）
secrets/example.env  -> ../deploy/tenant.env.example   (symlink)
.env.example         -> deploy/tenant.env.example      (symlink)
```
- 三者仍被 git 跟踪（git 存符号链接为 blob，内容是链接目标路径）。
- `.gitignore` 现有 `secrets/*` + `!example.env` 反例继续放行软链本体。
- 相对路径：`secrets/example.env` 在 `secrets/` 下 → 目标 `../deploy/tenant.env.example`；`.env.example` 在根 → 目标 `deploy/tenant.env.example`。

### 2.2 模板内容（键集 = taishan.env ∪ 现有模板键 ∪ config.py getenv 覆盖）
分区（保持 taishan.env 顺序，补注释）：
1. 部署模式：`DEPLOYMENT_MODE / TENANT_ID / TENANT_NAME`
2. Chat 模型：`MODEL_PROVIDER / MODEL_API_KEY(占位) / MODEL_BASE_URL / MODEL_CHAT_MODEL`
3. Embedding：`MODEL_EMBEDDING_MODEL / EMBEDDING_API_KEY(占位) / EMBEDDING_BASE_URL`
4. 存储：`VECTOR_COLLECTION / DATA_DIR / LOG_DIR`
5. 钉钉基础：`DINGTALK_ENABLED / _MESSAGE_MODE / _CORP_ID / _APP_KEY / _APP_SECRET(占位) / _ROBOT_CODE`
6. 钉钉流式卡片：`DINGTALK_STREAMING_ENABLED / _CARD_TEMPLATE_ID / _STREAM_UPDATE_INTERVAL_MS / _STREAM_MIN_CHUNK_CHARS`
7. 钉钉 HTTP 回调（stream 模式留空）：`DINGTALK_ENCRYPT_TOKEN / _AES_KEY`
8. 钉钉多媒体理解（图片+语音，taishan 新增，模板缺失）：`DINGTALK_MEDIA_ENABLED / _MEDIA_BASE_URL / _MEDIA_API_KEY(占位) / DINGTALK_VISION_MODEL / DINGTALK_AUDIO_MODEL`
9. 钉钉快捷入口注册：`DINGTALK_REGISTER_QUICK_ENTRY / _PUBLIC_URL / _QUICK_ENTRY_CLEAR_FIRST / _QUICK_ENTRY_ENTRIES / _QUICK_ENTRY_NAME`
10. 知识引擎：`KNOWLEDGE_ENGINE / HYBRID_RETRIEVAL / ONTOLOGY_VECTOR_FALLBACK`（后者来自旧模板，保留）
11. 联网搜索兜底：`BOCHA_API_KEY(占位)`
12. Neo4j（**注明 CI/CD 由生成器从 `secrets/neo4j.env` 注入，无源码机无需填**）：`NEO4J_URI / _USER / _PASSWORD(占位) / _DATABASE / _VISUAL_URL`
13. 引导流程：`GUIDED_FLOWS_ENABLED`（来自旧 .env.example，保留）
14. 话题路由：`TOPIC_ROUTING_ENABLED`
15. 场景教练：`SCENARIO_COACH_ENABLED / SCENARIO_COACH_CONFIDENCE_THRESHOLD`

头部注释说明：本文件为唯一权威模板；`secrets/example.env`、`.env.example` 均软链于此；改动会随 CI/CD 自动发布到无源码机。

### 2.3 CI/CD 发布链
- `deploy/Dockerfile`：`WORKDIR /deploy` 后加 `COPY tenant.env.example /deploy/tenant.env.example`。
- `deploy-remote.sh`：在 compose up **之前**（或紧随参数校验后）加一段幂等落盘：
  ```
  if [ -f /deploy/tenant.env.example ]; then
    mkdir -p "$WORKSPACE/secrets"
    cp -f /deploy/tenant.env.example "$WORKSPACE/secrets/example.env"
    echo "[deploy-remote] 已同步 env 模板 → secrets/example.env"
  fi
  ```
  - 只写保留名 `example.env`，绝不碰 `<tenant>.env`（AC4）。
  - 幂等：每次部署覆盖，实现「每次更新随 CI/CD 发过去」。

## 3. 边界 / 兼容性
- 有源码机：软链随 git 同步，`readlink` 正常（`deploy/` 存在）。无源码机：`secrets/example.env` 由 deploy-remote.sh 写成**真实文件**（非软链，`deploy/` 不在目标机），内容等价。两侧殊途同归。
- `package-deployment.sh:71` 仍 `copy_file deploy/tenant.env.example ...`（真实文件）→ 不受影响。
- `deploy-release.sh` 菜单排除逻辑：软链 + `-type f` → 自然不列入，安全。

## 4. 回滚
- 纯文件/脚本改动，无 DB、无运行时代码。回滚 = `git revert` 本任务提交；无源码机下次部署 deploy-remote.sh 不再写模板（若同时回滚 Dockerfile/脚本）。已落盘的 `secrets/example.env` 是惰性模板，残留无害。

## 5. 风险
- R-1 软链在某些 Windows/打包场景不被保留 → 本仓库 Linux 部署，`package-deployment.sh` 只打真实文件，风险低。
- R-2 `cp` 之外的工具若按软链名判断类型可能异常 → 已核实唯一消费者 deploy-release.sh 用 `-type f` 天然兼容。
