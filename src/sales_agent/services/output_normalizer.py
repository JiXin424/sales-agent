"""输出契约标准化器 — 确保访前/访后卡片输出固定段落名称和顺序。

模型输出可能存在段落名称变化、遗漏、乱序等问题。
本模块在 model 输出解析后、返回给用户前进行标准化处理。
"""

from __future__ import annotations

import logging
from typing import Any

from sales_agent.services.task_router import VISIT_PREPARATION, POST_VISIT_REVIEW

logger = logging.getLogger(__name__)

# --- 访前作战卡固定段落 ---
PRE_VISIT_CANONICAL = [
    "本次沟通目标",
    "客户可能关心的 3 件事",
    "建议开场话术",
    "必问 3 个问题",
    "重点价值表达",
    "本次必须争取的下一步",
    "最大风险提醒",
    "可直接使用的收口话术",
]

# --- 访后机会推进卡固定段落 ---
POST_VISIT_CANONICAL = [
    "本次沟通结论",
    "客户真实需求判断",
    "当前机会状态",
    "最大卡点",
    "下一步最佳动作",
    "可直接发送的跟进话术",
    "是否需要管理者介入",
    "下次复盘观察点",
]

# --- 任务类型 → 固定段落映射 ---
CANONICAL_SECTIONS: dict[str, list[str]] = {
    VISIT_PREPARATION: PRE_VISIT_CANONICAL,
    POST_VISIT_REVIEW: POST_VISIT_CANONICAL,
}

# --- 模型常见变体 → 规范名称的别名映射 ---
SECTION_ALIASES: dict[str, dict[str, str]] = {
    VISIT_PREPARATION: {
        "沟通目标": "本次沟通目标",
        "拜访目标": "本次沟通目标",
        "本次拜访目标": "本次沟通目标",
        "客户关心的事": "客户可能关心的 3 件事",
        "客户关心的3件事": "客户可能关心的 3 件事",
        "客户可能关心的事": "客户可能关心的 3 件事",
        "客户可能关心的3件事": "客户可能关心的 3 件事",
        "开场": "建议开场话术",
        "开场话术": "建议开场话术",
        "必问问题": "必问 3 个问题",
        "探询问题": "必问 3 个问题",
        "三个问题": "必问 3 个问题",
        "必问的3个问题": "必问 3 个问题",
        "价值": "重点价值表达",
        "价值表达": "重点价值表达",
        "核心价值": "重点价值表达",
        "下一步": "本次必须争取的下一步",
        "下一步目标": "本次必须争取的下一步",
        "争取的下一步": "本次必须争取的下一步",
        "风险": "最大风险提醒",
        "风险提醒": "最大风险提醒",
        "收口": "可直接使用的收口话术",
        "收口话术": "可直接使用的收口话术",
        "结束话术": "可直接使用的收口话术",
    },
    POST_VISIT_REVIEW: {
        "沟通结论": "本次沟通结论",
        "结论": "本次沟通结论",
        "本次拜访结论": "本次沟通结论",
        "真实需求": "客户真实需求判断",
        "需求判断": "客户真实需求判断",
        "客户需求": "客户真实需求判断",
        "机会状态": "当前机会状态",
        "机会分类": "当前机会状态",
        "卡点": "最大卡点",
        "阻碍": "最大卡点",
        "最大阻碍": "最大卡点",
        "成交阻碍": "最大卡点",
        "下一步动作": "下一步最佳动作",
        "最佳动作": "下一步最佳动作",
        "下一步行动": "下一步最佳动作",
        "跟进话术": "可直接发送的跟进话术",
        "跟进消息": "可直接发送的跟进话术",
        "消息草稿": "可直接发送的跟进话术",
        "管理者": "是否需要管理者介入",
        "需要管理者": "是否需要管理者介入",
        "管理者介入": "是否需要管理者介入",
        "复盘观察": "下次复盘观察点",
        "观察点": "下次复盘观察点",
        "下次观察": "下次复盘观察点",
    },
}

# --- 缺失段落兜底内容 ---
FALLBACK_CONTENT = "当前信息不足，建议补充后再优化。"


def _resolve_title(task_type: str, raw_title: str) -> str | None:
    """将模型输出的段落标题映射为规范标题。

    先精确匹配，再查别名表，最后做子串包含匹配。
    返回 None 表示无法映射（属于额外段落）。
    """
    canonical = CANONICAL_SECTIONS.get(task_type, [])
    aliases = SECTION_ALIASES.get(task_type, {})

    # 1. 精确匹配规范标题
    if raw_title in canonical:
        return raw_title

    # 2. 精确匹配别名
    if raw_title in aliases:
        return aliases[raw_title]

    # 3. 子串包含匹配（别名键是规范标题的子串）
    for alias_key, canonical_title in aliases.items():
        if alias_key in raw_title or raw_title in alias_key:
            return canonical_title

    # 4. 规范标题子串匹配
    for c_title in canonical:
        if c_title in raw_title or raw_title in c_title:
            return c_title

    return None


def normalize_answer(task_type: str, answer: dict[str, Any]) -> dict[str, Any]:
    """标准化卡片类任务输出，确保固定段落名称和顺序。

    对于非卡片任务类型，原样返回不做修改。
    对于卡片类型：
    1. 通过别名映射统一段落标题
    2. 按规范顺序排列段落
    3. 缺失段落插入兜底内容
    4. 额外段落追加到末尾
    5. 设置 card_type 字段

    Args:
        task_type: 任务类型字符串
        answer: 模型输出的结构化回答 dict（含 summary 和 sections）

    Returns:
        标准化后的回答 dict
    """
    if task_type not in CANONICAL_SECTIONS:
        return answer

    canonical_titles = CANONICAL_SECTIONS[task_type]
    sections = answer.get("sections", [])

    # 将已有段落映射为 {canonical_title: content}
    mapped: dict[str, str] = {}
    extra_sections: list[dict[str, str]] = []

    for section in sections:
        raw_title = section.get("title", "")
        content = section.get("content", "")
        resolved = _resolve_title(task_type, raw_title)

        if resolved is not None:
            # 如果同一段落出现多次，保留内容更长的
            if resolved in mapped:
                if len(content) > len(mapped[resolved]):
                    mapped[resolved] = content
            else:
                mapped[resolved] = content
        else:
            extra_sections.append(section)

    # 按规范顺序重建 sections
    new_sections: list[dict[str, str]] = []
    for title in canonical_titles:
        content = mapped.get(title, FALLBACK_CONTENT)
        new_sections.append({"title": title, "content": content})

    # 追加额外段落
    if extra_sections:
        # 合并额外段落为"补充建议"
        extra_lines = []
        for s in extra_sections:
            t = s.get("title", "")
            c = s.get("content", "")
            if t and c:
                extra_lines.append(f"**{t}**：{c}")
            elif c:
                extra_lines.append(c)
        if extra_lines:
            new_sections.append({
                "title": "补充建议",
                "content": "\n".join(extra_lines),
            })

    # 构建标准化后的 answer
    normalized = dict(answer)
    normalized["sections"] = new_sections
    normalized["card_type"] = task_type

    # 更新 summary 前缀
    summary = normalized.get("summary", "")
    if task_type == VISIT_PREPARATION and "访前作战卡" not in summary:
        normalized["summary"] = f"访前作战卡 | {summary}" if summary else "访前作战卡"
    elif task_type == POST_VISIT_REVIEW and "访后机会推进卡" not in summary:
        normalized["summary"] = f"访后机会推进卡 | {summary}" if summary else "访后机会推进卡"

    logger.debug(
        "Normalized card output: task=%s canonical=%d extra=%d",
        task_type, len(new_sections) - (1 if extra_sections else 0), len(extra_sections),
    )

    return normalized
