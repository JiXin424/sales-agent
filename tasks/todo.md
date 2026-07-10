# Multi-tenant/instance config & feature toggle research (READ-ONLY)

Goal: understand per-tenant/per-instance config so a feature can be toggled ON on one
agent instance and OFF on another, then migrated to another instance via CI/CD.

- [ ] Identify tenant/instance ID mechanism (tenant_id, agent_id, instance config)
- [ ] Map configuration loading (files, settings, per-instance vs per-tenant)
- [ ] Find feature flag/toggle mechanism
- [ ] Trace deployment story (deploy-remote.sh, compose, Dockerfile, CI/CD, <tenant>-stream)
- [ ] Document DB/ORM/Alembic tenant-scoped data model
- [ ] Synthesize: how to toggle a feature ON for instance A / OFF for instance B and ship via CI/CD

## Research delegated to 5 parallel subagents (read-only, no worktree needed).
