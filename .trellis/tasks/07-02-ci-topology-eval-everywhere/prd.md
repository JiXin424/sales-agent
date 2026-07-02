# CI/CD 拓扑重构: 仅 prod2 留源码 + eval 全机可用

## Goal

把分发模型从「每台目标机 git 全量同步源码 + 现场渲染 compose」改为：**仅 prod2(开发机)保留全量源码**；prod3 只保留 CI 职责（Gitea + runner + registry），其业务租户也改为镜像部署（不再依赖源码）；其余服务器（test / 未来机）维持镜像部署。在此之上：

- **个性化能力天然就绪**：每 agent 独特的凭证（`secrets/<tenant>.env`，各机本地）、prompt（DB `prompt_versions` 表 + 控制台 UI 运行时改）——无源码机已支持，无需开发。
- **eval 每台都能跑**：image-deploy 无源码机也能执行 eval（修复镜像缺口 + 同步运行入口 + 每机 env）。
- **时间旅行全机支持**：功能自包含在镜像里（无迁移/无配置/无 secrets），各机部署到含该功能的新镜像即覆盖。

## Background（经代码 + 运行时调研）

### 机器身份（已核实，纠正历史混淆，见 lessons.md #17/#18）

| 名义 | 公网 | 私网 | 职责 | 当前业务租户 |
|---|---|---|---|---|
| **prod2 / 本机** | 47.120.50.181 | **172.25.186.209** | 开发机（唯一留源码） | taishan、taishankaifa2（实测容器在跑） |
| **prod3 / 主控** | **47.120.55.219** | **172.25.186.210** | CI 主控（Gitea+runner+registry） | songbai、taishanyanshi（见 subdomain-routing 设计） |
| **test** | 47.118.16.235 | — | image-deploy 目标 | fuduoduo |

- `registry.internal` → `172.25.186.210` = **prod3 私网 IP**（`bootstrap-new-master.sh:36` `BENJI_PRIVATE_IP`）。registry 本来就在 prod3，**与「CI/registry 留 prod3」一致，无冲突**。本机 `.209` 上的 `sales-agent-registry` 容器是闲置副本（`infra/cicd-compose.yml:5-7` 注释）。
- `docs/superpowers/specs/2026-06-25-sourceless-deploy-design.md` 是 `image-deploy` 机制的来源，当时**显式把「prod3/prod2 保留源码、不改其部署方式」列为非目标**。本任务**反转该决策**：prod3 部署层也去源码化。
- 时间旅行调研结论（`changelog/2026-07-02.md` + `graph_debug.py`）：checkpoint 表由 LangGraph `AsyncPostgresSaver.setup()` 首次使用时自建，**不动 DB schema、无 alembic migration、无新配置/secrets**；后端 + 前端全在镜像里。仅部署新镜像即可。
- eval 调研结论：`eval/` 已 `COPY` 进镜像（`Dockerfile:55`），但 `.dockerignore` 的 `*.md` 把 `eval/questions.md`（126 题题库，80 题带参考答案）排除了；DeepEval/检索 eval 需 `DATABASE_URL` + `MODEL_*` + `OPENAI_API_KEY`（裁判）+ `data/` 卷；`run_retrieval_eval.py:413` 默认 ground_truth 路径指错（指向 `scripts/`，实际在 `eval/`）。

### 当前痛点

1. prod3 当前是 `deploy-release, local:true`（`deploy-targets.json:5`），依赖 `/root/code/sales-agent` 全量源码做本地渲染部署——与「仅 prod2 留源码」冲突。
2. 无源码机（test）跑不了 eval（host 上无 `eval/`，镜像里题库缺）。
3. `deploy-targets.json` 的 `_comment` 仍写「prod2 不在此列表」，与实际不符。
4. CI 每次 fan-out 对 prod2（开发机）`git reset --hard origin/main`（`ci-fanout.sh:90`），会打乱本地工作区。

## Requirements（父级，跨子任务）

- **PR1** 仅 prod2 保留全量源码工作树；prod3 不再为部署保留持久源码克隆（CI 用自有 ephemeral checkout 不受影响）。
- **PR2** prod3 的业务租户（songbai、taishanyanshi）部署方式从「需源码的 deploy-release」切到「无源码的 image-deploy」，租户本身不中断。
- **PR3** 每台服务器（含无源码机）都能正常跑 eval（脚本可达、依赖齐、题库在、env 可配）。
- **PR4** 时间旅行在 prod3 / test 上随新镜像生效（验证，非新开发）。
- **PR5** `registry.internal` 解析、各注释/文档与新拓扑一致；消除历史混淆残留。

## Constraints

- 复用现有 `image-deploy` 机制（deploy 镜像 + `deploy-remote.sh` + ci-fanout 分发），不另起炉灶。
- prod3 是 CI 主控，**改动须低风险分步**：先在 prod2 本地验证 deploy 镜像 + compose 渲染，再切 prod3，保留旧 sha 回退能力。
- 不动 app 镜像 entrypoint / alembic 行为。
- 个性化凭证（`secrets/`）永远不进 git / 不进镜像，各机本地维护。
- 遵循 lessons：`.dockerignore` 编辑须前导 `/` 锚定（#11）；traefik `shared_network` external 不能漏（#10，render 已支持）；机器身份先核实再下结论（#17/#18）。

## Task Map（子任务）

| 子任务 | slug | 独立可验证交付 |
|---|---|---|
| **C1 eval 全机可用** | `07-02-eval-on-all-servers` | 无源码机上一条命令跑通 eval，题库在镜像里，env 清单就位 |
| **C2 prod3 降级为纯 CI 机** | `07-02-prod3-pure-ci` | prod3 去 source 化部署后 songbai/taishanyanshi 不中断，registry/注释统一 |

时序：C1 与 C2 相互独立，可并行；C2 风险更高，建议先做 C1（低风险）暖身并验证 deploy 镜像链路，再推 C2。

## Cross-Child Acceptance Criteria

- [ ] prod2 仍是唯一保留全量源码的机器；prod3 `/root/code/sales-agent` 不再被部署依赖（可保留只读克隆或瘦身，二选一，见 C2）。
- [ ] prod3 songbai + taishanyanshi 经 image-deploy 重启后业务正常（`/ready` 通、钉钉 stream 重连）。
- [ ] test（无源码）上能跑 `run_conversation_eval.py` 且题库非空。
- [ ] 时间旅行页面在 prod3 / test 控制台可用（run 后产 checkpoint、可看时间轴）。
- [ ] `deploy-targets.json` `_comment` 与实际一致；`registry.internal` 解析无歧义。
- [ ] CI push main 后 fan-out 全绿，无目标被 reset --hard 冲掉本地工作区（prod2 若保留为自动目标则 stash 行为有文档）。
- [ ] changelog 当日记一条；README「产品文档对照」「更新日志」节同步。

## Out of Scope

- 不迁移 Gitea/runner/registry 到其它机器（用户已定：留在 prod3）。
- 不做 deploy 镜像签名/验证、secrets 加密同步（沿用 sourceless-deploy 设计的 YAGNI）。
- 不改生产 HTTP/钉钉执行路径代码、不改 prompt 解析逻辑。
- 不新增自动 eval 调度（cron/侧车）——本期只确保「能跑」，触发方式留待后续。
- 不动 prod2 是否留源码（它就是「本地」，保留）。

## Notes

- 子任务各自有 `prd.md` + `design.md` + `implement.md`。
- 关键设计基线见 `docs/superpowers/specs/2026-06-25-sourceless-deploy-design.md`、`docs/superpowers/specs/2026-06-26-subdomain-routing-design.md`、`tasks/lessons.md` #10/#11/#17/#18。
