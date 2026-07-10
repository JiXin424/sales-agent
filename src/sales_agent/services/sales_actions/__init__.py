"""销售动作理解层（understanding layer）。

公开符号（Task 2 产出）：

- :class:`SalesActionScope` / :class:`SalesActionExtraction` /
  :class:`SalesActionDecision` —— 类型化契约
- :data:`ActionIntent` / :data:`ActionType` —— 闭集字面量
- :func:`detect_fast_action_intent` —— 确定性正则快路由
- :func:`validate_action_extraction` —— 抽取结果校验 + 时间解析
- :func:`parse_sales_action_request` —— LLM 抽取器（失败降级为 none）
"""

from sales_agent.services.sales_actions.contracts import (
    ActionIntent,
    ActionType,
    SalesActionDecision,
    SalesActionExtraction,
    SalesActionScope,
)
from sales_agent.services.sales_actions.detector import detect_fast_action_intent
from sales_agent.services.sales_actions.parser import parse_sales_action_request
from sales_agent.services.sales_actions.time_parser import (
    CONFIDENCE_THRESHOLD,
    parse_scheduled_at,
    validate_action_extraction,
)

__all__ = [
    "ActionIntent",
    "ActionType",
    "CONFIDENCE_THRESHOLD",
    "SalesActionDecision",
    "SalesActionExtraction",
    "SalesActionScope",
    "detect_fast_action_intent",
    "parse_sales_action_request",
    "parse_scheduled_at",
    "validate_action_extraction",
]
