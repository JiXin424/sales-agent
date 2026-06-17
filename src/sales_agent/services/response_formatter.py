"""响应格式化服务。"""

from __future__ import annotations

from typing import Any

from sales_agent.core.config import get_settings


def format_text_output(answer: dict[str, Any]) -> str:
    """将结构化回答转换为面向销售的纯文本输出。

    对应 spec 8.2 节的文本输出模板。
    """
    lines = []
    summary = answer.get("summary", "")
    if summary:
        lines.append(summary)
        lines.append("")

    sections = answer.get("sections", [])
    for section in sections:
        title = section.get("title", "")
        content = section.get("content", "")
        if title and content:
            lines.append(f"{title}：")
            lines.append(content)
            lines.append("")

    return "\n".join(lines).strip()


def format_sales_visible_sources(sources: list[dict], max_visible: int = 3) -> str:
    """生成销售可见的来源文本。

    v0 只展示来源标题（spec 11.5.2 方案 A）。
    """
    if not sources:
        return ""

    settings = get_settings()
    max_visible = max_visible or settings.source_display.max_visible_sources

    visible = sources[:max_visible]
    lines = ["来源："]
    for i, source in enumerate(visible, 1):
        title = source.get("display_title") or source.get("title", "未知来源")
        lines.append(f"{i}. 《{title}》")

    if len(sources) > max_visible:
        lines.append(f"（共 {len(sources)} 条来源）")

    return "\n".join(lines)


def build_chat_response(
    conversation_id: str,
    tenant_id: str,
    task_type: str,
    answer: dict[str, Any],
    sources: list[dict] | None = None,
    risk: dict | None = None,
    debug: dict | None = None,
) -> dict[str, Any]:
    """构建完整的 Chat API 响应。

    对应 spec 8.1 节的 HTTP API 响应格式。
    """
    return {
        "conversation_id": conversation_id,
        "tenant_id": tenant_id,
        "task_type": task_type,
        "answer": answer,
        "sources": sources or [],
        "risk": risk or {"level": "none", "flags": [], "action": "allow"},
        "debug": debug or {},
    }
