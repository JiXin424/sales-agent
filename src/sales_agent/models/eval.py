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

    __table_args__ = (
        Index("ix_eval_runs_tenant_suite", "tenant_id", "eval_suite_id"),
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

    __table_args__ = (
        Index("ix_eval_run_results_run", "eval_run_id"),
    )
