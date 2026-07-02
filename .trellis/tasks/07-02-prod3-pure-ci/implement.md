# Implement — prod3 去源码化部署（选项 A）

> 前置：1.4 review gate 已确认选 **A**（保留租户 + image-deploy）。每步 Gate 绿再继续。prod3 是生产 CI 机，**严格分步、保留回退**。

## Step 0 — 取 prod3 线上 inventory（需 prod3 访问）

- [ ] SSH prod3（`47.120.55.219`），读 `/root/code/sales-agent/deploy/tenants.json`，记录 songbai + taishanyanshi 的端口/域名/neo4j/traefik shared_network。
- [ ] 记录 prod3 现行 `sales-agent-db` / neo4j / traefik 容器名与网络，供 compose 校对。
- **Gate 0**：拿到准确 inventory（不凭 `subdomain-routing` 文档猜，文档可能过期）。

## Step 1 — `deploy/tenants.prod3.json`

- [ ] 镜像 `tenants.test.json` 结构，填 songbai + taishanyanshi（Step 0 数据），含 `_comment` + `traefik.shared_network`。
- **Gate 1**：`OVERRIDE_IMAGE=registry.internal:5000/sales-agent:latest python3 scripts/render-multitenant-deploy.py deploy/tenants.prod3.json --skip-validation --compose-out /tmp/compose-prod3.yml` 成功；`grep shared_network /tmp/compose-prod3.yml` 命中（lessons #10）；`pytest tests/unit/test_render_multitenant_deploy.py -q` 绿。

## Step 2 — `deploy.yml` render 循环加 prod3

- [ ] 按 design 改动 2，把 render 改成 `for env in test prod3`。
- **Gate 2**：本地 `act` 干跑或人工 review YAML；render 产出 `compose-test.yml` + `compose-prod3.yml` 两份。

## Step 3 — ci-fanout local 分支 image-deploy 支持

- [ ] 按 design 改动 3 改 `ci-fanout.sh:52-59`。
- [ ] `bash -n scripts/ci-fanout.sh`。
- **Gate 3**：prod2 本地造一个 dummy deploy 镜像（`echo hi` entrypoint），造一条 `local:true, method:image-deploy, env:prod3` 临时 target，跑 `scripts/ci-fanout.sh`（用 dummy IMAGE/DEPLOY_IMG）确认走新分支、`docker run` 被触发、原 deploy-release 分支不受影响。验证后删临时 target。

## Step 4 — `deploy-targets.json` 切 prod3 + 修注释

- [ ] 按 design 改动 4 改 prod3 为 `image-deploy, local:true, env:prod3`；修 `_comment`。
- **Gate 4**：JSON 合法（`python3 -m json.tool`）；`diag` job 的校验（`deploy/deploy-targets.json` 存在）不受影响。

## Step 5 — CI build 验证（不切 prod3 部署）

- [ ] push → CI build-and-push；确认 deploy 镜像含 `compose-prod3.yml`：在 prod3 `docker run --rm registry.internal:5000/sales-agent-deploy:<sha> ls /deploy/`。
- [ ] 确认 test 仍正常部署（回归保护）。
- **Gate 5**：deploy 镜像两份 compose 都在；test fan-out 绿。

## Step 6 — 切 prod3（关键步骤，低峰期）

- [ ] 手动触发 fan-out 到 prod3（或等 push），观察：songbai + taishanyanshi 容器经 image-deploy 重启。
- [ ] 验证：`/ready` 通、`/health` 通、钉钉 stream 容器重连（日志）、控制台可访问、**时间旅行页面** run 后产 checkpoint。
- **Gate 6**：两租户业务正常 ≥ 10min。若异常 → Rollback（改回 deploy-release + 旧 sha）。

## Step 7 — prod3 源码 + registry 清理（稳定后）

- [ ] 文档标注 `/root/code/sales-agent` 克隆不再被部署维护。
- [ ] 查 `.209` `sales-agent-registry` 无引用 → `docker stop/rm`；更新 `infra/cicd-compose.yml` 注释。
- [ ] 文档写清 `registry.internal = prod3 私网 172.25.186.210`。
- **Gate 7**：prod2/prod3/test 仍能 `docker pull registry.internal:5000/...` 正常。

## Step 8 — prod2 fan-out 手动化（评估项，可选）

- [ ] 若 review 同意：prod2 从 `deploy-targets.json` 摘除（或加 `manual:true` + ci-fanout 跳过逻辑），文档说明 dev 手动 `deploy-release.sh`。
- **Gate 8**：push 后 prod2 不再被自动 reset；dev 手动部署流程文档化。

## Step 9 — 收尾

- [ ] `changelog/2026-07-02.md` 追加（对象=CI/部署，类型=架构变更，影响=prod3，明细+原因）。
- [ ] README「产品文档对照」「更新日志」节同步。
- [ ] 更新 `tasks/lessons.md`：记录 prod3 image-deploy 切换的注意点（如 REPO_DIR≠DIR、local 分支 image-deploy 新增）。
- [ ] 自检：资深工程师会批准吗？回退路径实测过吗？

## Rollback 触发条件

- Gate 6 任一业务验证失败 → 立即：
  1. `deploy-targets.json` prod3 改回 `deploy-release, local:true`（git revert 该文件）。
  2. prod3 `/root/code/sales-agent` 克隆还在 → `bash scripts/deploy-release.sh --yes` 恢复源码部署。
  3. 必要时指定旧 sha deploy 镜像。
