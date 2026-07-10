"""确定性快路由意图检测（detector）。

用正则/关键词把用户消息快速分到闭集动作意图；无法明确判定的
（包括潜在的 suggest 候选与普通闲聊）一律返回 ``"none"``，交给
LLM 抽取器 (:func:`parse_sales_action_request`) 做精细判断。
"""

from __future__ import annotations

import re

from sales_agent.services.sales_actions.contracts import ActionIntent

# 注意顺序：更具体的意图先判，宽泛的「提醒/安排」类 create 关键词最后判。
# 例如「取消…的提醒」同时含「取消」与「提醒」，cancel 必须在 create 之前命中。

# 完成动作：过去时态的完成表达
_COMPLETE_RE = re.compile(
    r"打完了|搞定了|办完了|做完了|已处理|已完成|回电了|已回访|已经回复|发完了|已经发了"
)
# 取消/删除动作
_CANCEL_RE = re.compile(
    r"取消|删掉|删除|不要提醒|别提醒了|撤销|不用提醒了|去掉.*提醒"
)
# 列出/查询任务
_LIST_RE = re.compile(
    r"哪些任务|还有哪些|待办|任务列表|我的任务|提醒列表|未完成.*任务|还有什么任务"
)
# 推迟/改期
_SNOOZE_RE = re.compile(
    r"推迟|延后|改时间|改到|推到|往后推|稍后提醒|改天提醒|延期"
)
# 创建提醒（explicit create 信号；最宽泛，故放在最后）
_CREATE_RE = re.compile(
    r"提醒我|帮我记|帮我提醒|设个提醒|定个提醒|安排提醒|"
    r"别忘记提醒|记得提醒|待会要提醒|提醒一下|加个提醒|设个闹"
)


def detect_fast_action_intent(text: str) -> ActionIntent:
    """用关键词/正则把 *text* 快速路由到一个动作意图。

    Returns
    -------
    ActionIntent
        ``create_action`` / ``complete_action`` / ``cancel_action`` /
        ``snooze_action`` / ``list_actions`` 之一；无法明确判定时返回
        ``"none"``（由 LLM 抽取器再判断 suggest vs none）。
    """
    if not text:
        return "none"

    # 顺序敏感：具体意图优先于宽泛的 create 关键词。
    if _COMPLETE_RE.search(text):
        return "complete_action"
    if _CANCEL_RE.search(text):
        return "cancel_action"
    if _LIST_RE.search(text):
        return "list_actions"
    if _SNOOZE_RE.search(text):
        return "snooze_action"
    if _CREATE_RE.search(text):
        return "create_action"
    return "none"
