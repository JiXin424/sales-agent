# Lessons · deploy / CI / 多机

> 详情文件;索引见 `tasks/lessons.md`。#编号稳定(外部 changelog/README/.trellis 文档大量引用 `lessons #N`)。

## #7 端口会漂移,先 curl 实测再信配置
- **教训**:报「连不上后端 / 未配置 Agent」先 curl 候选端口(`:8001`/`:8003`…),别被前端兜底文案带偏(`App.tsx` 的 `InstanceEntry` 对任意 fetch 失败一律显示「未配置 Agent」)。端口分环境:`tenants.prod3.json`=8001、`tenants.json`=8003(本机 8001 被 `ai-coach-fastapi` 占)。改 dev 端口别顺手改 prod 配置。三处真相源要一致:`deploy/tenants*.json` → `docker-compose.generated.yml` → `console/.env.development`。
- **检查**:`curl` 候选端口;`curl http://localhost:<vite>/src/api/client.ts` 看 vite 注入的 `VITE_API_BASE_URL` 实际值。
- **相关**:#10 #22

## #9 dedicated 新租户:端口/network 预检 + bootstrap 顺序
- **教训**:① 起服务前预检「主 compose postgres 端口映射」vs「现有运行栈」(generated compose 不映射端口);规避宿主端口冲突用叠加文件清空端口(`postgres.ports: []`)而非改主 compose。② db 容器一旦被 recreate,所有依赖它的 api 都要重启(asyncpg/SQLAlchemy 连接池持有旧容器连接,不自愈)。③ dedicated 新租户 bootstrap 顺序:`POST /tenants` 注册(只写表,不建 Agent)→ 重启 api(启动钩子 `ensure_default_agents` 建默认 Agent)→ 才能访问 `/instance/agent`;未 bootstrap 时该端点返回 **500**(`AgentNotFoundError` 未映射成 HTTP,非 404)。
- **检查**:`ss -ltn | grep 5432`;`docker network inspect <net>`;`docker inspect <db> --format '{{json .NetworkSettings.Networks}}'`;`docker exec <db> psql ...` 查 tenants/agents 表。
- **相关**:#10 #22

## #10 反代网关(Traefik)与应用容器跨 Docker network → 容器名解析失败 → 502
- **教训**:① 排查 502 先区分「后端挂了」vs「网关连不上」:证据组合 = 网关日志 upstream URL + 极短耗时(2ms)+ `docker exec <网关> getent hosts <后端容器名>` 是否解析 + 宿主直连后端端口是否 200。直连 200 + 网关解析不到 = 网络/路由问题。② **手动 `docker network connect` 是一次性的**——容器 recreate(compose up/redeploy)就丢。跨项目共享网络必须在 compose 声明 `networks:`(`external: true`)。③ 给服务显式声明 `networks:` 后它会**退出隐式 default 网络**,必须同时写 `default`,否则 api 连不上 postgres/neo4j。④ sourceless 部署(image-deploy)traefik 用 `host.docker.internal:<宿主端口>`,不依赖容器共享网络,不需要 shared_network;distroless traefik **没有 getent**,要看 access log 的 upstream+status 才准。
- **检查**:`docker exec <网关> getent hosts <后端容器名>`;`docker ps` + `docker inspect <gw> --format '{{json .NetworkSettings.Networks}}'`;`docker compose up -d --force-recreate --no-deps <svc>` 验网络自动保留。
- **相关**:#12 #22 #26

## #11 CI 镜像缺文件:先查「有没有提交进 git」,再查 .dockerignore
- **教训**:① 「工作树有、CI 镜像没有」= 先 `git ls-tree HEAD -- <path>` / `git ls-files <path>` 确认是否 track,再查 `.dockerignore`。本地构建用工作树(含未跟踪文件),CI 用干净 checkout(只 tracked)——任何「本地有、线上没有」的资源先确认进 git。② `.dockerignore` 排除「仅仓库根」文件用 `/`-锚定(裸名如 `cocah.html` 是递归匹配,会误删 `src` 下同名)。
- **检查**:`git ls-tree HEAD -- <path>`;`git ls-files <path>`;CI 重建后 `docker exec <api> ls /app/src/.../static/`。
- **相关**:#14 #23

## #12 共享域名多租户:Traefik 不能按 query 分流,tenant 标识必须进 path
- **教训**:① 共享域名下多租户入口分流,tenant 标识必须进 path 段(如 `/integrations/dingtalk/t/{tenant_id}/...`),让每租户 `PathPrefix` 天然不同;别用 query 参数区分租户再指望反代分流。② 审查生成的反代配置时,**多条路由的 rule+priority 不能完全相同**——那是未定义行为(Traefik 任选其一),不是「负载均衡」。③ 端点若只在「校验阶段」报错(`/quick` 渲染不校验、`/whoami` 才校验),现象像「页面能开但点了报 403」,先确认请求是否落到**正确后端实例**(看 Traefik access log 命中的 router/upstream)。④ 已加防御:`render_traefik_routes` 生成后断言无重复 `rule:` 行。
- **检查**:Traefik access log 的 router/upstream + status;生成的 traefik 配置里 rule+priority 是否重复。
- **相关**:#10

## #13 SSH 远程命令必须用绝对路径,别依赖 cd
- **教训**:① SSH 远程命令一律用绝对路径(`ls /root/code/...`、`git -C /root/code/...`),`cd` 最容易漏写;用变量聚合前缀(`ROOT=/root/code/sales-agent; mv $ROOT/src ...`)。② 批量 `mv`/`rm` 前先确认目录:命令开头 `echo "$(pwd) $(hostname)"` 或先 `ls <绝对路径>`。③ 危险操作先 `mv 到 backup/`(可回退),验证 OK 再 `rm`,别一步 `rm -rf`。
- **检查**:瘦身后 `ls /root/code/sales-agent/` 确认只剩预期目录;`docker ps` 确认运行容器不受影响(容器用镜像,mv 主机源码不中断服务)。
- **相关**:#18

## #14 .gitignore 裸名排除运行时资源 → CI 镜像缺失
- **教训**:`.gitignore` 裸名同样递归匹配。**运行时必需资源**(register 上传图标、H5 模板)必须入仓,否则 CI 镜像缺失、本地却正常(本地有物理文件),形成「本地好、CI 坏」错觉。用 `!` 反例放行 src 下正式副本(同 `secrets/example.env` 处理)。
- **检查**:`git ls-files | grep <file>`;`git check-ignore <path>`;CI 重建后 `docker exec <api> ls /app/src/.../static/`。
- **相关**:#11 #23

## #15 判断「系统实际跑什么」必须看部署生效的 env_file,不是代码默认值/README/根目录 .env
- **教训**:① 回答「系统当前实际用什么」先找 `docker-compose*.yml` 里服务的 `env_file:` 指向哪个文件(`secrets/<tenant>.env`),那才是生效配置;再看代码分流点(`grep knowledge_engine` 找 `if` 分支)确认走哪条路。绝不用 `config.py` 默认值或 README 当结论。② README 的「现有 vs 可选」是写文档时的状态,可能滞后于部署。③ 警惕反向混淆:`data/agents/*/ontology` 目录基本空 **≠ 没用 ontology**——本体引擎的 Entity/Fact/Evidence 存在 **Neo4j 图库**,PostgreSQL 只存入库任务+聊天日志。
- **检查**:`grep -rn KNOWLEDGE_ENGINE secrets/`(生效配置)→ 看 compose 的 `env_file:` → `grep -rn "knowledge_engine ==" src/`(代码分流)→ 看 `ingestion_jobs.engine` 实际值或 `/ready` 的 `knowledge_engine` 字段。
- **相关**:#16 #18

## #16 判断「文件的角色 / 是否共用」前,先查 git 跟踪与忽略状态
- **教训**:① 分析仓库里任意文件角色前,先 `git ls-files <path>`(是否跟踪)+ `git check-ignore -v <path>`(是否被忽略)+ 读 `.gitignore`,再读内容推断。gitignored 文件每台机器各自一份,看起来同名但内容不同、互不影响;tracked 文件才是全仓库共享的单一真相。② 警惕「同名的 gitignored 文件在多机各自存在」——那**不是重复**,是设计(`deploy/tenants.json` 每机一份本地真相)。`.gitignore` 注释往往直接点明设计意图,**先读注释**。
- **检查**:`git ls-files deploy/tenants*` → `git check-ignore deploy/tenants.json` → `.gitignore` 注释 → `deploy-release.sh` 默认 `INVENTORY`。
- **相关**:#11 #13 #15

## #17 判断 Gitea Actions「是否真触发」:查 action_run 表 + act_runner 日志,别只信 yml 的 on:push
- **教训**:① CI 是否触发的**真相源是 Gitea 运行时**(DB `action_run` 表 + runner 实际接活日志),**不是** workflow yml 的 `on:` 声明(可能残留 / 被 repo 级禁用 / 改手动)。② runner 在线 ≠ CI 会触发。③ **git push 用的 token ≠ Gitea REST API token**:origin URL 里的 token 能 push(git-over-http basic auth),但调 `/api/v1/...` 一律返回 `user does not exist`——是 git-only 凭据,无 API/actions 权限。要触发 `workflow_dispatch` 需另备带 `actions:write` scope 的有效 PAT,否则只能 Web UI 手动。
- **检查**:`docker exec gitea sqlite3 /data/gitea/gitea.db "SELECT id,status,event,substr(commit_sha,1,7) FROM action_run ORDER BY id DESC LIMIT 5;"`;`journalctl --no-pager -n 20 -t act_runner`(act_runner 是二进制 `/usr/local/bin/act_runner daemon` 非容器,日志走 journald tag `act_runner`,不在 `docker ps`)。
- ⚠️ **本条结论(「CI 没触发」等)曾在错误 Gitea 实例上得出,方法对但必须在正确机器上用——见 #18。**
- **相关**:#18

## #18 多机环境:确认「当前机器就是目标服务所在机」要用实证,别凭「本机有该容器」归纳
- **教训**:① 多机环境(dev/主控/生产分机)动手查任何服务内部状态(DB/日志/容器)前,先证伪「当前机 == 目标服务所在机」。三个最便宜验证任一:**HEAD 对比**(`git ls-remote <origin> HEAD` vs 本机服务持有 sha)、**IP 对比**(`hostname -I` vs 文档目标机私网 IP)、**端口/容器归属**(`docker port <容器>` vs 访问 host:port)。② origin/push 的 **host**(`47.120.55.219:3002`)≠ 本机回环(`127.0.0.1:3002`),查状态前先确认「这个 host 是本机吗」。③ 看到「服务在本机跑 + 但状态/数据和预期矛盾」(HEAD 没更新、runner 没接活),**第一反应应是「我可能查错实例/机器了」**,矛盾本身是证伪信号。
- **检查**:`git ls-remote <origin> HEAD` vs 本机 sha → `hostname -I` vs 文档目标机私网 IP → `docker port <容器>` vs 访问 host:port → 不一致则 SSH 到真正目标机(用绝对路径见 #13)。
- **相关**:#15 #17

## #19 ssh -n 与「管道传 stdin」互斥:照搬同文件模式前先理解 -n 的前提
- **教训**:`ssh -n` = 把 stdin 重定向到 /dev/null,会**丢弃管道里 tar 的输出流**。同文件用 `ssh -n` 的真实原因是那些命令在 `while read ... < file` 循环里(防 ssh 吞循环输入);但 `tar | ssh` 场景下 ssh 的 stdin 已被 tar 管道占用,`-n` 多此一举且致命。正解:`tar ... | ssh -o BatchMode=...`(**不带 `-n`**)。复制既有模式前先理解它的前提。**本地试跑再次救场**——若直接 push 等 CI 跑,得去 Actions 日志才看到远端报错。
- **检查**:`printf ... | bash repro.sh` 本地试跑当场抓 bug。
- **相关**:#4 #8 #24

## #22 CI fan-out deploy-release 跨机部署卡点:tenants.json 跨机污染 / DINGTALK_PUBLIC_URL / 孤儿容器;跨机 rsync 会覆盖 secrets+tenants.json
- **教训**:① CI 部署容器没更新 → ssh 目标机手跑 `deploy-release.sh` 看真实报错(别猜),按三卡点排查:`tenants.json` 是否混入别机 tenant / `DINGTALK_PUBLIC_URL` 是否缺 / 是否孤儿容器占名(`docker rm -f` 清掉再 up)。② **跨机同步代码目录(rsync/scp)必须排除 `secrets/` 和 `deploy/tenants.json`**——这俩每机本地配置,跨机覆盖会让目标机配置丢失(secrets 变空壳/串成源机的)。`git reset` 不动它们(gitignored),但 rsync 全目录会。③ traefik `shared_network` 每机不同(prod2 用 `taishan-network`,prod3 用 `sales-agent_default`)——不能跨机复制;目标机 traefik 和 api 若同在 default network 就不需 shared_network。④ 配置丢失但容器在跑 → 从 `docker inspect` 的 `.Config.Env`/`.Mounts` 重建(运行容器 env 含部署时完整配置,含密钥)。
- **检查**:ssh 跑 `deploy-release.sh` 看报错 → 三卡点排查 → 配置丢失从容器 inspect 重建(env 写 secrets + Mounts/ports/domain 写 tenants.json)。
- **相关**:#7 #10 #18 #26

## #23 .dockerignore 的 *.md(带通配符)只匹配根级、不递归;裸名才递归——别信调研结论,最小 build 实测
- **教训**:`.dockerignore` 用 Go `filepath.Match` 语义,`*` **不跨 `/`**。故 `*.md` 只匹配根级 `*.md`(如 `README.md`),**不匹配嵌套** `eval/questions.md`。而 **裸名**(无通配、无 `/`,如 `cocah.html`)被 BuildKit **递归匹配**任意深度(见 #11)——带通配符模式与裸名的关键区别。判断「某文件是否进 docker 镜像」**永远最小 build 实测**(`FROM python:3.10-slim` + `COPY <dir>/` + `RUN ls`),别凭规则推断、也别全信子代理二手结论。
- **检查**:怀疑某文件被 dockerignore 排除 → 写 3 行临时 Dockerfile `COPY` 该目录 + `ls` → build → 在/不在一目了然 → 再决定改不改。
- **相关**:#11 #14

## #26 render-multitenant-deploy.py 有副作用:默认写 traefik 动态配置;本机 render 非本机 inventory 会覆盖本机正在用的 traefik 路由 → 域名 502
- **教训**:① **本机 render 任何「非本机 inventory」一律 `--traefik-out /dev/null`**(CI 的 deploy.yml 早就这么做了,本地手动 render 也必须)。同理 `--compose-out` 也别写到正在用的 compose 路径。② render/生成器脚本要当「有副作用」对待:先 grep 它写哪些文件(不止你传的 `--compose-out`),别假设它是纯生成。③ traefik `watch: true` + 动态目录 = 改文件即生效,往那个目录写东西要极小心,写错立刻影响线上域名。
- **检查**:要在本机渲染别的环境 inventory → 命令必带 `--traefik-out /dev/null --compose-out /tmp/xxx.yml`;事后 `stat /root/code/traefik/dynamic.d/generated-sales-agent.yml` 的 mtime 确认没被自己改到。
- **相关**:#10 #22

## #29 CI fan-out 脚本用 cmd || echo 容错会吞掉 exit code——schema 校验失败时 CI job 仍 success
- **教训**:① `|| echo` 在 cmd 失败时跑 echo(返回 0),整个表达式返回 0,`set -e` 下 `||` 会抑制 cmd 的非零退出。fan-out 脚本若要「继续下一台但最终 job fail」,必须显式收集失败:`cmd || { echo "⚠️"; FAILED=1; }` ... 循环末尾 `[ "${FAILED:-0}" = 1 ] && exit 1`。② **「校验失败即 CI fail」需要端到端 exit code 传播**:校验脚本 exit 1 → deploy-remote.sh exit 1 → ci-fanout 必须 exit 1 → job fail。中间任何一环用 `|| echo`/`|| true`/`|| :` 兜底都会断链。③ CI「静默 success」是反模式:部署类 CI 的容错应「继续执行 + 末尾汇总失败」,而非「逐台吞错」。
- **检查**:给部署链路加硬约束校验(schema/健康检查/冒烟)后,必须 grep fan-out 脚本里调它的那行有没有 `|| echo`/`|| true`/`|| :`——有就说明失败被吞,校验形同摆设(job 永远绿)。
- **相关**:#24 #25 #28

## #32 ci-fanout 部署 prod2(开发机=本机)会 git stash + git reset --hard origin/main 本机工作区——push 后本机 tracked 改动会「消失」进 stash
- **教训**:① **prod2(172.25.186.209,开发机)是 CI deploy-release target,本机工作区会被 ci-fanout 每次 push 后 stash+reset**(先 stash tracked 改动 → hard reset `origin/main` → 跑 deploy-release)。在 prod2 本机做未提交工作时,要么先 commit/branch,要么预期它会被 stash。**别把本机工作区当稳定工作面**。② 本机工作区 tracked 改动「消失」→ 先查 `git stash list` + `git reflog`,别慌重做:ci-fanout 的 stash 消息形如 `WIP on main: <刚push的sha>`,reflog 有 `reset: moving to origin/main`。改动在 stash 里没丢。③ 区分「CI 的 stash」vs「自己/别人的 stash」:CI stash 的 base sha = 刚 push 的 commit,且通常在 stash list 顶部。`git stash show --stat stash@{N}` 看文件判断归属。④ 恢复时机:等 ci-fanout 全部跑完(三台容器 tag 都更新、prod3 上无 `ci-fanout.sh` 进程)再 pop,避免与 CI git 操作竞态。
- **检查**:push 后监控发现本机 `git status` 的 tracked modified 消失 → `git stash list` 找 `WIP on main: <sha>` → `git stash show --stat stash@{0}` 确认归属 → 等 CI 结束 → `git stash pop stash@{0}`。
- **相关**:#4 #22

## #39 多机手动镜像部署：compose `NEO4J_PASSWORD` 插值漂移 + `--no-deps` + `docker save|gzip|ssh|load` 跨 registry
- **教训**:① **不同宿主 compose 的 `${NEO4J_PASSWORD}` 插值来源不同**——本机(47.120.50.181)有 root `.env` 自动加载，prod3/test(47.118.16.235)无 root `.env`，密码在 `secrets/neo4j.env`（需 `export $(grep NEO4J_PASSWORD secrets/neo4j.env | xargs)` 手动注入，和 deploy-remote.sh 一致）。没 source 的话 compose 把 `${NEO4J_PASSWORD}` 解析成空字符串 → neo4j/db 服务的 `environment:` 和运行中的容器产生配置漂移 → compose 想重建共享 db/neo4j → 要么和已有的 db/neo4j 容器名冲突(GivenName error)，要么直接重建（丢数据危险）。② **`docker compose up -d --no-deps <services>` 是保险丝**——即使插值正确，`--no-deps` 保证 compose 绝不触碰 depends_on 链上的共享 infra(db/neo4j)，只重建你指定的服务（如 fuduoduo-api/stream/worker）。手动部署必带 `--no-deps`。③ **多机 registry 不互通，`docker save | gzip | ssh host 'gunzip | docker load'` 是最快跨机传镜像法**（1.75GB ~45 秒），绕开 registry auth/push 链。加载后本地 docker 有该镜像，compose 默认 `pull_policy: missing` 优先用本地。④ **deploy-targets.json 可能过期**——本机实际 IP 47.120.50.181 不在 targets 里，prod3(47.120.55.219)/test(47.118.16.235)靠 SSH 可达性实测，不靠文档 IP。
- **检查**:手动部署到远程机 → ① SSH 过去 `ls -la /root/code/sales-agent/.env` 看有没有 root `.env`，没的话 `ls secrets/neo4j.env` 找密码源 → ② compose 前 `export $(grep -E '^NEO4J_PASSWORD=' secrets/neo4j.env | xargs)` → ③ `docker compose up -d --no-deps <tenant-api> <tenant-stream> <tenant-worker>`（绝不裸跑 `up -d`）→ ④ `docker ps` 确认 db/neo4j 没被碰（Up time 不变）→ ⑤ `docker logs <tenant>-stream` 确认连上钉钉且无 crash。
- **相关**:#15 #18 #35

## #40 无源码机 env 模板投递：deploy 镜像 COPY + deploy-remote 落盘 + 软链单一真源
- **教训**:无源码机(test/prod3)拿不到整仓,但新增 env 变量（如 `SCENARIO_COACH_ENABLED`）必须让目标机运维知道存在——否则默认 `enabled=False` 的新功能必然「本地好、服务器坏」(fuduoduo scenarios 不生效即此因)。① **env 模板唯一能到无源码机的位置是 `deploy/` 下**(deploy 镜像 build context = `deploy/`;`deploy.yml` 用 `docker build -f deploy/Dockerfile deploy/`)。权威模板放 `deploy/tenant.env.example`,`deploy/Dockerfile` `COPY` 进镜像,`deploy-remote.sh` 部署时幂等 `cp -f` 落到目标机 `secrets/example.env`(只写保留名,绝不碰真实 `<tenant>.env`)。② **软链消除 template drift**:三份模板(`deploy/tenant.env.example` 真源 + `secrets/example.env` + `.env.example`)改软链指向真源,改一处多处同步;`cp` 解引用软链 → `cp .env.example .env` 行为不变;`deploy-release.sh` 的 `find -type f` 天然排除软链、菜单不受影响。③ **新增 env 变量必须同时更新模板**,不能只改代码或只改可工作那台机的 env——那是修了机器不是修了部署流程。
- **检查**:新增 env 变量后 → `deploy/tenant.env.example` 已含该键(带注释)→ `grep -nE "API_KEY=|_SECRET=|PASSWORD=" deploy/tenant.env.example` 确认无真实密钥 → `docker build -f deploy/Dockerfile deploy/` 成功且镜像内 `/deploy/tenant.env.example` 存在 → `deploy-remote.sh` 第 0 步落盘逻辑在位 → deploy 后 SSH 目标机确认 `secrets/example.env` 已更新。
- **相关**:#35 #39
