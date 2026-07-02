"""评估回归测试模型。"""

from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from sales_agent.core.database import Base
from sales_agent.models.base import TimestampMixin, generate_id


class EvalSuite(TimestampMixin, Base):
    """评估测试套件。

    定义一组评估用例，可从 JSONL fixture 文件加载。
    """

    __tablename__ = "eval_suites"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    fixture_path: Mapped[str] = mapped_column(Text, nullable=True)
    case_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active"
    )  # active / archived
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # --- Knowledge iteration columns ---
    suite_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="fixed"
    )  # fixed / exploration
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    parent_suite_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_eval_suites_tenant_agent_name_ver", "tenant_id", "agent_id", "name", "version", unique=True),
    )


class EvalCase(TimestampMixin, Base):
    """评估测试用例。

    每个用例定义输入文本和期望的输出行为。
    """

    __tablename__ = "eval_cases"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_suite_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(Text, nullable=False)  # JSONL 中的 case_id
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_task_type: Mapped[str] = mapped_column(Text, nullable=True)
    expected_risk_level: Mapped[str] = mapped_column(Text, nullable=True)
    must_include_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    must_not_include_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # --- Knowledge iteration columns ---
    question_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    answerability: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_facts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    forbidden_claims_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_fact_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_document_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    expected_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    lineage_case_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending"
    )  # pending / quarantined / accepted


class EvalRun(TimestampMixin, Base):
    """评估运行记录。

    一次评估套件的执行结果，记录整体通过/失败计数和配置快照。
    """

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    eval_suite_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="running"
    )  # running / completed / failed
    total_cases: Mapped[int] = mapped_column(nullable=False, default=0)
    passed: Mapped[int] = mapped_column(nullable=False, default=0)
    failed: Mapped[int] = mapped_column(nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(nullable=False, default=0)
    prompt_version_id: Mapped[str] = mapped_column(Text, nullable=True)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[str] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str] = mapped_column(Text, nullable=True)
    # --- Knowledge iteration columns ---
    iteration_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    router_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    random_seed: Mapped[int | None] = mapped_column(nullable=True)
    run_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="fixed"
    )  # fixed / exploration / targeted / sibling / safety / cross_tenant
    artifact_prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    heartbeat_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_eval_runs_tenant_suite", "tenant_id", "eval_suite_id"),
        Index("ix_eval_runs_tenant_iter_cand", "tenant_id", "iteration_id", "candidate_id"),
    )


class EvalRunResult(TimestampMixin, Base):
    """评估运行单条结果。

    记录每个测试用例的实际输出与期望对比结果。
    """

    __tablename__ = "eval_run_results"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_id)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    eval_case_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=True)
    passed: Mapped[str] = mapped_column(Text, nullable=False, default="false")  # "true" / "false"
    actual_task_type: Mapped[str] = mapped_column(Text, nullable=True)
    actual_risk_level: Mapped[str] = mapped_column(Text, nullable=True)
    route_match: Mapped[str] = mapped_column(Text, nullable=False, default="true")  # "true" / "false"
    content_checks_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failure_reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    latency_ms: Mapped[int] = mapped_column(nullable=True)
    # --- Knowledge iteration columns ---
    actual_output_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    route_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    route_confidence: Mapped[float | None] = mapped_column(nullable=True)
    retrieval_triggered: Mapped[bool | None] = mapped_column(nullable=True)
    retrieval_skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewritten_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_count: Mapped[int | None] = mapped_column(nullable=True)
    fact_coverage: Mapped[float | None] = mapped_column(nullable=True)
    forbidden_claim_count: Mapped[int | None] = mapped_column(nullable=True)
    token_usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_completeness: Mapped[str] = mapped_column(
        Text, nullable=False, default="full"
    )  # full / partial / missing

    __table_args__ = (
        Index("ix_eval_run_results_run", "eval_run_id"),
    )
