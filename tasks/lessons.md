# Lessons 索引

> 会话开始**只读本文件**;详情按主题在 `tasks/lessons/*.md`,按需加载。
> **#编号稳定(#1-#36)**:外部 changelog / README / `.trellis/` 文档引用的 `lessons #N` 不受本次重构影响。
>
> **新增条目规矩**:本索引加 1 行(教训一句话 + 主题标签),详情追加到对应主题文件。详情一律短写——教训 + 检查命令 + 相关 #编号;长篇场景/根因复盘进 task journal,不进 lessons。

## 用法
- recall 时凭索引的一句话判断相关性 → 命中再读对应主题文件详情。
- 主题标签:`[deploy]` `[graph]` `[db]` `[bash]` `[format]` `[verify]`。

## deploy / CI / 多机  (18 条)  → [lessons/deploy-cicd.md](lessons/deploy-cicd.md)
- #7  端口会漂移,先 curl 实测再信配置  `[deploy]`
- #9  dedicated 新租户:端口/network 预检 + bootstrap 顺序  `[deploy]`
- #10 traefik 跨 Docker network → 容器名解析失败 → 502;手动 connect 会丢  `[deploy]`
- #11 CI 镜像缺文件:先查 git track 再查 .dockerignore  `[deploy]`
- #12 共享域名多租户:Traefik 不能按 query 分流,tenant 必须进 path  `[deploy]`
- #13 SSH 远程命令必须用绝对路径,别依赖 cd  `[deploy]`
- #14 .gitignore 裸名排除运行时资源 → CI 镜像缺失  `[deploy]`
- #15 判断「系统实际跑什么」看生效 env_file,不是代码默认/README/.env  `[deploy]`
- #16 判断「文件角色/是否共用」前先查 git track/ignore 状态  `[deploy]`
- #17 CI 是否真触发:查 action_run + act_runner 日志,别只信 yml on:push  `[deploy]`
- #18 多机:先证伪「本机==目标服务所在机」再查内部  `[deploy]`
- #19 ssh -n 与「管道传 stdin」互斥;照搬模式前先理解前提  `[deploy]`
- #22 ci-fanout 跨机部署卡点;rsync 覆盖 secrets+tenants.json  `[deploy]`
- #23 .dockerignore 的 *.md 只根级不递归;最小 build 实测  `[deploy]`
- #26 render 有副作用,本机渲染非本机 inventory 必须 --traefik-out /dev/null  `[deploy]`
- #29 CI fan-out `cmd || echo` 吞 exit code → 校验失败 job 仍 success  `[deploy]`
- #32 ci-fanout 部署 prod2(本机)会 stash+reset 本机工作区  `[deploy]`
- #39 多机手动部署:compose `NEO4J_PASSWORD` 插值因宿主不同(source 各异)漂移→误碰共享 db/neo4j;`--no-deps` 是保险丝;`docker save|gzip|ssh|load` 绕 registry 推镜像  `[deploy]`
- #40 无源码机 env 模板投递:模板必放 `deploy/`(唯一进 deploy 镜像的位置)+`deploy-remote.sh` 落盘 `secrets/example.env`;软链单一真源消除三份漂移;新增 env 变量必须同步模板否则「本地好、服务器坏」  `[deploy]`
- #41 neo4j **社区版无 `STOP DATABASE`**(Enterprise 才有);dump/load 运行中的库用「`docker stop` 容器 + `docker run --rm` 临时容器挂同一卷跑 `neo4j-admin database dump|load`」,临时目录 `chmod 777` 否则 AccessDenied;两端同大版本即可物理复制 store(带 schema/index)  `[deploy]`
- #42 compose 服务名 DNS 别名可能未注册:api 能解析 `postgres`、容器名 `sales-agent-neo4j`,唯独解析不了服务名 `neo4j`(gaierror),bolt 本身正常(IP 直连 OK)→ `ensure_ontology_schema` 在 FastAPI lifespan startup 卡死(不报错不崩、`/health` 不响应);修:`docker network disconnect`+`connect --alias neo4j <net> sales-agent-neo4j`  `[deploy]`
- #43 钉钉 Stream 收不到消息/图片/知识库全失效,根因是 websockets 15.x 默认 ping_interval=20s 与钉钉网关冲突->ConnectionClosedError 反复重连(`[start] network exception, error=` 空);SDK `websockets.connect(uri)` 未覆盖 ping 且自带 60s keepalive;修:monkey-patch `websockets.connect.__init__` 强制 ping_interval=None;prod2 是 16.0 不受影响、test 锁 15.0.1 是重灾区;"收不到消息"优先查 stream network exception 而非代码  `[deploy]`
- #44 CI image-deploy 在主控机 render,env_file 在目标机本地不存在 -> render 脚本 _tenant_knowledge_engine fallback 检查 env_file 失败返回 legacy_rag -> 生成的 compose 退回 legacy_rag 且不注入 NEO4J_*,每次 CI 部署都把 ontology 租户打回 pgvector(无引用);根治:tenants.*.json 给租户显式写 knowledge_engine 字段(不依赖 env_file 推断)  `[deploy]`
- #45 钉钉 Stream 重启后有 **3-5 分钟静默期**（wss 连着、0 exception、但钉钉不推消息）。每次重启都重置静默期——调试时反复重启导致用户在静默期发消息"收不到"→误判为 bug→再重启→恶性循环。文字/语音/图片/知识库全受影响。**规则:stream 重启后先等 5 分钟再让用户测试;不要为开 DEBUG 反复重启(用 `docker logs` 看已有日志,等静默期过了再测)。**  `[deploy]`

## graph / LangGraph  (6 条)  → [lessons/langgraph.md](lessons/langgraph.md)
- #20 跨层 response 形状契约必须写死;checkpoint 字段别假设,probe dump  `[graph]`
- #21 astream(stream_mode=list) 返回 tuple 非 dict;进程内验证≠覆盖所有路径  `[graph]`
- #27 检索子图自己出答案(skip_generation)绕过主生成 → prompt 失效  `[graph]`
- #33 改 mermaid 输出后用 mmdc + chrome headless 真渲染验证  `[graph]`
- #34 tags 不从 get_graph().nodes 暴露,用映射表;节点改 async 全 grep 调用方  `[graph]`
- #35 rollout switch 必须全覆盖所有入口 + 测试  `[graph]`

## DB / SQLAlchemy / Alembic  (4 条)  → [lessons/db-migration.md](lessons/db-migration.md)
- #2  SQLAlchemy:先 flush 子对象再设外键,否则外键丢失  `[db]`
- #3  create_all 不处理已有表加列,必须用 Alembic  `[db]`
- #5  测试用 _make_agent 而非 ensure_default_agent_for_tenant  `[db]`
- #28 init_db create_all 在 upgrade 前 → migration 含建表+加列必幽灵漂移  `[db]`

## bash / shell  (2 条)  → [lessons/bash-shell.md](lessons/bash-shell.md)
- #8  `((n++))` 在 set -e 下旧值为 0 杀脚本  `[bash]`
- #24 `shift` 耗尽返回非零,set -e 下无声杀脚本  `[bash]`

## 模板渲染 / str.format  (2 条)  → [lessons/templates-format.md](lessons/templates-format.md)
- #1  str.format 双花括号是转义,不是占位符  `[format]`
- #30 DB prompt 字面花括号抛 KeyError;用 format_map+SafeDict;Edit 被 revert 用 git diff 验证  `[format]`

## 验证方法论 / eval≠生产 / 不可见故障 / 子代理交接  (9 条)  → [lessons/verification.md](lessons/verification.md)
- #4  验证永远走生产入口(钉钉 stream 非 HTTP);解耦改造接入面逐链路核对  `[verify]`
- #6  superpowers SDD:brief 路径不稳定,必须给子代理确切代码  `[verify]`
- #25 第三方追踪装饰器(deepeval @observe)生产无 key 仍纯负债,序列化随时炸  `[verify]`
- #31 eval(ChatPipeline 老路径)≠ 生产(graph 纯规则);嵌套 JSON 不能用 re.search  `[verify]`
- #36 Edit 接入类改动后立即 grep 验证持久化;孤儿文件让 tsc 假通过  `[verify]`
- #37 会话开始第一动作就复习 lessons 索引,别等用户提醒(索引化后成本极低)  `[verify]`
- #38 prompt「引用」≠「运行时可达」;从生产入口反追可达性;共享目录会被并发 reset --hard 清,用 worktree 隔离  `[verify]`
- #46 现象描述「X 没渲染/报错/慢」指代多个候选产物(钉钉端/eval HTML/MD·CSV/前端页)时先问清是哪个,别默认最大流量入口就派子代理  `[verify]`
- #47 worktree 合回 main 遇 main 被并发推进:worktree 内 merge main 解冲突再 FF;主目录未跟踪文件挡 FF 先 diff 确认一致再删;CJK 块 Edit 失配用 Write/Python ASCII 锚点 splice;子代理 429 降级内联执行  `[verify]`
- #48 大批积压提交 push 触发部署,别信 deploy-fanout 绿/红:它「任一台失败即红、且继续下一台」,容器 up 在新 SHA≠schema 通过(recreate 在 schema 校验前)。必逐台核对 check_schema_consistency.py+alembic heads。双 head(两迁移同 down_revision)致 upgrade 拒执行→线性化后半个链;线性化后 create_all 抢建新表致 upgrade 撞 DuplicateTable→stamp-head 兜底跳 add_column→幽灵漂移,补 backfill migration(仿 0012,ADD COLUMN IF NOT EXISTS 幂等)。stream 401 authFailed 查 env mtime+容器启动即报判定既有凭证问题非本次  `[verify]`
- #49 「答应了但没发生」先判功能路径 vs 幻觉:核对出站文案是否命中该功能硬编码模板(如 `已创建提醒：…`),命中=走了功能路径、未命中=落普通 chat 模型幻觉承诺一个门控关/未接通的能力。根因常是 prompt 迁 YAML 落下+调用点缺 import+调用在 try 外击穿降级契约(#38 同类)。门控功能修复用暗启动(休眠代码全铺零行为变化)+金丝雀(单租户 env 开关先端到端验)  `[verify]`
- #50 给 LLM 的「当前时间」必须与 prompt 声明时区一致:容器默认 UTC + prompt 写 Asia/Shanghai → LLM 把相对时间(「1分钟后」)按 +08:00 输出同钟点数,换算回 UTC 早 8h → 被判 past_time 拒建。构造抽取消息时 now.astimezone(声明时区) 再写入,tz-aware 比较才对(timestamptz 存储天然安全)  `[verify]`
- #51 跨轮合并的 `field=[...] or old.field` 兜底会复活旧值(new 补全后列表为空即回退旧),checkpoint 半成品一旦中毒每轮都失败(title 已给仍判 missing_title)。合并后字段按**合并后实际值**重算;且一条本身完整的新请求不该与陈旧半成品合并,直接旁路(否则旧的过期时间/空标题反污染完整请求)  `[verify]`
- #52 钉钉 AI 流式卡片(createAndDeliver callbackType=STREAM)创建后必须发 streaming_finalize 结束帧(isFinalize=true)才关闭"生成中"、定格内容,否则永远"加载中"不出内容;一次性推送(提醒/digest)也要 finalize,别只照搬聊天流的 create。DB deliveries.status=success ≠ 用户看到内容(createAndDeliver 本身返回成功即算投递成功),渲染态必须真机看  `[verify]`
- #53 共享库多租户、每租户各跑一个 worker 扫同一张表时,认领/调度类查询必须按 TENANT_ID 过滤,否则 A 租户 worker 用 FOR UPDATE SKIP LOCKED 抢走 B 租户任务、并用**自己**的凭证投递(串台)。"跨租户全局认领"的注释是"单一全局调度器"旧假设,与"每租户一 worker"部署冲突。启用共享资源上的多租户功能前先问"这查询有没有 tenant 边界";此类 bug 单租户测不出,要多租户共库才暴露  `[verify]`
