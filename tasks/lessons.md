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

## 27. 检索子图自己出答案（`skip_generation=True`）会绕过主生成节点 → 租户定制 prompt 全失效。检索层只产证据，生成层只走一条主管道

- **场景**：用户问"福多多的竞品有哪些"，机器人直接端出 ontology 图谱原始结果（"推断聚优和东福是竞品"），没有 markdown 格式、没有销售话术、没结合 RAG。排查发现 graph 新路径（钉钉 Stream）走 ontology 时，`_retrieve_via_ontology` 调一个独立子图，子图用一道极简 prompt（`_ONTOLOGY_RESPONSE_PROMPT`，只说"基于图谱事实回答"）自己生成答案，然后设 `skip_generation=True`。而 `skip_generation=True` 让主 `generate_node` 整个跳过——**PromptRegistry（Agent 绑定→tenant active→内置默认三级回退的 system/task prompt）只在 `generate_node` 生效**。于是所有租户定制的"markdown 格式/销售口吻/综合 RAG"指令对 ontology 路径全部失效。
- **根因**：架构上把"检索"和"生成"塞进同一个子图，子图又用旁路标志（`skip_generation`）短路主生成节点。任何一个绕过主 `generate_node` 的"自己出答案"路径，都会让集中式的 prompt 管理形同虚设。HTTP 老路径（`chat_pipeline.py`）本就把 ontology 纳入主 agent 生成，是对的；graph 新路径搞了个并行实现且短路了，是错的。
- **教训**：
  1. **检索层只产证据（context/source text），生成层只走一条主管道（`generate_node` → `execute_agent` → PromptRegistry）**。任何"检索后自己出答案并跳过主生成"的旁路都会让 prompt 管理失效——这是 prompt 体系的核心不变量。
  2. **`skip_generation` 这类短路标志要审慎**：它只该用于"确实不需要生成"的场景（如 evidence gate 判定 required 知识缺失而 block），绝不该被检索路径用来"我已经出过答案了"。
  3. **新路径（graph）和老路径（chat_pipeline）必须行为对齐**：老路径怎么把 ontology 纳入生成、怎么传 `ontology_context`，新路径照搬，别另起炉灶搞出一套不一致的旁路。
  4. **排查"prompt 没生效"先查调用链终点**：`generate_node` 是否被 `skip_generation`/early-return 跳过？`resolve_execution_prompts` 是否真的进了 LLM 的 messages？别只盯 prompt 文本本身。
- **检查范式**：用户反馈某类查询输出"像没经过定制 prompt" → 沿路径 grep `skip_generation`/`answer_dict.*precompute`/early-return → 看是否某检索/旁路分支短路了 `generate_node` → 改成"只产证据、回流主生成"。
- **相关**：#1（`str.format` 占位符没注入，prompt 体系失效同族——都是"prompt 没真正进 LLM"）。

## 28. `init_db` 里 `create_all` 跑在 `alembic upgrade` 之前 → migration 同时含 `create_table`+`add_column` 时必漂移：create_all 抢建新表 → upgrade 撞 DuplicateTableError → stamp head 兜底 → 跳过 add_column（幽灵漂移：版本号=head 但列缺失）

- **场景**：prod3（image-deploy 生产）钉钉 stream 容器虽 `Up`，但每条消息都报 `InFailedSQLTransactionError: current transaction is aborted`，graph 新路径全废；同镜像（`eb889df`）在 prod2（开发机）正常。排查：prod3 `conversation_messages` 缺 `topic_id` 列（migration `0011` 加的），但 `alembic_version` 已是 `0011_topic_memory`——版本号在撒谎。`retrieval_profiles`/`eval_*`/`document_chunks` 也同源漂移。
- **根因**：`init_db()` 顺序是 `Base.metadata.create_all()` → `_run_auto_migrations()`（upgrade head）。`0011` 同时 `create_table conversation_topics`（新表）+ `add_column conversation_messages.topic_id`（老表加列）。`create_all` 因 ORM model 已有 `ConversationTopic` 抢先建了 `conversation_topics` → upgrade 跑到 0011 的 `create_table` 撞 `DuplicateTableError` → 兜底 `stamp head` 把版本号标到 0011，**跳过 0011 里剩余的 `add_column topic_id`**。列永远没加，但版本号说加过了。prod2 列在（库历史不同），prod3 被 bug 漂移。
- **教训**：
  1. **`create_all` 和 `alembic upgrade` 不是顺序无关的**：当 migration 同时含建表+加列、且要建的表已在 ORM model 里，`create_all` 会抢建，让 upgrade 撞 `DuplicateTableError`。正确顺序是 **先 upgrade（create_all 还没预建，create_table 不撞）→ 后 create_all（幂等补 model-only 表）**。本次修复即调换此顺序。
  2. **`upgrade 失败 → stamp head` 兜底是「谎言式容错」**：它把 alembic_version 标到 head，掩盖了失败 revision 的 add_column 没执行。「版本号到位、schema 没到位」的幽灵漂移极难发现——要等业务查询缺列 crash（且报的是次生 `InFailedSQLTransactionError`，不是原始 `UndefinedColumn`）。兜底必须打全异常 + 明确警告「add_column 被跳过、必须 backfill」。
  3. **`InFailedSQLTransactionError: current transaction is aborted` 永远是次生错误**：它只说明事务里**更早**的某条 SQL 失败了。真正的根因（`UndefinedColumn` 等）在它前面的日志里。别只盯这条，往前翻第一条 ERROR。
  4. **同镜像在不同环境行为不同 → 先怀疑环境状态（DB schema/secrets/env），不是代码**：prod2/prod3 同 `eb889df`，差异在 DB schema 漂移。**`alembic_version` 相同 ≠ schema 相同**——必须查实际列（`information_schema.columns` 跨环境 diff）。
  5. **CI 部署 DB 不能只靠运行时 `init_db` 自动迁移**：漂移只能靠业务 crash 暴露（本次漂移了 4 个 commit 才因 stream crash 被发现）。CI 应加「部署后 schema 一致性校验」（对比 alembic_version 与关键列是否存在）+ migration 预演（临时 pgvector 库 dry-run upgrade），把漂移拦在部署阶段。新 ORM 表必须同一 PR 配 `create_table` migration（model 先进、migration 后补 = 埋漂移）。
- **检查范式**：某环境 stream/api 报 `InFailedSQLTransactionError` 而其它环境正常 → 查该环境 DB：`SELECT column_name FROM information_schema.columns WHERE table_name='X'` 与正常环境 diff → 缺列则查 `alembic_version`（若版本号=正常环境但列缺 = 幽灵漂移）→ 写幂等 backfill migration（`ADD COLUMN IF NOT EXISTS`，**不要含 create_table**，否则又会被 stamp 跳过）补齐 → 同时修 `init_db` 顺序防前向再发。事务 dry-run（`BEGIN; <migration SQL>; ROLLBACK;`）可无风险验证 SQL 兼容性。
- **相关**：#25（生产 500 但日志干净的「不可见故障」同族——都是兜底/装饰器吞掉真相）、#26（render 副作用跨环境覆盖——都是「环境状态被隐式改写」）、#27（graph 新路径漂移——本次根因正是 0011 引入 topic 时的 schema 没跟上 graph 代码）。

## 29. CI fan-out 脚本用 `cmd || echo "⚠️ 继续下一台"` 容错会吞掉子步骤 exit code——schema 校验失败时 CI job 仍 success，只有日志 ❌，「漂移即 CI 失败」承诺失效
- **现象**：本次给 `deploy-remote.sh` 加了 schema 校验（失败 exit 1），本意是「漂移即部署失败、CI 红」。但 `scripts/ci-fanout.sh` 每个部署分支都是 `ssh ... "docker run ... deploy-remote.sh" || echo "⚠️ [$name] 失败，继续下一台" >&2`——`cmd || echo` 在 cmd 失败时跑 echo（返回 0），整个表达式返回 0，`while` 循环继续下一台，脚本末尾隐式 exit 0。结果：deploy-remote.sh exit 1 被 `|| echo` 吞掉，**CI job 仍 success**，只有 ssh stdout 里的 ❌ 日志（用户得主动翻 Gitea Actions 日志才看到）。
- **根因**：`|| echo` 是「fan-out 容错」设计（一台挂不阻塞其它台），但它把「子步骤失败」和「整个 job 失败」解耦了——对「尽力部署、一台失败不影响其它」合理，但对「schema 校验必须通过否则是严重 bug」这类硬约束，静默 success 比 fail 更危险（部署挂了 CI 绿，和 #25「生产 500 但日志干净」同族）。
- **教训**：
  1. **`cmd || echo` 吞 exit code**：在 `set -e` 下 `||` 会抑制 cmd 的非零退出，echo 返回 0。fan-out 脚本若要「继续下一台但最终 job fail」，必须显式收集失败：`cmd || { echo "⚠️"; FAILED=1; }` ... 循环末尾 `[ "${FAILED:-0}" = 1 ] && exit 1`。别让容错变成静默。
  2. **「校验失败即 CI fail」需要端到端 exit code 传播**：校验脚本 exit 1 → deploy-remote.sh exit 1 → ci-fanout 必须 exit 1 → Gitea Actions job fail。中间任何一环用 `|| echo` / `|| true` 兜底都会断链。加校验时必须同时审 fan-out 脚本的失败传播。
  3. **CI「静默 success」是反模式**：部署类 CI 的容错应「继续执行 + 末尾汇总失败」，而非「逐台吞错」。前者用户能在 job status 看到 red；后者要翻日志才发现。
- **检查范式**：给部署链路加硬约束校验（schema / 健康检查 / 冒烟）后，必须 grep fan-out 脚本里调它的那行有没有 `|| echo` / `|| true` / `|| :`——有就说明失败被吞，校验形同摆设（job 永远绿）。修法：收集 FAILED 标志末尾 exit 1。
- **相关**：#25（生产 500 但日志干净——兜底/装饰器吞真相同族）、#28（init_db stamp head 兜底掩盖 add_column 跳过——「容错变成谎言」同族，本次 schema 校验正是为拦 #28 的幽灵漂移）、#24（bash 隐式失败 + set -e 同族——bash 失败语义反直觉）。

## 30. DB 版 prompt 含字面花括号（`{群聊/私聊}`）→ `str.format` 当未知占位符抛 `KeyError`，LLM 路由每次必崩；用 `format_map` + `__missing__` SafeDict 兼容三类花括号。**+ Edit 被 hook 静默 revert，必须 `git diff` 验证而非 Read**
- **场景**：eval 跑 10 道知识库题，`_llm_route` 用 `(router_prompt).format(message=message)` 渲染 DB 版 task_router prompt，每次抛 `KeyError: '群聊/私聊'` → LLM 路由崩溃 → 回退规则路由 → 知识题（不含规则关键词的）误判 `general_sales_coaching`、ontology 检索永不触发。
- **根因**：DB active 版 task_router prompt 含**三类花括号**：① 真占位符 `{message}`；② JSON escape `{{...}}`（`.format` 还原为 `{...}`）；③ **作者漏 escape 的字面花括号** `{群聊/私聊}`/`{已有人@你/无人@你}`/`{发送者姓名}`。`.format(message=...)` 把第 ③ 类当未知占位符 → `KeyError`。prompt 经 `PromptRegistry` 从 DB 取回（非 Python 常量），故 `.format` 安全性依赖 DB 数据格式——DB 数据没人 review escape，必炸。
- **教训**：
  1. **`str.format` 不适合渲染「外部数据源（DB/配置）的模板」**：模板作者无法保证所有字面花括号都 escape 成 `{{}}`。改用 `format_map` + `__missing__` 返回 `"{" + key + "}"` 的 SafeDict——未知占位符原样保留，三类花括号全正确：真占位符替换、`{{}}` escape 还原、字面 `{key}` 保留。不改 DB 数据、不破坏 escape 语义。
  2. **`.replace("{message}", msg)` 是错解**：它跳过 `.format`，导致 `{{...}}` escape 不被还原、原样发给 LLM → LLM 模仿输出双花括号 JSON → 下游解析又炸（本次第二层 `JSONDecodeError`）。format_map 才是正解：既替换真占位符、又还原 escape、又保留字面花括号。
  3. **Edit 可能被 hook 静默 revert**：本次首次 Edit `task_router.py`（`.format`→`.replace`）被某 hook 静默撤销，`Read` 显示的是缓存/旧状态看不出问题，只有 `git diff` 才权威显示「改动没在」。**验证改动落盘一律用 `git diff`，不要用 Read**——Read 可能给 stale 视图。
  4. **`PromptRegistry` 解析后的 prompt 仍是「待 format 的模板」**：调用方拿到 `router_prompt` 后自己 `.format`/`.format_map` 注入变量。这意味着 prompt 的 escape 规范（`{{}}`）和调用方的渲染方式（`.format` 系）必须配套——DB prompt 改了 escape 但调用方用 `.replace`，或反过来，都会出问题。规范：DB prompt 一律按 `.format` 语义 escape（字面花括号写 `{{}}`），调用方一律用 `format_map+SafeDict`（双保险：即便作者漏 escape 也不炸）。
- **检查范式**：LLM 路由/任何 `prompt.format(...)` 报 `KeyError: '<中文/非占位符词>'` → 该词是 DB prompt 里的字面花括号（作者漏 escape）→ 改 `.format` 为 `format_map(_KeepMissingDict(...))`，`__missing__` 返回 `"{" + key + "}"`。验证改动用 `git diff`。配套：prompt 里 JSON 示例的 `{{}}` 确认被还原为 `{}`（不会被 `.replace` 破坏）。
- **相关**：#1（`str.format` 占位符没注入 prompt 失效同族——都是「prompt 没真正按预期进 LLM」）、#27（检索旁路绕过主生成——同属「eval/老路径行为偏离预期」）、#31（eval 路径 ≠ 生产路径——本 bug 只影响 eval/cli，生产 stream 纯规则路由不炸）。

## 31. eval（ChatPipeline 老路径，含 LLM 路由兜底）≠ 生产（graph `route_task_rules_only` 纯规则）；DB prompt schema 与代码 parser schema 会独立漂移；嵌套 JSON 不能用 `re.search(r"\{[^}]+\}")`
- **场景**：修完 #30 的 `KeyError` 后，`_llm_route` 又报 `JSONDecodeError` 7 次——DB active prompt 输出 **intent schema**（`intent`/`intent_reason`/`channel_queries` 嵌套结构），但 `_llm_route` 解析代码期望 **task_type schema**（`task_type`/`confidence`/`needs_retrieval`）。两者脱节 + 嵌套 JSON 让 `re.search(r"\{[^}]+\}")` 在第一个 `}` 处截断 → 非法 JSON → 解析失败 → 仍回退规则路由。且发现：**生产钉钉 stream 走 graph `route_task_rules_only`（纯规则，从不调 `_llm_route`）**，所以 #30/#31 的所有 bug **只影响 eval/cli，生产零影响**——eval 评估的根本不是生产真实路由行为。
- **根因**：① DB prompt（`prompt_versions` 表，可被网页端编辑、独立版本化）和代码 parser（`_llm_route`）是两个独立演进面，没有契约校验——prompt 改了输出 schema，代码不知道；代码改了期望 schema，DB prompt 不更新。② `re.search(r"\{[^}]+\}")` 只匹配「第一个 `{` 到第一个 `}`」、不支持嵌套，对 `{"a": {"b": 1}}` 这类必然截断。③ 系统有两条路由路径（graph 纯规则 vs ChatPipeline 规则+LLM 兜底），eval 跑的是后者，生产跑的是前者——eval 指标不反映生产路由。
- **教训**：
  1. **prompt 在 DB 版本管理 → prompt schema 与 parser 代码必须同步契约**：要么加启动期校验（解析 prompt 里的 JSON 示例确认字段名与代码期望一致），要么 parser 对 schema 容错（同时认 `intent` 和 `task_type`、缺字段给默认）。本次走容错路线：`_INTENT_TO_TASK` 映射 + 兼容旧 task_type + `_extract_first_json` 平衡花括号。
  2. **提取嵌套 JSON 不能用 `re.search(r"\{[^}]+\}")`**：它不支持嵌套花括号，遇到 `{"a":{"b":1}}` 截断在第一个 `}`。必须按花括号深度配对扫描（depth 计数 + 字符串字面量内跳过花括号），见 `_extract_first_json`。这是 JSON-from-LLM 提取的通用陷阱。
  3. **eval 路径 ≠ 生产路径 → eval 指标不代表生产行为**：本项目 eval 跑 ChatPipeline（规则+LLM 路由），生产 stream 跑 graph `route_task_rules_only`（纯规则）。eval 里 LLM 路由的 bug 在生产不存在（生产不调 LLM 路由），反之生产纯规则路由的边界 eval 也测不到。**用 eval 验证生产前，先确认 eval 跑的是哪条路径**（grep eval 脚本调 `ChatPipeline` 还是 `graph`），别假设一致。
  4. **「修了 eval 的 bug」不等于「修了生产的 bug」**：本次 #30/#31 修的是 eval/cli 路径，生产 stream 不受影响（也不受益）。若要让生产也用 LLM 路由兜底，需在 graph `routing.py` 接 `_llm_route`——那是另一个决策，别混为一谈。
- **检查范式**：`_llm_route`/任何 LLM JSON 解析报 `JSONDecodeError` → 先看 LLM 实际输出的 schema（`logger.debug(response)`）vs 代码 `data.get(...)` 的 key 是否对得上 → 对不上 = prompt 与 parser schema 漂移 → parser 容错（认多 schema + 默认值）+ 平衡花括号提取。eval 报路由 bug → 先 grep eval 入口调 ChatPipeline 还是 graph，确认是哪条路径的 bug，别误判为生产故障。
- **相关**：#30（同一次 eval 排查的前一层——字面花括号 KeyError）、#27（检索旁路绕过主生成——同属「eval/老路径 ChatPipeline 行为偏离 graph 新路径」）、#4（验证永远优先走生产入口——本项目生产入口是钉钉 stream 非 HTTP `/agent/chat`，eval 跑的是 ChatPipeline 老路径，恰是 #4 警告的那条非生产路径）。

---

## 32. ci-fanout 部署 prod2（开发机=本机）会 `git stash` + `git reset --hard origin/main` 本机工作区——push 后本机 tracked 改动会「消失」进 stash
- **场景**：commit + push 后监控三台部署，发现本机（prod2=172.25.186.209）工作区的 `task_router.py`（别人并行未提交工作）突然从 `git status` 消失，只剩 untracked 文件。`git reflog` 显示 `reset: moving to origin/main`，`git stash list` 多了一条 `WIP on main: <刚push的sha>`。本机是 `deploy/deploy-targets.json` 里 `method=deploy-release` 的 target。
- **根因**：`scripts/ci-fanout.sh` 对 prod2 的部署命令序列是 `ssh root@172.25.186.209 "git stash 2>/dev/null; git fetch origin main && git reset --hard origin/main && echo '[fanout] git synced to' ...; REGISTRY_IMAGE=... deploy-release.sh --yes"`。即**先 stash 本机 tracked 改动 → hard reset 到 origin/main → 再跑 deploy-release.sh**。目的是让 prod2 源码与刚 push 的镜像 SHA 严格对齐（deploy-release 在源码树上 render compose）。副作用：本机任何未提交的 tracked 改动被 stash 走、工作区被强制对齐 origin/main。untracked 文件（`docs/`、`uv.lock`）不受 `git stash` 默认行为影响，仍留在工作区。
- **教训**：
  1. **prod2（172.25.186.209，开发机）是 CI deploy-release target，本机工作区会被 ci-fanout 每次 push 后 stash+reset**。在 prod2 本机做未提交工作时，要么先 commit/branch，要么预期它会被 stash。别把本机工作区当稳定工作面。
  2. **本机工作区 tracked 改动「消失」→ 先查 `git stash list` + `git reflog`，别慌重做**：ci-fanout 的 stash 消息形如 `WIP on main: <刚push的sha>`，reflog 有 `reset: moving to origin/main`。改动在 stash 里没丢，`git stash pop stash@{N}` 即可恢复。
  3. **区分「CI 的 stash」vs「自己/别人的 stash」**：CI stash 的 base sha = 刚 push 的 commit，且通常在 CI 跑完时是 stash list 顶部（stash@{0}）。`git stash show --stat stash@{N}` 看文件内容判断归属，别误 pop 别人的工作。本次 CI stash 只含 `task_router.py`（+91/-19，是 #30/#31 的 eval 修复），识别后安全 pop 恢复。
  4. **恢复时机**：等 ci-fanout 全部跑完（三台容器 tag 都更新到新 SHA、prod3 上无 `ci-fanout.sh` 进程）再 pop stash，避免与 CI 的 git 操作竞态。
- **检查范式**：push 后监控部署时发现本机 `git status` 的 tracked modified 消失 → `git stash list` 找 `WIP on main: <刚push的sha>` → `git stash show --stat stash@{0}` 确认文件归属 → 等 CI 结束（`ssh prod3 'ps aux|grep ci-fanout'` 为空 + 三台 tag 更新）→ `git stash pop stash@{0}` 恢复。
- **相关**：#4（验证走生产入口，本机即 prod2，`docker logs <tenant>-stream` 在本机直接看）、`scripts/ci-fanout.sh`（deploy-release target 的 stash+reset 序列）、`deploy/deploy-targets.json`（三台 target 清单：prod3 image-deploy / prod2 deploy-release / test image-deploy）。

---

## 33. 改动 mermaid 输出（后端装饰 class/classDef / shape / 语法）后，用 mermaid 官方 CLI `mmdc` + 系统 chrome headless 真渲染验证——别只靠"语法看着对"
- **场景**：graph_debug 给 online 图 mermaid 追加 `class guided_flow,chat subgraphNode` + `classDef subgraphNode fill:#fff3e0,stroke:#ff9800,stroke-width:3px,color:#e65100` 让子图入口节点橙色加粗高亮。前端用 `mermaid@^11.16.0` 的 `mermaid.render()` 渲染。想在 node 环境直接 `mermaid.parse(text)` 预验证，但 mermaid 11 是 pure-ESM + 依赖 DOMPurify，在无 DOM 的 node 里报 `DOMPurify.sanitize is not a function`（三个图含未改动的同报错 → 证明是环境问题不是语法问题），`jsdom` + 动态 import 各种绕都失败。
- **手法**：系统已装 `google-chrome`。临时目录 `/tmp/mmdcheck` 里 `PUPPETEER_SKIP_DOWNLOAD=1 npm i mermaid@<前端同版本> @mermaid-js/mermaid-cli@<同版本> --no-save`，写 puppeteer config `{ "executablePath": "/usr/bin/google-chrome", "args": ["--no-sandbox","--disable-gpu"] }`，`mmdc -i graph.mmd -o out.svg --puppeteerConfigFile pptr.json`。**exit=0 = 语法合法 + 前端同款引擎必能渲染**；`grep -oE '(fill|stroke)[: ]*#颜色' out.svg` 确认 class 真生效（不只解析通过）。前端 `mermaid.render` 失败会 catch 退化成 `<pre>` 纯文本（见 `GraphDebugPage.tsx:154`），光看前端不报错不够，要确认样式真应用。
- **教训**：① 后端改 mermaid 文本（classDef/class/shape/任意语法）→ 部署前用 mmdc 真渲染验证，别靠"语法和 LangGraph 自带 classDef 同款所以肯定行"的论证。② mermaid 11 在纯 node 验证走不通（DOMPurify 依赖 DOM），mmdc（puppeteer+系统 chrome）是最权威本地预验证，比装 jsdom 折腾 DOMPurify 靠谱。③ 验证 class 真生效要 grep 渲染出的 svg 里的 fill/stroke 颜色，不只看 parse/render 不报错。④ 子图节点识别：LangGraph `add_node(name, compiled)` 的节点 `isinstance(nd.data, CompiledStateGraph)` 为真；但被 wrapper 函数包一层（如 `chat_node()` 内部调子图）的节点 LangGraph 看不出是子图，要靠节点 id 归一化后命中 `GRAPH_REGISTRY` 补识别——双信号才不漏。
- **相关**：#4（验证优先走生产入口——mmdc 是部署前本地预验证，部署后还要 curl 端点 + `docker logs <tenant>-stream`）、`api/routes/graph_debug.py` `_decorate_mermaid`/`_identify_subgraph_nodes`。

## 34. LangGraph 节点 `add_node(tags=...)` 不从 `compiled.get_graph().nodes` 暴露——识别 LLM/特殊节点要用集中映射表，别指望 tag 运行时读回；节点 `def`→`async def` 改签名要全局 grep 调用方测试同步改
- **场景**：需求是图调试区分纯函数节点和 LLM 节点。想用 LangGraph `add_node("route_task", routing_node, tags=["llm"])` 标记（已有 `tags=[TAG_HIDDEN]` 先例），再在 `graph_debug` 从 `compiled.get_graph().nodes` 读 tag 识别 LLM 节点。探针 `dir(nd)` + `nd.metadata` 实测：node 的 `metadata` 恒为 None，`RunnableCallable` 无 tags 属性——**tags 注册后运行时读不回**。改用集中映射表 `graph/node_metadata.py` 声明 22 节点的类型/是否 LLM/对应 prompt，一举解决「区分节点」+「prompt 标注」两个需求。
- **教训**：
  1. **LangGraph tags 是给 tracing/回调用的，不进 graph 结构**——`get_graph().nodes[*].data` 是 `RunnableCallable`，`metadata` 恒 None。想在 graph_debug 这类「读图结构」的场景识别节点特性，用集中映射表（单一事实源），别用 tag。映射表还顺带提供节点→prompt 对应、节点描述，比 tag 信息量大。
  2. **节点 `def`→`async def`（加 `runtime: Runtime`）改签名后，所有同步调用方测试都要改**：grep `routing_node\|risk_check_node` 找调用点，测试里 `result = node(state)` → `result = await node(state, mock_runtime)` + `@pytest.mark.asyncio` + mock_runtime fixture（`runtime.context = {"chat_model": None, "db": None}`，flag 默认 false 走原路径）。本次改了 4 个测试文件（test_routing_node/test_risk_node/test_topic_memory_flow/test_graph_debug）。**别只跑当前目录测试就以为完事**——全量 `pytest tests/unit/graph` 才发现 test_routing_node 漏改。
  3. **「LLM 失败静默放行」是风控致命默认值**：`check_llm_risk` 失败兜底返回 `RiskCheckResult()`（action=allow）。图节点接入 LLM 风控必须 try/except 回退规则结果 + `merge_risk_results` 取更严等级，不能裸调。这也是要 feature flag 灰度（默认 False）的原因——不能一开全开。
  4. **service 层函数「同名不同行为」要核对**：`route_task_rules_only` 自带 `apply_evidence_policy_guard`，`route_task`（async）的 rule 早返回路径不跑 guard。接入 LLM 路径后要在节点层补 guard，而非改 service 层（改 service 影响 chat_pipeline 老路径 + cli + 现有测试，面更大）。最小影响原则：节点层补，service 层不动。
- **检查范式**：识别 LLM 节点 → 探针 `for nid,nd in g.nodes.items(): print(nid, type(nd.data), getattr(nd,'metadata',None))`，metadata 全 None 就用映射表。节点改 async 后 → `pytest tests/unit/graph/ -q` 全跑，grep `node(state)` 找漏改的同步调用。
- **相关**：#33（改 mermaid 要 mmdc 真渲染——本次加 llmNode classDef 也要 mmdc 验证）、#4（flag 默认 False 部署后查 stream 日志确认行为不变）、#31（eval≠生产路径，LLM 路由修复是 eval 路径，节点接入才是生产路径）。

## 35. 加 rollout switch（默认关闭绕过某节点）必须全覆盖：① 所有入口把 config 开关传到 state ② 所有测该节点的测试在 state 里显式开启——漏一处 = 开关失效或测试假失败
- **场景**：commit `48c6f9f` 给 `normalize_turn` 加 `topic_routing_enabled` rollout switch（`online_graph.py:122-124`：`if flow_action=="chat" and not topic_routing_enabled: flow_action="direct_chat"`），默认 False 时绕过 context_resolution/evidence_routing 直连 chat。同天 commit `15e1452` 写了测 context_resolution 的图集成测试，但 state 里没设 `topic_routing_enabled=True` → normalize_turn 路由 direct_chat → context_resolution 被绕过 → `context_status=None` → 3 个测试失败。同时钉钉 stream 入口 `graph_stream.py` 的 input_state 设了 `guided_flows_enabled` 但漏设 `topic_routing_enabled` → 即使 `TOPIC_ROUTING_ENABLED=true`，stream 路径 topic routing 仍关闭（开关失效）。HTTP 入口 `online_conversation.py:221` 设了，stream 漏了。
- **根因**：rollout switch 加得不全。switch 在 `normalize_turn` 读 `state["topic_routing_enabled"]`（默认 False），但 state 由各入口各自构造——HTTP 入口传了 config，stream 入口没传，测试也没传。三处构造 state 的地方只覆盖了一处。
- **教训**：
  1. **加「默认关闭绕过节点 X」的 rollout switch 时，grep 所有构造该图 input_state 的入口**（HTTP / 钉钉 stream / CLI / 测试），全部把 config 开关传到 state。漏一个入口 = 那条路径开关失效。本次 stream 漏设 = 生产主入口开关失效（默认关闭时无感，一旦运营想开启就发现不生效）。
  2. **测被 switch 绕过的节点 X 的集成测试，必须在 state 里显式开启 switch**，否则 normalize_turn/路由节点把消息绕过 X → X 不执行 → 测试断言 X 的输出全 None。这种失败现象是「context_status=None / response_kind 不对」，容易误判为节点 bug，实为测试没开 switch。
  3. **诊断「节点没执行」类失败**：先看路由节点（normalize_turn）的 switch 逻辑，确认消息没被绕过；再看节点签名/context 机制。本次先怀疑 context 机制分裂（`__pregel_runtime` vs `runtime: Runtime`），探针证实 LangGraph 1.2.7 向后兼容（`ainvoke(context=)` 仍塞 `__pregel_runtime`），老机制能拿到 ctx——机制不是问题，switch 绕过才是。
  4. **探针验证节点是否执行**：monkeypatch 被测节点为 spy（打印 config + 短路返回 cancel 路由到 END），ainvoke 后看 spy 是否被调用。spy 没被调用 = 上游路由绕过了它；spy 被调用 = 节点执行了，问题在节点内部。
- **检查范式**：图集成测试失败 `context_status=None` → 先 grep 路由节点的 switch（`state.get("xxx_enabled", False)` + 改 flow_action 绕过）→ 看测试 state 有没有开 switch → 没开就是根因。加 rollout switch → grep 所有 `input_state = {` 构造点，全部传开关到 state。
- **相关**：#4（stream 是生产主入口，开关失效在 stream = 生产 bug）、#34（节点改 async 要全 grep 调用方——同款「改动要全覆盖」模式）、commit 48c6f9f（加 switch）/ 15e1452（测试漏开 switch）。
