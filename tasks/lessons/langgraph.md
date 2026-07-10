# Lessons · LangGraph / graph 子图

> 详情文件;索引见 `tasks/lessons.md`。#编号稳定。

## #20 跨层 response 形状契约必须写死;别假设 LangGraph checkpoint 字段名——跑最小 probe dump 真实对象
- **教训**:① **跨层 response 形状是契约**,必须在 design.md 写死到「裸数组 vs 包对象」级别,并在验收里加一条「前端实际拿到的字段非 undefined」。子代理并行实现前后端时,形状漂移是最高频 bug。② **第三方框架的字段名/结构别凭记忆,跑一次最小 probe dump 真实对象**。langgraph>=1.2 的 `metadata` 只有 `source`/`step`/`parents`,**没有 writes**,`node` 全 null——`tasks[*].name` 才是节点名来源。③ **进程内直调端点函数 + 共享 InMemorySaver** 是绕过 HTTP/Docker/DB 的最快验证法:monkey-patch `get_checkpointer` 返回同一个 `InMemorySaver`,`run` 写、端点读,一秒验证字段映射 + 403 + 形状,无需起 server。
- **检查**:涉及前后端新端点 → design.md 明确 response 形状(包对象 vs 裸数组)→ 实现后写进程内 probe(共享 InMemorySaver)dump 真实 `metadata`/`tasks`/`next` → 前端消费处加 `?? []` 兜底前,先确认字段名拼写与后端一致。
- **相关**:#4 #21

## #21 LangGraph astream(stream_mode=[list]) 返回 tuple[mode,payload] 不是 dict;「进程内验证端点函数」≠「验证了所有代码路径」
- **教训**:① **LangGraph 流式:stream_mode 是 list → chunk 是 tuple;是单字符串 → chunk 是 dict**。解包前先 `isinstance(chunk, tuple)` 归一化,别假设一种形状。② **「验证了端点函数」 ≠ 「验证了所有代码路径」**。SSE 流式(generator yield)、异步迭代、分支逻辑要单独触发。漏了 SSE 路径,因为只直调了同步返回函数,给了「已验证」的假象。③ 真实 HTTP 跑一次是发现这类 bug 的最终手段;环境受限时(本机无 DB、容器外代码)至少为「流式/异步」路径写专门的进程内 probe(直调 generator 收集 yield),而非只测同步返回。
- **检查**:任何 `async for chunk in graph.astream(...)` → 先 `print(type(chunk), chunk)` 确认形状 → 归一化 → 再分支。SSE 端点写进程内 probe:`async for evt in streamer(): collect`,验证 yield 的事件类型符合预期。
- **相关**:#20 #4

## #27 检索子图自己出答案(skip_generation=True)会绕过主生成节点 → 租户定制 prompt 全失效
- **教训**:① **检索层只产证据(context/source text),生成层只走一条主管道**(`generate_node` → `execute_agent` → `PromptRegistry`)。任何「检索后自己出答案并跳过主生成」的旁路都会让集中式 prompt 管理形同虚设——这是 prompt 体系的核心不变量。② **`skip_generation` 这类短路标志要审慎**:它只该用于「确实不需要生成」(如 evidence gate 判定 required 知识缺失而 block),绝不该被检索路径用来「我已经出过答案了」。③ **新路径(graph)和老路径(chat_pipeline)必须行为对齐**:老路径怎么把 ontology 纳入生成、怎么传 `ontology_context`,新路径照搬,别另起炉灶搞出一套不一致的旁路。④ 排查「prompt 没生效」先查调用链终点:`generate_node` 是否被 `skip_generation`/early-return 跳过?别只盯 prompt 文本本身。
- **检查**:用户反馈某类查询输出「像没经过定制 prompt」→ 沿路径 grep `skip_generation` / `answer_dict.*precompute` / early-return → 看是否某检索/旁路分支短路了 `generate_node` → 改成「只产证据、回流主生成」。
- **相关**:#1 #31

## #33 改动 mermaid 输出(后端装饰 class/classDef / shape / 语法)后,用 mermaid 官方 CLI mmdc + 系统 chrome headless 真渲染验证
- **教训**:① 后端改 mermaid 文本(classDef/class/shape/任意语法)→ 部署前用 mmdc 真渲染验证,别靠「语法和 LangGraph 自带 classDef 同款所以肯定行」。② mermaid 11 在纯 node 验证走不通(DOMPurify 依赖 DOM,无 DOM 报 `DOMPurify.sanitize is not a function`),mmdc(puppeteer+系统 chrome)是最权威本地预验证,比装 jsdom 折腾 DOMPurify 靠谱。③ **验证 class 真生效要 grep 渲染出的 svg 里的 fill/stroke 颜色**,不只看 parse/render 不报错(前端 `mermaid.render` 失败会 catch 退化成 `<pre>` 纯文本,光看前端不报错不够)。④ 子图节点识别:LangGraph `add_node(name, compiled)` 的节点 `isinstance(nd.data, CompiledStateGraph)` 为真;但被 wrapper 函数包一层的节点 LangGraph 看不出是子图,要靠节点 id 归一化后命中 `GRAPH_REGISTRY` 补识别——双信号才不漏。
- **检查**:临时目录 `/tmp/mmdcheck` 里 `PUPPETEER_SKIP_DOWNLOAD=1 npm i mermaid@<前端同版本> @mermaid-js/mermaid-cli@<同版本> --no-save`,写 puppeteer config `{ "executablePath": "/usr/bin/google-chrome", "args": ["--no-sandbox","--disable-gpu"] }`,`mmdc -i graph.mmd -o out.svg --puppeteerConfigFile pptr.json`;`grep -oE '(fill|stroke)[: ]*#颜色' out.svg` 确认 class 生效。
- **相关**:#4 #34

## #34 LangGraph 节点 add_node(tags=...) 不从 get_graph().nodes 暴露——识别 LLM/特殊节点要用集中映射表;节点 def→async def 改签名要全局 grep 调用方测试同步改
- **教训**:① **LangGraph tags 是给 tracing/回调用的,不进 graph 结构**——`get_graph().nodes[*].data` 是 `RunnableCallable`,`metadata` 恒 None。想在 graph_debug 这类「读图结构」的场景识别节点特性,用集中映射表(单一事实源,`graph/node_metadata.py`),别用 tag。② **节点 `def`→`async def` 改签名后,所有同步调用方测试都要改**:grep 调用点,测试里 `result = node(state)` → `result = await node(state, mock_runtime)` + `@pytest.mark.asyncio` + mock_runtime fixture。**别只跑当前目录测试就以为完事**——全量 `pytest tests/unit/graph` 才发现漏改。③ **「LLM 失败静默放行」是风控致命默认值**:图节点接入 LLM 风控必须 try/except 回退规则结果 + `merge_risk_results` 取更严等级,不能裸调;这也是要 feature flag 灰度(默认 False)的原因。④ service 层函数「同名不同行为」要核对:`route_task_rules_only` 自带 `apply_evidence_policy_guard`,`route_task`(async)的 rule 早返回路径不跑 guard。接入 LLM 路径后要在**节点层**补 guard(最小影响),非改 service 层(影响 chat_pipeline 老路径 + cli + 现有测试,面更大)。
- **检查**:识别 LLM 节点 → 探针 `for nid,nd in g.nodes.items(): print(nid, type(nd.data), getattr(nd,'metadata',None))`,metadata 全 None 就用映射表。节点改 async 后 → `pytest tests/unit/graph/ -q` 全跑,grep `node(state)` 找漏改的同步调用。
- **相关**:#33 #35 #4

## #35 加 rollout switch(默认关闭绕过某节点)必须全覆盖:① 所有入口把 config 开关传到 state ② 所有测该节点的测试在 state 里显式开启——漏一处 = 开关失效或测试假失败
- **教训**:① 加「默认关闭绕过节点 X」的 rollout switch 时,grep **所有构造该图 input_state 的入口**(HTTP / 钉钉 stream / CLI / 测试),全部把 config 开关传到 state。漏一个入口 = 那条路径开关失效(本次 stream 漏设 `topic_routing_enabled` = 生产主入口开关失效)。② **测被 switch 绕过的节点 X 的集成测试,必须在 state 里显式开启 switch**,否则路由节点把消息绕过 X → X 不执行 → 断言 X 的输出全 None(现象是 `context_status=None` / `response_kind` 不对,容易误判为节点 bug,实为测试没开 switch)。③ 诊断「节点没执行」类失败:先看路由节点(`normalize_turn`)的 switch 逻辑,确认消息没被绕过;再看节点签名/context 机制。④ 探针验证节点是否执行:monkeypatch 被测节点为 spy(打印 config + 短路返回 cancel 路径到 END),ainvoke 后看 spy 是否被调用。
- **检查**:图集成测试失败 `context_status=None` → 先 grep 路由节点的 switch(`state.get("xxx_enabled", False)` + 改 flow_action 绕过)→ 看测试 state 有没有开 switch → 没开就是根因。加 rollout switch → grep 所有 `input_state = {` 构造点,全部传开关到 state。
- **相关**:#4 #34
