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

## 验证方法论 / eval≠生产 / 不可见故障 / 子代理交接  (7 条)  → [lessons/verification.md](lessons/verification.md)
- #4  验证永远走生产入口(钉钉 stream 非 HTTP);解耦改造接入面逐链路核对  `[verify]`
- #6  superpowers SDD:brief 路径不稳定,必须给子代理确切代码  `[verify]`
- #25 第三方追踪装饰器(deepeval @observe)生产无 key 仍纯负债,序列化随时炸  `[verify]`
- #31 eval(ChatPipeline 老路径)≠ 生产(graph 纯规则);嵌套 JSON 不能用 re.search  `[verify]`
- #36 Edit 接入类改动后立即 grep 验证持久化;孤儿文件让 tsc 假通过  `[verify]`
- #37 会话开始第一动作就复习 lessons 索引,别等用户提醒(索引化后成本极低)  `[verify]`
- #38 prompt「引用」≠「运行时可达」;从生产入口反追可达性;共享目录会被并发 reset --hard 清,用 worktree 隔离  `[verify]`
