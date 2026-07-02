"""Add iteration observability and report persistence tables.

Revision ID: 0008_iteration_observability_reports
Revises: 0007_knowledge_facts
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_iteration_observability_reports"
down_revision: Union[str, None] = "0007_knowledge_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _make_timestamps():
    return [
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # -- Extend optimization_iterations with observability columns --
    op.add_column(
        "optimization_iterations",
        sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("current_stage", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("progress_current", sa.Integer(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("progress_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("selected_candidate_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("published_release_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("post_publish_eval_run_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("final_report_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("parent_iteration_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "optimization_iterations",
        sa.Column("forked_from_checkpoint_id", sa.Text(), nullable=True),
    )

    # -- iteration_events --
    op.create_table(
        "iteration_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("iteration_id", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=True),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("actor_type", sa.Text(), nullable=False, server_default="system"),
        sa.Column("actor_id", sa.Text(), nullable=True),
        *_make_timestamps(),
    )
    op.create_index("ix_iev_tenant_id", "iteration_events", ["tenant_id"])
    op.create_index("ix_iev_iteration_id", "iteration_events", ["iteration_id"])
    op.create_index("ix_iev_tenant_iteration", "iteration_events", ["tenant_id", "iteration_id"])
    op.create_index("ix_iev_tenant_agent", "iteration_events", ["tenant_id", "agent_id", "created_at"])
    op.create_unique_constraint(
        "uq_iev_tenant_iteration_seq",
        "iteration_events",
        ["tenant_id", "iteration_id", "sequence_no"],
    )

    # -- iteration_reports --
    op.create_table(
        "iteration_reports",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("iteration_id", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("release_id", sa.Text(), nullable=True),
        sa.Column("baseline_eval_run_id", sa.Text(), nullable=True),
        sa.Column("candidate_eval_run_id", sa.Text(), nullable=True),
        sa.Column("report_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("formula_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="generating"),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("effect_index_before", sa.Float(), nullable=True),
        sa.Column("effect_index_after", sa.Float(), nullable=True),
        sa.Column("effect_index_delta", sa.Float(), nullable=True),
        sa.Column("hard_gates_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("risk_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("trend_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("data_snapshot_hash", sa.Text(), nullable=True),
        sa.Column("artifact_uris_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("artifact_hashes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        *_make_timestamps(),
    )
    op.create_index("ix_irep_tenant_id", "iteration_reports", ["tenant_id"])
    op.create_index("ix_irep_iteration_id", "iteration_reports", ["iteration_id"])
    op.create_index("ix_irep_tenant_agent", "iteration_reports", ["tenant_id", "agent_id", "created_at"])
    op.create_unique_constraint(
        "uq_irep_tenant_iteration_type_candidate_ver",
        "iteration_reports",
        ["tenant_id", "iteration_id", "report_type", "candidate_key", "report_version"],
    )

    # -- iteration_report_metrics --
    op.create_table(
        "iteration_report_metrics",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("before_value", sa.Float(), nullable=True),
        sa.Column("after_value", sa.Float(), nullable=True),
        sa.Column("before_normalized", sa.Float(), nullable=True),
        sa.Column("after_normalized", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("threshold_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("applicable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("gate_result", sa.Text(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        *_make_timestamps(),
    )
    op.create_index("ix_irepm_tenant_id", "iteration_report_metrics", ["tenant_id"])
    op.create_index("ix_irepm_report_id", "iteration_report_metrics", ["report_id"])
    op.create_index("ix_irepm_tenant_report", "iteration_report_metrics", ["tenant_id", "report_id"])

    # -- iteration_report_cases --
    op.create_table(
        "iteration_report_cases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("case_name", sa.Text(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("result_id", sa.Text(), nullable=True),
        sa.Column("before_pass", sa.Boolean(), nullable=True),
        sa.Column("after_pass", sa.Boolean(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=False, server_default="unchanged"),
        sa.Column("cause", sa.Text(), nullable=True),
        sa.Column("score_delta", sa.Float(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("rank_delta", sa.Integer(), nullable=True),
        sa.Column("latency_delta_ms", sa.Float(), nullable=True),
        sa.Column("token_delta", sa.Integer(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        *_make_timestamps(),
    )
    op.create_index("ix_irepc_tenant_id", "iteration_report_cases", ["tenant_id"])
    op.create_index("ix_irepc_report_id", "iteration_report_cases", ["report_id"])
    op.create_index("ix_irepc_tenant_report", "iteration_report_cases", ["tenant_id", "report_id"])


def downgrade() -> None:
    # Drop in reverse order
    op.drop_index("ix_irepc_tenant_report", table_name="iteration_report_cases")
    op.drop_index("ix_irepc_report_id", table_name="iteration_report_cases")
    op.drop_index("ix_irepc_tenant_id", table_name="iteration_report_cases")
    op.drop_table("iteration_report_cases")

    op.drop_index("ix_irepm_tenant_report", table_name="iteration_report_metrics")
    op.drop_index("ix_irepm_report_id", table_name="iteration_report_metrics")
    op.drop_index("ix_irepm_tenant_id", table_name="iteration_report_metrics")
    op.drop_table("iteration_report_metrics")

    op.drop_index("ix_irep_tenant_agent", table_name="iteration_reports")
    op.drop_index("ix_irep_iteration_id", table_name="iteration_reports")
    op.drop_index("ix_irep_tenant_id", table_name="iteration_reports")
    op.drop_constraint(
        "uq_irep_tenant_iteration_type_candidate_ver",
        "iteration_reports",
    )
    op.drop_table("iteration_reports")

    op.drop_index("ix_iev_tenant_agent", table_name="iteration_events")
    op.drop_index("ix_iev_tenant_iteration", table_name="iteration_events")
    op.drop_index("ix_iev_iteration_id", table_name="iteration_events")
    op.drop_index("ix_iev_tenant_id", table_name="iteration_events")
    op.drop_constraint("uq_iev_tenant_iteration_seq", "iteration_events")
    op.drop_table("iteration_events")

    # Remove added columns from optimization_iterations
    op.drop_column("optimization_iterations", "forked_from_checkpoint_id")
    op.drop_column("optimization_iterations", "parent_iteration_id")
    op.drop_column("optimization_iterations", "final_report_id")
    op.drop_column("optimization_iterations", "post_publish_eval_run_id")
    op.drop_column("optimization_iterations", "published_release_id")
    op.drop_column("optimization_iterations", "selected_candidate_id")
    op.drop_column("optimization_iterations", "progress_total")
    op.drop_column("optimization_iterations", "progress_current")
    op.drop_column("optimization_iterations", "current_stage")
    op.drop_column("optimization_iterations", "event_sequence")
