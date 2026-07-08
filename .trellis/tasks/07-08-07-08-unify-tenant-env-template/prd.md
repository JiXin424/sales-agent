# 统一租户 env 模板并随 CI/CD 发布到无源码机

## Goal

消除三份互相漂移的 env 模板，确立 `deploy/tenant.env.example` 为唯一权威租户 env 模板（内容按最全的 `secrets/taishan.env` 补齐），并让它每次部署自动随 CI/CD 发布到无源码目标机（test / prod3），使目标机运维在编辑 `secrets/<tenant>.env` 时始终能看到最新、完整的可用变量清单。

## Background / 触发原因

- fuduoduo（test 服务器无源码机）的 scenarios 功能不生效，根因是其 `secrets/fuduoduo.env` 缺 `SCENARIO_COACH_ENABLED=true`（默认关闭）。
- 深层问题：无源码机拿不到任何 env 模板 —— CI/CD 到无源码机的唯一自动通道是 deploy 镜像（`docker build -f deploy/Dockerfile deploy/`，build context 仅 `deploy/`），当前只带 `compose-*.yml` + `deploy-remote.sh`，**不带 env 模板**。运维看不到新增变量（如 `SCENARIO_COACH_ENABLED`、`DINGTALK_MEDIA_*`）存在，形成「本地好、服务器坏」。
- 同时仓库有三份模板并存且已漂移：`secrets/example.env`（deploy-release.sh 认的权威）、`deploy/tenant.env.example`（打进离线包）、`.env.example`（根，本机 dev）。三份都缺 taishan.env 的 `EMBEDDING_*`、`DINGTALK_MEDIA_*`、`DINGTALK_VISION_MODEL`/`AUDIO_MODEL`、`HYBRID_RETRIEVAL`、`BOCHA_API_KEY`。

## Requirements

- R1 权威模板 = `deploy/tenant.env.example`，内容为 `secrets/taishan.env` 键集 ∪ 现有三份模板已有键（不得丢失 `GUIDED_FLOWS_ENABLED`、`ONTOLOGY_VECTOR_FALLBACK` 等既有覆盖）。
- R2 模板不含任何真实密钥：所有敏感值（`*_API_KEY`、`*_APP_SECRET`、`NEO4J_PASSWORD`、`BOCHA_API_KEY`、`DINGTALK_MEDIA_API_KEY`）用占位符。Neo4j 连接段注明「CI/CD 由生成器从 `secrets/neo4j.env` 注入，无源码机无需在此填写」。
- R3 `secrets/example.env` 与 `.env.example` 改为符号链接指向 `deploy/tenant.env.example`，实现单一真源、零漂移。
- R4 `deploy/Dockerfile` 把权威模板 COPY 进 deploy 镜像；`deploy-remote.sh` 每次部署把它落盘到目标机 `${WORKSPACE}/secrets/example.env`（幂等覆盖），实现「每次更新随 CI/CD 发过去」。
- R5 不破坏既有链路：`scripts/deploy-release.sh` 租户发现仍正确排除 example.env；`cp .env.example .env`（本机 dev）仍能得到真实内容；`scripts/package-deployment.sh` 仍能打进模板。
- R6 按 CLAUDE.md：更新 `README.md`（项目结构/部署节/产品文档对照）+ 新增 `changelog/2026-07-08.md` 记录；不新建/改数据库，无需 Alembic。

## Non-Goals

- 不自动修改 fuduoduo 的实际 env（那是运维在 test 机上加一行 + 重启，属另一动作）。
- 不改模板变量的语义/默认值，不动 `config.py` 读取逻辑。
- 不做模板从 taishan.env 自动再生成（本次是手工权威模板 + CI 自动发布，不是自动再生成）。

## Acceptance Criteria

- [ ] AC1 `deploy/tenant.env.example` 含 taishan.env 全部键 + 既有模板独有键；无真实密钥（grep 校验占位符）。
- [ ] AC2 `secrets/example.env`、`.env.example` 均为软链且 `readlink` 指向 `deploy/tenant.env.example`；`git ls-files` 仍跟踪三者。
- [ ] AC3 `deploy/Dockerfile` 含 `COPY tenant.env.example /deploy/`；本地 `docker build -f deploy/Dockerfile deploy/` 成功且镜像内 `/deploy/tenant.env.example` 存在。
- [ ] AC4 `deploy-remote.sh` dry-run（或读代码 + 局部执行落盘逻辑）确认会把模板写到 `${WORKSPACE}/secrets/example.env`，且不覆盖任何真实 `<tenant>.env`。
- [ ] AC5 `bash -n` 通过；`scripts/deploy-release.sh` 的 `discover_env_files` 在软链存在时仍不把 example.env 列入租户菜单。
- [ ] AC6 模板键集覆盖 `config.py` + dingtalk config 中所有 `os.getenv(` 读取的部署级变量（完整性对比，缺失则补）。
- [ ] AC7 README 相关节 + `changelog/2026-07-08.md` 已更新。
