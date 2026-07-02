"""Add optimization workflow tables.

Revision ID: 0006_optimization_workflow
Revises: 0005_eval_trace_schema
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_optimization_workflow"
down_revision: Union[str, None] = "0005_eval_trace_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _make_timestamps():
    return [
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # optimization_iterations
    op.create_table("optimization_iterations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("iteration_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("baseline_release_id", sa.Text(), nullable=True),
        sa.Column("baseline_knowledge_version_id", sa.Text(), nullable=True),
        sa.Column("fixed_suite_id", sa.Text(), nullable=True),
        sa.Column("exploration_suite_id", sa.Text(), nullable=True),
        sa.Column("baseline_eval_run_id", sa.Text(), nullable=True),
        sa.Column("max_candidates", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_consecutive_failures", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("allowed_change_types_json", sa.Text(), nullable=False, server_default='["router","retrieval","document"]'),
        sa.Column("token_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("heartbeat_at", sa.Text(), nullable=True),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        *_make_timestamps(),
    )
    op.create_index("ix_iter_tenant_id", "optimization_iterations", ["tenant_id"])
    op.create_index("ix_iter_tenant_agent", "optimization_iterations", ["tenant_id", "agent_id"])
    op.create_unique_constraint("uq_iter_tenant_agent_no", "optimization_iterations", ["tenant_id", "agent_id", "iteration_no"])

    # failure_diagnoses
    op.create_table("failure_diagnoses",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("iteration_id", sa.Text(), nullable=False),
        sa.Column("cluster_key", sa.Text(), nullable=False),
        sa.Column("primary_cause", sa.Text(), nullable=False),
        sa.Column("secondary_causes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("affected_case_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("blocked_checks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("recommended_action", sa.Text(), nullable=False, server_default="human_review"),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("diagnoser_version", sa.Text(), nullable=True),
        *_make_timestamps(),
    )
    op.create_index("ix_fd_tenant_id", "failure_diagnoses", ["tenant_id"])
    op.create_index("ix_fd_iteration_id", "failure_diagnoses", ["iteration_id"])
    op.create_index("ix_fd_tenant_iteration", "failure_diagnoses", ["tenant_id", "iteration_id"])

    # optimization_candidates
    op.create_table("optimization_candidates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("iteration_id", sa.Text(), nullable=False),
        sa.Column("diagnosis_id", sa.Text(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("patch_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("document_diff", sa.Text(), nullable=True),
        sa.Column("changed_variables_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("patch_hash", sa.Text(), nullable=True),
        sa.Column("sandbox_knowledge_version_id", sa.Text(), nullable=True),
        sa.Column("sandbox_retrieval_profile_id", sa.Text(), nullable=True),
        sa.Column("sandbox_router_profile_id", sa.Text(), nullable=True),
        sa.Column("creator_id", sa.Text(), nullable=True),
        *_make_timestamps(),
    )
    op.create_index("ix_oc_tenant_id", "optimization_candidates", ["tenant_id"])
    op.create_index("ix_oc_iteration_id", "optimization_candidates", ["iteration_id"])
    op.create_index("ix_oc_tenant_iteration", "optimization_candidates", ["tenant_id", "iteration_id"])

    # candidate_eval_runs
    op.create_table("candidate_eval_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.Text(), nullable=False),
        sa.Column("eval_run_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
        *_make_timestamps(),
    )
    op.create_index("ix_cer_tenant_id", "candidate_eval_runs", ["tenant_id"])
    op.create_index("ix_cer_candidate_id", "candidate_eval_runs", ["candidate_id"])
    op.create_index("ix_cer_tenant_candidate", "candidate_eval_runs", ["tenant_id", "candidate_id"])
    op.create_unique_constraint("uq_cer_candidate_eval", "candidate_eval_runs", ["candidate_id", "eval_run_id"])

    # iteration_graph_checkpoints
    op.create_table("iteration_graph_checkpoints",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("iteration_id", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=True),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("manifest_hash", sa.Text(), nullable=True),
        *_make_timestamps(),
    )
    op.create_index("ix_igc_tenant_id", "iteration_graph_checkpoints", ["tenant_id"])
    op.create_index("ix_igc_iteration_id", "iteration_graph_checkpoints", ["iteration_id"])
    op.create_index("ix_igc_tenant_iteration", "iteration_graph_checkpoints", ["tenant_id", "iteration_id"])
    op.create_unique_constraint("uq_igc_tenant_thread_checkpoint", "iteration_graph_checkpoints", ["tenant_id", "thread_id", "checkpoint_id"])

    # optimization_jobs
    op.create_table("optimization_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("iteration_id", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        *_make_timestamps(),
    )
    op.create_index("ix_oj_tenant_id", "optimization_jobs", ["tenant_id"])
    op.create_index("ix_oj_iteration_id", "optimization_jobs", ["iteration_id"])
    op.create_index("ix_oj_tenant_iteration", "optimization_jobs", ["tenant_id", "iteration_id"])
    op.create_index("ix_oj_status_lease", "optimization_jobs", ["status", "lease_expires_at"])
    op.create_unique_constraint("uq_oj_tenant_idempotency", "optimization_jobs", ["tenant_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_index("ix_oj_status_lease", table_name="optimization_jobs")
    op.drop_index("ix_oj_tenant_iteration", table_name="optimization_jobs")
    op.drop_index("ix_oj_iteration_id", table_name="optimization_jobs")
    op.drop_index("ix_oj_tenant_id", table_name="optimization_jobs")
    op.drop_constraint("uq_oj_tenant_idempotency", "optimization_jobs")
    op.drop_table("optimization_jobs")

    op.drop_index("ix_igc_tenant_iteration", table_name="iteration_graph_checkpoints")
    op.drop_index("ix_igc_iteration_id", table_name="iteration_graph_checkpoints")
    op.drop_index("ix_igc_tenant_id", table_name="iteration_graph_checkpoints")
    op.drop_constraint("uq_igc_tenant_thread_checkpoint", "iteration_graph_checkpoints")
    op.drop_table("iteration_graph_checkpoints")

    op.drop_index("ix_cer_tenant_candidate", table_name="candidate_eval_runs")
    op.drop_index("ix_cer_candidate_id", table_name="candidate_eval_runs")
    op.drop_index("ix_cer_tenant_id", table_name="candidate_eval_runs")
    op.drop_constraint("uq_cer_candidate_eval", "candidate_eval_runs")
    op.drop_table("candidate_eval_runs")

    op.drop_index("ix_oc_tenant_iteration", table_name="optimization_candidates")
    op.drop_index("ix_oc_iteration_id", table_name="optimization_candidates")
    op.drop_index("ix_oc_tenant_id", table_name="optimization_candidates")
    op.drop_table("optimization_candidates")

    op.drop_index("ix_fd_tenant_iteration", table_name="failure_diagnoses")
    op.drop_index("ix_fd_iteration_id", table_name="failure_diagnoses")
    op.drop_index("ix_fd_tenant_id", table_name="failure_diagnoses")
    op.drop_table("failure_diagnoses")

    op.drop_index("ix_iter_tenant_agent", table_name="optimization_iterations")
    op.drop_index("ix_iter_tenant_id", table_name="optimization_iterations")
    op.drop_constraint("uq_iter_tenant_agent_no", "optimization_iterations")
    op.drop_table("optimization_iterations")
