"""Optimization API authentication and authorization.

Defines the OptimizationPrincipal, scope/policy helpers, and bearer token
parsing. MCP callers are identified by bearer tokens; Web/CLI callers are
operator principals with full access.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.core.database import get_db

from collections.abc import AsyncGenerator


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session."""
    async for session in get_db():
        yield session

from sales_agent.models.optimization_auth import OptimizationApiCredential
from sales_agent.security.optimization_credentials import verify_token, utcnow

logger = logging.getLogger(__name__)

# ── Principal ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OptimizationPrincipal:
    """Identity and permissions for an optimization API caller."""

    tenant_id: str
    subject: str
    credential_id: str
    agent_ids: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    actor_type: str = "claude_code_mcp"

    @classmethod
    def operator(cls, tenant_id: str) -> OptimizationPrincipal:
        """Full-access principal for Web/CLI (not MCP)."""
        return cls(
            tenant_id=tenant_id,
            subject="operator",
            credential_id="__operator__",
            agent_ids=frozenset(),
            scopes=frozenset({
                "iteration:start", "iteration:read",
                "candidate:request", "evaluation:rerun",
                "approval:write", "publish:write", "rollback:write",
            }),
            actor_type="operator",
        )

    @property
    def is_operator(self) -> bool:
        return self.actor_type == "operator"

    @property
    def is_mcp(self) -> bool:
        return self.actor_type == "claude_code_mcp"


# ── Scope enforcement ────────────────────────────────────────────────────────


class OptimizationAuthorizationError(HTTPException):
    """403 with structured error code for MCP clients."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(
            status_code=403,
            detail={"error": {"code": code, "detail": detail}},
        )


HUMAN_ONLY_ERROR = OptimizationAuthorizationError(
    "human_approval_required",
    "Approval, publish, and rollback require a human operator",
)

SCOPE_DENIED_ERROR = OptimizationAuthorizationError(
    "scope_denied",
    "This credential does not have the required scope",
)


def require_scope(principal: OptimizationPrincipal, scope: str) -> None:
    """Raise SCOPE_DENIED_ERROR if *principal* lacks *scope*."""
    if scope not in principal.scopes:
        raise SCOPE_DENIED_ERROR


def require_human_principal(principal: OptimizationPrincipal) -> None:
    """Raise HUMAN_ONLY_ERROR if *principal* is an MCP identity."""
    if not principal.is_operator:
        raise HUMAN_ONLY_ERROR


def require_agent_ownership(
    principal: OptimizationPrincipal, agent_id: str,
) -> None:
    """Raise 404 if *principal* cannot access *agent_id*."""
    if not principal.is_operator and agent_id not in principal.agent_ids:
        raise HTTPException(status_code=404, detail="Agent not found")


# ── Bearer token authentication ──────────────────────────────────────────────


async def authenticate_bearer(
    db: AsyncSession, authorization: str | None,
) -> OptimizationPrincipal | None:
    """Parse a Bearer token and return a principal, or None if absent/bad."""
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None

    token = authorization[7:].strip()
    if not token or not token.startswith("saopt_"):
        return None

    import json

    # Look up by token prefix
    prefix = token[6:14]  # first 8 chars after "saopt_"
    credential = await db.scalar(
        select(OptimizationApiCredential).where(
            OptimizationApiCredential.token_prefix == prefix,
        )
    )
    if credential is None:
        return None

    # Verify token hash
    if not verify_token(token, credential.token_hash):
        return None

    # Check expiry/revocation
    from datetime import datetime, timezone
    if credential.revoked_at:
        return None
    if credential.expires_at:
        try:
            if credential.expires_at < datetime.now(timezone.utc).isoformat():
                return None
        except ValueError:
            pass

    # Update last-used
    credential.last_used_at = utcnow()
    await db.flush()

    agent_ids = frozenset(json.loads(credential.agent_ids_json or "[]"))
    scopes = frozenset(json.loads(credential.scopes_json or "[]"))

    return OptimizationPrincipal(
        tenant_id=credential.tenant_id,
        subject=credential.subject,
        credential_id=credential.id,
        agent_ids=agent_ids,
        scopes=scopes,
        actor_type="claude_code_mcp",
    )


async def get_optimization_principal(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> OptimizationPrincipal:
    """Resolve principal from Authorization header or fallback to operator."""
    principal = await authenticate_bearer(db, authorization)
    if principal is not None:
        return principal
    return OptimizationPrincipal.operator(tenant_id="")
