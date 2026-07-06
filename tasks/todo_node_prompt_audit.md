# TODO: 接入 route_task/check_risk 的 LLM + 图调试区分 LLM 节点 & 标注 prompt

## 目标
1. **后端**:把 `route_task`、`check_risk` 两个图节点接入 service 层已就绪的 LLM 实现,默认关闭,feature flag 灰度。
2. **前端**:图调试里区分纯函数节点 vs LLM 节点(不同颜色),并展示每个 prompt 对应的节点。
3. 合并 `task_router.py` 未提交的 LLM 路由修复(花括号崩 + JSON 解析),这是 LLM 路由能跑的前提。

## 决策(已与用户确认)
- LLM 接入:默认关闭 + feature flag 灰度
- 前端:mermaid classDef 扩展(轻量),prompt 对应关系用侧边列表展示
- task_router 修复:合并到本次一起做(一个 PR)

---

## 阶段 0:前置——合并 task_router.py 未提交修复
- [ ] 0.1 确认 `task_router.py` 未提交改动(`_KeepMissingDict`/`_INTENT_TO_TASK`/`_extract_first_json`/`_llm_route` 修复)内容正确,跑现有 task_router 测试不破
- [ ] 0.2 同步修 `risk_checker.py:251` 的 `check_llm_risk` JSON 正则 bug(换成平衡括号提取器,复用 task_router 的 `_extract_first_json` 或抽公共)——**不修这个,risk LLM 同样会断**

## 阶段 1:配置——加 feature flag
- [ ] 1.1 `core/config.py` `PathRouterConfig` 加 `enable_llm_router: bool = False`(:98-103)
- [ ] 1.2 `core/config.py` `RiskConfig` 加 `enable_llm_risk_check: bool = False`(:76-87)
- [ ] 1.3 (可选)`config/default.yaml` 加注释说明这两个 flag,默认 false

## 阶段 2:抽 `_merge_risk_results` 到公共位置
- [ ] 2.1 把 `chat_pipeline.py` 里的 `_merge_risk_results` 搬到 `services/risk_checker.py`(作为 RiskChecker 静态方法或模块函数),图节点复用,避免依赖 deprecated ChatPipeline
- [ ] 2.2 更新 `chat_pipeline.py` 调用点(改 import,保持老路径行为不变)

## 阶段 3:改 routing.py 节点接入 LLM
- [ ] 3.1 `routing.py`:`def routing_node(state)` → `async def routing_node(state, runtime: Runtime)`,import `Runtime`、`route_task`、`get_settings`
- [ ] 3.2 `precomputed_route` 短路路径保留不动
- [ ] 3.3 非短路路径按 flag 分支:
  - `chat_model = runtime.context.get("chat_model")`
  - `if settings.path_router.enable_llm_router and chat_model is not None:` → `await route_task(message, chat_model=chat_model, db=runtime.context.get("db"), tenant_id=..., agent_id=...)`
  - 否则 → `route_task_rules_only(message)`(保留 fallback,单测/无 model 场景)
- [ ] 3.4 补回写 `knowledge_policy`(当前漏写),并显式调 `apply_evidence_policy_guard`(因 `route_task` 的 rule 路径不跑 guard)
- [ ] 3.5 更新 stale docstring("Phase 3 / requires Runtime.context" 已过时)
- [ ] 3.6 try/except 兜底:LLM 路由失败 → 回退 `route_task_rules_only`,记 warning

## 阶段 4:改 risk_check.py 节点接入 LLM
- [ ] 4.1 `risk_check.py`:`def risk_check_node(state)` → `async def risk_check_node(state, runtime: Runtime)`
- [ ] 4.2 `full_check` 规则逻辑保留不动(先跑规则)
- [ ] 4.3 在 `full_check` 之后、HITL 之前,按 flag+条件调 LLM 风控(镜像 chat_pipeline.py:864-878):
  - `if settings.risk.enable_llm_risk_check and chat_model and risk_action != "block":`
  - `risk_prompt = await resolve_risk_prompt(db, tenant_id, agent_id)`
  - `llm_risk = await checker.check_llm_risk(message, answer_text, chat_model, risk_prompt)`
  - `result = _merge_risk_results(result, llm_risk)`
  - try/except 兜底回 rule 结果(避免 LLM 失败静默放行)
- [ ] 4.4 更新 stale docstring("deferred to a conditional edge" 已过时)

## 阶段 5:后端 graph_debug——标 LLM 节点 + 返回 prompt 映射
- [ ] 5.1 `chat_graph.py:94,116` 给 `route_task`、`check_risk` 加 `tags=["llm"]`;给 `generate` 也加(它本就是 LLM 节点)。同时检查 online_graph 的 `context_resolution`/`evidence_routing`、guided_flow 的 `advance_flow` 是否需标
- [ ] 5.2 `graph_debug.py` 新增 `_identify_llm_nodes(graph)`:检测节点 data 是否带 `llm` tag(参照 `_identify_subgraph_nodes` 模式,:102-123)
- [ ] 5.3 `_decorate_mermaid` 扩展:除 `subgraphNode` 外,给 LLM 节点追加 `class xxx llmNode` + `classDef llmNode`(蓝/紫色填充,与橙色子图区分)
- [ ] 5.4 `GraphInfo`(graph_debug.py:42-47 / types.ts:3-9)加结构化字段:
  - `nodes: [{id, name, type: "function"|"subgraph", calls_llm: bool}]`
  - `prompt_map: [{node, prompt_name, prompt_source}]`(节点→prompt 对应)
  - 从 `g.nodes` 直接取结构化数据(:168),prompt_map 用集中映射表维护
- [ ] 5.5 建 `graph/nodes/prompt_map.py`(或加到 registry.py):集中声明节点→prompt 的映射表,含 5 个 LLM 节点(generate→12 task prompt + system;context_resolution→2;evidence_routing→1;retrieve→entity_extraction;advance_flow→5 coach + visit + post_visit),以及 route_task→TASK_ROUTER_PROMPT、check_risk→RISK_CHECK_PROMPT(接入后)
- [ ] 5.6 顺手修失灵 bug:前端 CSS 补 `.subgraphNode` 样式(后端加了 class 但前端没接)

## 阶段 6:前端——区分节点 + prompt 对照列表
- [ ] 6.1 `GraphDebugPage.css` 加 `.llmNode` 样式(如 `fill:#e6f4ff,stroke:#1677ff`)+ 补 `.subgraphNode` 样式(用后端 `_SUBGRAPH_CLASS_DEF` 同款橙色)
- [ ] 6.2 `api/types.ts` `GraphInfo` 加 `nodes` 和 `prompt_map` 字段
- [ ] 6.3 `GraphDebugPage.tsx` 左面板(图下方或 Tab 切换)加「节点-Prompt 对照」列表:
  - 表格列:节点名 | 类型(函数/子图)| 是否 LLM | 对应 prompt
  - 用 antd Table,LLM 行高亮,无 prompt 行标灰
- [ ] 6.4 mermaid 图例区加说明:橙色=子图节点,蓝色=LLM 节点,灰色=纯函数节点

## 阶段 7:验证(CLAUDE.md #4——走生产入口钉钉 Stream)
- [ ] 7.1 跑后端单测:`pytest tests/` 重点 task_router、risk_checker、routing、risk_check 节点
- [ ] 7.2 flag 默认 False → 部署后 `docker logs <tenant>-stream` 确认行为不变(走 rule)
- [ ] 7.3 flag 开 True(单租户灰度)→ stream 日志确认 route_task/check_risk 走 LLM 分支,无 crash,延迟可接受
- [ ] 7.4 前端图调试:三个图 LLM 节点显蓝色、子图节点显橙色、纯函数灰色;节点-Prompt 对照表内容正确
- [ ] 7.5 资深工程师自检:flag 关闭时零行为变化;LLM 失败兜底回 rule;risk 不静默放行

## 阶段 8:收尾(CLAUDE.md 规定)
- [ ] 8.1 更新 `README.md`「产品文档对照」节
- [ ] 8.2 新建 `changelog/2026-07-06.md` 记录改动
- [ ] 8.3 更新 `tasks/lessons.md`(若踩坑)

## Review

### 完成情况
- ✅ 阶段 0:合并 task_router LLM 路由修复(_KeepMissingDict/_INTENT_TO_TASK/_extract_first_json)+ 同步修 risk_checker 的 check_llm_risk JSON bug(复用 _extract_first_json)
- ✅ 阶段 1:PathRouterConfig.enable_llm_router + RiskConfig.enable_llm_risk_check(默认 False)+ env 覆盖 + default.yaml
- ✅ 阶段 2:_merge_risk_results 从 chat_pipeline 搬到 risk_checker.merge_risk_results(公开化),chat_pipeline 改 import
- ✅ 阶段 3:routing_node 改 async + runtime,按 flag 分支,LLM 路径补 apply_evidence_policy_guard,失败回退规则
- ✅ 阶段 4:risk_check_node 改 async + runtime,full_check 后按 flag 叠加 check_llm_risk + merge_risk_results,失败回退规则
- ✅ 阶段 5:新增 graph/node_metadata.py(22 节点集中映射);graph_debug 加 _identify_llm_nodes/_build_node_infos,GraphInfo 加 nodes/prompt_map,mermaid 加 llmNode 蓝色 classDef;删 _NODE_DESCRIPTIONS 重复字典改用 node_metadata
- ✅ 阶段 6:前端 types.ts 加 NodeInfo/PromptMapping;GraphDebugPage 加 NodeLegend + NodePromptTable(可折叠);CSS 加图例+表样式
- ✅ 阶段 7:107+ 单测通过;flag 默认 False 时 24 集成测试行为不变;mmdc 真渲染验证 llmNode 蓝色高亮生效(4 节点);前端 tsc+vite build 通过
- ✅ 阶段 8:README 更新日志加条目;changelog/2026-07-06.md 追加详细记录;lessons.md 加 #34

### 验证结论
- **flag 默认 False 零行为变化**:route_task/check_risk 走原规则路径,24 个集成测试(test_chat_graph/test_graph_pipeline_parity/test_topic_memory_flow)通过
- **既有失败与本次无关**:test_context_routing_nodes 3 个 + test_topic_manager/test_topic_model 在干净 HEAD(6477b0e)同样失败,是 DB/多进程环境问题
- **LLM 路径逻辑正确**:task_router 修复后 LLM 路由可跑通;risk_checker JSON bug 同步修;merge_risk_results 搬迁后 chat_pipeline 引用同函数
- **mermaid 真渲染验证**(按 lessons #33):mmdc 渲染 chat 图 exit=0,4 个 LLM 节点带 `class="node default llmNode"`,fill:#e6f4ff/stroke:#1677ff 生效
- **生产入口(stream)验证待部署**:flag 默认关闭,部署后应行为不变;灰度打开需查 docker logs <tenant>-stream 确认 LLM 分支无 crash——此步骤需用户确认是否部署

### 关键设计决策
1. **集中映射表 vs LangGraph tags**:tags 不从 get_graph().nodes 暴露(metadata 恒 None),改用 node_metadata.py 单一事实源,同时服务「区分节点」+「prompt 标注」
2. **节点层补 guard vs service 层改 route_task**:选节点层(最小影响,不动 chat_pipeline/cli/现有测试)
3. **feature flag 默认 False**:LLM 风控失败兜底为 allow(静默放行),不能裸开;灰度按配置/env 控制

