"""风险检查 LLM prompt。

从 ``services/risk_checker.py`` 外移为纯常量模块，便于纳入 DB 版本管理
（``prompt_defaults`` 注册表引用），同时避免 services ↔ registry 循环依赖。
运行时由 ``PromptRegistry.resolve_prompt("risk", "risk_check", ...)`` 解析，
未配置 DB 版本时回退到本常量。
"""

RISK_CHECK_PROMPT = """你是一个销售合规检查员。请判断以下销售回复是否包含不合规内容。

检查要点：
1. 是否做出未确认的价格承诺或折扣承诺
2. 是否做出无法保证的交付承诺（如"一周内一定上线"）
3. 是否包含绝对化表达（如"保证业绩翻倍"、"100%成功"）
4. 是否恶意攻击竞品
5. 是否包含操纵性销售技巧

用户请求：{message}

销售回复：{answer}

请以 JSON 格式回复：
{{"level": "none|low|medium|high", "flags": ["风险类型列表"], "action": "allow|warn|rewrite|block", "notice": "提示信息"}}"""
