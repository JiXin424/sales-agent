# Sales Agent Release Bundle

This bundle contains:

- App Docker image: images/sales-agent-image.tar
- Deployment inventory template: deploy/tenants.example.json
- Concrete usage guide: deploy/DEPLOY_USAGE.md
- Tenant env template: deploy/tenant.env.example
- One-command deploy script: scripts/deploy-release.sh
- Generated compose renderer and health checks

First deployment on a target server:

1. Extract this archive.
2. Copy deploy/tenants.example.json to deploy/tenants.json and edit tenant ids, domains, ports, and env_file paths.
3. Copy deploy/tenant.env.example to each secrets/<tenant>.env and replace all model and DingTalk credentials.
4. Run: scripts/deploy-release.sh
5. Type DEPLOY only after the script prints the config paths and you confirm they are current.

Update deployment:

1. Extract the new archive over or next to the existing deployment directory.
2. Keep the existing deploy/tenants.json and secrets/*.env.
3. Run: scripts/deploy-release.sh
