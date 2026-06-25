# Ontology / Neo4j 接入 CI 自动部署 — 设计文档

- **日期**：2026-06-25
- **状态**：已批准（待 spec review）
- **分支**：`feat/ontology-neo4j-knowledge-engine`
- **作者**：Claude（与用户协同设计）

## 1. 背景与动机

本次迭代引入了基于 Neo4j 的本体知识引擎（`KNOWLEDGE_ENGINE=ontology_neo4j`）。代码侧已就绪（`src/sales_agent/ontology/`、`migrations/versions/0003_ontology_neo4j_metadata.py`、`config.py` 的 `OntologyConfig`/`Neo4jConfig`），但 **CI/CD 部署链路完全没有接上 Neo4j**，导致 push 后即便触发部署，ontology 引擎也无法在生产/测试环境运行。

### 现状的三个断点

1. **Neo4j 容器不会被拉起**：生产部署用的是 `scripts/render-multitenant-deploy.py` 生成的 `docker-compose.generated.yml`。该生成器的 `render_compose()` / `render_tenant_services()` 只产出 `postgres` + 每租户 `api/frontend/stream/worker`，**根本没有 Neo4j 服务定义**。手写 `docker-compose.yml` 里虽有 `profiles:["ontology"]` 的 Neo4j，但生产不走该文件，`deploy-release.sh` 的 `docker compose up -d` 也不带 `--profile ontology`。
2. **应用未启用 ontology 引擎**：`config.py` 默认 `knowledge_engine="legacy_rag"`，需 env `KNOWLEDGE_ENGINE` 覆盖。租户模板 `secrets/example.env` 缺该段；`taishan.env` 里 `KNOWLEDGE_ENGINE=` 为空。
3. **pg 元数据迁移不自动执行**：`migrations/versions/0003` 给 `ingestion_jobs` 加列（ontology 元数据），但 `scripts/docker-entrypoint.sh` 不跑 `alembic upgrade`，纯按 `PROCESS_ROLE` 启进程。旧库缺列会在运行时报错。

## 2. 目标与非目标

### 目标
- 手动触发 `deploy` workflow 后，**prod3（主控+生产）与杭州 test** 自动：更新镜像 → 起 Neo4j → 跑 pg migration → 以 `ontology_neo4j` 引擎拉起应用。
- **prod2 dev**（不在 CI fan-out）通过本地 `deploy-release.sh` 同样能起 Neo4j（生成器统一支持，触发仍为手动——这是现状）。
- 三套环境（prod2/prod3/test）默认启用 `ontology_neo4j`。
- 凭证不进 git、不进 generated compose 字面量。

### 非目标（YAGNI）
- 不改 CI 触发方式（保持 `workflow_dispatch` 手动，不改成 push 自动）。
- 不做每租户独立 Neo4j / 多 database 路由（代码 `Neo4jClient` 用单 database，决策已排除）。
- 不做 Neo4j 备份、集群、HA（单实例够用）。
- 不把 prod2 纳入 CI fan-out（保持现状手动管）。

## 3. 关键设计决策（已与用户对齐）

| 决策点 | 选定 | 理由 |
|---|---|---|
| Neo4j 部署形态 | **共享单实例** | 与 `Neo4jClient(settings.neo4j)` 单 database 代码一致；租户靠图中 `tenant_id`/`agent_id` 属性隔离；资源省、改动最小 |
| pg migration 触发 | **entrypoint 自动**（api 角色启动前 `alembic upgrade head`） | 改动集中在一处；只 api 跑避免 stream/worker 并发竞争；幂等 |
| 启用范围 | **全部含 dev**（prod3 + 杭州 + prod2 dev） | 三套 `tenants.*.json` 均开启 |
| 凭证管理 | **secrets env 注入**（`secrets/neo4j.env` + compose `${NEO4J_PASSWORD}` 插值） | 凭证不进 git/generated compose；neo4j 容器与 app 容器共享同一来源，自动对齐 |

## 4. 架构概览

```
每台机器生成的 docker-compose.generated.yml：
  postgres (已有)
    └─ <tenant>-api / -stream / -worker (已有)
         • environment 注入 NEO4J_URI/USER/PASSWORD/DATABASE
         • depends_on: neo4j (service_healthy)
  neo4j (新增，共享单实例)
         • bolt://neo4j:7687  (container 内部网络)
         • NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
         • volume: neo4jdata (持久化)
         • healthcheck: cypher-shell
         • 凭证来自 secrets/neo4j.env (--env-file 注入)
  migration:
         api 容器启动前 alembic upgrade head (幂等，仅 api 角色)
```

调用链路：`app (api/stream/worker) ──bolt──► neo4j (共享) ──volume──► neo4jdata`。应用层检索分支见 `chat_pipeline.py:421`（`knowledge_engine == "ontology_neo4j"` 时走本体检索）。

## 5. 详细组件改动

### 5.1 `scripts/render-multitenant-deploy.py`（核心）

**inventory 顶层新增 `neo4j` 段**（可选，缺省时按自动检测）：

```json
"neo4j": {
  "enabled": true,
  "image": "registry.internal:5000/neo4j:5",
  "password_env_file": "secrets/neo4j.env",
  "expose_ports": false
}
```

- `enabled` 缺省时：渲染器扫描所有租户 env，若任一 `KNOWLEDGE_ENGINE=ontology_neo4j` 则视为启用（复用已有 `_env_has()` 机制）。
- `image`：prod3/test 用 `registry.internal:5000/neo4j:5`；prod2 dev 可用 `docker.1ms.run/neo4j:5`（本地拉取方便）或同样指 registry。

**`render_compose()` 改动**：Neo4j 启用时输出 Neo4j 服务块：
- `container_name: sales-agent-neo4j`
- `restart: unless-stopped`
- `environment: NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}`（compose 变量插值，来自 `--env-file`）
- `ports`：仅当 `expose_ports=true` 时映射 `7474/7687`（生产默认不暴露，仅容器内可达）
- `volumes: neo4jdata:/data`
- `healthcheck`：`cypher-shell -u neo4j -p ${NEO4J_PASSWORD} 'RETURN 1'`，`start_period: 30s`（模板已在 dev `docker-compose.yml` 验证）
- `volumes` 顶层段新增 `neo4jdata`

**`render_tenant_services()` 改动**：Neo4j 启用时，给每个 `api`/`stream`/`worker` 服务：
- `environment` 追加：`NEO4J_URI: bolt://neo4j:7687`、`NEO4J_USER: neo4j`、`NEO4J_PASSWORD: ${NEO4J_PASSWORD}`、`NEO4J_DATABASE: neo4j`
- `depends_on` 追加：`neo4j: condition: service_healthy`
- 前端容器不需要 Neo4j 配置。

### 5.2 `scripts/docker-entrypoint.sh`

在 `api)` 分支的 `exec` 之前插入 migration 步骤：

```bash
api)
  if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "Running alembic upgrade head..."
    alembic upgrade head || { echo "alembic upgrade failed"; exit 1; }
  fi
  exec sales-agent serve --host 0.0.0.0 --port "${PORT}"
  ;;
```

- 仅 `api` 角色跑（`stream`/`worker` 不跑，避免并发竞争）。
- `RUN_MIGRATIONS` 开关默认 `1`，便于调试时绕过。
- 失败 `exit 1`（fail-fast；0003 仅加列、幂等、无破坏性）。
- `alembic` 用容器已有的 `DATABASE_URL`（`alembic.ini` 的 `sqlalchemy.url` 为空，由 env/`env.py` 注入）。

### 5.3 inventory 模板

- `deploy/tenants.example.json`：顶层加 `neo4j` 段，`enabled: true`、`image: registry.internal:5000/neo4j:5`（统一默认）。
- `deploy/tenants.prod2.json`：`neo4j.enabled=true`，`image=registry.internal:5000/neo4j:5`（prod2 与 prod3 同 VPC，私网可达 registry.internal）。
- `deploy/tenants.prod3.json`：`neo4j.enabled=true`，`image=registry.internal:5000/neo4j:5`。
- `deploy/tenants.test.json`：`neo4j.enabled=true`，`image=registry.internal:5000/neo4j:5`。

> 三套统一用自家 registry 镜像（CI 已 mirror）。`docker.1ms.run/neo4j:5` 仅用于 §8 的本地临时验证（手写 `docker-compose.yml`），不进 inventory。

### 5.4 secrets 模板

- `secrets/example.env`：补充（对齐顶层 `.env.example` 已有段）：
  ```
  KNOWLEDGE_ENGINE=ontology_neo4j
  ONTOLOGY_VECTOR_FALLBACK=conservative
  # Neo4j 共享凭证由 secrets/neo4j.env 注入，此处不再单独列 NEO4J_PASSWORD
  ```
  注：`NEO4J_URI/USER/PASSWORD/DATABASE` 由生成器写入 compose environment，**不**放租户 env（避免每租户重复维护同一共享密码）。租户 env 只留 `KNOWLEDGE_ENGINE` 开关。
- 新建 `secrets/neo4j.env.example`：
  ```
  # 共享 Neo4j 凭证（每台机器手动复制为 secrets/neo4j.env，chmod 600，勿提交）
  NEO4J_PASSWORD=change-me-strong-password
  ```
- 各机真实 `secrets/neo4j.env`：手动放置（已 gitignore，600）。

### 5.5 `scripts/deploy-release.sh`

第 9 步（`docker compose up -d`）改为条件加 `--env-file`：

```bash
ENV_FILE_ARGS=()
if [ -f "secrets/neo4j.env" ]; then
  ENV_FILE_ARGS+=(--env-file secrets/neo4j.env)
fi
docker compose -f "$COMPOSE_FILE" "${ENV_FILE_ARGS[@]}" up -d
```

- 仅当 `secrets/neo4j.env` 存在时注入，向后兼容未启用 ontology 的部署。
- compose 的 `${NEO4J_PASSWORD}` 插值来源即此 `--env-file`。

### 5.6 CI workflow `.gitea/workflows/deploy.yml`

- 触发**保持** `workflow_dispatch`。
- `build-and-push` job 增一步「mirror Neo4j 基础镜像到自家 registry」：
  ```yaml
  - name: Mirror neo4j image to registry
    run: |
      docker pull docker.1ms.run/neo4j:5
      docker tag  docker.1ms.run/neo4j:5 ${REGISTRY}/neo4j:5
      docker push ${REGISTRY}/neo4j:5
  ```
  - 解决杭州跨地域拉官方镜像不稳（与 pgvector 同策略，见 `docs/deploy/cicd-gitea.md` §7-3）。

## 6. 凭证流转

```
secrets/neo4j.env (每台手放, gitignore, 600)
   │  NEO4J_PASSWORD=<strong>
   ▼
deploy-release.sh --env-file secrets/neo4j.env
   │  docker compose 变量插值 ${NEO4J_PASSWORD}
   ├─► neo4j 容器:  NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}   (初始化/鉴权)
   └─► app 容器:    NEO4J_PASSWORD=${NEO4J_PASSWORD}      (连接鉴权)
```

两端共享同一插值变量 → 密码天然一致，无需在多处手填。凭证不出现在 git、不出现在 generated compose 字面量（compose 里只有 `${NEO4J_PASSWORD}` 占位）。

## 7. 错误处理与回退

| 场景 | 行为 |
|---|---|
| Neo4j 起不来 | app `depends_on: service_healthy` 不启动（ontology 为硬依赖，可接受）；`main.py:43` 另有「连接失败仅告警不阻断」兜底 |
| 临时回退 ontology | 租户 env `KNOWLEDGE_ENGINE=legacy_rag` → Neo4j 容器仍起但不被使用，零风险 |
| migration 重跑 | alembic 幂等，无副作用 |
| `secrets/neo4j.env` 缺失 | deploy 跳过 `--env-file`，`${NEO4J_PASSWORD}` 为空 → Neo4j 容器 `NEO4J_AUTH` 无效、启动失败（部署前 checklist 会拦截） |

**depends_on 软硬权衡**：采用 `service_healthy`（硬依赖）。理由：启用 ontology 的场景 Neo4j 是必需组件，硬依赖能在部署时尽早暴露 Neo4j 故障，优于「app 起来后运行时才发现连不上」。Neo4j healthcheck 已设 `start_period: 30s` 容忍冷启动。

## 8. 测试与验证

### 单元测试
- `render-multitenant-deploy.py`：新增快照断言——
  - `neo4j.enabled=true` 的 inventory → 输出含 `neo4j` 服务、`neo4jdata` volume、app 服务含 `NEO4J_*` env 与 `depends_on: neo4j`。
  - `neo4j` 段缺省但无租户启用 ontology → 输出**不含** neo4j（向后兼容）。
  - `neo4j` 段缺省但有租户 `KNOWLEDGE_ENGINE=ontology_neo4j` → 自动启用。

### 本地 dev 验证
- `docker compose -f docker-compose.yml --profile ontology up -d neo4j`（已有路径）先验证 Neo4j + ontology 端到端可用。
- **注意**：手写 `docker-compose.yml` 的 neo4j 用写死测试密码 `neo4jtest123`（见 `docker-compose.yml:53`），仅作临时验证，**不要**与 generated 部署混用同一 Neo4j 实例。prod2 的正式 dev 部署仍走 generated compose + `secrets/neo4j.env`（`${NEO4J_PASSWORD}`），与生产路径一致。

### 部署后 checklist
- 三台 `docker inspect sales-agent-neo4j` → `Status=running`、healthcheck healthy。
- 三台 app `/ready` → 返回 ontology 就绪信息（`health.py:53`）。
- `ingestion_jobs` 表含 0003 新增列（`\d ingestion_jobs` 或查询）。
- 跑一次 ontology ingest（`/api/routes/ontology.py`）→ 验证写图成功、`ingestion_jobs.engine=ontology_neo4j`。
- 杭州 `docker pull registry.internal:5000/neo4j:5` 成功（跨地域公网+TLS+CA+SG+ufw 全链路）。

## 9. Rollout 顺序

1. dev（prod2）本地 `deploy-release.sh --env-file secrets/neo4j.env` 先验证（风险最低）。
2. prod3 触发 `deploy` workflow（fan-out 含 prod3 本地 + 杭州）。
3. 杭州（跨地域）单独观察镜像拉取与 Neo4j 健康。
4. 每台跑部署后 checklist。

## 10. 受影响文件清单

| 文件 | 改动类型 |
|---|---|
| `scripts/render-multitenant-deploy.py` | 修改（核心：渲染 Neo4j + app env 注入） |
| `scripts/docker-entrypoint.sh` | 修改（api 角色加 alembic upgrade） |
| `scripts/deploy-release.sh` | 修改（条件 `--env-file`） |
| `.gitea/workflows/deploy.yml` | 修改（mirror neo4j 镜像 step） |
| `deploy/tenants.example.json` | 修改（加 neo4j 段） |
| `deploy/tenants.prod2.json` / `prod3.json` / `test.json` | 修改（加 neo4j 段） |
| `secrets/example.env` | 修改（补 KNOWLEDGE_ENGINE 段） |
| `secrets/neo4j.env.example` | 新增 |
| `secrets/neo4j.env` | 各机手动放置（不入库） |
| 测试文件（render 生成器单测） | 新增 |
| `changelog/2026-06-25.md` | 追加本次改动记录 |
| `README.md` | 更新「产品文档对照」/部署说明（如涉及） |

## 11. 风险

- **跨地域镜像拉取**：杭州拉 `registry.internal:5000/neo4j:5` 依赖主控两层防火墙（安全组 + ufw）放行 :5000（doc §3）。已在 CI mirror 步骤统一推到自家 registry，规避 docker.1ms.run 不稳。
- **`alembic upgrade` 阻断启动**：若 migration 失败，api 容器不启动。mitigation：0003 是纯加列、幂等；fail-fast 比运行时缺列报错更早暴露。
- **共享 Neo4j 多租户写入冲突**：代码已用 `tenant_id`/`agent_id` 属性隔离，无 schema 层冲突；单实例性能上限留待后续评估。
