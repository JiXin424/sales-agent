"""DingTalk channel Agent resolution — extract the bound Agent for a tenant's DingTalk channel.

Returns the bound active Agent when present; otherwise falls back to the
tenant default Agent (backward compatible with tenants that haven't bound
a channel-level Agent yet).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent import Agent
from sales_agent.models.agent_channel_config import AgentChannelConfig
from sales_agent.services.agent_service import resolve_tenant_agent_id

logger = logging.getLogger(__name__)


async def resolve_dingtalk_agent_id(db: AsyncSession, tenant_id: str) -> str:
    """Resolve the Agent ID bound to the DingTalk channel for *tenant_id*.

    Looks up the ``dingtalk`` channel binding in ``AgentChannelConfig``
    that is in ``configured`` or ``verified`` status.  Returns the bound
    Agent's ID if the Agent is still ``active``; otherwise falls back to
    ``resolve_tenant_agent_id(db, tenant_id, None)`` and returns that
    Agent's ID.

    Args:
        db: Async SQLAlchemy session.
        tenant_id: Tenant identifier.

    Returns:
        An Agent ID string.
    """
    dt_agent_id: str | None = None
    try:
        row = (
            await db.execute(
                select(AgentChannelConfig).where(
                    AgentChannelConfig.tenant_id == tenant_id,
                    AgentChannelConfig.channel == "dingtalk",
                    AgentChannelConfig.status.in_(("configured", "verified")),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            a = (
                await db.execute(
                    select(Agent).where(Agent.id == row.agent_id)
                )
            ).scalar_one_or_none()
            if a is not None and a.status == "active":
                dt_agent_id = a.id
    except Exception as e:
        logger.warning("DingTalk channel->agent resolution failed: %s", e)

    if dt_agent_id is not None:
        return dt_agent_id

    agent = await resolve_tenant_agent_id(db, tenant_id, None)
    return agent.id
