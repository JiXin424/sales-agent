"""Release resolution node: pins immutable release manifest IDs into graph state.

Resolves the active release binding for the tenant/Agent once at graph entry.
All downstream nodes consume the pinned IDs — they never resolve "current active
configuration" from the database after initialization.
"""

from __future__ import annotations

from typing import Any

from sales_agent.services.release_types import ReleaseManifest


def _build_release_state(
    state: dict[str, Any],
    manifest: ReleaseManifest | None,
) -> dict[str, Any]:
    """Build the state update from a resolved (or missing) release manifest.

    When the feature flag is disabled or no binding exists, all version IDs
    are ``None`` and the compatibility path is used.
    """
    if manifest is None:
        return {
            "release_id": None,
            "knowledge_version_id": None,
            "retrieval_profile_id": None,
            "router_profile_id": None,
            "release_manifest_hash": None,
        }

    return {
        "release_id": manifest.id,
        "knowledge_version_id": manifest.knowledge_version_id,
        "retrieval_profile_id": manifest.retrieval_profile_id,
        "router_profile_id": manifest.router_profile_id,
        "release_manifest_hash": manifest.manifest_hash,
    }


async def resolve_release_node(
    state: dict[str, Any],
    runtime: Any = None,
) -> dict[str, Any]:
    """LangGraph node: resolve the active release for the current tenant/Agent.

    Reads ``tenant_id`` and ``agent_id`` from state, looks up the active
    runtime binding via the release service, and pins the manifest IDs.

    Args:
        state: Current graph state containing ``tenant_id`` and ``agent_id``.
        runtime: Optional pre-configured runtime context with db session
            and feature flags.

    Returns:
        State update with version-pinned IDs.
    """
    tenant_id: str = state.get("tenant_id", "")
    agent_id: str | None = state.get("agent_id")

    # Feature flag guard: if knowledge_iteration is disabled, skip resolution
    if runtime is not None:
        feature_flags = getattr(runtime, "feature_flags", {}) or {}
        if not feature_flags.get("knowledge_iteration", False):
            return _build_release_state(state, None)

    # Without a runtime/db handle, return None (compatibility path)
    if runtime is None:
        return _build_release_state(state, None)

    db = getattr(runtime, "db", None)
    if db is None:
        return _build_release_state(state, None)

    if not tenant_id or not agent_id:
        return _build_release_state(state, None)

    try:
        from sqlalchemy import select
        from sales_agent.models.runtime_release import AgentRuntimeBinding
        from sales_agent.services.release_service import ReleaseService

        service = ReleaseService(db)
        binding = await db.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.tenant_id == tenant_id,
                AgentRuntimeBinding.agent_id == agent_id,
            )
        )
        if binding is None:
            return _build_release_state(state, None)

        manifest = await service.get_manifest(tenant_id, binding.active_release_id)
        return _build_release_state(state, manifest)
    except Exception:
        # On any error, fall back to compatibility path
        return _build_release_state(state, None)
