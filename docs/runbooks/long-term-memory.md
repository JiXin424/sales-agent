# Long-Term Atomic Memory -- Operations Runbook

## What this stores

Spec 2 stores governed sales-user atomic memories only:

- `user_fact`
- `response_preference`
- `coaching_goal`
- `sales_pattern`
- `recurring_challenge`

It does not store customer profiles, organization profiles, or prompt-injected user profiles.

## Tables

- `agent_memories`: source of truth for atomic memories.
- `memory_outbox`: asynchronous inferred-candidate jobs.
- `memory_audit_events`: audit trail for activation, correction, forget, expiry, rejection, and failure.

## Safe inspection

```sql
SELECT id, memory_type, normalized_key, status, evidence_count, confidence_band, sensitivity, expires_at
  FROM agent_memories
 WHERE tenant_id = '<tenant>'
   AND agent_id = '<agent>'
   AND subject_type = 'user'
   AND subject_id = '<user>'
 ORDER BY updated_at DESC;
```

Do not paste `content_json` from production into issue trackers or eval datasets.

## Gates

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/sales_agent_test \
  bash scripts/run_long_term_memory_gate.sh
```

## Failure handling

- Explicit write failure: user sees a retry message and no success claim.
- Outbox extraction failure: ordinary answer remains successful; job retries with bounded backoff.
- Dead-letter jobs: inspect `memory_outbox.last_error`, fix the root cause, then reset `status='pending'`, `attempts=0`, and `available_at=now()`.
- Forget: application reads exclude deleted memories immediately.

## Rollback

Disable `long_term_memory.enabled`, deploy the previous application version, and leave the tables in place. Do not drop memory tables during rollback unless privacy policy requires erasure and the tenant owner approves.
