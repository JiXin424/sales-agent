# Todo: 离域/未知问题路由到 web 搜索(而非 KB)

## 背景 / 根因(已查清)
- 现象:用户问「昨天阿根廷和埃及谁赢了」(体育)或发随机照片 → 被路由进 KB 检索,回一堆无关引用(体育题回 8 条销售文档);用户认为这种未知/离域问题应走 web(已配 BOCHA_API_KEY)。
- 根因(3 个缺口):
  1. **路由器无「域外 → web」出口**:`EVIDENCE_ROUTER_PROMPT` 只 12 个销售域意图,`knowledge_policy` 只有 none/optional/required,无 web。web 搜索目前**只是 retrieve_node 的兜底,且仅 KB 完全空时触发**(retrieval.py:184/:280)。
  2. **`vs` 正则误升级**:`task_router.py:109` `(竞品|竞争对手|对比|区别|vs)` 把「阿根廷 **vs** 埃及」命中 → policy guard 升级成 required → 必进 KB。
  3. **引用无条件拼接**:`graph_stream.py:244` 把 sources 一股脑附末尾,不看相关性。但修好路由后 KB 引用只出现在真 KB 题上,此条优先级降为次要。

## 目标(验收)
- 明显离域的事实/时事问题(体育/娱乐/新闻/通用常识/技术代码)→ 路由到 Bocha web 搜索,返回 web 答案 + 「网络搜索」标签引用(而非 KB 销售文档)。
- 域内销售问题路由行为零回归(产品/价格/竞品仍进 KB)。
- `vs` 正则不再把体育对比题误升级。
- 176 个图测试 + dingtalk 测试全绿。

## 设计(7 处改动,最小影响)

### 改动 1 — `EvidenceDecision` 加 `web` policy
`services/structured_router_output.py:104`
`knowledge_policy: Literal["none","optional","required"]` → 加 `"web"`。
validator `_require_retrieval_query_when_required`(:110)扩展:`web` 也强制要 retrieval_query(web 搜索词)。

### 改动 2 — 路由 prompt 加离域 → web 规则
`prompts/evidence_router_prompt.py`
- `## 知识检索策略` 加 `web` 取值说明:明显超出企业福利销售领域的事实/时事(体育赛果/娱乐/新闻/通用常识/技术代码)→ `knowledge_policy="web"`(联网搜索)。
- `## 检索激活原则` 补一条:**离域事实题 → web**(与「宁可多搜」并列,明确域外走 web 而非 KB)。
- 输出 JSON 说明:`knowledge_policy` 枚举加 `web`。
- 关键:措辞要精确——只**明显离域**走 web,**域内或模糊**仍 KB(宁可多搜),避免误把销售题送 web 丢掉 KB。

### 改动 3 — 修 `vs` 正则
`services/task_router.py:109`
`(竞品|竞争对手|对比|区别|vs)` → 去掉裸 `vs`(太贪,体育对比全中);保留 `(竞品|竞争对手|对比|区别)`。销售对比由「对比/区别」覆盖。

### 改动 4 — policy guard 不破坏 web
`services/task_router.py:165` 降级条件 `knowledge_policy != "required"` → `knowledge_policy not in ("required","web")`(web 粘性,不被非事实信号降级)。升级条件(:157)已只动 none/optional,web 自动存活,无需改。

### 改动 5 — `needs_retrieval` 纳入 web
`graph/online/nodes.py:1490` 与 `:1556`
`needs_retrieval = decision.knowledge_policy in ("required","optional")` → 加 `"web"`。
(evidence_routing_node / direct_evidence_routing_node 两处)

### 改动 6 — `select_retrieval_path` 加 web 分支
`graph/chat/edges.py:41`
在 `needs_retrieval`/`none` 检查后,加:`if state.get("knowledge_policy") == "web": return [Send("retrieve", {**ctx, "retrieval_path": "web"})]`(单 Send,复用 fan-out 通道)。`ctx` 复用现有构造(:95-100)。
`graph/chat/graph.py:139` path_map 加 `"web": "retrieve"`。

### 改动 7 — `retrieve_node` 处理 web 正路
`graph/chat/nodes/retrieval.py`
入口判断 `retrieval_path == "web"`(或 policy=web):跳过 ontology/RAG,直接 `web_fallback_and_analyze(message=...)` 当**正路**调用;成功→写 sources(source_type=web)+ ontology_context_text;失败/无 key→sources 空走 evidence_gate 兜底(干净拒答,不假引用)。

## 测试(TDD)
- [ ] `test_evidence_decision_web_policy`:web policy 合法 + 强制 retrieval_query;validator 拒绝无 query 的 web。
- [ ] `test_policy_guard_keeps_web`:web 不被升级/降级;`vs` 不再升级体育题。
- [ ] `test_select_retrieval_path_web`:knowledge_policy=web → 返回 web Send。
- [ ] `test_retrieve_node_web_primary`:retrieval_path=web → 调 web_fallback(不调 KB),sources 带 source_type=web。
- [ ] 路由 prompt 回归:mock LLM 输出,断言域内题仍 required、离域题 web。
- [ ] 全量图测试 + dingtalk 测试零回归。

## 验证(CLAUDE.md:走生产入口)
- worktree:`PYTHONPATH=src pytest` 相关测试红→绿 + 图/dingtalk 全量回归。
- 部署到 dev(更新 taishan + kaifa2)后:查 `sales-agent-taishan-stream` 日志确认「阿根廷vs埃及」走 web(bocha_search 调用 + source_type=web),钉钉实测体育题给真实赛果 + 网络搜索引用。

## 风险
- **LLM 误判**:把域内销售题误分到 web → 丢 KB。靠 prompt 精确措辞 + 域内回归测试守卫。
- **图拓扑**:加 web 分支改 select_retrieval_path 返回值 + path_map,需 176 图测试全过。
- **BOCHA 失败**:web 无结果 → evidence_gate 兜底干净拒答(可接受,优于假 KB 引用)。

## Review(完成后回填)
- 7 处改动全部落地,TDD 10 测试红→绿。
- 回归:路由器+图测试 302 passed;chat_graph/online_graph/evidence_gate 49 passed;dingtalk 111 passed。
- test_graph_debug 5 failed 经基线对比(main HEAD 同样 5 failed)= 既存 stale 计数测试(硬编码 15节点/25边 vs 实际 28边,图长过 scenario_coach/memory),与本次改动无关,我的改动零加节点/边。
- 精度保证:plumbing(schema/guard/routing/web检索)由单测锁定;**prompt 精度(LLM 判域外)部署后在 taishan 用真实样例验证**(体育题→web、销售题→KB、边界题留 KB)。
- 已知边界:LLM 路由器两次失败走 `_deterministic_fallback` 时不产 web(域外→none→干净拒答,可接受,无假引用)。
- 待办:合并 main+dev(更新 taishan/kaifa2)→ 查 stream 日志确认体育题走 bocha_search + source_type=web → 钉钉实测。
