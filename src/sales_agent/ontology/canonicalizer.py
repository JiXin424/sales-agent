from __future__ import annotations

import re


_SPACE_RE = re.compile(r"\s+")


def canonical_key(name: str) -> str:
    """Build a stable key for entity de-duplication."""
    return _SPACE_RE.sub("", (name or "").strip().lower())


def normalize_aliases(aliases: list[str] | None) -> list[str]:
    """Trim aliases, remove blanks, de-duplicate while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases or []:
        clean = alias.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out
