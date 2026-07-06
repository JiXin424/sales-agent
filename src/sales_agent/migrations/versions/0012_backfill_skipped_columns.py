"""Backfill columns skipped by the init_db create_all/stamp-head race.

Revision ID: 0012_backfill_skipped_columns
Revises: 0011_topic_memory
Create Date: 2026-07-06

背景
----
``init_db`` 先跑 ``Base.metadata.create_all`` 再跑 ``alembic upgrade head``。当一个
migration 同时含 ``create_table``（新表）与 ``add_column``（老表加列）时，``create_all``
会抢先建好那张新表（因为 ORM model 里已有它），导致 alembic ``upgrade`` 撞
``DuplicateTableError``，进而触发 ``_run_auto_migrations`` 的 ``stamp head`` 兜底——
于是同一 migration 里剩余的 ``add_column`` 被整体跳过，老表的新列永远不会被加上，
而 ``alembic_version`` 却已标到 head，形成「版本号到位、schema 没到位」的幽灵漂移。

典型受害者：prod3 的 ``conversation_messages.topic_id``（来自 0011），stream 走 graph
新路径在 ``context_load`` 查询 ``topic_id`` 时列不存在，整个事务进入 aborted 状态，
后续所有 SQL 报 ``InFailedSQLTransactionError``，钉钉 stream 持续失败。同源漂移还波及
``document_chunks`` / ``eval_*`` / ``retrieval_profiles`` 等表（不影响 stream，但需一并补齐）。

本 revision 用 ``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` 幂等补齐
所有被跳过的列与索引：schema 已完整的环境（prod2 等）执行为 no-op，缺列环境（prod3）
真正补上。列定义与 prod2（schema 权威库）信息架构导出的值一致。

根因（``_run_auto_migrations`` 的 stamp 兜底）由本 revision 之外的代码改动修复；
本 revision 只负责把已经漂移的 schema 拉回正轨。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0012_backfill_skipped_columns"
down_revision: Union[str, None] = "0011_topic_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (表名, 列 DDL 片段，不含前导 ALTER/ADD COLUMN)。用 raw SQL 因 alembic op.add_column
# 不支持 IF NOT EXISTS；server_default 与 prod2 信息架构导出的值保持一致。
_BACKFILL_COLUMNS: list[tuple[str, str]] = [
    # 0011_topic_memory 残留：conversation_messages.topic_id（stream 阻塞点）
    ("conversation_messages", "topic_id TEXT"),
    # document_chunks（0010_ontology_retrieval_profile 等）
    ("document_chunks", "knowledge_version_id TEXT"),
    ("document_chunks", "document_revision_id TEXT"),
    ("document_chunks", "chunker_version TEXT"),
    ("document_chunks", "chunk_config_hash TEXT"),
    # eval_cases
    ("eval_cases", "question_type TEXT"),
    ("eval_cases", "answerability TEXT"),
    ("eval_cases", "difficulty TEXT"),
    ("eval_cases", "expected_answer TEXT"),
    ("eval_cases", "required_facts_json TEXT DEFAULT '[]'"),
    ("eval_cases", "forbidden_claims_json TEXT DEFAULT '[]'"),
    ("eval_cases", "source_fact_ids_json TEXT DEFAULT '[]'"),
    ("eval_cases", "source_document_ids_json TEXT DEFAULT '[]'"),
    ("eval_cases", "expected_route TEXT"),
    ("eval_cases", "role_type TEXT"),
    ("eval_cases", "generation_strategy TEXT"),
    ("eval_cases", "generator_version TEXT"),
    ("eval_cases", "lineage_case_id TEXT"),
    ("eval_cases", "quality_status TEXT DEFAULT 'pending'"),
    # eval_run_results
    ("eval_run_results", "actual_output_json TEXT DEFAULT '{}'"),
    ("eval_run_results", "route_type TEXT"),
    ("eval_run_results", "route_confidence DOUBLE PRECISION"),
    ("eval_run_results", "retrieval_triggered BOOLEAN"),
    ("eval_run_results", "retrieval_skip_reason TEXT"),
    ("eval_run_results", "rewritten_query TEXT"),
    ("eval_run_results", "source_count INTEGER"),
    ("eval_run_results", "fact_coverage DOUBLE PRECISION"),
    ("eval_run_results", "forbidden_claim_count INTEGER"),
    ("eval_run_results", "token_usage_json TEXT DEFAULT '{}'"),
    ("eval_run_results", "error_code TEXT"),
    ("eval_run_results", "trace_completeness TEXT DEFAULT 'full'"),
    # eval_runs
    ("eval_runs", "iteration_id TEXT"),
    ("eval_runs", "candidate_id TEXT"),
    ("eval_runs", "knowledge_version_id TEXT"),
    ("eval_runs", "retrieval_profile_id TEXT"),
    ("eval_runs", "router_profile_id TEXT"),
    ("eval_runs", "judge_model TEXT"),
    ("eval_runs", "judge_config_json TEXT DEFAULT '{}'"),
    ("eval_runs", "random_seed INTEGER"),
    ("eval_runs", "run_type TEXT DEFAULT 'fixed'"),
    ("eval_runs", "artifact_prefix TEXT"),
    ("eval_runs", "heartbeat_at TEXT"),
    # eval_suites
    ("eval_suites", "suite_type TEXT DEFAULT 'fixed'"),
    ("eval_suites", "version INTEGER DEFAULT 1"),
    ("eval_suites", "parent_suite_id TEXT"),
    ("eval_suites", "generator_version TEXT"),
    ("eval_suites", "knowledge_version_id TEXT"),
    ("eval_suites", "generation_config_json TEXT DEFAULT '{}'"),
    ("eval_suites", "content_hash TEXT"),
    # retrieval_profiles（NOT NULL，带 server_default 回填既有行）
    ("retrieval_profiles", "entity_limit INTEGER NOT NULL DEFAULT 42"),
    ("retrieval_profiles", "facts_per_entity INTEGER NOT NULL DEFAULT 20"),
    ("retrieval_profiles", "max_entities_for_prompt INTEGER NOT NULL DEFAULT 10"),
    ("retrieval_profiles", "max_facts_for_prompt INTEGER NOT NULL DEFAULT 43"),
    ("retrieval_profiles", "vector_fallback_top_k INTEGER NOT NULL DEFAULT 8"),
]

# (索引名, 表名, 列定义)。与 prod2 pg_indexes 导出的 indexdef 一致（默认 btree）。
_BACKFILL_INDEXES: list[tuple[str, str, str]] = [
    ("ix_conversation_messages_topic_id", "conversation_messages", "topic_id"),
    ("ix_chunks_document_revision", "document_chunks", "tenant_id, document_revision_id"),
    ("ix_chunks_knowledge_version", "document_chunks", "tenant_id, knowledge_version_id"),
    ("ix_eval_runs_tenant_iter_cand", "eval_runs", "tenant_id, iteration_id, candidate_id"),
    ("ix_rp_agent_id", "retrieval_profiles", "agent_id"),
    ("ix_rp_tenant_id", "retrieval_profiles", "tenant_id"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # ADD/CREATE IF NOT EXISTS 是 PG 专属语法；其它方言由 create_all 兜底，此处跳过。
        return

    for table, col_ddl in _BACKFILL_COLUMNS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_ddl}")

    for index_name, table, columns in _BACKFILL_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})")


def downgrade() -> None:
    # No-op 且刻意为之：这些列/索引本属于更早的 migration（0010/0011 等），本 revision
    # 仅补救被 init_db stamp-head 兜底跳过的 DDL，并不「拥有」它们。downgrade 删除会破坏
    # schema 已完整的环境（prod2 等）。如需回退到 0011 之前的 schema，请 downgrade 对应的
    # 原始 migration。
    pass
