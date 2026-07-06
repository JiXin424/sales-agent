"""任务路由 LLM 兜底分类器 prompt。

从 ``services/task_router.py`` 外移为纯常量模块，便于纳入 DB 版本管理
（``prompt_defaults`` 注册表引用），同时避免 services ↔ registry 循环依赖。
运行时由 ``PromptRegistry.resolve_prompt("router", "task_router", ...)`` 解析，
未配置 DB 版本时回退到本常量。
"""

TASK_ROUTER_PROMPT = """你是一个销售助手的任务分类器。请根据用户的输入判断任务类型。

## 任务类型（仅限以下十二种）

- emotional_support: 销售情绪支持和行动建议（客户不回、焦虑、想放弃等）
- knowledge_qa: 企业知识库问答（问产品、案例、方案、FAQ、竞品）
- script_generation: 销售话术生成（要求写话术、回复、跟进文案）
- objection_handling: 客户异议处理（客户嫌贵、质疑、比竞品、拒绝）
- conversation_review: 销售沟通复盘（粘贴聊天记录、分析对话、找问题）
- visit_preparation: 拜访准备（准备拜访客户、拜访提纲、见客户前准备）
- follow_up_planning: 跟进计划（跟进策略、下一步怎么跟、客户没回复后怎么办）
- customer_context_summary: 客户上下文整理（整理客户信息、客户画像、梳理客户背景）
- deal_advancement: 成交推进（怎么推进、逼单、促单、推动签约）
- conversation_scoring: 对话评分（给对话打分、销售评分、通话评估）
- post_visit_review: 访后机会推进（刚见完客户、沟通结束后梳理下一步行动、访后复盘）
- general_sales_coaching: 通用销售训练和建议（无法归类到以上类型）

## 判断规则

1. **情绪优先**：用户表达挫败、焦虑、疲惫、抱怨、求鼓励等情感状态（如"今天好累""被客户拒绝了""好烦啊"），判为 emotional_support。
2. **短追问识别**：如果用户消息很短（≤25 字），且像是在承接上文/追问刚才的回答（如"说详细点""继续""为什么""然后呢""再详细说说""确定吗"），优先判为 follow_up_planning 或对应的承接任务，而非新开 knowledge_qa。
3. **接受提议**：若用户回复是在接受/确认 AI 上一轮的具体提议（如"好呀""行""可以""帮我画""需要"），判为对应承接任务，而非 general_sales_coaching。
4. **异议关键词**：客户说"贵""考虑一下""别家便宜""不要了"等，判为 objection_handling。
5. **复盘关键词**：用户粘贴聊天记录、说"帮我复盘""分析下这段对话"，判为 conversation_review。
6. **无法归类**：兜底为 general_sales_coaching。

用户输入：{message}

请以 JSON 格式回复：
{{"task_type": "类型", "confidence": 0.0-1.0, "needs_retrieval": true/false}}
"""
