"""Optimization API authentication models: scoped credentials and command audit.

Credentials are tenant-scoped with allowed Agent and scope allowlists.
Only token hashes are stored. Plaintext tokens are returned once at issuance.
"""

from sqlalchemy import Text, Integer, Boolean, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class OptimizationApiCredential(TimestampMixin, Base):
    """Revokable tenant-scoped optimization API credential.

    Tokens are stored as scrypt hashes. The lookup prefix is the first 8
    characters of the token enough for DB indexing but insufficient to
    authenticate.
    """

    __tablename__ = "optimization_api_credentials"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    token_prefix: Mapped[str] = mapped_column(Text, nullable=False)  # first 8 chars
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)  # scrypt
    agent_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    scopes_json: Mapped[str] = mapped_column(
        Text, nullable=False,
        default='["iteration:start","iteration:read","candidate:request","evaluation:rerun"]',
    )
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "subject", name="uq_oac_tenant_subject"),
        Index("ix_oac_tenant_token_prefix", "tenant_id", "token_prefix"),
        Index("ix_oac_tenant_agent_idx", "tenant_id", "agent_ids_json"),
    )

    def __repr__(self) -> str:
        return (
            f"<OptimizationApiCredential(subject={self.subject}, "
            f"tenant={self.tenant_id})>"
        )


class OptimizationCommandAudit(TimestampMixin, Base):
    """Immutable audit record for every mutating MCP call."""

    __tablename__ = "optimization_command_audits"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    credential_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sanitized_input_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    outcome: Mapped[str] = mapped_column(Text, nullable=False, default="success")
    error_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_oca_tenant_credential", "tenant_id", "credential_id"),
        Index("ix_oca_tenant_action", "tenant_id", "action", "created_at"),
        UniqueConstraint(
            "tenant_id", "credential_id", "idempotency_key",
            name="uq_oca_tenant_credential_idempotency",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<OptimizationCommandAudit(action={self.action}, "
            f"outcome={self.outcome})>"
        )
