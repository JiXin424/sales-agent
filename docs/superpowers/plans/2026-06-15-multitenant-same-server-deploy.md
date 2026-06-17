# Multi-Tenant Same-Server Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build repeatable same-server multi-enterprise deployment assets for Sales Agent using one shared infrastructure stack and one dedicated API/Stream/Worker group per tenant.

**Architecture:** Use a JSON tenant inventory as the source of truth. A standard-library Python renderer generates Docker Compose and Traefik dynamic config from the inventory, while shell checks validate tenant env files and running services. Application code remains unchanged.

**Tech Stack:** Docker Compose, Traefik file provider, Python 3 standard library, POSIX shell, Sales Agent FastAPI/React/DingTalk services.

---

## File Structure

- Create `deploy/tenants.example.json`: sample same-server tenant inventory.
- Create `deploy/tenant.env.example`: complete per-enterprise env template including model, DingTalk, data, and log settings.
- Create `scripts/render-multitenant-deploy.py`: render generated compose and Traefik files from the inventory.
- Create `scripts/check-tenant.sh`: verify one tenant API health/readiness and container status.
- Create `scripts/check-all-tenants.sh`: run tenant checks for every tenant in the inventory.
- Create `docs/multitenant-deployment.md`: operator guide for adding, upgrading, and verifying enterprises.

### Task 1: Tenant Inventory And Env Template

**Files:**
- Create: `deploy/tenants.example.json`
- Create: `deploy/tenant.env.example`

- [x] **Step 1: Add a sample tenant inventory**

The inventory contains shared image/network/database settings and a list of tenants. Each tenant has a unique id, env file, domain, host API port, and optional enabled roles.

- [x] **Step 2: Add a full tenant env template**

The template includes all required tenant, model, DingTalk, data, and log variables. Secrets remain in env files and are excluded from source control by the existing `secrets/` ignore rule.

### Task 2: Deployment Renderer

**Files:**
- Create: `scripts/render-multitenant-deploy.py`

- [x] **Step 1: Implement JSON parsing and validation**

The renderer rejects missing tenant ids, duplicate tenant ids, missing env files, duplicate host ports, invalid role names, and missing domains when Traefik generation is enabled.

- [x] **Step 2: Render shared services**

The generated compose includes shared `postgres` and `traefik` services and a named `pgdata` volume.

- [x] **Step 3: Render per-tenant services**

Each tenant gets `TENANT-api`, `TENANT-stream`, and `TENANT-worker` services when enabled. All three use the same image tag and tenant env file. Stream and worker expose no public ports.

- [x] **Step 4: Render Traefik dynamic routes**

Each tenant domain routes to its API service. The renderer writes a generated dynamic config that can be mounted by Traefik.

### Task 3: Verification Scripts

**Files:**
- Create: `scripts/check-tenant.sh`
- Create: `scripts/check-all-tenants.sh`

- [x] **Step 1: Add single tenant checks**

The script checks `api`, `stream`, and `worker` containers, then calls `/health` and `/ready` on the tenant API port.

- [x] **Step 2: Add inventory-wide checks**

The script reads the JSON inventory with Python standard library and invokes `check-tenant.sh` for every tenant.

### Task 4: Operator Documentation

**Files:**
- Create: `docs/multitenant-deployment.md`

- [x] **Step 1: Document the target topology**

One server runs shared Postgres/Traefik and one dedicated container group per tenant.

- [x] **Step 2: Document new enterprise onboarding**

Copy env template, fill secrets, add inventory entry, render, start, create tenant, import knowledge, run checks.

- [x] **Step 3: Document version alignment**

All tenants on a server use the same image tag. Upgrades change the inventory image tag, render again, pull, recreate, and verify all tenants.

## Self-Review

- Spec coverage: same-server multi-enterprise deployment, config isolation, fast copy, frontend/API/proxy considerations, stream singleton, and version alignment are covered.
- Placeholder scan: no unresolved implementation markers remain in created files; env examples intentionally use obvious example values.
- Type consistency: tenant inventory fields are consistent across renderer, check scripts, and docs.
