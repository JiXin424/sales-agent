from __future__ import annotations

import re
from typing import Any


_KEY_PATTERNS: list[tuple[str, str]] = [
    (r"(负责|区域|大区|华东|华南|华北|华中|西南|东北)", "sales_region"),
    (r"(短一点|简洁|长一点|详细|表格|要点|语气|风格)", "response_style"),
    (r"(目标|提升|训练|练习|能力|异议处理|开场|逼单)", "coaching_goal"),
    (r"(产品线|主推|重点产品|卖.*产品)", "product_focus"),
    (r"(反复|经常|总是|卡点|难点|挑战)", "recurring_challenge"),
]


def normalize_key(text: str) -> str:
    compact = text.strip().lower()
    for pattern, key in _KEY_PATTERNS:
        if re.search(pattern, compact):
            return key
    safe = re.sub(r"[^0-9a-zA-Z一-鿿]+", "_", compact).strip("_")
    return safe[:64] or "unknown"


def build_search_text(content: dict[str, Any]) -> str:
    values: list[str] = []
    for key in sorted(content):
        value = content[key]
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (int, float, bool)):
            values.append(str(value))
    return " ".join(values).strip()
