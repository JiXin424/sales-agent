"""Release service types: exceptions, manifest, and result containers."""

from __future__ import annotations

from dataclasses import dataclass, field


class ReleaseError(Exception):
    """Base exception for release operations."""


class ReleaseNotFound(ReleaseError):
    """A release with the given ID and tenant does not exist."""


class StaleRuntimeBinding(ReleaseError):
    """The runtime binding's lock_version does not match the expected value."""


class ReleaseNotDraft(ReleaseError):
    """Only draft releases may be activated."""


@dataclass
class ReleaseManifest:
    """Frozen snapshot of the full release configuration."""

    id: str
    tenant_id: str
    agent_id: str | None
    release_number: int
    knowledge_version_id: str
    retrieval_profile_id: str
    router_profile_id: str
    manifest_hash: str
    model_snapshot_json: str = "{}"
    graph_definition_version: str | None = None
    code_revision: str | None = None


@dataclass
class ActivateResult:
    """Result of a successful activation."""
    binding_id: str
    release_id: str
    previous_release_id: str | None
    new_lock_version: int
