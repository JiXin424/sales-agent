"""Topic Restore Resolver prompt.

Decides which (if any) closed candidate topic the user wants to restore,
or whether to start a new topic. Returns a structured
:class:`~sales_agent.services.structured_router_output.TopicRestoreDecision`.
"""

TOPIC_RESTORE_RESOLVER_PROMPT = """你负责从有限候选中判断用户想恢复哪个旧话题。
只能输出 JSON，字段为：
{"resolution":"restore|new|ambiguous","selected_topic_id":null,
 "supplemental_message":null,"confidence":0.0,"reason_code":""}

规则：
1. 只能选择候选列表中真实存在的 topic_id。
2. 用户明确说新问题、换话题时 resolution=new。
3. 用户内容能唯一对应一个摘要时 resolution=restore。
4. 无法唯一确定时 resolution=ambiguous，selected_topic_id=null。
5. 不得补充候选列表之外的事实。
"""
