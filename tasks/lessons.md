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

## 10. 反代网关(Traefik)与应用容器跨 Docker network → 容器名解析失败 → 502
- **场景**：钉钉快捷入口两个 agent 全量 502 Bad Gateway。Traefik access log 显示
  `... 502 ... "http://sales-agent-taishan-api:8000" 2ms`（1~5ms 即 502 = 网关**连不上 upstream**，
  非后端慢）。后端直连 `curl 127.0.0.1:8003` 返回 200，应用本身健康。
- **根因**：在跑的共享网关 `traefik`（compose project `taishanxd`）只挂 `taishan-network`，而
  sales-agent 所有容器只挂 `sales-agent_default`，**两网无交集** → 网关按容器名
  `sales-agent-taishan-api` 解析不到（`docker exec traefik getent hosts <name>` 返回空）。
  `/root/code/traefik/docker-compose.yml` 设计的 `sales-agent-traefik`（挂 `sales-agent_default`）
  根本没在跑——**设计与运行实例不一致**。
- **触发**：57 分钟前 `docker compose up -d` 重建了 api 容器；`docker-compose.generated.yml`
  **未声明任何 `networks:`**，recreate 后容器只落默认 `sales-agent_default`，把此前手动
  `docker network connect` 到 `taishan-network` 的临时挂载丢了 → 网关随即解析失败。
- **教训**：
  1. **手动 `docker network connect` 是一次性的**——只要容器被 recreate（compose up/redeploy）就丢失。
     凡是跨项目共享网络（反代网关 ↔ 后端），**必须在 compose 里声明 `networks:`（external: true）**，
     不能靠手动 connect 常驻。
  2. 排查 502 的第一步：区分「后端挂了」vs「网关连不上」。证据组合 = ①网关日志里 upstream URL +
     极短耗时(2ms)；②`docker exec <网关> getent hosts <后端容器名>` 是否解析；③宿主直连后端端口是否 200。
     直连 200 + 网关解析不到 = 网络/路由问题，不是应用问题。
  3. 多项目共享一台机的反代，**先确认哪个网关容器真正绑了 80/443**（`docker ps` + `docker inspect
     <gw> --format '{{.Config.Labels "com.docker.compose.project"}}'` + 看其 `NetworkSettings.Networks`），
     别假定 `/root/code/<proj>/docker-compose.yml` 里那个就是在跑的那个。
- **持久化（已做）**：把共享网关网络声明进 compose 即可让 recreate 自动保留。
  在 `scripts/render-multitenant-deploy.py` 读 `traefik.shared_network`（opt-in，本机 `deploy/tenants.json`
  设 `"taishan-network"`），非空时给每个 `*-api` 服务加 `networks: [default, <net>]` 并在文件尾声明
  `<net>: external: true`。**关键坑**：给服务显式声明 `networks:` 后它会**退出隐式 default 网络**，
  所以必须同时写 `default`，否则 api 连不上 postgres/neo4j/前端代理。验证持久化用
  `docker compose up -d --force-recreate --no-deps <svc>` 看 recreate 后网络是否自动保留。
  见 changelog 2026-06-25「持久化修复」节。

## 11. `.dockerignore` 裸名匹配是递归的——会误删 src 下的同名正式文件
- **场景**：prod3/test 的 `/integrations/dingtalk/quick` 返回 **500 `{"detail":"H5 template not found"}`**，
  直接 curl 后端也 500；而 dev 机同样请求 200。repo 里 `src/sales_agent/integrations/dingtalk/static/cocah.html`
  明明存在。
- **根因**：`.dockerignore` 里有裸名 `cocah.html`、`coach_mode.png`、`cocah.mp4`（本意是排除仓库根的游离大文件，
  根副本后来被删了）。**BuildKit/.dockerignore 的裸名(无前导 /)是递归匹配**——把 `src/.../static/cocah.html`
  和 `coach_mode.png` 也从构建上下文踢掉 → CI 镜像(`:87836fa`)里没这俩文件 → H5 页 500。dev 机的 `:latest`
  是更早的本地构建、还带文件，所以 200，造成「dev 好但 prod 坏」的错觉。
- **教训**：
  1. `.dockerignore` 要排除「仅仓库根」的文件，**必须用前导 `/` 锚定**（`/cocah.html`），裸名会递归命中任意层级。
     这点和 `.gitignore` 一致，别凭直觉写裸名。
  2. 「repo 有、镜像没有」= 先怀疑构建上下文被 ignore。验证：`docker exec <容器> ls <path>` 对比 repo。
  3. 502/500 先分清层级：网关日志短耗时 + 解析不到容器名 = 网络；后端直连也 500 + JSON detail = 应用/构建。
- **持久化**：已把三个裸名改成 `/`-锚定（changelog 2026-06-25「.dockerignore 修复」节），CI 重建镜像即恢复。

## 12. 共享域名多租户：Traefik 不能按 query 分流，tenant 标识必须进 path
- **场景**：钉钉快捷入口两个租户（taishan / taishankaifa2）共用同一 `DINGTALK_PUBLIC_URL`（`aijiaolian.com.cn`），`tenant_id` 只在 URL query 参数里。`render-multitenant-deploy.py` 为每租户生成的 Traefik 路由 rule（`Host + PathPrefix(/integrations/dingtalk/)`）与 priority（210）**完全相同**。结果 Traefik 无法区分，所有钉钉请求只落到一个 api 容器 → 另一租户 whoami 校验报 **403 Tenant mismatch**（钉钉端 H5 显示「操作失败：Tenant mismatch」，用户复述为「操作失误：tenant mismatch」）。
- **根因**：Traefik（及绝大多数 HTTP 反代）的路由规则只匹配 method/host/path/header，**不能基于 query 参数分流**。多个租户共用 hostname 时，若 path 也完全相同、只靠 query 区分租户，生成的多条路由必然 rule+priority 重复，Traefik 任选其一 → 跨租户串话。
- **教训**：
  1. **共享域名下的多租户入口分流，tenant 标识必须进 path 段**（如 `/integrations/dingtalk/t/{tenant_id}/...`），让每租户的 `PathPrefix` 天然不同；不要用 query 参数区分租户再指望反代分流。
  2. 审查生成的反代配置时，**多条路由的 rule+priority 不能完全相同**——那是未定义行为（Traefik 任选其一），不是「负载均衡」。
  3. 端点若只在「校验阶段」报错而「页面渲染阶段」不校验（如 `/quick` 渲染 HTML 不校验、`/whoami` 才校验），会让现象看起来像「页面能开但点了报错」，别误判为前端 bug——先确认请求是否落到了**正确的后端实例**（看 Traefik access log 命中的 router/upstream）。
- **相关**：见 changelog 2026-06-25「钉钉快捷入口 tenant mismatch」节；与 lessons #10 同为 Traefik 路由层问题（#10 是跨 Docker network 解析不到容器 → 502，本条是 rule 冲突 → 落到错容器 → 403）。
- **已加防御校验**：`render_traefik_routes` 生成配置后断言无重复 `rule:` 行（同 Host+PathPrefix 即 `SystemExit` 拒绝），把冲突挡在部署前。未来任意服务器加租户，只要 tenant_id 唯一（validate_inventory 已保证）就必然安全——这是"多租户也不会复发"的根本保证。

## 13. SSH 远程命令必须用绝对路径，别依赖 `cd`（反复漏 cd 差点误删 /root）
- **场景**：在 prod2 通过 SSH 操作 prod3/杭州仓库时，多次把命令跑在 `/root`（SSH 默认进 `$HOME`）而非 `/root/code/sales-agent`。最严重一次杭州「瘦身」：命令里写了 `mv src .git scripts ... backup/` 但**漏了 `cd`**，实际在 `/root` 执行——幸好 `/root` 下没有 `src/.git/scripts` 等目录（全被 `2>/dev/null` 忽略），只误建了空 `/root/backup`，**没有数据损失**；但若 `/root` 下碰巧有同名文件就会误删。
- **根因**：SSH 默认登录到用户 `$HOME`（root → `/root`），不进仓库。心里想着「先 cd」手上却只写了 `pwd && ...` 或直接 git 命令，反复犯同一 slip（诊断阶段也漏过几次，靠 `pwd=/root` 才发现）。
- **教训**：
  1. SSH 远程命令**一律用绝对路径**（`ls /root/code/sales-agent/...`、`git -C /root/code/sales-agent ...`），不依赖 `cd`——`cd` 最容易漏写；用变量聚合前缀（`ROOT=/root/code/sales-agent; mv $ROOT/src $ROOT/backup/`）也可。
  2. 批量 `mv`/`rm` 前**先确认目录**：命令开头 `echo "$(pwd) $(hostname)"` 或先 `ls <绝对路径>` 看一眼再动。
  3. 危险操作（mv/rm 源码）先 `mv 到 backup/`（可回退），验证 OK 再 `rm`——别一步 `rm -rf`。
- **验证范式**：瘦身后 `ls /root/code/sales-agent/` 确认只剩预期目录；`docker ps` 确认运行容器不受影响（容器用镜像，mv 主机源码不中断服务）。
