"""Capture only public outbound delivery from DingTalk staging (Spec 4 §3.4).

The staging runner drives ``handle_dingtalk_event`` with staging users and
databases. The processor's ``reply_fn`` may be invoked with internal/audit
text (error acknowledgements, memory-write notifications) that must NEVER be
scored as user-facing output. :class:`PublicReplyCapture` is the async
``reply_fn`` the staging runner installs: it keeps only the public outbound
messages, dropping anything prefixed with ``[internal]``, ``[audit]``, or
``[memory-internal]`` (case-insensitive).
"""
from __future__ import annotations

import re

# Messages with these prefixes are internal bookkeeping (audit trails, memory
# write notifications, profile updates) and are never shown to the user. The
# staging capture must exclude them so the trajectory/conversation metrics only
# score genuinely public delivery.
_INTERNAL_PREFIXES = (
    "[internal]",
    "[audit]",
    "[memory-internal]",
)

# Case-insensitive regex fallback for variants like ``[INTERNAL]`` or ``[Audit]``.
_INTERNAL_RE = re.compile(r"^\[(internal|audit|memory-internal)\]", re.IGNORECASE)


class PublicReplyCapture:
    """An async ``reply_fn`` that keeps only public outbound messages.

    Installed as the ``reply_fn`` for ``handle_dingtalk_event`` in the staging
    runner. After each turn, ``public_replies`` holds the public text that was
    actually delivered, and ``kinds`` holds the per-reply delivery kind.
    """

    def __init__(self) -> None:
        self.public_replies: list[str] = []
        self.kinds: list[str] = []

    async def reply(self, text: str, *, kind: str = "text") -> None:
        stripped = (text or "").strip()
        # Drop empty and internal/audit messages — they are not public delivery.
        if not stripped:
            return
        if any(stripped.startswith(p) for p in _INTERNAL_PREFIXES):
            return
        if _INTERNAL_RE.match(stripped):
            return
        self.public_replies.append(stripped)
        self.kinds.append(kind)


__all__ = ["PublicReplyCapture"]
