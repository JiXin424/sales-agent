"""Iteration observability models: durable events, effect reports, metrics, and
case-level classifications.

Every table is tenant-scoped. Events are append-only with monotonic sequence
numbers allocated atomically via ``optimization_iterations.event_sequence``.
Reports bind to pinned evaluation runs and formula versions so they are
deterministically reproducible.
"""

from sqlalchemy import Text, Integer, Float, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class IterationEvent(TimestampMixin, Base):
    """Immutable ordered event for one optimization iteration."""

    __tablename__ = "iteration_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "iteration_id", "sequence_no",
            name="uq_iev_tenant_iteration_seq",
        ),
        Index("ix_iev_tenant_iteration", "tenant_id", "iteration_id"),
        Index("ix_iev_tenant_agent", "tenant_id", "agent_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<IterationEvent(seq={self.sequence_no}, type={self.event_type}, "
            f"stage={self.stage})>"
        )


class IterationReport(TimestampMixin, Base):
    """Immutable effect report for a candidate or post-publish evaluation."""

    __tablename__ = "iteration_reports"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    iteration_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)  # candidate / final
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_key: Mapped[str] = mapped_column(Text, nullable=False)  # candidate_id or "__final__"
    release_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_eval_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_eval_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    formula_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="generating")
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    effect_index_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    effect_index_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    effect_index_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_gates_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    risk_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    trend_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    data_snapshot_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_uris_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    artifact_hashes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "iteration_id", "report_type", "candidate_key",
            "report_version", name="uq_irep_tenant_iteration_type_candidate_ver",
        ),
        Index("ix_irep_tenant_agent", "tenant_id", "agent_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<IterationReport(type={self.report_type}, "
            f"candidate_key={self.candidate_key}, v={self.report_version})>"
        )


class IterationReportMetric(TimestampMixin, Base):
    """Per-metric row inside an iteration report."""

    __tablename__ = "iteration_report_metrics"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # higher / lower
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    before_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    before_normalized: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_normalized: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gate_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_irepm_tenant_report", "tenant_id", "report_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<IterationReportMetric(group={self.group_name}, "
            f"metric={self.metric_name}, delta={self.delta})>"
        )


class IterationReportCase(TimestampMixin, Base):
    """Per-eval-case classification row inside an iteration report."""

    __tablename__ = "iteration_report_cases"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    report_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    after_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    classification: Mapped[str] = mapped_column(
        Text, nullable=False, default="unchanged"
    )  # improved / regressed / unchanged / new / error
    cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    rank_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_delta_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_irepc_tenant_report", "tenant_id", "report_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<IterationReportCase(case={self.case_id}, "
            f"classification={self.classification})>"
        )
