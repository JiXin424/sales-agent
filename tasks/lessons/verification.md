# Lessons · 验证方法论 / eval≠生产 / 不可见故障 / 子代理交接

> 详情文件;索引见 `tasks/lessons.md`。#编号稳定。

## #4 验证永远优先走生产入口;解耦改造的「接入面」必须逐链路核对
- **教训**:① **解耦类改造要列出所有调用点**(grep 函数名),逐个确认是否接入新路径,不能只改主链路(半成品)。本次用子代理梳理出 4 个调用点(chat_pipeline / streaming_handler / cli×2)+ router/risk/coach。② **验证永远优先走生产入口**:本项目生产入口是**钉钉 Stream**(非 HTTP `/agent/chat`)。验证部署/修复必须查 stream 容器日志(`docker logs <tenant>-stream`)确认连上且无 crash,不能只 curl HTTP 200 就判断「全机健康」——HTTP 走 ChatPipeline 老路径,stream 走 graph 新路径,两条路径不同。
- **检查**:解耦改造 → grep 函数名列出所有调用点逐个核对。部署验证 → `docker logs <tenant>-stream` 确认无 crash + 连上。
- **相关**:#27 #31 #36

## #6 superpowers SDD:brief 文件路径不稳定,必须给子代理「确切代码」
- **教训**:① 插件缓存下的 `.superpowers/sdd/`(如 `/root/.claude/plugins/cache/.../6.0.3/.superpowers/sdd/task-N-brief.md`)**间歇性消失**——部分子代理能读到、部分读不到。仓库本地的 `/root/code/sales-agent/.superpowers/sdd/` 才稳定。② 把任务 brief 写到**仓库本地** `.superpowers/sdd/task-N-brief.md`(用 Write 复制计划里该任务全文),或直接在 dispatch prompt 里**内联确切代码**。绝不只给子代理一段散文摘要就让它做「转录」任务。③ 对转录类任务,提交后**自己抽查**实际落盘代码是否与计划一致。④ reviewer 子代理的环境 ≠ implementer 的 `.venv`,所以不要让 reviewer 自己跑测试(会误报 `pgvector not installed`),依赖 implementer 报告 + diff + 内联 spec 即可。控制器(自己)可用 `.venv/bin/pytest` 复核。
- **检查**:转录类任务提交后,diff 实际落盘代码 vs 计划,抽查关键段落。
- **相关**:#4

## #25 第三方「追踪/观测」装饰器(deepeval @observe)在生产没配 key 时仍是纯负债——会算一堆 trace 再丢弃,且其序列化路径随时可能炸整个请求;上线前必须「无 key 也能安全 no-op」或直接移除
- **教训**:① 追踪/观测类装饰器上线前问一句:**生产环境(无 key / 未启用)下它是 no-op 还是仍跑副作用?** 仍跑 = 负债。deepeval `@observe` 即使没 key 也照常建 span + 序列化,只是不 POST。② **第三方序列化路径**(`make_json_serializable` / `vars(obj).items()` 这类「遍历活对象内部」)是定时炸弹——你控不了用户对象何时新增惰性字段。能不用就不用;用了要能整体关掉。③ **「容器在跑、/health 200」≠「业务通」**:500 在 catch-all 里被包成 JSON 返回,traceback 因日志配置没进 stdout(写文件/被吞),`docker logs` 完全看不到——**生产对 500 几乎零可见性**。健康检查必须覆盖真实业务路径(`/agent/chat` 冒烟),不能只 `/ready`。④ 根因定位别只看自己的代码:堆栈全在 `site-packages/deepeval/`,但触发点是自己的 `@observe` 装饰器。
- **检查**:线上 500 但日志干净 → 怀疑被 catch-all 吞 + 日志没进 stdout → 直接 `curl` 复现拿 `response.detail` → 按 detail 串(如 "dictionary changed size")反查装饰器/中间件序列化路径 → 无收益的观测装饰器直接移除。
- **相关**:#15 #20 #29

## #31 eval(ChatPipeline 老路径,含 LLM 路由兜底)≠ 生产(graph route_task_rules_only 纯规则);DB prompt schema 与代码 parser schema 会独立漂移;嵌套 JSON 不能用 re.search(r"\{[^}]+\}")
- **教训**:① **prompt 在 DB 版本管理 → prompt schema 与 parser 代码必须同步契约**:要么加启动期校验(解析 prompt 里的 JSON 示例确认字段名与代码期望一致),要么 parser 对 schema 容错(同时认 `intent` 和 `task_type`、缺字段给默认)。② **提取嵌套 JSON 不能用 `re.search(r"\{[^}]+\}")`**:它不支持嵌套花括号,遇到 `{"a":{"b":1}}` 截断在第一个 `}`。必须按花括号深度配对扫描(depth 计数 + 字符串字面量内跳过花括号),见 `_extract_first_json`。③ **eval 路径 ≠ 生产路径 → eval 指标不代表生产行为**:本项目 eval 跑 ChatPipeline(规则+LLM 路由),生产 stream 跑 graph `route_task_rules_only`(纯规则)。eval 里 LLM 路由的 bug 在生产不存在。**用 eval 验证生产前,先确认 eval 跑的是哪条路径**(grep eval 脚本调 `ChatPipeline` 还是 `graph`),别假设一致。④ 「修了 eval 的 bug」不等于「修了生产的 bug」。
- **检查**:`_llm_route`/任何 LLM JSON 解析报 `JSONDecodeError` → 先看 LLM 实际输出 schema(`logger.debug(response)`)vs 代码 `data.get(...)` 的 key 是否对得上 → 对不上 = schema 漂移 → parser 容错(认多 schema + 默认值)+ 平衡花括号提取。eval 报路由 bug → 先 grep eval 入口调 `ChatPipeline` 还是 `graph`,确认是哪条路径的 bug,别误判为生产故障。
- **相关**:#27 #30 #4

## #36 Edit 报 success 但内容可能被并发任务/hook 还原——接入类改动 Edit 后必须立即 grep 验证持久化;孤儿文件让 tsc 假通过
- **教训**:① **接入类改动(import / 路由 / 菜单注册)Edit 后,立即 `grep -n "<接入关键字>" <file>` 验证目标行确实在磁盘**,不能只信工具返回值。工作区有并发任务(多个 `.trellis` task 同时 in_progress)或 format-on-save hook 时,接入行可能被覆盖还原。**Edit 成功 ≠ 持久化**。② **孤儿文件(新增但未被任何地方 import 的 .tsx/.ts)会让 `tsc --noEmit` 假通过**——类型检查不报「未使用文件」。所以「tsc 通过」不能替代「接入验证」。接入断开时 tsc 照过。③ 验证顺序:新文件语法 → 接入 grep(持久化)→ tsc(类型)→ 运行时 import。**grep 持久化要排在 tsc 前**。
- **检查**:Edit 接入后跑 `grep -n "<接入关键字>" <相关文件>`;关键字缺失即重做。
- **相关**:#4 #35

## #37 会话开始第一动作就复习 lessons 索引——别等用户提醒(索引化后复习成本极低,无理由跳过)
- **教训**:CLAUDE.md 第 3 条硬规矩「每次会话开始时先复习相关项目的 lessons」。索引化后(`tasks/lessons.md` 仅 ~58 行)复习成本极低,更没有理由跳过。会话第一个工具调用前先 Read 索引,recall 凭一句话判断相关性、命中再读详情文件——而非等用户问「你读了 lessons 吗」才补读。
- **检查**:会话第一个工具调用前,确认已 Read `tasks/lessons.md`(索引)。
- **相关**:#4

## #38 prompt「引用」≠「运行时可达」——从生产入口反追可达性；共享工作目录会被并发 reset --hard 清掉，用 worktree 隔离
- **教训**:① 审计「哪些 prompt/代码在用」不能只看 import/引用,必须从**生产入口**(钉钉 Stream/HTTP graph)反追**运行时执行路径**。import 了≠跑得到:死代码独占(ChatPipeline)、默认 OFF 开关(task_router/risk/topic_routing)、注册了但运行时硬编码「半残」(evidence_router)。逐个 prompt → 调用节点 → 触发条件 → 默认开关 → 🟢可达 / 🟡默认OFF / 🔴不可达。② 多会话**共享同一工作目录**时,任一方 `git reset --hard`/`stash`/`checkout -- .` 会清掉**所有人**未提交改动——commit 只动暂存区,**reset --hard 才是核武器**。本次 router 改动被并发 reset --hard 清掉,靠 stash@{0} 找回。③ 找回:`git reflog`(reset 痕迹)+`git stash list`+`git fsck --lost-found`(dangling/stash 残留)。④ 预防:CLAUDE.md 加规则「实现类任务先 EnterWorktree 隔离」——worktree 物理隔离,主目录 reset 伤不到。
- **检查**:可达性审计 → 逐个追 调用节点→触发条件→默认开关,标绿/黄/红。改动丢失 → reflog + stash list + fsck --lost-found 找 dangling/stash 残留。
- **相关**:#4 #27 #31 #32 #36
