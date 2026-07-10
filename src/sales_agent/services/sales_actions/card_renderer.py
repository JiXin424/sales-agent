"""销售动作卡片 Markdown 渲染器（纯函数，无 DB / 网络）。

渲染四种卡片内容：

- 到期提醒（``render_due_reminder``）
- 早间汇总（``render_morning_digest``）
- 晚间汇总（``render_evening_digest``）
- 按钮回执（``render_acknowledgement``）

设计要点：

- 只引用 **回调载荷所需** 的字段（action_id / 标题 / 客户 / 时间），
  **不** 输出原始敏感上下文（``context_snapshot`` / ``source_event_id`` 等），
  以免把内部快照泄漏到钉钉卡片正文。
- 时间渲染按动作自身时区本地化（Asia/Shanghai 默认），方便用户阅读。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CardView:
    """渲染器输入：从 :class:`SalesActionCard` 映射而来的轻量视图。"""

    action_id: str
    title: str
    customer_name: str | None
    action_type: str
    scheduled_at: datetime
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_card(cls, card) -> "CardView":
        return cls(
            action_id=card.id,
            title=card.title,
            customer_name=card.customer_name,
            action_type=card.action_type,
            scheduled_at=card.scheduled_at,
            timezone=getattr(card, "timezone", "Asia/Shanghai") or "Asia/Shanghai",
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ACTION_TYPE_LABELS: dict[str, str] = {
    "call_back": "回拨电话",
    "send_proposal": "发送方案",
    "follow_up_quote": "跟进报价",
    "visit_prepare": "拜访准备",
    "post_visit_review": "拜访复盘",
    "send_material": "发送资料",
    "other": "事项",
}


def _action_label(action_type: str) -> str:
    return _ACTION_TYPE_LABELS.get(action_type, "事项")


def _localize(scheduled_at: datetime, tz_name: str) -> datetime:
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=ZoneInfo("UTC"))
    try:
        return scheduled_at.astimezone(ZoneInfo(tz_name))
    except Exception:
        return scheduled_at.astimezone(ZoneInfo("Asia/Shanghai"))


def _fmt_local(scheduled_at: datetime, tz_name: str) -> str:
    """``YYYY-MM-DD HH:MM`` 本地时间，附时区缩写。"""
    local = _localize(scheduled_at, tz_name)
    return local.strftime("%Y-%m-%d %H:%M") + f" ({local.tzname() or tz_name})"


# ---------------------------------------------------------------------------
# due reminder
# ---------------------------------------------------------------------------


def render_due_reminder(card: CardView) -> str:
    """到期提醒 Markdown：标题 + 客户 + 类型 + 本地时间 + action_id 引用。"""
    lines = [
        f"## ⏰ {card.title}",
        "",
    ]
    if card.customer_name:
        lines.append(f"- 客户：**{card.customer_name}**")
    lines.append(f"- 类型：{_action_label(card.action_type)}")
    lines.append(f"- 时间：{_fmt_local(card.scheduled_at, card.timezone)}")
    lines.append("")
    lines.append(f"<!-- action_id: {card.action_id} -->")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------


def _render_digest_card_line(card: CardView) -> str:
    customer = f"（{card.customer_name}）" if card.customer_name else ""
    return (
        f"- {_action_label(card.action_type)}｜{card.title}{customer}"
        f"｜{_fmt_local(card.scheduled_at, card.timezone)}"
        f" <!-- action_id: {card.action_id} -->"
    )


def render_morning_digest(cards: list[CardView]) -> str:
    """早间汇总：列出当日所有 pending 动作。"""
    if not cards:
        return "## 🌅 早安\n\n今天暂无待办销售动作，保持节奏。\n"
    lines = ["## 🌅 早安·今日待办", ""]
    lines.extend(_render_digest_card_line(c) for c in cards)
    lines.append("")
    n = len(cards)
    lines.append(f"共 {n} 项待办，点击对应动作卡片可完成/推迟/取消。")
    return "\n".join(lines)


def render_evening_digest(cards: list[CardView]) -> str:
    """晚间汇总：未完成动作清单。"""
    if not cards:
        return "## 🌙 晚间小结\n\n今天没有未完成的销售动作，辛苦了。\n"
    lines = ["## 🌙 晚间小结·未完成动作", ""]
    lines.extend(_render_digest_card_line(c) for c in cards)
    lines.append("")
    n = len(cards)
    lines.append(f"仍有 {n} 项未完成，可推迟到明天或标记完成。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# acknowledgement (button click result)
# ---------------------------------------------------------------------------


def render_acknowledgement(
    *,
    action_id: str,
    status: str,
    new_time: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> str:
    """按钮回执：complete / cancel / snooze 后的卡片正文。"""
    if status == "done":
        body = "已标记为 **完成** ✅。"
    elif status == "cancelled":
        body = "已 **取消** ❎。"
    elif status == "snoozed":
        when = _fmt_local(new_time, timezone) if new_time else "稍后"
        body = f"已 **推迟** 到 {when} ⏰。"
    else:
        body = f"状态已更新（{status}）。"
    return (
        f"## 销售动作更新\n\n{body}\n\n"
        f"<!-- action_id: {action_id} -->"
    )


# ---------------------------------------------------------------------------
# digest idempotency key
# ---------------------------------------------------------------------------


def build_digest_idempotency_key(
    kind: str,
    tenant_id: str,
    agent_id: str,
    user_id: str,
    day: date,
) -> str:
    """``{kind}:{tenant}:{agent}:{user}:{date.isoformat()}``。

    Brief Step 1 断言此格式逐字匹配。
    """
    return f"{kind}:{tenant_id}:{agent_id}:{user_id}:{day.isoformat()}"
