from __future__ import annotations

import re

from sales_agent.services.memory.profile_repository import StoredUserMemoryProfile


_TRANSPARENCY_PATTERNS = [
    re.compile(r"(你|系统|助手).*(记得|保存).*(我|我的).*(什么|哪些|信息)?"),
    re.compile(r"(你记得我什么|你还记得哪些关于我的信息)"),
]


def detect_transparency_command(text: str) -> bool:
    stripped = text.strip()
    return any(pattern.search(stripped) for pattern in _TRANSPARENCY_PATTERNS)


def _append_value(
    lines: list[str],
    title: str,
    value,
    memory_ids: list[str] | None,
) -> None:
    if value is None or value == [] or value == "":
        return
    if isinstance(value, list):
        rendered_value = "、".join(str(item) for item in value)
    else:
        rendered_value = str(value)
    ids = "、".join(memory_ids or [])
    suffix = f"（记忆ID：{ids}）" if ids else ""
    lines.append(f"- {title}：{rendered_value}{suffix}")


def render_memory_transparency(profile: StoredUserMemoryProfile | None) -> str:
    if profile is None:
        return '我目前还没有保存关于你的长期记忆。你可以说“记住我负责华东区”这类明确指令来保存。'

    data = profile.profile
    evidence = profile.evidence_map
    lines = [f"我目前记得这些（资料版本 {profile.version}）：", ""]

    lines.append("工作背景")
    _append_value(
        lines, "角色", data["work_context"].get("role"),
        evidence.get("work_context.role"),
    )
    _append_value(
        lines, "负责区域", data["work_context"].get("sales_region"),
        evidence.get("work_context.sales_region"),
    )
    _append_value(
        lines, "关注产品", data["work_context"].get("product_focus"),
        evidence.get("work_context.product_focus"),
    )

    lines.append("")
    lines.append("回复偏好")
    _append_value(
        lines, "详略偏好", data["response_preferences"].get("verbosity"),
        evidence.get("response_preferences.verbosity"),
    )
    _append_value(
        lines, "格式偏好", data["response_preferences"].get("format"),
        evidence.get("response_preferences.format"),
    )
    _append_value(
        lines, "教练风格", data["response_preferences"].get("coaching_style"),
        evidence.get("response_preferences.coaching_style"),
    )

    lines.append("")
    lines.append("成长目标")
    _append_value(
        lines, "教练目标", data["development"].get("coaching_goals"),
        evidence.get("development.coaching_goals"),
    )
    _append_value(
        lines, "常见卡点", data["development"].get("recurring_challenges"),
        evidence.get("development.recurring_challenges"),
    )
    _append_value(
        lines, "有效打法", data["development"].get("confirmed_sales_patterns"),
        evidence.get("development.confirmed_sales_patterns"),
    )

    visible_items = [line for line in lines if line.startswith("- ")]
    if not visible_items:
        return "我目前还没有可展示的长期记忆。你可以明确告诉我需要记住的稳定偏好或工作背景。"

    lines.append("")
    lines.append(
        '如果不准确，你可以直接说“我不负责华东了，现在负责华南”，'
        '或“忘记我的区域信息”。'
    )
    return "\n".join(lines)
