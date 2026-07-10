# Implement — 执行计划

> 全程在 EnterWorktree 隔离的 worktree 内完成；完成后 commit → 合回 main → ExitWorktree。

## Step 0 — 完整性基线（读）
- [ ] `grep -rhoE "os\.getenv\(\"[A-Z0-9_]+\"" src/sales_agent/core/config.py src/sales_agent/integrations/dingtalk/config.py` 收集部署级 env 键，作为 AC6 对比基线。
- [ ] 确认 taishan.env 键序（已抓取），作为模板骨架。

## Step 1 — 写权威模板 `deploy/tenant.env.example`
- [ ] 按 design §2.2 的 15 分区重写，占位符替换所有敏感值。
- [ ] 头部注释：唯一权威 + 软链说明 + 随 CI/CD 发布说明。
- 验证：`grep -nE "API_KEY=|SECRET=|PASSWORD=|BOCHA_API_KEY=" deploy/tenant.env.example` 全为占位符（无 `sk-` 真值、无真实密码）。

## Step 2 — 软链收敛
- [ ] `rm secrets/example.env && ln -s ../deploy/tenant.env.example secrets/example.env`
- [ ] `rm .env.example && ln -s deploy/tenant.env.example .env.example`
- 验证：`readlink secrets/example.env` == `../deploy/tenant.env.example`；`readlink .env.example` == `deploy/tenant.env.example`；`cat .env.example | head` 显示真实内容；`git status` 显示两者为 type change。

## Step 3 — deploy 镜像 COPY
- [ ] `deploy/Dockerfile` 加 `COPY tenant.env.example /deploy/tenant.env.example`。
- 验证：`docker build -f deploy/Dockerfile deploy/ -t sa-deploy-test` 成功；`docker run --rm --entrypoint ls sa-deploy-test -l /deploy/tenant.env.example` 存在。

## Step 4 — deploy-remote.sh 落盘
- [ ] 在参数/挂载校验后、compose up 前插入幂等 `cp -f /deploy/tenant.env.example "$WORKSPACE/secrets/example.env"`（design §2.3）。
- 验证：`bash -n deploy/deploy-remote.sh`；构造临时 `WORKSPACE=/tmp/ws`（含 `secrets/neo4j.env` + 假 `<tenant>.env`），只执行落盘片段，确认写出 example.env 且 `<tenant>.env` 未被改。

## Step 5 — 完整性对比（AC6）
- [ ] 把 Step 0 收集的键与模板键做 diff，缺失的部署级变量补进模板对应分区（记录哪些故意不放：如纯本机 dev / 内部默认）。

## Step 6 — 回归验证
- [ ] `bash -n scripts/deploy-release.sh`；本地临时建 `secrets/example.env` 软链后跑 `discover_env_files` 等价命令 `find secrets/ -maxdepth 1 -type f -name "*.env" ! -name "example.env"`，确认软链不被列入。
- [ ] `python3 scripts/render-multitenant-deploy.py deploy/tenants.test.json --skip-validation --compose-out /tmp/compose-test.yml --traefik-out /dev/null` 仍成功（未被模板改动影响）。

## Step 7 — 文档（CLAUDE.md 硬性）
- [ ] `README.md`：项目结构图（`.env.example` / `secrets/example.env` 标注软链）、部署节（`cp` 说明不变）、「产品文档对照」/「更新日志」索引指向新 changelog。
- [ ] 新增 `changelog/2026-07-08.md`（若已存在则追加）：逐条记录改动对象/类型/影响/明细/原因。
- [ ] 如有教训 → `tasks/lessons/deploy-cicd.md` 追加「无源码机模板投递」条。

## Step 8 — 收尾
- [ ] Trellis：`task.py finish` 前跑 check；spec 更新（如需）。
- [ ] commit 到 worktree 分支 → 合回 main → `ExitWorktree`。

## 验证命令汇总
```bash
bash -n deploy/deploy-remote.sh scripts/deploy-release.sh
grep -nE "API_KEY=|SECRET=|PASSWORD=" deploy/tenant.env.example      # 应全占位
readlink secrets/example.env .env.example                            # 指向权威
docker build -f deploy/Dockerfile deploy/ -t sa-deploy-test          # COPY 生效
find secrets/ -maxdepth 1 -type f -name "*.env" ! -name example.env  # 软链不入菜单
```
