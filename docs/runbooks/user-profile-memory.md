# User Profile Memory — Operations Runbook

## Source of truth

`agent_memories` remains authoritative. `user_memory_profiles` is a deterministic projection and can be rebuilt.

## Safe inspection

```sql
SELECT id, version, status, source_memory_version, generated_at
  FROM user_memory_profiles
 WHERE tenant_id = '<tenant>'
   AND agent_id = '<agent>'
   AND user_id = '<user>';
```

Do not paste `profile_json` from production into tickets or eval datasets.

## Rebuild

```sql
INSERT INTO user_profile_rebuild_jobs
  (id, tenant_id, agent_id, user_id, reason, source_memory_id, status, attempts, available_at, created_at, updated_at)
VALUES
  ('manual-' || md5(random()::text), '<tenant>', '<agent>', '<user>', 'manual_rebuild', NULL, 'pending', 0, now(), now(), now())
ON CONFLICT ON CONSTRAINT uq_user_profile_rebuild_scope_reason DO NOTHING;
```

## Recall constraints

- Max 5 memory items.
- Max 1,200 Chinese characters.
- Product, price, policy, and competitor facts still require trusted tenant knowledge retrieval.
- Memory outage degrades personalization only.

## Gate

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent_test \
  bash scripts/run_user_profile_memory_gate.sh
```

## Rollback

Disable `user_profile_memory.enabled`, deploy the previous app, and leave projection tables in place. Because profiles are projections, stale rows are harmless when recall is disabled.
