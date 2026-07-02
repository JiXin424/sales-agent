"""Add knowledge iteration foundation tables and chunk version columns.

Revision ID: 0004_knowledge_iteration_foundation
Revises: 0003_ontology_neo4j_metadata
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_knowledge_iteration_foundation"
down_revision: Union[str, None] = "0003_ontology_neo4j_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- document_revisions ---
    op.create_table(
        "document_revisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("parent_revision_id", sa.Text(), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("change_source", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("effective_start", sa.Text(), nullable=True),
        sa.Column("effective_end", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("creator_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_doc_rev_tenant_id", "document_revisions", ["tenant_id"])
    op.create_index("ix_doc_rev_agent_id", "document_revisions", ["agent_id"])
    op.create_index("ix_doc_rev_document_id", "document_revisions", ["document_id"])
    op.create_index("ix_doc_rev_tenant_document", "document_revisions", ["tenant_id", "document_id"])
    op.create_unique_constraint("uq_doc_rev_tenant_doc_number", "document_revisions", ["tenant_id", "document_id", "revision_number"])

    # --- knowledge_versions ---
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("parent_version_id", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("manifest_hash", sa.Text(), nullable=True),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kv_tenant_id", "knowledge_versions", ["tenant_id"])
    op.create_index("ix_kv_agent_id", "knowledge_versions", ["agent_id"])
    op.create_index("ix_kv_tenant_agent", "knowledge_versions", ["tenant_id", "agent_id"])
    op.create_unique_constraint("uq_kv_tenant_agent_version", "knowledge_versions", ["tenant_id", "agent_id", "version_number"])

    # --- knowledge_version_documents ---
    op.create_table(
        "knowledge_version_documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("knowledge_version_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("document_revision_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_kvd_tenant_id", "knowledge_version_documents", ["tenant_id"])
    op.create_index("ix_kvd_knowledge_version_id", "knowledge_version_documents", ["knowledge_version_id"])
    op.create_index("ix_kvd_document_revision_id", "knowledge_version_documents", ["document_revision_id"])
    op.create_index("ix_kvd_tenant_version", "knowledge_version_documents", ["tenant_id", "knowledge_version_id"])
    op.create_unique_constraint("uq_kvd_version_document", "knowledge_version_documents", ["knowledge_version_id", "document_id"])

    # --- retrieval_profiles ---
    op.create_table(
        "retrieval_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_profile_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("retrieval_mode", sa.Text(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("candidate_k", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("min_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("keyword_weight", sa.Float(), nullable=False, server_default="0.3"),
        sa.Column("rrf_constant", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("reranker", sa.Text(), nullable=True),
        sa.Column("query_rewrite_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tenant_synonyms_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("chunk_settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("config_hash", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rp_tenant_id", "retrieval_profiles", ["tenant_id"])
    op.create_index("ix_rp_agent_id", "retrieval_profiles", ["agent_id"])
    op.create_index("ix_rp_tenant_agent", "retrieval_profiles", ["tenant_id", "agent_id"])
    op.create_unique_constraint("uq_rp_tenant_agent_version", "retrieval_profiles", ["tenant_id", "agent_id", "version_number"])

    # --- router_profiles ---
    op.create_table(
        "router_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_profile_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("prompt_version_id", sa.Text(), nullable=True),
        sa.Column("rules_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default="0.6"),
        sa.Column("knowledge_trigger_rules_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("config_hash", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rtp_tenant_id", "router_profiles", ["tenant_id"])
    op.create_index("ix_rtp_agent_id", "router_profiles", ["agent_id"])
    op.create_index("ix_rtp_tenant_agent", "router_profiles", ["tenant_id", "agent_id"])
    op.create_unique_constraint("uq_rtp_tenant_agent_version", "router_profiles", ["tenant_id", "agent_id", "version_number"])

    # --- optimization_releases ---
    op.create_table(
        "optimization_releases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("release_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_release_id", sa.Text(), nullable=True),
        sa.Column("rollback_of_release_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("manifest_hash", sa.Text(), nullable=False),
        sa.Column("knowledge_version_id", sa.Text(), nullable=False),
        sa.Column("retrieval_profile_id", sa.Text(), nullable=False),
        sa.Column("router_profile_id", sa.Text(), nullable=False),
        sa.Column("prompt_set_id", sa.Text(), nullable=True),
        sa.Column("model_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("graph_definition_version", sa.Text(), nullable=True),
        sa.Column("code_revision", sa.Text(), nullable=True),
        sa.Column("iteration_id", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("rolled_back_by", sa.Text(), nullable=True),
        sa.Column("rolled_back_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rel_tenant_id", "optimization_releases", ["tenant_id"])
    op.create_index("ix_rel_agent_id", "optimization_releases", ["agent_id"])
    op.create_index("ix_rel_tenant_agent", "optimization_releases", ["tenant_id", "agent_id"])
    op.create_unique_constraint("uq_rel_tenant_agent_number", "optimization_releases", ["tenant_id", "agent_id", "release_number"])

    # --- agent_runtime_bindings ---
    op.create_table(
        "agent_runtime_bindings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("active_release_id", sa.Text(), nullable=False),
        sa.Column("previous_release_id", sa.Text(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("activated_at", sa.Text(), nullable=True),
        sa.Column("activated_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_runtime_binding_tenant_agent", "agent_runtime_bindings", ["tenant_id", "agent_id"])

    # --- release_events ---
    op.create_table(
        "release_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("release_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status_transition", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rel_ev_tenant_id", "release_events", ["tenant_id"])
    op.create_index("ix_rel_ev_release_id", "release_events", ["release_id"])
    op.create_index("ix_rel_ev_tenant_release", "release_events", ["tenant_id", "release_id"])

    # --- Extend document_chunks with version columns ---
    op.add_column("document_chunks", sa.Column("knowledge_version_id", sa.Text(), nullable=True))
    op.add_column("document_chunks", sa.Column("document_revision_id", sa.Text(), nullable=True))
    op.add_column("document_chunks", sa.Column("chunker_version", sa.Text(), nullable=True))
    op.add_column("document_chunks", sa.Column("chunk_config_hash", sa.Text(), nullable=True))
    op.create_index("ix_chunks_knowledge_version", "document_chunks", ["tenant_id", "knowledge_version_id"])
    op.create_index("ix_chunks_document_revision", "document_chunks", ["tenant_id", "document_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_document_revision", table_name="document_chunks")
    op.drop_index("ix_chunks_knowledge_version", table_name="document_chunks")
    op.drop_column("document_chunks", "chunk_config_hash")
    op.drop_column("document_chunks", "chunker_version")
    op.drop_column("document_chunks", "document_revision_id")
    op.drop_column("document_chunks", "knowledge_version_id")

    op.drop_index("ix_rel_ev_tenant_release", table_name="release_events")
    op.drop_index("ix_rel_ev_release_id", table_name="release_events")
    op.drop_index("ix_rel_ev_tenant_id", table_name="release_events")
    op.drop_table("release_events")

    op.drop_constraint("uq_runtime_binding_tenant_agent", "agent_runtime_bindings")
    op.drop_table("agent_runtime_bindings")

    op.drop_index("ix_rel_tenant_agent", table_name="optimization_releases")
    op.drop_index("ix_rel_agent_id", table_name="optimization_releases")
    op.drop_index("ix_rel_tenant_id", table_name="optimization_releases")
    op.drop_constraint("uq_rel_tenant_agent_number", "optimization_releases")
    op.drop_table("optimization_releases")

    op.drop_index("ix_rtp_tenant_agent", table_name="router_profiles")
    op.drop_index("ix_rtp_agent_id", table_name="router_profiles")
    op.drop_index("ix_rtp_tenant_id", table_name="router_profiles")
    op.drop_constraint("uq_rtp_tenant_agent_version", "router_profiles")
    op.drop_table("router_profiles")

    op.drop_index("ix_rp_tenant_agent", table_name="retrieval_profiles")
    op.drop_index("ix_rp_agent_id", table_name="retrieval_profiles")
    op.drop_index("ix_rp_tenant_id", table_name="retrieval_profiles")
    op.drop_constraint("uq_rp_tenant_agent_version", "retrieval_profiles")
    op.drop_table("retrieval_profiles")

    op.drop_index("ix_kvd_tenant_version", table_name="knowledge_version_documents")
    op.drop_index("ix_kvd_document_revision_id", table_name="knowledge_version_documents")
    op.drop_index("ix_kvd_knowledge_version_id", table_name="knowledge_version_documents")
    op.drop_index("ix_kvd_tenant_id", table_name="knowledge_version_documents")
    op.drop_constraint("uq_kvd_version_document", "knowledge_version_documents")
    op.drop_table("knowledge_version_documents")

    op.drop_index("ix_kv_tenant_agent", table_name="knowledge_versions")
    op.drop_index("ix_kv_agent_id", table_name="knowledge_versions")
    op.drop_index("ix_kv_tenant_id", table_name="knowledge_versions")
    op.drop_constraint("uq_kv_tenant_agent_version", "knowledge_versions")
    op.drop_table("knowledge_versions")

    op.drop_index("ix_doc_rev_tenant_document", table_name="document_revisions")
    op.drop_index("ix_doc_rev_document_id", table_name="document_revisions")
    op.drop_index("ix_doc_rev_agent_id", table_name="document_revisions")
    op.drop_index("ix_doc_rev_tenant_id", table_name="document_revisions")
    op.drop_constraint("uq_doc_rev_tenant_doc_number", "document_revisions")
    op.drop_table("document_revisions")
