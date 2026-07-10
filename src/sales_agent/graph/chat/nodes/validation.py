"""Request validation node.

Mirrors `services/request_validator.validate_chat_request()`.
"""

from __future__ import annotations

from sales_agent.graph.chat.state import ChatGraphState


def validate_node(state: ChatGraphState) -> dict:
    """Validate required fields in the incoming request.

    Args:
        state: Current graph state with input fields.

    Returns:
        Empty dict on success, or dict with `error` set on validation failure.
    """
    tenant_id = (state.get("tenant_id") or "").strip()
    user_id = (state.get("user_id") or "").strip()
    message = (state.get("message") or "").strip()

    if not tenant_id:
        return {"error": "tenant_id is required"}
    if not user_id:
        return {"error": "user_id is required"}
    if not message:
        return {"error": "message is required"}

    return {}
