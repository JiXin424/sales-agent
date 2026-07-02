"""Evaluation trace models: metric results, retrieval traces, and trace hits.

Extends the existing eval tables with granular per-metric results and
ranked retrieval evidence for failure attribution.
"""

from sqlalchemy import Text, Float, Integer, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, generate_id


class EvalMetricResult(TimestampMixin, Base):
    """One row per evaluated case and metric.

    Stores normalized score, applicability, reason, and judge configuration
    so attribution can distinguish "not applicable" from "failed."
    """

    __tablename__ = "eval_metric_results"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_run_result_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_version: Mapped[str] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    applicability: Mapped[str] = mapped_column(
        Text, nullable=False, default="applicable"
    )  # applicable / not_applicable / invalid
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    raw_output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_emr_tenant_run", "tenant_id", "eval_run_id"),
        Index("ix_emr_tenant_result", "tenant_id", "eval_run_result_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<EvalMetricResult(metric={self.metric_name}, "
            f"applicability={self.applicability}, score={self.score})>"
        )


class RetrievalTrace(TimestampMixin, Base):
    """One row per evaluated retrieval call.

    Captures the retrieval configuration, query, channel weights, and
    latency so attribution can determine whether retrieval fired and how.
    """

    __tablename__ = "retrieval_traces"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_run_result_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    retrieval_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_query: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_queries_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    candidate_k: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    vector_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    keyword_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    rrf_constant: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_snapshot_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_rt_tenant_result", "tenant_id", "eval_run_result_id"),
    )

    def __repr__(self) -> str:
        return f"<RetrievalTrace(query={self.original_query[:40]}, hits_see below)>"


class RetrievalTraceHit(TimestampMixin, Base):
    """One row per candidate chunk and channel in a retrieval trace.

    Records per-channel rank/score and final RRF rank/score so attribution
    can distinguish recall from ranking failures.
    """

    __tablename__ = "retrieval_trace_hits"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    retrieval_trace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # vector / keyword
    channel_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_for_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_gold_document: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gold_fact_support: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    __table_args__ = (
        Index("ix_rth_tenant_trace_channel", "tenant_id", "retrieval_trace_id", "channel", "channel_rank"),
        Index("ix_rth_tenant_doc_revision", "tenant_id", "document_revision_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<RetrievalTraceHit(ch={self.channel}, "
            f"ch_rank={self.channel_rank}, final_rank={self.final_rank})>"
        )
