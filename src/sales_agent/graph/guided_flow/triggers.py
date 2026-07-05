from __future__ import annotations

import unicodedata


ACTION_TO_FLOW = {
    "visit_preparation": "visit_preparation",
    "post_visit_review": "post_visit_review",
    "small_win_appreciation": "small_win_appreciation",
    "sales_block_breakthrough": "sales_block_breakthrough",
}

PHRASE_TO_FLOW = {
    "访前准备": "visit_preparation",
    "访后复盘": "post_visit_review",
    "小赢欣赏": "small_win_appreciation",
    "卡点破框": "sales_block_breakthrough",
}

CANCEL_COMMANDS = frozenset({"退出", "取消", "结束", "cancel", "exit", "/exit", "/cancel"})


def normalize_explicit_text(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().lower()


def resolve_requested_flow(*, message: str | None, entry_action: str | None) -> str | None:
    action = normalize_explicit_text(entry_action)
    if action in ACTION_TO_FLOW:
        return ACTION_TO_FLOW[action]
    return PHRASE_TO_FLOW.get(normalize_explicit_text(message))


def is_cancel_command(message: str | None) -> bool:
    return normalize_explicit_text(message) in CANCEL_COMMANDS
