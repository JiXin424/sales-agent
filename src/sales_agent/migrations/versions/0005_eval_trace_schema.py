"""Add eval trace tables and extend existing eval tables with iteration columns.

Revision ID: 0005_eval_trace_schema
Revises: 0004_knowledge_iteration_foundation
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_eval_trace_schema"
down_revision: Union[str, None] = "0004_knowledge_iteration_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Extend eval_suites ---
    for col, col_type, server_default in [
        ("suite_type", sa.Text(), "fixed"),
        ("version", sa.Integer(), "1"),
        ("parent_suite_id", sa.Text(), None),
        ("generator_version", sa.Text(), None),
        ("knowledge_version_id", sa.Text(), None),
        ("generation_config_json", sa.Text(), "{}"),
        ("content_hash", sa.Text(), None),
    ]:
        op.add_column("eval_suites", sa.Column(col, col_type, nullable=True, server_default=sa.text(f"'{server_default}'") if server_default else None))

    # --- Extend eval_cases ---
    for col, col_type, server_default in [
        ("question_type", sa.Text(), None),
        ("answerability", sa.Text(), None),
        ("difficulty", sa.Text(), None),
        ("expected_answer", sa.Text(), None),
        ("required_facts_json", sa.Text(), "[]"),
        ("forbidden_claims_json", sa.Text(), "[]"),
        ("source_fact_ids_json", sa.Text(), "[]"),
        ("source_document_ids_json", sa.Text(), "[]"),
        ("expected_route", sa.Text(), None),
        ("role_type", sa.Text(), None),
        ("generation_strategy", sa.Text(), None),
        ("generator_version", sa.Text(), None),
        ("lineage_case_id", sa.Text(), None),
        ("quality_status", sa.Text(), "pending"),
    ]:
        op.add_column("eval_cases", sa.Column(col, col_type, nullable=True, server_default=sa.text(f"'{server_default}'") if server_default else None))

    # --- Extend eval_runs ---
    for col, col_type, server_default in [
        ("iteration_id", sa.Text(), None),
        ("candidate_id", sa.Text(), None),
        ("knowledge_version_id", sa.Text(), None),
        ("retrieval_profile_id", sa.Text(), None),
        ("router_profile_id", sa.Text(), None),
        ("judge_model", sa.Text(), None),
        ("judge_config_json", sa.Text(), "{}"),
        ("random_seed", sa.Integer(), None),
        ("run_type", sa.Text(), "fixed"),
        ("artifact_prefix", sa.Text(), None),
        ("heartbeat_at", sa.Text(), None),
    ]:
        op.add_column("eval_runs", sa.Column(col, col_type, nullable=True, server_default=sa.text(f"'{server_default}'") if server_default else None))
    op.create_index("ix_eval_runs_tenant_iter_cand", "eval_runs", ["tenant_id", "iteration_id", "candidate_id"])

    # --- Extend eval_run_results ---
    for col, col_type, server_default in [
        ("actual_output_json", sa.Text(), "{}"),
        ("route_type", sa.Text(), None),
        ("route_confidence", sa.Float(), None),
        ("retrieval_triggered", sa.Boolean(), None),
        ("retrieval_skip_reason", sa.Text(), None),
        ("rewritten_query", sa.Text(), None),
        ("source_count", sa.Integer(), None),
        ("fact_coverage", sa.Float(), None),
        ("forbidden_claim_count", sa.Integer(), None),
        ("token_usage_json", sa.Text(), "{}"),
        ("error_code", sa.Text(), None),
        ("trace_completeness", sa.Text(), "full"),
    ]:
        op.add_column("eval_run_results", sa.Column(col, col_type, nullable=True, server_default=sa.text(f"'{server_default}'") if server_default else None))

    # --- Create eval_metric_results ---
    op.create_table(
        "eval_metric_results",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("eval_run_result_id", sa.Text(), nullable=False),
        sa.Column("eval_run_id", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("metric_version", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("applicability", sa.Text(), nullable=False, server_default="applicable"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column("judge_config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("raw_output_ref", sa.Text(), nullable=True),
        sa.Column("eval_cost", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_emr_tenant_id", "eval_metric_results", ["tenant_id"])
    op.create_index("ix_emr_tenant_run", "eval_metric_results", ["tenant_id", "eval_run_id"])
    op.create_index("ix_emr_tenant_result", "eval_metric_results", ["tenant_id", "eval_run_result_id"])

    # --- Create retrieval_traces ---
    op.create_table(
        "retrieval_traces",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("eval_run_result_id", sa.Text(), nullable=False),
        sa.Column("retrieval_profile_id", sa.Text(), nullable=True),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("rewritten_queries_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("candidate_k", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("vector_weight", sa.Float(), nullable=True),
        sa.Column("keyword_weight", sa.Float(), nullable=True),
        sa.Column("rrf_constant", sa.Integer(), nullable=True),
        sa.Column("chunk_snapshot_hash", sa.Text(), nullable=True),
        sa.Column("retrieval_latency_ms", sa.Float(), nullable=True),
        sa.Column("retrieval_triggered", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rt_tenant_id", "retrieval_traces", ["tenant_id"])
    op.create_index("ix_rt_tenant_result", "retrieval_traces", ["tenant_id", "eval_run_result_id"])

    # --- Create retrieval_trace_hits ---
    op.create_table(
        "retrieval_trace_hits",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("retrieval_trace_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("document_revision_id", sa.Text(), nullable=True),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("channel_rank", sa.Integer(), nullable=False),
        sa.Column("channel_score", sa.Float(), nullable=True),
        sa.Column("final_rank", sa.Integer(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("selected_for_context", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_gold_document", sa.Boolean(), nullable=True),
        sa.Column("gold_fact_support", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rth_tenant_id", "retrieval_trace_hits", ["tenant_id"])
    op.create_index("ix_rth_trace_id", "retrieval_trace_hits", ["retrieval_trace_id"])
    op.create_index("ix_rth_tenant_trace_channel", "retrieval_trace_hits", ["tenant_id", "retrieval_trace_id", "channel", "channel_rank"])
    op.create_index("ix_rth_tenant_doc_revision", "retrieval_trace_hits", ["tenant_id", "document_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_rth_tenant_doc_revision", table_name="retrieval_trace_hits")
    op.drop_index("ix_rth_tenant_trace_channel", table_name="retrieval_trace_hits")
    op.drop_index("ix_rth_trace_id", table_name="retrieval_trace_hits")
    op.drop_index("ix_rth_tenant_id", table_name="retrieval_trace_hits")
    op.drop_table("retrieval_trace_hits")

    op.drop_index("ix_rt_tenant_result", table_name="retrieval_traces")
    op.drop_index("ix_rt_tenant_id", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")

    op.drop_index("ix_emr_tenant_result", table_name="eval_metric_results")
    op.drop_index("ix_emr_tenant_run", table_name="eval_metric_results")
    op.drop_index("ix_emr_tenant_id", table_name="eval_metric_results")
    op.drop_table("eval_metric_results")

    # --- Revert eval_run_results columns ---
    for col in (
        "trace_completeness", "error_code", "token_usage_json",
        "forbidden_claim_count", "fact_coverage", "source_count",
        "rewritten_query", "retrieval_skip_reason", "retrieval_triggered",
        "route_confidence", "route_type", "actual_output_json",
    ):
        op.drop_column("eval_run_results", col)

    op.drop_index("ix_eval_runs_tenant_iter_cand", table_name="eval_runs")
    for col in (
        "heartbeat_at", "artifact_prefix", "run_type", "random_seed",
        "judge_config_json", "judge_model", "router_profile_id",
        "retrieval_profile_id", "knowledge_version_id", "candidate_id",
        "iteration_id",
    ):
        op.drop_column("eval_runs", col)

    for col in (
        "quality_status", "lineage_case_id", "generator_version",
        "generation_strategy", "role_type", "expected_route",
        "source_document_ids_json", "source_fact_ids_json",
        "forbidden_claims_json", "required_facts_json", "expected_answer",
        "difficulty", "answerability", "question_type",
    ):
        op.drop_column("eval_cases", col)

    for col in (
        "content_hash", "generation_config_json", "knowledge_version_id",
        "generator_version", "parent_suite_id", "version", "suite_type",
    ):
        op.drop_column("eval_suites", col)
