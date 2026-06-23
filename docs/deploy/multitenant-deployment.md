# Multi-Tenant Same-Server Deployment

This guide describes the recommended production shape for running multiple enterprise tenants on one server.

## Topology

One server runs shared infrastructure and one dedicated service group per enterprise:

- Shared: `postgres`, `traefik`.
- Per tenant: `<tenant>-api`, `<tenant>-stream`, `<tenant>-worker`.
- Per tenant files: `secrets/<tenant>.env`, `data/<tenant>/`, `logs/<tenant>/`.

Each tenant remains `DEPLOYMENT_MODE=dedicated`. Tenant secrets, DingTalk credentials, data directories, logs, and runtime checks are isolated by tenant id. The database can be shared because the application already enforces `tenant_id` filtering and dedicated-mode request checks.

## Add A New Enterprise

1. Copy the env template:

```bash
cp deploy/tenant.env.example secrets/acme.env
chmod 600 secrets/acme.env
```

2. Edit `secrets/acme.env` and set:

- `TENANT_ID`
- `TENANT_NAME`
- `MODEL_API_KEY`
- `MODEL_BASE_URL`
- `MODEL_CHAT_MODEL`
- `MODEL_EMBEDDING_MODEL`
- `DINGTALK_CORP_ID`
- `DINGTALK_APP_KEY`
- `DINGTALK_APP_SECRET`
- `DINGTALK_ROBOT_CODE`
- `VECTOR_COLLECTION`
- `DATA_DIR`
- `LOG_DIR`
- `DINGTALK_PUBLIC_URL`

3. Add the tenant to `deploy/tenants.json`:

```json
{
  "id": "acme",
  "name": "ACME Enterprise",
  "domain": "acme-agent.example.com",
  "api_port": 8101,
  "env_file": "secrets/acme.env",
  "data_dir": "./data/acme",
  "logs_dir": "./logs/acme",
  "roles": ["api", "stream", "worker"]
}
```

4. Render deployment files:

```bash
python3 scripts/render-multitenant-deploy.py deploy/tenants.json
```

5. Start or update services:

```bash
docker compose -f docker-compose.generated.yml up -d
```

6. Create the tenant and import knowledge:

```bash
curl -X POST "http://127.0.0.1:8101/tenants" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"acme","name":"ACME Enterprise"}'

docker compose -f docker-compose.generated.yml exec acme-api \
  sales-agent ingest --tenant acme --path /data/acme/documents --rebuild
```

7. Verify:

```bash
scripts/check-tenant.sh acme 8101
```

## Upgrade All Tenants On A Server

All tenants on a server should use the same Sales Agent image tag. To upgrade:

1. Change `image` in `deploy/tenants.json`, for example from `sales-agent:v0.3.1` to `sales-agent:v0.3.2`.
2. Render the generated files again:

```bash
python3 scripts/render-multitenant-deploy.py deploy/tenants.json
```

3. Pull and recreate:

```bash
docker compose -f docker-compose.generated.yml pull
docker compose -f docker-compose.generated.yml up -d
```

4. Verify every tenant:

```bash
scripts/check-all-tenants.sh deploy/tenants.json
```

## Operational Rules

- Run exactly one Stream container per tenant.
- Do not put real secrets in `deploy/tenants.json`; keep them in `secrets/<tenant>.env`.
- Use domain routing when possible: `acme-agent.example.com` routes to `acme-api:8000`.
- Keep tenant ids lowercase and stable. They become service names, volume paths, vector collection names, and log paths.
- Prefer image tags based on release versions or git SHAs. Do not manually edit code on customer servers after deployment.
