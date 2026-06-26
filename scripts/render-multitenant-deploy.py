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


def _neo4j_enabled(data: dict[str, Any]) -> bool:
    """inventory 显式开关优先；缺省时扫描租户 env 自动检测。"""
    cfg = data.get("neo4j") or {}
    if "enabled" in cfg:
        return bool(cfg["enabled"])
    for tenant in data.get("tenants", []):
        env_path = Path(tenant.get("env_file", "")).resolve()
        if _env_has(env_path, "KNOWLEDGE_ENGINE", "ontology_neo4j"):
            return True
    return False


# 共享 Traefik 动态配置目录 — directory provider 会 watch 此目录所有 .yml
_SHARED_TRAEFIK_DIR = Path("/root/code/traefik/dynamic.d")


def main(argv=None) -> int:
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
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="跳过 validate_inventory（用于无源码目标：env_file 在目标机不在主控）",
    )
    args = parser.parse_args(argv)

    inventory_path = Path(args.inventory)
    data = load_inventory(inventory_path)
    if not args.skip_validation:
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
    # 共享反代网关所在网络（external）：设了就把 api 容器同时挂上去，让网关按容器名解析到 api。
    shared_network = (data.get("traefik") or {}).get("shared_network", "")
    if shared_network and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", shared_network):
        raise SystemExit(f"invalid traefik.shared_network name: {shared_network!r}")

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
    neo4j_on = _neo4j_enabled(data)
    shared_network = (data.get("traefik") or {}).get("shared_network", "")
    for tenant in data["tenants"]:
        lines.extend(render_tenant_services(tenant, image, database_url, neo4j_on, shared_network))

    if neo4j_on:
        neo4j_cfg = data.get("neo4j") or {}
        neo4j_image = neo4j_cfg.get("image", "registry.internal:5000/neo4j:5")
        lines.extend([
            "  neo4j:",
            f"    image: {neo4j_image}",
            f"    container_name: {project_name}-neo4j",
            "    restart: unless-stopped",
            "    environment:",
            "      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}",
        ])
        if neo4j_cfg.get("expose_ports", False):
            lines += [
                "    ports:",
                '      - "7474:7474"',
                '      - "7687:7687"',
            ]
        lines += [
            "    volumes:",
            "      - neo4jdata:/data",
            "    healthcheck:",
            '      test: ["CMD-SHELL", "cypher-shell -u neo4j -p ${NEO4J_PASSWORD} \'RETURN 1\' || exit 1"]',
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 12",
            "      start_period: 30s",
            "",
        ]

    lines.extend(["volumes:", "  pgdata:"])
    if neo4j_on:
        lines.append("  neo4jdata:")
    lines.append("")
    if shared_network:
        # 声明共享网关网络为 external（由服务器级 Traefik 实例创建，本栈只接入不创建）。
        lines += [
            "networks:",
            "  default:",
            f"  {shared_network}:",
            "    external: true",
            "",
        ]
    return "\n".join(lines)


def render_tenant_services(tenant: dict[str, Any], image: str, database_url: str, neo4j_enabled: bool = False, shared_network: str = "") -> list[str]:
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
            ]
        )
        if neo4j_enabled:
            lines += [
                "      NEO4J_URI: bolt://neo4j:7687",
                "      NEO4J_USER: neo4j",
                "      NEO4J_PASSWORD: ${NEO4J_PASSWORD}",
                "      NEO4J_DATABASE: neo4j",
            ]
        lines += [
            "    volumes:",
            f"      - {data_dir}:/data/{tenant_id}",
            f"      - {logs_dir}:/logs/{tenant_id}",
            "    ports:",
            f'      - "{tenant["api_port"]}:8000"',
            "    depends_on:",
            "      postgres:",
            "        condition: service_healthy",
        ]
        if neo4j_enabled:
            lines += [
                "      neo4j:",
                "        condition: service_healthy",
            ]
        # 共享反代网关（如服务器级 Traefik）所在网络：api 同时挂 default（连 postgres/neo4j/
        # 被前端 nginx 代理）和该外部网络，使网关能按容器名解析到 api。手动 docker network
        # connect 会在容器 recreate 时丢失，必须在此声明才持久（见 tasks/lessons.md §10）。
        if shared_network:
            lines += [
                "    networks:",
                "      - default",
                f"      - {shared_network}",
            ]
        lines.append("")

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
            ]
        )
        if neo4j_enabled:
            lines += [
                "      NEO4J_URI: bolt://neo4j:7687",
                "      NEO4J_USER: neo4j",
                "      NEO4J_PASSWORD: ${NEO4J_PASSWORD}",
                "      NEO4J_DATABASE: neo4j",
            ]
        lines += [
            "    volumes:",
            f"      - {logs_dir}:/logs/{tenant_id}",
            "    depends_on:",
            "      postgres:",
            "        condition: service_healthy",
        ]
        if neo4j_enabled:
            lines += [
                "      neo4j:",
                "        condition: service_healthy",
            ]
        lines.append("")

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
            ]
        )
        if neo4j_enabled:
            lines += [
                "      NEO4J_URI: bolt://neo4j:7687",
                "      NEO4J_USER: neo4j",
                "      NEO4J_PASSWORD: ${NEO4J_PASSWORD}",
                "      NEO4J_DATABASE: neo4j",
            ]
        lines += [
            "    volumes:",
            f"      - {data_dir}:/data/{tenant_id}",
            f"      - {logs_dir}:/logs/{tenant_id}",
            "    depends_on:",
            "      postgres:",
            "        condition: service_healthy",
        ]
        if neo4j_enabled:
            lines += [
                "      neo4j:",
                "        condition: service_healthy",
            ]
        lines.append("")

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
        for DingTalk integration endpoints (/integrations/dingtalk/t/{tenant_id}/).
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
        backend = tenant.get("backend", "")  # NEW: optional remote host:port
        env_path = Path(tenant["env_file"]).resolve()
        api_container = f"sales-agent-{tenant_id}-api"
        frontend_container = f"sales-agent-{tenant_id}-frontend"

        # Resolve dingtalk backend URL: remote or local container
        if backend:
            dingtalk_backend_url = f"http://{backend}"
        else:
            dingtalk_backend_url = f"http://{api_container}:8000"

        if domain and not backend:
            # Host-based route → 前端 nginx 容器（仅本机租户有前端容器）
            # 远端租户 (backend 有值) 跳过此 catch-all，避免 502
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

        if domain:
            # NEW: 子域名 + DingTalk PathPrefix → 租户 API（本机或远端）
            # 这是快捷入口/免登录的核心路由：Host(subdomain) &&
            # PathPrefix(/integrations/dingtalk/t/{tenant_id}/) → api backend
            router_name = f"sales-agent-{tenant_id}-sub-dingtalk"
            service_name = f"sales-agent-{tenant_id}-sub-dingtalk-svc"
            lines.extend([
                f"    {router_name}:",
                f'      rule: "Host(`{domain}`) && PathPrefix(`/integrations/dingtalk/t/{tenant_id}/`)"',
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
                    f'          - url: "{dingtalk_backend_url}"',
                ])

        # PathPrefix route for DingTalk integration (shared domain).
        # 多租户共用同一 public_url（hostname）时，必须靠 path 段 /t/{tenant_id}/ 区分——
        # 否则两条 rule 完全相同（同 Host + 同 PathPrefix + 同 priority）的路由会让 Traefik
        # 把所有请求都打到其中一个实例，导致其它租户 whoami 校验报 Tenant mismatch。
        public_url = _env_get(env_path, "DINGTALK_PUBLIC_URL")
        if public_url:
            hostname = urlparse(public_url).hostname
            if hostname:
                router_name = f"sales-agent-{tenant_id}-dingtalk"
                service_name = f"sales-agent-{tenant_id}-dingtalk-svc"
                lines.extend([
                    f"    {router_name}:",
                    f'      rule: "Host(`{hostname}`) && PathPrefix(`/integrations/dingtalk/t/{tenant_id}/`)"',
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

    # 防御性校验：禁止生成完全相同的 Traefik rule（同 Host+PathPrefix 会让 Traefik
    # 无法分流、随机漂移——正是曾经导致跨租户 tenant mismatch 的根因）。共享域名下
    # 每个租户的 PathPrefix 含唯一 tenant_id，正常不会重复；此断言把冲突挡在部署前，
    # 防止 inventory 异常或本函数被误改时悄悄生成会漂移的配置。未来任意新增租户
    # 只要 tenant_id 唯一（validate_inventory 已保证），就必然安全。
    rule_lines = [ln.strip() for ln in lines if ln.strip().startswith("rule:")]
    dup = {r for r in rule_lines if rule_lines.count(r) > 1}
    if dup:
        raise SystemExit(
            "render_traefik_routes: 检测到重复的 Traefik rule（会导致分流冲突/漂移）:\n  "
            + "\n  ".join(sorted(dup))
            + "\n共享域名下每个租户的 PathPrefix 必须唯一（含 tenant_id）。"
        )

    if not service_lines:
        return "\n".join(lines)

    lines.append("")
    lines.append("  services:")
    lines.extend(service_lines)
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
