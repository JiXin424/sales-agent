# prod3 降级: 去源码化部署 + registry/注释统一

> 父任务: `07-02-ci-topology-eval-everywhere`

## Goal

让 prod3 在**部署层面不再依赖源码**：`/root/code/sales-agent` 不再被 fan-out 的 `git reset --hard` 维护，prod3 的业务租户改走 image-deploy（与 test 一致）。prod3 仍保留 CI 职责（Gitea + runner + registry）。顺带清理 `registry.internal` 解析残留、修正 `deploy-targets.json` 注释、评估 prod2 fan-out 安全性。

## ✅ 设计岔口：已确认选 A（2026-07-02 review gate）

**prod3 现在跑着业务租户 songbai + taishanyanshi**（`docs/superpowers/specs/2026-06-26-subdomain-routing-design.md:14`：.219 = songbai(8003)、taishanyanshi(8002)）。用户答 Q2 时说「prod3 降级为**纯 CI** 机」，但字面「纯 CI」= 不跑业务租户。二者冲突，有三条路：

| 选项 | 含义 | 代价 |
|---|---|---|
| **A（推荐）** | prod3 保留 songbai/taishanyanshi，但部署改 image-deploy、删源码 | 小：新增 `tenants.prod3.json` + CI 多渲染一份 compose + 改 target method；租户不中断 |
| B | songbai/taishanyanshi 迁出 prod3（到 prod2/test），prod3 真·纯 CI | 大：DB 数据迁移、端口/traefik 重路由、停机窗口 |
| C | 维持现状，prod3 仍是 deploy-release（保留源码） | 与父任务「仅 prod2 留源码」冲突，**不推荐** |

**已确认选 A**（2026-07-02 review gate）：租户留 prod3，部署切 image-deploy、去源码。design/implement 均按 A 写。

## Background

- prod3 = 公网 `47.120.55.219` = 私网 `172.25.186.210`（`bootstrap-new-master.sh:35-36`），**同一台机**。`registry.internal → .210` 即指向 prod3，与「registry 留 prod3」一致，**无真冲突**（纠正历史混淆，见 lessons.md #17/#18）。
- prod3 当前在 `deploy-targets.json:4` 为 `deploy-release, local:true`，依赖 `/root/code/sales-agent` 全量源码本地渲染部署。
- `docs/superpowers/specs/2026-06-25-sourceless-deploy-design.md` 当时显式「不改 prod3/prod2 部署方式」——本任务反转此决策。
- `image-deploy` 链路已就绪（test 在用）：CI 渲染 `compose-<env>.yml` → 烤进 `sales-agent-deploy` 镜像 → ci-fanout `docker run` 挂 docker.sock + secrets。
- `deploy.yml` 现仅渲染 `test` 一份 compose（build-and-push step 7）；需扩到 `prod3`。
- ci-fanout 的 **local 分支不支持 image-deploy**（`:52-59` 只处理 deploy-release/image-retag）——见 design 改动。
- 本机 `.209` 上有个闲置 `sales-agent-registry` 容器（`:5000`），是历史副本，可清。

## Requirements

- **R1（选项 A）** prod3 业务租户 songbai + taishanyanshi 部署方式由 `deploy-release` 切到 `image-deploy`，租户运行不中断（`/ready` 通、钉钉 stream 重连）。
- **R2** 新增并提交 `deploy/tenants.prod3.json`（prod3 inventory，镜像 `tenants.test.json` 做法）；CI render 循环加 `prod3`，产出 `compose-prod3.yml` 烤进 deploy 镜像。
- **R3** ci-fanout **local 分支增加 image-deploy 支持**（prod3 是 runner 本机，`local:true`），本地 `docker run sales-agent-deploy`。
- **R4** prod3 `/root/code/sales-agent` 不再被 fan-out 维护源码；可瘦身只留 `secrets/`（+ 可选 data/logs 卷），或保留只读克隆——二选一，文档化。
- **R5** `deploy-targets.json` `_comment` 修正为与实际一致；移除/改写关于 prod2 的过期注释。
- **R6** 清理本机 `.209` 闲置 `sales-agent-registry` 容器（确认无引用后），统一 `registry.internal` 语义在文档里写清（= prod3 私网 IP）。
- **R7（评估项）** prod2 是否从「自动 fan-out 目标」改为「手动部署」，避免开发机被 `git reset --hard`/`stash` 打乱——给出建议并文档化（不强制改）。

## Constraints

- prod3 是 CI 主控，**严禁一刀切**：先 prod2 本地 build + 验证 deploy 镜像与 `compose-prod3.yml`，再切 prod3 target，保留旧 sha deploy 镜像回退。
- 不动 Gitea/runner/registry 进程本身（只动部署 target + render 循环 + ci-fanout local 分支）。
- 遵循 lessons：traefik `shared_network` external 不能漏（#10，render 已支持）；机器身份先核实（#17/#18）。
- `secrets/` 永不进 git；prod3 songbai/taishanyanshi 的 `secrets/*.env` 留在 prod3 本地。

## Out of Scope

- 不迁 Gitea/runner/registry 到别的机器。
- 不做 deploy 镜像签名/验证。
- 不改 app 镜像 entrypoint / alembic。
- 选项 B（租户迁出 prod3）的 DB 迁移细节——仅当 review 选 B 才展开。

## Acceptance Criteria

- [ ] `deploy/tenants.prod3.json` 提交，含 songbai + taishanyanshi，`--skip-validation` 可渲染出 `compose-prod3.yml`。
- [ ] CI build 后 deploy 镜像里含 `compose-prod3.yml`（`docker run --rm <deploy-img> ls /deploy/`）。
- [ ] ci-fanout local 分支能跑 image-deploy（prod2 本地用临时容器冒烟过）。
- [ ] prod3 切换后 songbai + taishanyanshi 经 image-deploy 重启，`/ready` 通、钉钉 stream 重连、控制台可访问。
- [ ] prod3 fan-out 不再对 `/root/code/sales-agent` 做 `git reset --hard`。
- [ ] `deploy-targets.json` `_comment` 与实际一致；本机闲置 registry 容器已清（或文档说明保留原因）。
- [ ] 时间旅行在 prod3 控制台可用（随父任务验证项）。
- [ ] changelog 当日记一条；README 同步。

## Notes

- 详细切换步骤、回退、prod2 fan-out 评估见 `design.md`；执行 checklist 见 `implement.md`。
- 设计岔口已确认选 **A**（2026-07-02 review gate）。
