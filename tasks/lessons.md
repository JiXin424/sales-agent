# Lessons（教训记录）

> 每次纠正后更新。记录模式，防止重复犯错。

## 1. `str.format` 双花括号是转义，不是占位符
- **场景**：prompt 模板用户输入区写了 `{{message}}`，executor 用 `template.format(message=...)` 填充。
- **教训**：Python `str.format` 中 `{{` → 字面 `{`，`{{message}}` **不会**被替换，会原样输出
  `{message}`。JSON 示例区用双花括号是对的（要输出字面 JSON 结构），但**变量占位符必须用单花括号**
  `{message}`。
- **检查方法**：`string.Formatter().parse()` 对 `{{message}}` 也识别为含字段 `message`，所以
  `"{message}" in prompt` 这种子串校验**无法发现此 bug**。必须对 `.format()` **渲染后的结果**做断言
  （值是否真的注入）。见 `tests/unit/test_visit_post_visit_placeholders.py`。

## 2. SQLAlchemy：先 flush 子对象再设外键，否则外键可能丢失
- **场景**：测试里 `db.add(prompt_set)` 后立即 `agent.prompt_set_id = prompt_set.id`，一次 `flush`，
  结果 `agent.prompt_set_id` 没持久化（读回为 `None`）。
- **教训**：新增子对象（如 `AgentPromptSet`）并绑定到父对象外键时，**先 `flush` 子对象拿到稳定 id，
  再设父外键，再 `flush`**。`add + 赋值 + 一次 flush` 的组合会导致外键丢失（与 identity map / dirty
  追踪时序有关）。
- **正确范式**（见 `test_two_agents_different_prompt_versions`）：
  ```python
  db.add(ps); await db.flush()      # 先持久化 ps
  agent.prompt_set_id = ps.id
  await db.flush()                  # 再持久化外键
  ```
- **排查手段**：`_resolve_agent_prompt_version` 返回 None 时，打印 `agent.prompt_set_id` 确认是否持久化。

## 3. `create_all` 不处理已有表的加列，必须用 Alembic
- **场景**：项目用 `Base.metadata.create_all` 建表，改模型加列后，已有库不会自动加列。
- **教训**：生产 DB schema 变更**必须走 Alembic migration**（CLAUDE.md 强制要求）。`create_all` 只建
  新表，不改已有表结构。
- **baseline 策略**：对已有生产库用 `alembic stamp head` 标记当前状态（不执行 DDL），再 `alembic
  upgrade head` 跑增量 migration。新库可直接 `upgrade head`（建表仍由 create_all 完成）。

## 4. 解耦改造的"接入面"必须逐链路核对
- **场景**：prompt 解耦第一阶段只接了主 Web 链路，钉钉流式 + CLI 绕过，导致运营改后台对生产主渠道
  不生效——半成品。
- **教训**：解耦类改造要**列出所有调用点**（grep 函数名），逐个确认是否接入新路径，不能只改主链路。
  本次用子代理梳理出 4 个调用点（chat_pipeline / streaming_handler / cli×2）+ router/risk/coach。

## 5. 测试用 `_make_agent` 而非 `ensure_default_agent_for_tenant` 建 Agent
- **场景**：`ensure_default_agent_for_tenant` 会自己创建 prompt_set 并绑定 agent，测试中覆写
  `prompt_set_id` 时行为异常（配合 lessons #2 的 flush 时序问题）。
- **教训**：需要精确控制 agent 的 prompt_set 绑定的测试，用 `AgentService.create_agent`（不预绑
  prompt_set）+ 手动建 set + 设外键，可控性更好。

## 6. superpowers SDD：brief 文件路径不稳定，必须给子代理"确切代码"
- **场景**：执行 ontology-neo4j 计划时，skill 的 `scripts/task-brief` 把任务 brief 写到插件缓存路径
  `/root/.claude/plugins/cache/.../6.0.3/.superpowers/sdd/task-N-brief.md`，该文件**间歇性消失**——部分
  子代理能读到，部分读不到。Task 6 的 implementer 读不到 brief，仅凭我 prompt 里的高层描述就**自行编造了
  完全不同的风险模型**（矛盾/不确定检测，而非计划里的价格承诺/资质/政策模型），且会挂掉计划自带的测试。
- **根因**：插件缓存下的 `.superpowers/sdd/` 不是稳定的交接位置。仓库本地的
  `/root/code/sales-agent/.superpowers/sdd/` 才稳定（reviewer/implementer 一直能正常读那里的 report 文件）。
- **如何避免**：把任务 brief 写到**仓库本地** `.superpowers/sdd/task-N-brief.md`（用 Write 工具，复制计划里
  该任务全文），或直接在 dispatch prompt 里**内联确切代码**。绝不只给子代理一段散文摘要就让它做"转录"任务。
  对转录类任务，提交后**自己抽查**实际落盘代码是否与计划一致——因为本环境的 reviewer 子代理也跑不动
  pytest（它们的 venv 与 implementer 不同，会误报 "pgvector not installed"）。
- **相关**：reviewer 子代理的环境 ≠ implementer 的 `.venv`，所以不要让 reviewer 自己跑测试，依赖
  implementer 报告 + diff + 内联 spec 即可。控制器（自己）可用 `.venv/bin/pytest` 复核。

## 7. 多项目共用宿主机：端口会漂移，先 curl 实测再信配置
- **场景**：`console` 下 `npm run dev` 报「当前实例尚未配置 Agent」+
  `ERR_CONNECTION_REFUSED http://localhost:8001/instance/agent`。前端 `.env.development` 和手写
  `docker-compose.yml` 都写 8001，但本机 8001 早被**另一个项目** `ai-coach-fastapi` 占用（404）；
  sales-agent 后端实际按 `deploy/tenants.json`（`api_port: 8003`）跑在 8003（200，Agent 存在且 active）。
- **根因**：本宿主机上跑了 omniagent / ai-coach / sales-agent 等多个项目，端口 8000/8001 都被占，
  sales-agent dev 被挤到 8003，但**两处 dev 配置（前端 env + 手写 compose）没同步**，仍写 8001。
- **教训**：
  1. 报「连不上后端 / 未配置 Agent」类问题，**先 `curl` 实测候选端口**（`:8001`、`:8003` …）再下结论，
     不要被前端兜底文案带偏——`App.tsx` 的 `InstanceEntry` 对任意 fetch 失败一律显示「未配置 Agent」，
     把连通性问题误诊成业务缺数据。
  2. 端口是**分环境**的：`deploy/tenants.prod3.json` = 8001（生产机，无 ai-coach，正确）；
     `deploy/tenants.json` = 8003（本 dev 机）。修 dev 端口时**不要顺手改 prod 配置**。
  3. 三处真相源要一致：`deploy/tenants*.json`（生成器输入）→ `docker-compose.generated.yml`（运行实际）
     → `console/.env.development`（前端）。手写 `docker-compose.yml` 容易漏更新。
- **验证范式**：改完前端 env 后，启动 `npm run dev`，`curl http://localhost:<vite>/src/api/client.ts`
  可看到 vite 注入的 `import.meta.env.VITE_API_BASE_URL` 实际值，确认前端真用新端口。

## 8. bash `((n++))` 在 `set -e` 下当旧值为 0 会杀死脚本
- **场景**：`scripts/deploy-release.sh` 交互菜单默认高亮首项（`selected=0`），按 ↓ 执行
  `((selected++))`，脚本直接退出、无任何报错——用户「按一下方向键就退出」。
- **根因**：`(( ))` 的退出码由**表达式值**决定——值为 0 → 退出码 1，值非 0 → 退出码 0。
  `selected++` 是**后置自增**，取值为旧值；旧值为 0 时退出码 1，被 `set -euo pipefail`
  当成错误，终止整个脚本。`((selected--))` 被 `[ selected -gt 0 ]` 守住所以侥幸安全。
- **教训**：在 `set -e` 脚本里**永远不要用 `((n++))` / `((n--))` / `((n+=...))` 做自增自减**，
  统一改用算术赋值 `n=$((n + 1))` / `n=$((n - 1))`（赋值恒为退出码 0）。同理 `read` 在
  EOF/Ctrl-D 返回非零也会被 `set -e` 杀死，交互循环要 `read ... || { 干净退出; }` 兜底。
- **验证范式**：交互式 `read` 循环**无法靠人手测覆盖**，但逻辑可用 `printf '%b' '\x1b[B\n' | bash repro.sh`
  （管道喂按键字节）复现，配合 `rc=$?` 判断每个按键是否让脚本崩溃。改完用同一 harness 回归。
- **相关**：见 `changelog/2026-06-25.md`「deploy-release.sh 交互菜单方向键崩溃修复」。

## 9. dedicated 新增租户：端口/network 预检 + bootstrap 顺序
- **场景**：给 sales-agent 加第二租户 taishankaifa2（dedicated + 共享 PG 库）。用主 `docker-compose.yml`
  `--profile taishankaifa2-split up` 时，主 compose 的 `postgres` 映射宿主 5432（本机被 `app-postgres-1`
  占用）→ compose recreate `sales-agent-db` 端口冲突失败，**db 容器重建后没附到任何 network**
  （`NetworkSettings.Networks={}`）→ 现有 `taishan-api` 连接池全部 stale → `/instance/agent` 500，
  新租户 api 解析 `postgres` 主机名失败、崩溃循环。
- **根因**：①起服务前没预检「主 compose postgres 端口映射」vs「现有运行栈（generated compose 不映射端口）」；
  ②recreate db 容器会丢 network 归属，且依赖它的 api 连接池不自愈。
- **教训**：
  1. 现有栈用 `docker-compose.generated.yml`（postgres **不映射宿主端口**）跑；手写 `docker-compose.yml`
     的 postgres 映射 5432。两者混用前**必须预检端口 + network**：`ss -ltn | grep 5432`、
     `docker network inspect <net>` 看 db 是否在内、`docker inspect <db> ...Networks`。
  2. 规避宿主端口冲突用**叠加文件清空端口**（`postgres.ports: []`）而非改主 compose：
     `docker compose -f docker-compose.yml -f override.yml --profile <p> up -d`。
  3. db 容器一旦被 recreate，**所有依赖它的 api 都要重启**（asyncpg/SQLAlchemy 连接池持有旧容器连接，
     不会自愈）——别只等自愈。
  4. dedicated 新租户 bootstrap 顺序：`POST /tenants` 注册（只写 tenants 表，**不建 Agent**）→
     **重启 api**（启动钩子 `ensure_default_agents` 遍历 tenants 表建默认 Agent）→ 才能访问
     `/instance/agent`。未 bootstrap 时该端点返回 **500**（`AgentNotFoundError` 未映射成 HTTP，非 404），
     别误判为别的故障。
- **验证范式**：`docker inspect <db> --format '{{json .NetworkSettings.Networks}}'` 确认 db 在
  `<project>_default` 且 alias 含 service 名（如 `postgres`）；`docker exec <db> psql ...` 直查
  tenants/agents 表确认数据层按 tenant_id 隔离。
