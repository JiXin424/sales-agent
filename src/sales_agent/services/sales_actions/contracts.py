"""销售动作理解层契约（typed contracts）。

定义动作意图 / 动作类型的闭集字面量，以及 LLM 抽取结果
(:class:`SalesActionExtraction`) 与下游决策 (:class:`SalesActionDecision`)
的数据结构。本模块为纯 Pydantic + dataclass，不依赖 Task 1 的 DB 模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 用户动作意图闭集（detector 快路由 / LLM 抽取均使用此词表）
ActionIntent = Literal[
    "create_action",
    "complete_action",
    "cancel_action",
    "snooze_action",
    "list_actions",
    "suggest_action",
    "none",
]

# 销售动作类型闭集
ActionType = Literal[
    "call_back",
    "send_proposal",
    "follow_up_quote",
    "visit_prepare",
    "post_visit_review",
    "send_material",
    "other",
]


class SalesActionScope(BaseModel):
    """一个销售动作会话的作用域（租户 / Agent / 用户 / 渠道）。"""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    agent_id: str
    user_id: str
    channel: str = "dingtalk"
    dingtalk_user_id: str | None = None


class SalesActionExtraction(BaseModel):
    """LLM 抽取出的销售动作结构化结果。

    confidence 为 0-1 闭区间；低于阈值 (0.75) 的抽取结果在
    :func:`validate_action_extraction` 中会被降级为 clarify。
    """

    intent: ActionIntent
    explicit_create: bool = False
    title: str = ""
    customer_name: str | None = None
    action_type: ActionType = "other"
    time_text: str | None = None
    scheduled_at: str | None = None
    timezone: str = "Asia/Shanghai"
    confidence: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None


@dataclass(frozen=True)
class SalesActionDecision:
    """校验后对下游（图节点 / 卡片投递）的最终决策。

    action 取值：create（创建提醒）/ clarify（请求澄清）/ suggest（建议动作，
    非 explicit_create 的可执行计划）/ ignore（忽略，按普通聊天处理）。
    """

    action: Literal["create", "clarify", "suggest", "ignore"]
    title: str = ""
    customer_name: str | None = None
    action_type: str = "other"
    scheduled_at: datetime | None = None
    timezone: str = "Asia/Shanghai"
    response_text: str = ""
    reason_code: str = ""
