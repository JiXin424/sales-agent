-- Agent Instance migration (2026-06-15)
-- Adds nullable agent_id to runtime/operations/config tables.
-- New Agent tables (agents, agent_prompt_sets, agent_knowledge_scopes,
-- agent_risk_policies, agent_channel_configs, agent_clone_manifests) are created
-- by the application via Base.metadata.create_all; this script only backfills
-- agent_id columns onto existing tables for dedicated/shared deployments.
--
-- Run after creating Agent tables, then run the Python backfill:
--   python3 -m sales_agent.cli agent-migrate-defaults
-- (or ensure_default_agents(db) wired into init_db handles it automatically).
-- Idempotent: ADD COLUMN IF NOT EXISTS.

-- Conversations & retrieval & messages
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_conversations_agent_id ON conversations (agent_id);

ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_retrieval_logs_agent_id ON retrieval_logs (agent_id);

ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_conversation_messages_agent_id ON conversation_messages (agent_id);

ALTER TABLE conversation_summaries ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_conversation_summaries_agent_id ON conversation_summaries (agent_id);

-- Runs & steps
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_id ON agent_runs (agent_id);

ALTER TABLE agent_run_steps ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_agent_run_steps_agent_id ON agent_run_steps (agent_id);

-- Feedback, review, knowledge gaps
ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_feedbacks_agent_id ON feedbacks (agent_id);

ALTER TABLE review_items ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_review_items_agent_id ON review_items (agent_id);

ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_knowledge_gaps_agent_id ON knowledge_gaps (agent_id);

-- Model call logs
ALTER TABLE model_call_logs ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_model_call_logs_agent_id ON model_call_logs (agent_id);

-- Eval
ALTER TABLE eval_suites ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_eval_suites_agent_id ON eval_suites (agent_id);

ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_eval_runs_agent_id ON eval_runs (agent_id);

-- Alerts & alert rules
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_alerts_agent_id ON alerts (agent_id);

ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_alert_rules_agent_id ON alert_rules (agent_id);

-- Pilot reports
ALTER TABLE pilot_reports ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_pilot_reports_agent_id ON pilot_reports (agent_id);

-- Knowledge documents / chunks / source files
ALTER TABLE documents ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_documents_agent_id ON documents (agent_id);

ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_document_chunks_agent_id ON document_chunks (agent_id);

ALTER TABLE source_files ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_source_files_agent_id ON source_files (agent_id);

-- Prompt versions (agent-scoped prompt set copies)
ALTER TABLE prompt_versions ADD COLUMN IF NOT EXISTS agent_id TEXT;
CREATE INDEX IF NOT EXISTS ix_prompt_versions_agent_id ON prompt_versions (agent_id);
