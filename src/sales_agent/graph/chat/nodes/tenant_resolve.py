"""Tenant resolution node.

Calls TenantResolver via DB session from Runtime.context.
"""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from sales_agent.graph.chat.state import ChatGraphState
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)


async def resolve_tenant_node(state: ChatGraphState, runtime: Runtime) -> dict:
    """Resolve tenant info and model provider from DB.

    Requires ``runtime.context["db"]`` (AsyncSession) to be set by the caller.

    Args:
        state: Current graph state.
        runtime: LangGraph runtime with context containing ``db``.

    Returns:
        Dict with ``tenant_info`` populated.
    """
    db = runtime.context.get("db")
    if db is None:
        logger.warning("No DB session in runtime.context, using passthrough")
        return {"tenant_info": {"tenant_id": state["tenant_id"], "config": {}}}

    resolver = TenantResolver(db)
    tenant_info = await resolver.resolve(state["tenant_id"])

    return {"tenant_info": tenant_info}
