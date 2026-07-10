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

## #46 现象描述里的「X」指代多个候选产物时先问清是哪个——别因 CLAUDE.md 强调「主入口」就默认最大流量入口
- **教训**:用户报「生成的完整回答没渲染 markdown」,我因 CLAUDE.md 反复强调「钉钉 Stream 是生产主入口」而默认 X=钉钉端回答,直接派了钉钉渲染链路子代理;实际 X=**deepeval 生成的 HTML 报告**里的回答字段。停掉重派,浪费一轮算力。① 现象描述的主语(「X 没渲染」「X 报错」「X 慢」)在本项目常可指多个候选产物:钉钉端回答 / eval HTML 报告 / eval MD·CSV 报告 / 前端图调试页 / API 响应体——**最大流量入口 ≠ 用户实际遇到的那个**。② CLAUDE.md「钉钉是主入口」的语境适用于「**验证部署/修复**」(查 stream 日志确认全机健康),**不适用于「用户报告某处现象」**——后者要先问清具体产物。③ 线索:用户前一轮在聊 deepeval 链路,「生成的回答」更可能指 eval 产物;但别靠猜,一句话澄清成本远低于一个错方向子代理。
- **检查**:用户报「某处没渲染/报错/慢」且主语可指多个产物 → 先一句话列出候选问清是哪个,再派子代理/动手。
- **相关**:#31

## #47 worktree 合回 main 时 main 已被并发推进:worktree 内 merge main 解冲突再 FF;Edit 对本仓 CJK 块常失配
- **教训**:① 在 worktree 隔离干活期间,**并发会话会把 main 推进**(本次 main 从 15e04e9 推到 6a8f0ee,加了 scenario-coach 修复 + LLM 参数 docs)。合回时不是 FF,要在 **worktree 内 `git merge main`** 把冲突隔离解决(本次冲突在 README 更新日志表 + changelog/2026-07-10.md,双方各追加同日条目,两侧都保留),commit merge 后 `git -C 主目录 merge --ff-only <worktree分支>` 回 main。② FF 前主目录若有**worktree 现已 tracked 的未跟踪文件**(如进 worktree 前在主目录建的 spec/plan 草稿),FF 会报「untracked would be overwritten」--先 `diff` 确认与提交版字节一致再删,FF 会以 tracked 重建。③ `tasks/plan-stream-websockets-fix.md` 这类他人未跟踪文件别碰,分支不 track 它就不挡 FF。④ **Edit 工具对本仓 CJK 代码块常失配**(不可见码点差异,`old_string` 怎么贴都「not found」):大块 CJK 改动别用 Edit,改用 **Write 整文件**(测试)或 **Python 按 ASCII 锚点 splice**(如 `# Step 5:` 起、`async def _next(` 止),Python 读真字节不靠匹配。⑤ subagent 派发可能撞账户 5h 用量上限(429)中途死,有 plan 全代码的任务可**降级为本会话内联执行**(转录+测试),不必卡死等恢复。
- **检查**:worktree 合回前先 `git log 15e04e9..main` 看主目录是否被推进;是 -> worktree 内 merge main 解冲突 -> 主目录 FF。主目录未跟踪文件挡 FF -> diff 确认一致再删。CJK 块 Edit 失配 -> Write 整文件 / Python ASCII 锚点 splice。子代理 429 -> plan 有全代码就内联执行。
- **相关**:#38 #6 #30

## #49 「机器人答应了但没发生」先判功能路径 vs 模型幻觉;门控功能修复走暗启动+金丝雀
- **教训**:用户报「让机器人5分钟后提醒我,到点没提醒」。第一反应别当「提醒功能有 bug」去 debug 调度器——先判**到底走没走功能路径**。方法:核对生产出站文案是否命中该功能的**硬编码模板**(建提醒是 `已创建提醒：{when}，提醒你{title}。`,失败是「销售动作处理失败」)。本次出站是「收到,5分钟后提醒你上厕所…」+教练式发挥,两模板都不命中 → 走的是普通 chat,LLM 凭训练经验**幻觉承诺**一个门控关闭/未接通的能力,后端零动作。根因三连(#38 同类):① prompt 在「参数/prompt 迁 YAML」重构中被落下(`get_prompt("task","sales_action_extractor")` 查无键);② 调用文件缺 `import get_prompt`;③ 调用在 `try` 外击穿了 docstring 承诺的「失败即降级」契约(异常直接上浮成「处理失败」)。修复=补 YAML prompt+补 import+把构造消息移进 try。门控功能(enabled 默认关)上线用**暗启动+金丝雀**:门控关时休眠代码零行为变化,故可先把代码经 CI 全铺(功能仍关),再单租户开 env 开关做真实端到端(钉钉发「1分钟后提醒我测试」→查 worker 投递日志+用户确认卡片到达),验过再逐个开其余,生产租户开关最后翻。
- **检查**:现象「答应了但没执行」→ 先核对出站文案是否命中功能硬编码模板,判功能路径 vs 幻觉,别直接 debug 后端。prompt 迁 YAML 后必 grep 每个 `get_prompt(...)` 键在 YAML 存在 + 调用文件 import 齐全 + 调用在 try 内。门控功能改动走暗启动(休眠全铺)+金丝雀(单租户 env 开关先验)。
- **相关**:#38 #4 #35
