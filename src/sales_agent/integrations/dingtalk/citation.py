"""钉钉回答末尾的引用来源格式化（方案 A：代码层拼接）。

来源类型映射：ontology → 知识图谱，web → 网络搜索，其余（含 rag 业务类型、空）→ 知识库。
LLM 不参与来源列表生成，标题原样取自 sources，保证精确。
"""
from __future__ import annotations

from typing import Any

_SOURCE_TYPE_LABELS = {
    "ontology": "知识图谱",
    "web": "网络搜索",
    "scenario_coach": "教练手册",
}
_DEFAULT_LABEL = "知识库"
_MAX_SOURCES = 3


def source_type_label(source_type: str | None) -> str:
    """source_type → 中文标签。未知 / 空 → 知识库。"""
    if not source_type:
        return _DEFAULT_LABEL
    # web_fallback_and_analyze emits "web_search"; accept any web* variant.
    if source_type.startswith("web"):
        return _SOURCE_TYPE_LABELS["web"]
    return _SOURCE_TYPE_LABELS.get(source_type, _DEFAULT_LABEL)


def _source_title(src: dict[str, Any]) -> str:
    title = src.get("display_title") or src.get("title")
    if title:
        return str(title)
    return source_type_label(src.get("source_type")) + "来源"


def format_citation_block(sources: list[dict[str, Any]] | None) -> str:
    """把 sources 格式化成文末引用块。

    规则：上限 3 条、按 title 去重、空 → 返回空串（不输出引用块）。
    """
    if not sources:
        return ""
    seen: set[str] = set()
    rows: list[str] = []
    for src in sources:
        title = _source_title(src)
        if title in seen:
            continue
        seen.add(title)
        label = source_type_label(src.get("source_type"))
        rows.append(f"[{len(rows) + 1}] {title} · {label}")
        if len(rows) >= _MAX_SOURCES:
            break
    if not rows:
        return ""
    # 钉钉互动卡片 markdown：\n\n 用于把引用块与正文分开（空行成段），
    # 引用条目之间用 <br> 紧凑换行（单 \n 会被折叠，\n\n 又会留空行）。
    return "\n\n────📖 引用来源─────<br>" + "<br>".join(rows)
