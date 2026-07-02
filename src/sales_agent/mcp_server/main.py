"""Streamable HTTP MCP process entry point.

Usage::

    SALES_AGENT_API_URL=http://api:8000 sales-agent-mcp --host 0.0.0.0 --port 3001

The process has no database access. It forwards every request to the
Observability API using the caller's bearer token.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Sales Agent Iteration MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=3001, help="Bind port (default: 3001)")
    parser.add_argument("--log-level", default="info", help="Log level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    api_url = os.environ.get("SALES_AGENT_API_URL")
    if not api_url:
        print("FATAL: SALES_AGENT_API_URL environment variable is required", file=sys.stderr)
        sys.exit(1)

    from .server import create_mcp_server

    mcp = create_mcp_server()
    logger = logging.getLogger(__name__)
    logger.info("Starting MCP server on %s:%s, API backend: %s", args.host, args.port, api_url)

    # FastMCP uses uvicorn under the hood for Streamable HTTP
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
