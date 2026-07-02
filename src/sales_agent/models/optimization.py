"""Optimization workflow models: iterations, diagnoses, candidates, evals,
checkpoint mappings, and idempotent jobs.
"""

from sqlalchemy import Text, Integer, Float, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class OptimizationIteration(TimestampMixin, Base):
    """Root record for one optimization cycle per tenant/Agent."""

    __tablename__ = "optimization_iterations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    iteration_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    # Baseline references
    baseline_release_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_knowledge_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    fixed_suite_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    exploration_suite_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_eval_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Budget
    max_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    allowed_change_types_json: Mapped[str] = mapped_column(Text, nullable=False, default='["router","retrieval","document"]')
    # Progress
    token_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    heartbeat_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "iteration_no", name="uq_iter_tenant_agent_no"),
        Index("ix_iter_tenant_agent", "tenant_id", "agent_id"),
    )

    def __repr__(self) -> str:
        return f"<OptimizationIteration(tenant={self.tenant_id}, agent={self.agent_id}, no={self.iteration_no})>"


class FailureDiagnosis(TimestampMixin, Base):
    """Persisted diagnosis for a failure cluster."""

    __tablename__ = "failure_diagnoses"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    cluster_key: Mapped[str] = mapped_column(Text, nullable=False)
    primary_cause: Mapped[str] = mapped_column(Text, nullable=False)
    secondary_causes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    affected_case_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    blocked_checks_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False, default="human_review")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    diagnoser_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_fd_tenant_iteration", "tenant_id", "iteration_id"),
    )

    def __repr__(self) -> str:
        return f"<FailureDiagnosis(cause={self.primary_cause}, confidence={self.confidence})>"


class OptimizationCandidate(TimestampMixin, Base):
    """A proposed one-category optimization experiment."""

    __tablename__ = "optimization_candidates"

    ALLOWED_CHANGE_TYPES = {"router", "retrieval", "document"}

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    diagnosis_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    document_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_variables_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    patch_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sandbox references
    sandbox_knowledge_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_retrieval_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_router_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_oc_tenant_iteration", "tenant_id", "iteration_id"),
    )

    def __repr__(self) -> str:
        return f"<OptimizationCandidate(type={self.change_type}, attempt={self.attempt_number})>"


class CandidateEvalRun(TimestampMixin, Base):
    """Maps candidates to staged eval runs (targeted, sibling, fixed, safety, cross_tenant)."""

    __tablename__ = "candidate_eval_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False)  # targeted / sibling / fixed / safety / cross_tenant
    decision: Mapped[str] = mapped_column(Text, nullable=False, default="pending")  # pending / passed / failed
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint("candidate_id", "eval_run_id", name="uq_cer_candidate_eval"),
        Index("ix_cer_tenant_candidate", "tenant_id", "candidate_id"),
    )

    def __repr__(self) -> str:
        return f"<CandidateEvalRun(candidate={self.candidate_id}, stage={self.stage})>"


class IterationGraphCheckpoint(TimestampMixin, Base):
    """Maps optimization iteration/candidate stages to LangGraph thread/checkpoint IDs."""

    __tablename__ = "iteration_graph_checkpoints"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "thread_id", "checkpoint_id", name="uq_igc_tenant_thread_checkpoint"),
        Index("ix_igc_tenant_iteration", "tenant_id", "iteration_id"),
    )

    def __repr__(self) -> str:
        return f"<IterationGraphCheckpoint(stage={self.stage}, thread={self.thread_id})>"


class OptimizationJob(TimestampMixin, Base):
    """Idempotent, lease-based job queue for the optimization worker."""

    __tablename__ = "optimization_jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")  # queued / running / completed / failed
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_oj_tenant_idempotency"),
        Index("ix_oj_tenant_iteration", "tenant_id", "iteration_id"),
        Index("ix_oj_status_lease", "status", "lease_expires_at"),
    )

    def __repr__(self) -> str:
        return f"<OptimizationJob(stage={self.stage}, status={self.status}, key={self.idempotency_key})>"
