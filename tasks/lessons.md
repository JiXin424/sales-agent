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
- **补充（sourceless 部署不适用）**：上面这套 shared_network 只对「deploy-release + traefik 按容器名 upstream」
  的模型有效（dev 机那台就是）。杭州机走 image-deploy（源码镜像部署），traefik 路由用 `host.docker.internal:<宿主端口>`
  （router `qylx-dingtalk`，见 traefik access log 的 upstream 字段），**不依赖容器共享网络**，所以不需要 shared_network
  ——别给 sourceless 目标误设。排查时也注意：distroless 的 traefik 镜像**没有 `getent`**，`docker exec traefik getent hosts`
  返回空不代表解析失败，要看 traefik access log 的 upstream + status 才准。

## 11. CI 镜像缺文件：先查「有没有提交进 git」，再查 .dockerignore
- **场景**：prod3/test 的 `/integrations/dingtalk/t/{id}/quick` 返回 **500 `{"detail":"H5 template not found"}`**，
  直接 curl 后端也 500；dev 机同样请求 200。`src/.../static/cocah.html` 在工作树里明明存在。
- **真正根因**：`cocah.html`、`coach_mode.png` **从未被 git 跟踪**（漏 `git add`）→ CI 的干净 checkout
  (`git checkout FETCH_HEAD`) 里根本没有这俩文件 → 镜像里没有 → H5 500。dev 机正常，是因为 dev 镜像从
  **工作树**（含未跟踪文件）本地构建。`git ls-tree HEAD -- <path>` / `git ls-files <path>` 一查便知（HEAD 里压根没有）。
- **次要隐患（已一并修，但不是主因）**：`.dockerignore` 里的裸名 `cocah.html`/`coach_mode.png`/`cocah.mp4`
  是递归匹配，就算这俩文件被 track 也会被 BuildKit 误删；已改成 `/`-锚定（仅排除仓库根）。**但真正卡住 500 的是漏
  track，不是 .dockerignore**——我最初只改了 .dockerignore 推上去，部署后仍然 500，才回头查出是漏 track。
- **教训**：
  1. 「工作树有、CI 镜像没有」= **先 `git ls-tree HEAD -- <path>` 确认是否 track 了**，再查 `.dockerignore`/构建缓存。
     别一上来就怀疑 .dockerignore（我就这么误判了一圈，改完推了还 500）。
  2. 本地构建能过、CI 不能过，常见差异：本地用工作树（含未跟踪文件），CI 用干净 checkout（只 tracked 文件）。
     任何「本地有、线上没有」的资源文件，先确认它进 git 了。
  3. `.dockerignore` 排除「仅仓库根」的文件仍要用 `/`-锚定（潜在隐患），但优先级低于「先确认 track」。
- **持久化**：补 `git add` 提交 `cocah.html` + `coach_mode.png`（commit 927bc7a），CI 重建即恢复。

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

## 14. `.gitignore` 裸名排除运行时资源 → CI 镜像缺失 → register/页面 500
- **场景**：CI 镜像（杭州/prod3）register 钉钉按钮报 500 `coach_mode.png not found in static/`，教练视频页也缺 `cocah.html`。本机 prod2 正常（本地 build 时工作区有这俩物理文件）。
- **根因**：`.gitignore` 用裸名 `coach_mode.png` / `cocah.html` 排除「仓库根游离副本」，注释假设「正式副本在 src/static/ 下会进镜像」——但 `.gitignore` 裸名是**递归匹配**，连 `src/sales_agent/.../static/coach_mode.png` + `cocah.html` 一起排除 → 不进 git → CI clone 没有 → build 进镜像也没有。与 lessons #11（`.dockerignore` 裸名）同款，但 #11 只修了 `.dockerignore`，`.gitignore` 漏了。
- **教训**：`.gitignore` 裸名同样递归匹配。**运行时必需资源**（register 上传的图标、H5 模板）必须入仓，否则 CI 镜像缺失、本地却正常（本地有物理文件），形成「本地好、CI 坏」的错觉。用 `!` 反例放行 src 下正式副本（同 `secrets/example.env` 处理）。
- **验证范式**：`git ls-files | grep coach_mode.png` 确认入仓；`git check-ignore <path>` 确认不再忽略；CI 重建后 `docker exec <api> ls /app/src/.../static/` 确认镜像含。
- **相关**：lessons #11（`.dockerignore` 同款坑）；本次临时用 `docker cp` 补图标让 register 先跑通，持久化靠 `.gitignore` `!` 反例放行 + push。

## 15. 判断「系统实际跑什么」必须看部署生效的 env_file，不是代码默认值/README/根目录 .env
- **场景**：用户问「系统知识库是什么」。我先看根目录 `.env`（只有 `NEO4J_PASSWORD`，**无** `KNOWLEDGE_ENGINE`）、`config.py` 默认值（`knowledge_engine: str = "legacy_rag"`）、README 描述（把 legacy_rag 当现有、ontology 当「可选替换」），就下结论「RAG 为主、neo4j 可选」。**用户纠正后查证，实际生产用的是 Neo4j 本体引擎**，主次搞反了。
- **根因**：配置有三层，优先级/生效范围完全不同，我没分清就取了最低层当事实：
  1. 代码默认（`config.py: knowledge_engine="legacy_rag"`）— **设计基线，不是当前事实**
  2. 根目录 `.env` — 开发本地残留，可能不全（本项目就只有 `NEO4J_PASSWORD`，不代表部署）
  3. `secrets/<tenant>.env`（被 `docker-compose.generated.yml` 的 `env_file:` 加载）— **生产实际生效的配置**
  部署里 `secrets/taishan.env`、`taishankaifa2.env`、`example.env` 全部 `KNOWLEDGE_ENGINE=ontology_neo4j`，`taishan-api` 服务 `env_file: ./secrets/taishan.env` 覆盖了默认值，`chat_pipeline.py:421` 据此走 ontology 检索分支。
- **教训**：
  1. 回答「系统当前实际用什么」类问题，**先找 `docker-compose*.ygl` 里服务的 `env_file:` 指向哪个文件**，那个文件才是生效配置；再看代码分流点（`grep knowledge_engine` 找 `if` 分支）确认走了哪条路。绝不用 `config.py` 默认值或 README 描述当结论。
  2. README 的「现有 vs 可选」是**写文档时的状态**，可能滞后于部署——本项目 README 仍把 ontology 当「可选替换」，但部署早已全面切换。文档描述 ≠ 运行事实。
  3. 警惕反向混淆线索：`data/agents/*/ontology` 目录基本空 **≠ 没用 ontology**——本体引擎的 Entity/Fact/Evidence 存在 **Neo4j 图库**里，PostgreSQL 只存入库任务+聊天日志。看「数据在哪」要先确认引擎的存储模型。
- **检查范式**：`grep -rn KNOWLEDGE_ENGINE secrets/`（生效配置）→ 看 compose 的 `env_file:` → `grep -rn "knowledge_engine ==" src/`（代码分流）→ 若怀疑没真跑，看 `ingestion_jobs.engine` 实际值或 `/ready` 健康检查的 `knowledge_engine` 字段。
- **相关**：与 lessons #11/#14（「本地有、线上没有」错觉）同源——都是用错的参照系（本地默认 vs 线上生效）得出错误结论。

## 16. 判断「文件的角色 / 是否共用」前，先查 git 跟踪与忽略状态
- **场景**：用户问 `deploy/` 下多个 `tenants.*.json` 是否重复。我直接读各文件内容，发现 `deploy/tenants.json` 同时被本机 prod2 和 CI(prod3) 引用，就推断「一份文件被两机共用 → 仓库分叉 → CI reset 会冲掉 prod3 生产（定时炸弹）」，SSH 连环核实多轮。**最终 `git check-ignore deploy/tenants.json` 一查：它被 `.gitignore:55` 忽略**——是每台机器本地的真实 inventory（不进 git），prod2/prod3 各自独立一份，CI 的 `git reset --hard` 根本不碰它。所谓「共用 / 分叉 / 炸弹」全是建立在错误前提上的乌龙。
- **根因**：直接读文件**内容**推断其角色，跳过了「它是否被 git 跟踪」这个前置事实。gitignored 文件每台机器各自一份，看起来同名但内容不同、互不影响；tracked 文件才是全仓库共享的单一真相。混淆这两类，会把「各机本地配置差异」误诊成「仓库分叉」。
- **教训**：
  1. 分析仓库里任意文件的角色前，**先 `git ls-files <path>`（是否跟踪）+ `git check-ignore -v <path>`（是否被忽略）+ 读 `.gitignore`**，再读内容推断。这三步把「gitignored 本地文件 vs tracked 共享文件」分清，是一切推断的前提——比读文件内容优先。
  2. 警惕「同名的 gitignored 文件在多机各自存在」——那**不是重复**，是设计（本项目 `deploy/tenants.json` 每机一份本地真相）。`.gitignore` 的注释（本项目第 88 行「真实租户配置 deploy/tenants.json 已忽略」）往往直接点明设计意图，**先读注释**。
  3. 与 #11/#14（「先查 git track 再查 .dockerignore」）同源，但本条强调的是**配置/库存文件的角色判定**，不仅是资源文件是否进镜像。
- **检查范式**：`git ls-files deploy/tenants*`（哪些进 git）→ `git check-ignore deploy/tenants.json`（本地这份是否忽略）→ `.gitignore` 注释（设计意图）→ 再看 `deploy-release.sh` 默认 `INVENTORY=deploy/tenants.json`（实际读哪个）。
- **相关**：#11/#14（先查 git track）、#13（SSH 用绝对路径——本次核实 prod3 又反复漏 `cd`，最终靠 `git -C <path>` 绕开）、#15（看生效配置而非默认值）——都是「用对参照系」的同族教训。

## 17. 判断 Gitea Actions「是否真触发」：查 action_run 表 + act_runner 日志，别只信 yml 的 on:push
- **场景**：用户问「push 后 CI 会不会重建镜像」。我读 `.gitea/workflows/deploy.yml` 见 `on: push: branches:[main]`，git log 又有 `87836fa ci: push 到 main 自动触发 deploy`，就判断「会自动触发」。实际 `git push` 后查 Gitea DB `action_run` 表（最新 run 停在老 sha `e067a4f`）+ `journalctl -t act_runner`（日志停 3 天前的 6/23），**最近所有 push 都没触发 CI**——印证 `docs/deploy/cicd-gitea.md`「触发改为手动 `workflow_dispatch`」，yml 里的 `on: push` 是没清理的残留。
- **根因**：CI 是否触发的**真相源是 Gitea 运行时**（DB `action_run` 表 + runner 实际接活日志），**不是** workflow yml 的 `on:` 声明（可能残留 / 被 repo 级禁用 / 改手动）。yml 声明 ≠ 运行事实；`cicd-gitea.md` 文档反而准确，yml 滞后。
- **教训**：
  1. 判断「CI 会否触发 / 是否真跑过」，看**运行时证据**，绝不只读 yml `on:`：
     - Gitea sqlite 最新 run：`docker exec gitea sqlite3 /data/gitea/gitea.db "SELECT id,status,event,substr(commit_sha,1,7) FROM action_run ORDER BY id DESC LIMIT 5;"`（看最新一条的 commit_sha 是不是刚 push 的）；
     - runner 是否接活：`journalctl --no-pager -n 20 -t act_runner`（act_runner 是二进制进程 `/usr/local/bin/act_runner daemon`，非容器，日志走 journald tag `act_runner`，**不是** systemd unit 也**不在** `docker ps`）。
  2. runner 在线 ≠ CI 会触发：本机 `act_runner master-host v0.6.1` 进程在跑、`[actions] ENABLED=true`、runner 已注册，但 push 照样不触发——触发由 workflow 的实际启用方式决定，与 runner 存活无关。
  3. **git push 用的 token ≠ Gitea REST API token**：origin URL 里 `admin:<tok>@47.120.55.219:3002` 这个 token 能 push（git-over-http basic auth），但调 `/api/v1/...`（含 `Authorization: token` 和 basic auth 两种）一律返回 `{"message":"user does not exist [uid: 0]"`——是 git-only 凭据，无 API/actions 权限。要用 API 触发 `workflow_dispatch` 必须另备带 `actions:write` scope 的有效 PAT，否则只能 Web UI（`.../actions` → deploy → Run workflow）手动触发。
- **检查范式**：push 后 → `docker exec gitea sqlite3 ... action_run ORDER BY id DESC LIMIT 3`（新 sha 有没有 run）→ `journalctl -t act_runner -n 20`（runner 接没接活）→ 若无 run = 没触发，查 `cicd-gitea.md` 文档确认实际触发方式（手动/自动）→ 需触发则 Web UI 手动或换有效 PAT 调 dispatch API。
- **相关**：与 #15（判断「系统实际跑什么」看生效配置而非代码默认/文档）同源——都用错了真相源（yml 声明 vs 运行时 DB/日志）。
- ⚠️ **本条结论（「CI 没触发」「on:push 是残留」「运维 UI 禁用 workflow」）已被 #18 推翻**：本条全部是在**错误 Gitea 实例（本机 `172.25.186.209` 开发机）**上查的，而 push 实际到了**主控 `47.120.55.219`（另一台机）**。本条的**方法**（查 `action_run` + `act_runner` 日志）仍正确，但**前提「本机 = 主控」是错的**，必须在正确的机器上用——见 #18。

## 18. 多机环境：确认「当前机器就是目标服务所在机」要用实证，别凭「本机有该容器」归纳——否则级联出全套错结论
- **场景**：用户让「push 后检测 runner」。我 `docker ps` 看到本机有 `gitea/gitea:1.24` 容器 + `ps aux` 看到 `act_runner daemon` 进程，就断定「本机就是主控 Gitea 机（= origin 的 `47.120.55.219`）」，然后一路在本机 `docker exec gitea` 查 DB、`journalctl -t act_runner` 查日志，得出「CI 没触发 / on:push 残留 / 运维 UI 禁用 workflow」一整套结论（写成 #17）。**用户一句「本机不是主控」逼我复盘**，`git ls-remote` 一查才证伪：
  - 本机 `hostname -I` = `172.25.186.209`（cicd-gitea.md 里的 **prod2 开发机**）；
  - 主控 prod3 = `47.120.55.219` / 私网 `172.25.186.210`（**另一台机**）；
  - `git ls-remote 47.120.55.219:3002/.../sales-agent.git HEAD` = `8d112b5`（push 成功到了主控）；而本机 gitea（`127.0.0.1:3002`）连 `admin/sales-agent` 都 `Not found`——本机这个 gitea 是**另一个无关实例**。
  - 于是 #17 那套「CI 没触发」结论**全部作废**（在错实例上查的）。
- **根因**：把「本机有 gitea 容器 + runner 进程」**错误归纳**成「本机就是 origin 指向的、CI 真正跑的主控」。多机环境里 dev 机也常跑自己的 gitea/runner 做本地开发/镜像，「本机有该服务」≠「本机是生产权威实例」。没有用最便宜的实证去**证伪**这个前提，反而顺着它深挖好几轮（甚至写了 lesson）。
- **被忽略的红旗（任一都能秒证伪）**：
  1. origin host = `47.120.55.219`，我查的是本机 `127.0.0.1:3002`——从没验证这俩是不是同一台；
  2. `hostname -I`=`172.25.186.209` vs cicd-gitea.md 主控私网 `172.25.186.210`，**差一位**——读了文档却没对 IP；
  3. 本机 gitea bare repo HEAD 停在 `e067a4f`，而我刚 push `8d112b5`——若是同一实例 HEAD 必更新；这个矛盾我早看到却没用来证伪，反而去解释「为什么没触发」。
- **教训**：
  1. 多机环境（本项目 dev/主控/生产分机）里，动手查任何「服务内部状态」（DB/日志/容器）前，**先证伪「当前机 == 目标服务所在机」**。三个最便宜的验证任一即可：
     - **HEAD 对比**：`git ls-remote <origin-url> HEAD` 的 sha，和「本机该服务持有的最新 sha」对一下——不一致就不是同一实例（本次就是这招秒杀）；
     - **IP 对比**：`hostname -I`（或 `ip addr`）的本机私网 IP，和文档/配置里目标机私网 IP 对一下；
     - **端口/容器归属**：`docker port <容器>` 的 host:port，和访问入口的 host:port 对一下（公网 IP 经 NAT，不能只看公网 IP 是否命中）。
  2. origin/push 的 **host**（`47.120.55.219:3002`）≠ 本机回环（`127.0.0.1:3002`）。查服务状态前先确认「这个 host 是本机吗」：`ip addr | grep <host>` 或 curl 对比。**绝不默认公网 host = 本机**。
  3. 看到「服务在本机跑 + 但状态/数据和预期矛盾」（如 HEAD 没更新、runner 没接活），**第一反应应是「我可能查错实例/机器了」**，而不是顺着错前提深挖「为什么没触发」。矛盾本身是证伪信号，先验证前提。
- **检查范式**：动手查服务内部前 → `git ls-remote <origin> HEAD`（push 目标真实 sha）vs 本机服务持有的 sha → `hostname -I` vs 文档目标机私网 IP → `docker port <容器>` vs 访问 host:port → 三者一致才继续本机直查；不一致则 SSH 到真正的目标机（`ssh root@<主控公网>`，用绝对路径见 #13），别在本机空查。
- **相关**：#15（用对参照系）、#17（CI 触发判断方法对，但本条揭示必须在「正确的机器」上用）同族——都是「先确认参照系正确，再下结论」。本次级联最深：错前提 → 错结论 → 还写了 lesson，用户一句话才打断。**也是「先验证再深挖」的反面教材**——本可用一次 `git ls-remote`（1 秒）替代我前面五六轮 DB/日志深挖。

## 19. `ssh -n` 与「管道传 stdin」互斥：照搬同文件模式前先理解 `-n` 的前提

- **场景**：给 `scripts/ci-fanout.sh` 的 image-deploy 分支加「`tar | ssh` 同步运维脚本到无源码目标机」。同文件其它分支（deploy-release / self-deploy / image-deploy 的 docker run）都用 `ssh -n`，照搬成 `tar ... | ssh -n ... "tar -xf -"`。**本地试跑立即报** `tar: This does not look like a tar archive`，远端 tar 读到空。
- **根因**：`ssh -n` = 「Redirects stdin from /dev/null」，把 ssh 的 stdin 重定向到 /dev/null，**丢弃了管道里 tar 的输出流**，远端 `tar -xf -` 读到空 → 报错。同文件用 `ssh -n` 的真实原因是：那些命令在 `while read ... < /tmp/ci-targets.txt` 循环里，ssh 默认会吞掉循环的输入流，所以要 `-n` 挡住。但 `tar | ssh` 场景下 ssh 的 stdin 已被 tar 管道占用，本就不会读循环输入——`-n` 多此一举且致命。
- **正解**：`tar ... | ssh -o BatchMode=... ...`（**不带 `-n`**），并在注释里标明「此分支唯一不带 -n 的 ssh，stdin 由 tar 管道占用，不会读走 while 循环输入」。已落注释于 `scripts/ci-fanout.sh`。
- **教训**：① 复制既有模式前先理解它的前提（`-n` 是为防吞 while 输入，不是万能默认）；②「本地试跑」再次救场——若直接 push 等 CI 跑，得去 Gitea Actions 日志才看到远端 tar 报错，定位更慢。
- **相关**：#4（验证 Before Done——本次靠本地试跑当场抓 bug）、#15/#16（用对参照系 / 理解前提的同族）。

## 20. 跨层 response 形状契约必须写死；别假设 LangGraph checkpoint 字段名——跑最小 probe dump 真实对象

- **场景**：Graph Debug 时间旅行。① 后端 `list_checkpoints` 最初 `return summaries`（裸 `list[CheckpointSummary]`），但前端类型是 `CheckpointListResponse { checkpoints: [...] }`、消费处 `resp.checkpoints ?? []` → 时间轴恒空。② design.md 假设 `StateSnapshot.metadata.writes` 存在、从中推断节点名；进程内验证才发现 langgraph>=1.2 的 `metadata` 只有 `source`/`step`/`parents`，**没有 writes**，`node` 全 null。
- **根因**：① 后端、前端由两个子代理并行实现，两方各自"按 design.md 做"，但 design 对"列表是否包一层 `{checkpoints:...}`"没写死，形状漂移；任一方单看都"对"，合起来才暴露。② `metadata.writes` 是大量旧示例代码里的字段，当前大版本已不写——照搬记忆里的 API 形状而没实测。
- **教训**：
  1. **跨层 response 形状是契约，必须在 design.md 写死到"裸数组 vs 包对象"级别**，并在验收里加一条"前端实际拿到的字段非 undefined"。子代理并行实现前后端时，形状漂移是最高频 bug。
  2. **第三方框架的字段名/结构别凭记忆，跑一次最小 probe dump 真实对象**。本次 10 行脚本 `async for snap in aget_state_history: print(snap.metadata, snap.tasks, snap.next)` 同时证伪了 writes 假设、定位到 `tasks[*].name` 才是节点名来源。design.md 风险栏虽点名了"字段名跨版本差异"，但只有真跑才落实。
  3. **进程内直调端点函数 + 共享 InMemorySaver** 是绕过 HTTP/Docker/DB 的最快验证法：monkey-patch `get_checkpointer` 返回同一个 `InMemorySaver`，`run` 写、端点读，一秒验证字段映射 + 403 + 形状，无需起 server。
- **检查范式**：涉及前后端新端点 → design.md 明确 response 形状（包对象 vs 裸数组）→ 实现后写进程内 probe（共享 InMemorySaver）dump 真实 `metadata`/`tasks`/`next` → 前端消费处加 `?? []` 兜底前，先确认字段名拼写与后端一致。
- **相关**：#4（验证 Before Done——本次进程内 probe 当场抓两个 bug）、#19 同族（实证优先于假设）。

## 21. LangGraph `astream(stream_mode=[list])` 返回 `tuple[mode,payload]` 不是 dict；"进程内验证端点函数" ≠ "验证了所有代码路径"

- **场景**：A1 的 `/run` 端点 `async for chunk in graph.astream(..., stream_mode=["updates","custom","debug"]): chunk.get("type","")`。`astream` 在 stream_mode 是 **list** 时 yield `tuple[mode, payload]`，`.get` 对 tuple 抛 AttributeError → `/run` 真实运行时**只发 `error` 事件**，从没发 `node_start`/`node_output`/`node_end`/`done`。A1 验证时只跑了 checkpoint history 端点（进程内直调函数），**没跑 SSE 流式路径**，所以没发现。A2 抽 `_run_graph_sse` 时撞到，才修。
- **根因**：① LangGraph 的 `astream` 返回形状依赖 stream_mode 类型——单字符串 yield dict，list yield `tuple[mode,payload]`。照搬旧示例（dict 风格）没核对当前版本。② A1 的"进程内验证"只覆盖了端点的**业务逻辑**（`aget_state_history`），没覆盖 **SSE 流式路径**（`astream` chunk 解包），给了"已验证"的假象。
- **教训**：
  1. **LangGraph 流式：stream_mode 是 list → chunk 是 tuple；是单字符串 → chunk 是 dict**。解包前先 `isinstance(chunk, tuple)` 归一化，别假设一种形状。
  2. **"验证了端点函数" ≠ "验证了所有代码路径"**。SSE 流式（generator yield）、异步迭代、分支逻辑要单独触发。A1 漏了 SSE 路径，因为只直调了 list/state 函数。验证清单要覆盖每条 yield/return 路径。
  3. **真实 HTTP 跑一次是发现这类 bug 的最终手段**；环境受限时（本机无 DB、容器外代码）至少为"流式/异步"路径写专门的进程内 probe（直调 generator 收集 yield），而非只测同步返回。
- **检查范式**：任何 `async for chunk in graph.astream(...)` → 先 `print(type(chunk), chunk)` 确认形状 → 归一化 → 再分支。SSE 端点写进程内 probe：`async for evt in streamer(): collect`，验证 yield 的事件类型符合预期。
- **相关**：#20（langgraph 字段名别假设——同族：writes/tasks/astream-tuple 都是"照搬记忆里的 API 形状没实测"）、#4（验证 Before Done——本次是"验证覆盖不全"的变种）。

## 22. CI fan-out `deploy-release` 跨机部署卡点：tenants.json 跨机污染 / DINGTALK_PUBLIC_URL / 孤儿容器；跨机 rsync 会覆盖 secrets+tenants.json

- **场景**：push origin → CI 构建镜像 → ci-fanout 部署三台。test（image-deploy，自包含 deploy 镜像）成功；prod2 + prod3（deploy-release 脚本）都失败、容器不更新。
- **prod2 三卡点**：① 本机 `deploy/tenants.json` 混入别机 tenant（songbai/taishanyanshi/fuduoduo，env 在本机不存在）→ deploy-release `--yes` 给每 tenant 校验 env 报 missing 中断；② `DINGTALK_ENABLED=true`+`REGISTER_QUICK_ENTRY=true` 但没填 `DINGTALK_PUBLIC_URL` → "Configuration is not ready" 退出（dev 无公网入口就如实填 PUBLIC_URL，或确定不要快捷入口才设 false，别为绕过检查而绕过）；③ 孤儿容器（不在当前 compose project）占 container name → compose create 冲突，需 `docker rm -f` 清掉再 up。
- **prod3 根因（更严重）**：主控 `/root/code/sales-agent` 被 **prod2 全目录 rsync/copy 覆盖**——连 `.git`（remote 变 github）、`tenants.json`（变 prod2 的 taishan/taishankaifa2）、`secrets`（变 prod2 的 + 主控自己的 taishanyanshi/songbai 变 1 行空壳）。主控真实租户配置只剩运行容器的 env 里。重建：`docker inspect <api> --format .Config.Env` 提取完整配置（含密钥）写回 secrets + 从容器 `.Mounts`/端口/domain 反推 tenants.json + 去掉 prod2 的 `taishan-network`（主控 traefik/api 都在 `sales-agent_default`，不需 shared_network）。
- **教训**：
  1. **CI 部署容器没更新 → ssh 目标机手跑 `deploy-release.sh` 看真实报错**（别猜），按三卡点排查：tenants.json 是否混入别机 tenant / DINGTALK_PUBLIC_URL 是否缺 / 是否孤儿容器占名。
  2. **跨机同步代码目录（rsync/scp）必须排除 `secrets/` 和 `deploy/tenants.json`**——这俩是每机本地配置，跨机覆盖会让目标机配置丢失（secrets 变空壳/串成源机的）。`git reset` 不动它们（gitignored），但 rsync 全目录会。
  3. **traefik `shared_network` 每机不同**（prod2 用 `taishan-network`，prod3 用 `sales-agent_default`）——tenants.json 的 traefik 段不能跨机复制；目标机 traefik 和 api 若同在 default network 就不需 shared_network。
  4. **配置丢失但容器在跑 → 从 `docker inspect` 的 `.Config.Env`/`.Mounts` 重建**：运行容器 env 含部署时完整配置（含密钥），Mounts 含宿主 data/logs 路径，是重建 secrets/tenants.json 的最后兜底。
- **检查范式**：CI 部署失败 → ssh 跑 deploy-release.sh 看报错 → 三卡点排查 → 配置丢失从容器 inspect 重建（env 写 secrets + Mounts/ports/domain 写 tenants.json）。
- **相关**：#7（dev 机端口漂移先 curl 实测）、#10（traefik 跨 network 502）、#18（多机先确认参照系）同族——"多机部署，每机配置/网络不同，别假设一致"。

## 23. `.dockerignore` 的 `*.md`（带通配符）只匹配根级、不递归；裸名才递归——别信调研结论，最小 build 实测

- **场景**：eval 全机可用任务，调研子代理断言「`.dockerignore` 的 `*.md` 排除了 `eval/questions.md`，镜像里没题库」，据此计划加 `!eval/questions.md` 反向例外。实现期先做最小 build 实测：**原始 `.dockerignore`（未改）build 出的镜像里 `/app/eval/questions.md` 就在**（11958 字节），运行中的 taishan-api 容器里也有。调研结论错，改动是 no-op，已撤销。
- **根因**：`.dockerignore` 用 Go `filepath.Match` 语义，`*` **不跨 `/`**。故 `*.md` 只匹配根级 `*.md`（如 `README.md`），**不匹配嵌套** `eval/questions.md`。而 **裸名**（无通配、无 `/`，如 `cocah.html`）被 BuildKit **递归匹配**任意深度（见 #11）——这是带通配符模式与裸名的关键区别。
- **教训**：
  1. **判断「某文件是否进 docker 镜像」永远最小 build 实测**（`FROM python:3.10-slim` + `COPY <dir>/ ./<dir>/` + `RUN ls`），别凭 `.dockerignore` 规则凭空推断、也别全信子代理的二手结论。
  2. 改 `.dockerignore` 前先问：这条规则到底匹配根级还是递归？带通配符（`*.md`、`*.log`）= 根级；裸名（`foo.html`）= 递归；带 `/`（`/foo`、`a/b`）= 按路径。
  3. 见 #11（先查 git 跟踪再查 dockerignore）、#14（.gitignore 裸名同样递归）同族。
- **检查范式**：怀疑某文件被 dockerignore 排除 → 写 3 行临时 Dockerfile COPY 该目录 + ls → build → 在/不在一目了然 → 再决定改不改。

## 24. bash `shift` 耗尽参数返回非零，在 `set -e` 下会无声杀死脚本——参数解析循环别让末尾 `shift` 在空参数上跑

- **场景**：`scripts/run-eval.sh` 初版参数解析，`--` 分支用内层 `while` 把剩余参数全耗尽后，回到外层循环末尾的 `shift`——此时 `$#` 已为 0，`shift` 返回 1，`set -e` 立即终止脚本（且发生在所有 echo 之前），表现为「运行无任何输出就退出」。静态 `bash -n` + `--help`（不走 `--`）全过，没暴露；直到真实 `--` 调用才炸。
- **根因**：`shift` 无参数可移除时退出码非零；`set -e` 对此不豁免。循环 `while [ $# -gt 0 ]; do ...; shift; done` 里，若 case 体已自行 `shift`/`break` 漏掉对外层 `shift` 的保护，末尾 `shift` 就可能在空参数上失败。
- **教训**：
  1. **`--` 分隔符用 `shift; break` 模式**，别用内层 while 耗尽——break 后用 `$@` 取剩余，外层 `shift` 被 break 跳过，永不踩空。
  2. 或末尾 `shift` 加保护：`(( $# )) && shift` / `shift || true`。
  3. **`set -e` 脚本必须做一次「真实多分支调用」冒烟**（不只是 `bash -n` + `--help`），尤其走 `--` / 可选 flag 的路径——静态检查发现不了运行期 set -e 退出。
  4. 见 #8（`((n++))` 在 set -e 下旧值 0 杀脚本）、#19（`ssh -n` 与 stdin 管道互斥）同族——bash 隐式失败 + set -e 是反复踩的坑。
- **检查范式**：写带 `set -euo pipefail` 的参数解析 → `--` 用 `shift; break` → 每个分支（含 `--`）都实跑一次冒烟。

## 25. 第三方「追踪/观测」装饰器（deepeval `@observe`、各类 telemetry）在生产没配 key 时仍是纯负债——会算一堆 trace 再丢弃，且其序列化路径随时可能炸整个请求；上线前必须「无 key 也能安全 no-op」或直接移除

- **场景**：全站 `/agent/chat` 500（prod2/prod3/test 所有租户），`RuntimeError: dictionary changed size during iteration`。排查发现根因是 `ChatPipeline.execute()` 上的 `@observe(type="agent")`（deepeval 追踪）：函数退出时 `Observer.__exit__` 序列化子 span 的 input，deepeval `_serialize` 迭代某嵌套对象的**活 `__dict__`**，序列化触发惰性字段写回同一 dict → 迭代中改大小 → 崩。聊天本身算完了，是装饰器收尾炸。整条链还散布 5 处 `@observe`（agent/tool/llm/retriever×2），潜伏自 `bb2b1eb`，某次对象形态变化后触发。
- **关键认知**：prod **没设 `DEEPEVAL_*/Confident` key**，日志明写「Skipping trace posting」——trace 算了**全丢弃**。即这个装饰器在生产**零收益、纯风险**，却把每个请求搞 500。
- **教训**：
  1. **追踪/观测类装饰器上线前问一句：生产环境（无 key / 未启用）下它是 no-op 还是仍跑副作用？** 仍跑 = 负债。deepeval `@observe` 即使没 key 也照常建 span + 序列化，只是不 POST。
  2. **第三方序列化路径（`make_json_serializable` / `vars(obj).items()` 这类「遍历活对象内部」）是定时炸弹**——你控不了用户对象何时新增惰性字段。能不用就不用；用了要能整体关掉。
  3. **「容器在跑、/health 200」≠「业务通」**：这个 500 在 catch-all 里被包成 JSON 返回，traceback 还因日志配置没进 stdout（写文件/被吞），`docker logs` 完全看不到——**生产对 500 几乎零可见性**。健康检查必须覆盖真实业务路径（`/agent/chat` 冒烟），不能只 `/ready`。
  4. **根因定位别只看自己的代码**：堆栈全在 `site-packages/deepeval/`，但触发点是自己的 `@observe` 装饰器。装饰器/中间件引入的第三方调用栈，要一路看到底。
- **检查范式**：线上 500 但日志干净 → 怀疑被 catch-all 吞 + 日志没进 stdout → 直接 `curl` 复现拿 response.detail → 按 detail 串（如 "dictionary changed size"）反查装饰器/中间件序列化路径 → 无收益的观测装饰器直接移除。
- **相关**：#15（看部署生效的 env_file 不是代码默认）、#20（跨层契约写死、跑 probe dump 真实对象）同族——「第三方/隐式序列化对不可控对象的处理」是反复踩的坑。

## 26. `render-multitenant-deploy.py` 有副作用：默认会写 traefik 动态配置文件（`traefik.dynamic_output`）。本机 render「非本机 inventory」会**覆盖本机正在用的 traefik 路由** → 域名 502。本地 render 非本机 inventory 必须加 `--traefik-out /dev/null`

- **场景**：C2 准备阶段，子代理在本机（prod2）本地验证 `render ... deploy/tenants.prod3.json`（prod3 的 songbai/taishanyanshi 路由），没加 `--traefik-out /dev/null`。render 默认按 inventory 的 `traefik.dynamic_output` 写文件 → 把 prod2 的 `/root/code/traefik/dynamic.d/generated-sales-agent.yml`（prod2 的 taishan/taishankaifa2 路由）**覆盖成 prod3 的路由**（指向 prod2 上不存在的 `sales-agent-songbai-frontend` 等后端）→ prod2 的 taishan/taishankaifa2 域名经 traefik 会 502 约 2-3 分钟。已用 prod2 tenants.json 重渲染恢复。
- **根因**：`render-multitenant-deploy.py` 不是纯函数——除了写 compose，还按 `traefik.dynamic_output`（默认 `/root/code/traefik/dynamic.d/generated-sales-agent.yml`）写 traefik 动态配置。而 prod2 的 traefik 容器挂了 `/root/code/traefik → /etc/traefik` 且 `providers.file.directory: /etc/traefik/dynamic.d, watch: true`——**这个文件是活的、被监听的**，一改 traefik 立即重载。
- **教训**：
  1. **本机 render 任何「非本机 inventory」一律 `--traefik-out /dev/null`**（CI 的 deploy.yml 早就这么做了，本地手动 render 也必须）。同理 `--compose-out` 也别写到正在用的 compose 路径。
  2. **render/生成器脚本要当「有副作用」对待**：先 grep 它写哪些文件（不止你传的 --compose-out），别假设它是纯生成。
  3. **traefik `watch: true` + 动态目录 = 改文件即生效**：往那个目录写东西要极其小心，写错立刻影响线上域名。
- **检查范式**：要在本机渲染别的环境 inventory → 命令必带 `--traefik-out /dev/null --compose-out /tmp/xxx.yml`；事后 `stat` 一下 `/root/code/traefik/dynamic.d/generated-sales-agent.yml` 的 mtime 确认没被自己改到。
- **相关**：#22（跨机 rsync 覆盖 secrets/tenants.json）、#10（traefik 跨 network 502）同族——「每机的 traefik/网络/配置是本地的，跨机操作别覆盖」。
