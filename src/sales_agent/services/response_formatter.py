"""响应格式化服务。"""

from __future__ import annotations

import re
from typing import Any

from sales_agent.core.config import get_settings

# ── 置信度表述过滤 ──────────────────────────────────────────────
# LLM 有时会忽略 prompt 中"正文无需标注置信度"的指令，自行在回答
# 中插入"置信度90%"等表述。以下几组正则用于兜底移除这些编造的数字。
_CONFIDENCE_PATTERNS: list[re.Pattern] = [
    # （置信度90%）/ (置信度 90%)  — 括号包裹
    re.compile(r"[（(]\s*置信度\s*\d+\s*%?\s*[)）]"),
    re.compile(r"[（(]\s*可信度\s*\d+\s*%?\s*[)）]"),
    # 置信度：90% / 置信度: 90%
    re.compile(r"置信度\s*[：:]\s*\d+\s*%?"),
    re.compile(r"可信度\s*[：:]\s*\d+\s*%?"),
    # 置信度 90% / 置信度90%（无冒号无括号，跟在句末或独立出现）
    re.compile(r"置信度\s*\d+\s*%"),
    re.compile(r"可信度\s*\d+\s*%"),
    # 英文变体：confidence: 90%
    re.compile(r"[Cc]onfidence\s*[：:]\s*\d+\s*%?"),
]


def _strip_confidence_patterns(text: str) -> str:
    """移除 LLM 自行编造的置信度百分比表述。

    仅移除包含数字百分比的模式（如"置信度90%"），不会误伤不含数字
    的普通"置信度"一词（如文档标题中出现的情况）。
    """
    for pat in _CONFIDENCE_PATTERNS:
        text = pat.sub("", text)
    # 清理残留：空括号、多余空格、连续标点
    text = re.sub(r"[（(]\s*[)）]", "", text)      # 空括号 （）
    text = re.sub(r"\s{2,}", " ", text)             # 多余空格
    text = re.sub(r"[,，]\s*[,，]", "，", text)     # 连续逗号
    text = re.sub(r"[,，]\s+", "，", text)          # 逗号后多余空格归一
    text = re.sub(r"\s+[,，]", "，", text)          # 逗号前多余空格归一
    text = re.sub(r"[,，]{2,}", "，", text)         # 多重逗号
    return text.strip()


def format_text_output(answer: dict[str, Any]) -> str:
    """将结构化回答转换为面向销售的纯文本输出。

    对应 spec 8.2 节的文本输出模板。

    会自动清理 LLM 自行编造的置信度百分比表述（兜底 prompt 规则）。
    """
    lines = []
    summary = _strip_confidence_patterns(answer.get("summary", ""))
    if summary:
        lines.append(summary)
        lines.append("")

    sections = answer.get("sections", [])
    for section in sections:
        title = section.get("title", "")
        content = _strip_confidence_patterns(section.get("content", ""))
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
