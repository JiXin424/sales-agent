# 福多多旧 Prompt 迁移到新 23 Prompt — 设计文档

- 日期:2026-07-06
- 旧 prompt 源:`docs/福多多-提示词与节点.md`(8 条:system_prompt / intent_router / 5×qdrant_llm / web_llm)
- 新 prompt 池:`src/sales_agent/services/prompt_defaults.py` 的 `BUILTIN_PROMPTS`(23 条,迁移后 24 条)
- 部署目标:福多多专用(dedicated mode),system_constraint 允许含福多多业务知识

## 一、迁移总表

| # | 新 prompt | 类型 | 旧源 | 方式 | 在 23 内? |
|---|---|---|---|---|---|
| 1 | `system/system_constraint` | 直接替换 | `system_prompt` | 整体替换,剥"群聊信息边界"段 | 是 |
| 2 | `task/knowledge_qa` | 直接替换 | `qdrant_llm`×5 共享原则+红线+防火墙 | 替内容保 schema | 是 |
| 3 | `ONTOLOGY_RESPONSE_PROMPT`(代码常量) | 直接替换 | 同 #2 | 替内容保 schema | 否(老 HTTP 路径) |
| 4 | `router/task_router` | 改造 | `intent_router` | 借 follow_up+情绪识别,适配 12 类 | 是 |
| 5 | `router/evidence_router` | 改造 | `intent_router` | 借"宁可多搜"+触发词,适配 knowledge_policy | 是 |
| 6 | `web/web_analysis`(新增) | 新增 | `web_llm` | 新建 prompt + 生产 graph 路径加 web 兜底+独立分析 | 是(23→24) |

**改动汇总**:23 内改 5 个(1/2/4/5 直接替换或改造,6 新增→24)+ 23 外改 1 个(ONTOLOGY_RESPONSE_PROMPT)。

**不动**:`_ENTITY_EXTRACTION_PROMPT`(图架构特有,旧无等价)、其余 11 个 task、`risk/risk_check`、7 个 coach、4 个入库 prompt(ENTITY/FACT/IMAGE/md_optimizer)。

## 二、各改动明细

### 改动 1:system_constraint ← system_prompt

- **文件**:`src/sales_agent/prompts/system.py` 的 `SYSTEM_CONSTRAINT`
- **内容**:旧 `system_prompt` 全文(人设+福多多业务知识+回答规则+数据红线+8 场景模式),**删除"群聊信息边界"段**(旧文行 142-151,钉钉单聊不适用)
- **占位符**:无。旧文不含 `{}`,str.format 校验安全(已验证)
- **生效路径**:generate 节点作 system 消息,生产 graph 路径 + 老 HTTP 路径均生效
- **影响**:system_constraint 从 ~25 行涨到 ~200 行,每个到 generate 的轮次多付 token;agent 从通用变为福多多专项

### 改动 2:knowledge_qa ← qdrant_llm 共享原则

- **文件**:`src/sales_agent/prompts/knowledge_qa.py` 的 `KNOWLEDGE_QA_PROMPT`
- **内容**:保留新"严格规则"框架,把旧 5 个 qdrant_llm 共享的 13 条分析原则写入规则区:
  - 只提取与问题直接相关信息、每条标注来源文件名
  - 识别约束性表述(必须/不能/禁止/前提是 等)逐条提取
  - 多源矛盾时指出矛盾、无相关信息坦诚说明不编造
  - 不编造具体数字/人名/联系方式/内部文档名
  - 禁止跨文档推测、专有名词精确性
  - 竞品防火墙(竞品文档数据不用于描述福多多自身)
  - 跨品类防火墙(同竞品不同品类政策不互套)
- **schema**:**保 `{summary, sections}` 不变**(generate 节点 JSON 解析代码不动)
- **弃**:5 个 qdrant_llm 的 per-KB"本节点职责"(新单 ontology 图不分 CompanyProduct/SalesStrategy/Competitor/TopSalesBehavior/Customer 五库)

### 改动 3:ONTOLOGY_RESPONSE_PROMPT ← 同 #2

- **文件**:`src/sales_agent/ontology/answer_service.py:81` 的 `ONTOLOGY_RESPONSE_PROMPT`
- **内容**:同 #2 的 13 条原则迁移
- **schema**:保 `{answer, evidence, confidence}` 不变
- **生效路径**:仅老 HTTP `/agent/chat` 路径(生产 graph 路径不走此 prompt)
- **占位符**:保 `{graph_json}/{question}/{task_type}`

### 改动 4:task_router ← intent_router

- **文件**:`src/sales_agent/prompts/task_router_prompt.py` 的 `TASK_ROUTER_PROMPT`
- **借入**(从 intent_router):
  - follow_up 短追问优先:用户消息 ≤25 字 + AI 刚做详细回复(>100 字)→ 视为追问而非新任务
  - 情绪识别:挫败/焦虑/疲惫 → emotional_support
  - 用户接受 AI 提议(如"好呀""行""帮我画")→ follow_up 类承接
- **适配**:输出从旧 5 意图(chat/deep/follow_up/emotion/creative)→ 新 12 task_type;**保 `{task_type, confidence, needs_retrieval}` schema**
- **弃**:simple_reply 生成规则、群聊克制规则、5 库激活 JSON、channel_queries 生成

### 改动 5:evidence_router ← intent_router

- **文件**:`src/sales_agent/prompts/evidence_router_prompt.py` 的 `EVIDENCE_ROUTER_PROMPT`
- **借入**(从 intent_router):
  - "宁可多搜不可漏搜"原则(不确定是否属某库范围时激活检索)
  - 触发词对照表:产品/价格/功能类 → knowledge_policy=required;纯教练/情绪 → none
- **适配**:输出 `knowledge_policy`(none/optional/required)/`response_mode`/`retrieval_query`;**保现有 schema**
- **弃**:具体 5 库激活逻辑(新单 ontology 图检索,不分库)

### 改动 6:web 分析步骤(新增,生产 graph 路径)

- **新 prompt**:`src/sales_agent/prompts/web_analysis_prompt.py` 的 `WEB_ANALYSIS_PROMPT`
  - 内容:旧 `web_llm` 全文(网络信息分析专家 + confidence 判定 high/medium/low + 来源 URL 标注 + 与内部知识冲突标注 + 主语敏感/日期敏感 + 禁止编造/概括失真)
  - schema:保旧 `{analysis, has_relevant, constraints, confidence}`
  - 占位符:`{search_results}`
- **注册**:进 `BUILTIN_PROMPTS`,新增 category=`web`,key=`web_analysis`,`required_placeholders=("search_results",)`。走 PromptRegistry 三级回退,控制台可编辑(23→24)
- **同步改动**:`prompt_defaults.py:21` 的 category 注释 `# task | system | router | risk | coach` 需追加 `| web`。`_validate_for_category` 对非 task 类不校验 task_type,`required_placeholders_for` 通过 `get_builtin` 找到注册项后正确要求 `{search_results}`,验证安全
- **graph 改动**(生产路径):
  - `retrieve` 节点(`graph/nodes/retrieval.py`):ontology 路径 + rag 路径**都无结果**时,调 `bocha_search` 兜底(复用 `services/web_search.py`)
  - 兜底命中后,调独立 LLM(WEB_ANALYSIS_PROMPT)分析搜索结果
  - `analysis` 文本拼进 `ontology_context_text`(state 字段,已存在)
  - `generate` 节点**不动**:analysis 随 `ontology_context_text` 进入 `retrieval_content`(`agent_executor.py:231` 已有拼接逻辑)
- **配置**:复用 `WebSearchConfig`(BOCHA_API_KEY / BOCHA_TOP_N),生产路径需 `web_search.enabled=true`
- **触发条件**:仅 ontology + rag 都空(与 chat_pipeline 现有逻辑一致)

## 三、旧 8 条 prompt 去向

| 旧 prompt | 去向 |
|---|---|
| `system_prompt` | 全用 → system_constraint(改动 1) |
| `intent_router` | 部分用(意图分类+检索原则→task_router/evidence_router 改造,改动 4/5);部分弃(simple_reply/群聊克制/5 库激活 JSON) |
| `qdrant_llm_CompanyProduct` | 部分用(共享 13 原则→knowledge_qa+ONTOLOGY_RESPONSE,改动 2/3);部分弃(per-KB 职责) |
| `qdrant_llm_SalesStrategy` | 同上 |
| `qdrant_llm_Competitor` | 同上 |
| `qdrant_llm_TopSalesBehavior` | 同上 |
| `qdrant_llm_Customer` | 同上 |
| `web_llm` | 全用 → 新增 WEB_ANALYSIS_PROMPT(改动 6) |

## 四、新 prompt 未改动清单(24 − 6 改动 = 18 个)

- **task(11)**:emotional_support / script_generation / objection_handling / conversation_review / general_sales_coaching / visit_preparation / follow_up_planning / customer_context_summary / deal_advancement / conversation_scoring / post_visit_review
- **risk(1)**:risk_check
- **coach(7)**:coach_daily_eval / coach_daily_eval_system / coach_sw_system / coach_sb_system / coach_sw_card / coach_sb_split / coach_sb_card
- **代码常量不动(5)**:`_ENTITY_EXTRACTION_PROMPT` / `ENTITY_EXTRACTION_PROMPT` / `FACT_EXTRACTION_PROMPT` / `IMAGE_INTERPRET_PROMPT` / md_optimizer

## 五、影响与验证

### 影响
- **token 成本**:system_constraint 从 ~25 行→~200 行,每个到 generate 的轮次多付(常态);web 兜底仅在 ontology+rag 都空时触发(非常态)
- **生产路径行为变化**:改动 6 让生产 graph 路径从"不联网"变"兜底联网"
- **agent 专项化**:system_constraint 含福多多业务知识,agent 从通用变为福多多专项(部署确认为福多多专用)

### 验证(CLAUDE.md 第 4 条:生产入口优先)
1. 部署后查 `docker logs <tenant>-stream` 确认 stream 容器连上且无 crash
2. 触发一次知识问答走 ontology 路径,确认 knowledge_qa 新规则生效(回答带来源标注、不编造)
3. 模拟空检索(问一个知识库没有的问题)触发 web 兜底,确认 web analysis 拼入 context、generate 正常输出
4. 触发一次异议处理,确认 task_router/evidence_router 路由正确(改动 4/5)
5. system_constraint 生效:回答风格符合旧 system_prompt(有温度、一针见血、第一句给结论)

## 六、风险与回退

- **风险 1**:system_constraint 整体替换后,8 场景模式与对应 task prompt 的 JSON 结构指令并存。属互补(场景模式给"怎么响应",task prompt 给"输出什么结构"),不冲突,但需验证 generate 输出仍为合法 JSON
- **风险 2**:改动 6 改变生产路径行为(联网兜底)。若 Bocha API 不稳定,可能引入延迟。回退:设 `web_search.enabled=false` 即关闭,生产路径回到不联网
- **回退**:所有 prompt 改动均为文本替换,DB 无 active 版本时自动回退内置常量;改动 6 的 graph 改动通过 `web_search.enabled` 开关控制
