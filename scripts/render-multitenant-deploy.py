#!/usr/bin/env python3
"""Render same-server multi-tenant Docker Compose and Traefik config.

The input inventory is JSON so this script only needs the Python standard
library on a fresh server.

Traefik 容器不再由此脚本生成——所有项目共享 /root/code/traefik/ 下的
Traefik 实例。此脚本仅输出路由片段到 shared Traefik 目录。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


VALID_ROLES = {"api", "stream", "worker"}

# 共享 Traefik 动态配置目录 — directory provider 会 watch 此目录所有 .yml
_SHARED_TRAEFIK_DIR = Path("/root/code/traefik/dynamic.d")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inventory",
        nargs="?",
        default="deploy/tenants.json",
        help="Path to tenant inventory JSON",
    )
    parser.add_argument(
        "--compose-out",
        help="Override generated compose output path",
    )
    parser.add_argument(
        "--traefik-out",
        help="Override generated Traefik dynamic config path",
    )
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    data = load_inventory(inventory_path)
    validate_inventory(data, inventory_path)

    compose_path = Path(
        args.compose_out
        or data.get("output", {}).get("compose_file")
        or "docker-compose.generated.yml"
    )

    compose_path.write_text(render_compose(data), encoding="utf-8")
    print(f"wrote {compose_path}")

    if data.get("traefik", {}).get("enabled", True):
        traefik_out = Path(
            args.traefik_out
            or data.get("traefik", {}).get("dynamic_output")
            or str(_SHARED_TRAEFIK_DIR / "generated-sales-agent.yml")
        )
        traefik_out.parent.mkdir(parents=True, exist_ok=True)
        traefik_out.write_text(render_traefik_routes(data), encoding="utf-8")
        print(f"wrote {traefik_out}")

    return 0


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"inventory not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def validate_inventory(data: dict[str, Any], inventory_path: Path) -> None:
    tenants = data.get("tenants")
    if not isinstance(tenants, list) or not tenants:
        raise SystemExit("inventory must contain a non-empty tenants list")

    seen_ids: set[str] = set()
    seen_ports: set[int] = set()
    for tenant in tenants:
        tenant_id = tenant.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", tenant_id):
            raise SystemExit(f"invalid tenant id: {tenant_id!r}")
        if tenant_id in seen_ids:
            raise SystemExit(f"duplicate tenant id: {tenant_id}")
        seen_ids.add(tenant_id)

        env_file = tenant.get("env_file", "")
        if not env_file:
            raise SystemExit(f"tenant {tenant_id}: env_file is required")
        env_path = Path(env_file).resolve()
        if not env_path.exists():
            raise SystemExit(f"tenant {tenant_id}: env_file not found: {env_file}")

        api_port = tenant.get("api_port")
        if not isinstance(api_port, int) or api_port <= 0:
            raise SystemExit(f"tenant {tenant_id}: api_port must be a positive integer")
        if api_port in seen_ports:
            raise SystemExit(f"duplicate api_port: {api_port}")
        seen_ports.add(api_port)

        roles = set(tenant.get("roles") or ["api", "stream", "worker"])
        invalid_roles = roles - VALID_ROLES
        if invalid_roles:
            raise SystemExit(f"tenant {tenant_id}: invalid roles {sorted(invalid_roles)}")
        tenant["roles"] = sorted(roles)

        # 如果 env_file 里开启了 DingTalk Stream，必须包含 stream 角色
        if _env_has(env_path, "DINGTALK_STREAMING_ENABLED", "true") and "stream" not in roles:
            raise SystemExit(
                f"tenant {tenant_id}: DINGTALK_STREAMING_ENABLED=true but 'stream' role "
                f"is missing from roles ({sorted(roles)}). Add 'stream' to roles."
            )

        if data.get("traefik", {}).get("enabled", True) and not tenant.get("domain"):
            # 无独立域名 → 使用 PathPrefix 路由（共享域），校验 public_url
            env_path2 = Path(env_file).resolve()
            public_url = _env_get(env_path2, "DINGTALK_PUBLIC_URL")
            if not public_url:
                raise SystemExit(
                    f"tenant {tenant_id}: domain is required when Traefik is enabled "
                    f"and DINGTALK_PUBLIC_URL is not set in {env_file}"
                )


def _env_has(env_path: Path, key: str, value: str) -> bool:
    """Check if an env file contains key=value (ignoring quotes and whitespace)."""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("\"'").lower() == value.lower()
    except OSError:
        pass
    return False


def render_compose(data: dict[str, Any]) -> str:
    project_name = data.get("project_name", "sales-agent")
    # CI 可经 OVERRIDE_IMAGE 注入精确 sha tag;不设则用 inventory 的 image。
    image = os.environ.get("OVERRIDE_IMAGE") or data.get("image", "sales-agent:latest")
    postgres_image = data.get("postgres_image", "docker.1ms.run/pgvector/pgvector:pg16")
    db = data.get("database", {})
    db_name = db.get("name", "sales_agent")
    db_user = db.get("user", "sales_agent")
    db_password = db.get("password", "sales_agent_dev")

    lines = [
        "# Generated by scripts/render-multitenant-deploy.py. Do not edit by hand.",
        "",
        "# Traefik 容器由共享实例 /root/code/traefik/ 管理，不在此生成。",
        "",
        "services:",
    ]

    db_host_port = db.get("host_port", 5432)
    expose_host_port = db.get("expose_host_port", True)
    lines += [
        "  postgres:",
        f"    image: {postgres_image}",
        f"    container_name: {project_name}-db",
        "    restart: unless-stopped",
        "    environment:",
        f"      POSTGRES_DB: {db_name}",
        f"      POSTGRES_USER: {db_user}",
        f"      POSTGRES_PASSWORD: {db_password}",
    ]
    if expose_host_port:
        lines += ["    ports:", f'      - "{db_host_port}:5432"']
    lines += [
        "    volumes:",
        "      - pgdata:/var/lib/postgresql/data",
        "      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro",
        "    healthcheck:",
        f'      test: ["CMD-SHELL", "pg_isready -U {db_user} -d {db_name}"]',
        "      interval: 5s",
        "      timeout: 5s",
        "      retries: 5",
        "",
    ]

    database_url = f"postgresql+asyncpg://{db_user}:{db_password}@postgres:5432/{db_name}"
    for tenant in data["tenants"]:
        lines.extend(render_tenant_services(tenant, image, database_url))

    lines.extend(["volumes:", "  pgdata:", ""])
    return "\n".join(lines)


def render_tenant_services(tenant: dict[str, Any], image: str, database_url: str) -> list[str]:
    tenant_id = tenant["id"]
    env_file = tenant["env_file"]
    data_dir = tenant.get("data_dir", f"./data/{tenant_id}")
    logs_dir = tenant.get("logs_dir", f"./logs/{tenant_id}")
    roles = set(tenant["roles"])
    lines: list[str] = []

    if "api" in roles:
        lines.extend(
            [
                f"  {tenant_id}-api:",
                f"    image: {image}",
                f"    container_name: sales-agent-{tenant_id}-api",
                "    restart: unless-stopped",
                "    env_file:",
                f"      - ./{env_file}",
                "    environment:",
                "      PROCESS_ROLE: api",
                f"      DATABASE_URL: {database_url}",
                "    volumes:",
                f"      - {data_dir}:/data/{tenant_id}",
                f"      - {logs_dir}:/logs/{tenant_id}",
                "    ports:",
                f'      - "{tenant["api_port"]}:8000"',
                "    depends_on:",
                "      postgres:",
                "        condition: service_healthy",
                "",
            ]
        )

        # 前端容器（nginx serving SPA + API 代理）
        frontend_image = os.environ.get("FRONTEND_IMAGE", "sales-agent-frontend:latest")
        frontend_port = tenant.get("frontend_port", tenant["api_port"] + 3000)
        lines.extend(
            [
                f"  {tenant_id}-frontend:",
                f"    image: {frontend_image}",
                f"    container_name: sales-agent-{tenant_id}-frontend",
                "    restart: unless-stopped",
                "    environment:",
                f"      - BACKEND_HOST={tenant_id}-api",
                "    ports:",
                f'      - "{frontend_port}:80"',
                "    depends_on:",
                f"      - {tenant_id}-api",
                "",
            ]
        )

    if "stream" in roles:
        lines.extend(
            [
                f"  {tenant_id}-stream:",
                f"    image: {image}",
                f"    container_name: sales-agent-{tenant_id}-stream",
                "    restart: unless-stopped",
                "    env_file:",
                f"      - ./{env_file}",
                "    environment:",
                "      PROCESS_ROLE: stream",
                f"      DATABASE_URL: {database_url}",
                "    volumes:",
                f"      - {logs_dir}:/logs/{tenant_id}",
                "    depends_on:",
                "      postgres:",
                "        condition: service_healthy",
                "",
            ]
        )

    if "worker" in roles:
        lines.extend(
            [
                f"  {tenant_id}-worker:",
                f"    image: {image}",
                f"    container_name: sales-agent-{tenant_id}-worker",
                "    restart: unless-stopped",
                "    env_file:",
                f"      - ./{env_file}",
                "    environment:",
                "      PROCESS_ROLE: worker",
                f"      DATABASE_URL: {database_url}",
                "    volumes:",
                f"      - {data_dir}:/data/{tenant_id}",
                f"      - {logs_dir}:/logs/{tenant_id}",
                "    depends_on:",
                "      postgres:",
                "        condition: service_healthy",
                "",
            ]
        )

    return lines


def _env_get(env_path: Path, key: str) -> str | None:
    """Read a single value from an env file. Returns None if not found."""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("\"'")
    except OSError:
        pass
    return None


def render_traefik_routes(data: dict[str, Any]) -> str:
    """Generate a Traefik dynamic config fragment for sales-agent routes.

    Output goes to /root/code/traefik/generated-sales-agent.yml and is served
    by the shared Traefik instance on this host.

    Two routing strategies:
      - Tenant has 'domain' → Host-based route for the entire tenant API.
      - Tenant has DINGTALK_PUBLIC_URL → PathPrefix route under shared domain
        for DingTalk integration endpoints (/integrations/dingtalk/).
    """
    lines = [
        "# Generated by scripts/render-multitenant-deploy.py. Do not edit by hand.",
        "",
        "http:",
        "  routers:",
    ]

    service_lines: list[str] = []
    seen_services: set[str] = set()

    for tenant in data["tenants"]:
        tenant_id = tenant["id"]
        domain = tenant.get("domain", "")
        env_path = Path(tenant["env_file"]).resolve()
        api_container = f"sales-agent-{tenant_id}-api"
        frontend_container = f"sales-agent-{tenant_id}-frontend"

        if domain:
            # Host-based route — 用户域名 → 前端 nginx 容器（SPA + API 代理）
            router_name = f"sales-agent-{tenant_id}"
            service_name = f"sales-agent-{tenant_id}-backend"
            lines.extend([
                f"    {router_name}:",
                f'      rule: "Host(`{domain}`)"',
                "      entryPoints:",
                "        - websecure",
                "      tls:",
                "        certResolver: letsencrypt",
                f"      service: {service_name}",
            ])
            if service_name not in seen_services:
                seen_services.add(service_name)
                service_lines.extend([
                    f"    {service_name}:",
                    "      loadBalancer:",
                    "        servers:",
                    f'          - url: "http://{frontend_container}:80"',
                ])

        # PathPrefix route for DingTalk integration (shared domain)
        public_url = _env_get(env_path, "DINGTALK_PUBLIC_URL")
        if public_url:
            hostname = urlparse(public_url).hostname
            if hostname:
                router_name = f"sales-agent-{tenant_id}-dingtalk"
                service_name = f"sales-agent-{tenant_id}-dingtalk-svc"
                lines.extend([
                    f"    {router_name}:",
                    f'      rule: "Host(`{hostname}`) && PathPrefix(`/integrations/dingtalk/`)"',
                    "      entryPoints:",
                    "        - websecure",
                    "      tls:",
                    "        certResolver: letsencrypt",
                    f"      service: {service_name}",
                    "      priority: 210",
                ])
                if service_name not in seen_services:
                    seen_services.add(service_name)
                    service_lines.extend([
                        f"    {service_name}:",
                        "      loadBalancer:",
                        "        servers:",
                        f'          - url: "http://{api_container}:8000"',
                    ])

    if not service_lines:
        return "\n".join(lines)

    lines.append("")
    lines.append("  services:")
    lines.extend(service_lines)
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
