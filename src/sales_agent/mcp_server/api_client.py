"""Typed HTTPX client for the Observability API.

The MCP server never queries the database — it forwards authenticated calls
to the Observability API using the caller's bearer token.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from .types import WaitResult, IterationStatus

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class ObservabilityApiError(Exception):
    """Typed API error forwarded to the MCP caller."""

    def __init__(self, status_code: int, code: str, detail: str = "") -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(f"[{status_code}] {code}: {detail}")


class ObservabilityApiClient:
    """Authenticated HTTP client for the optimization Observability API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── Low-level ────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        req_headers = dict(headers or {})
        try:
            resp = await self._client.request(
                method, url, params=params, json=body,
                headers=req_headers, timeout=timeout or _DEFAULT_TIMEOUT,
            )
        except httpx.TimeoutException:
            raise ObservabilityApiError(503, "api_timeout", "API request timed out")
        except httpx.ConnectError:
            raise ObservabilityApiError(503, "api_unavailable", "Cannot reach API")

        if resp.status_code < 400:
            return resp.json() if resp.content else {}

        error_data: dict[str, Any] = {}
        try:
            error_data = resp.json()
        except Exception:
            pass

        error_code = "unknown"
        error_detail = resp.text[:256]
        if isinstance(error_data.get("error"), dict):
            error_code = error_data["error"].get("code", "unknown")
            error_detail = error_data["error"].get("detail", error_detail)

        raise ObservabilityApiError(resp.status_code, error_code, error_detail)

    # ── Tools ────────────────────────────────────────────────────────────

    async def start_iteration(
        self,
        agent_id: str,
        fixed_suite_id: str,
        *,
        exploration_suite_id: str | None = None,
        max_candidates: int = 3,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "fixed_suite_id": fixed_suite_id,
            "max_candidates": max_candidates,
        }
        if exploration_suite_id:
            body["exploration_suite_id"] = exploration_suite_id
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return await self._request(
            "POST",
            f"/agents/{quote(agent_id, safe='')}/optimization/iterations",
            body=body,
            headers=headers,
        )

    async def get_status(
        self, agent_id: str, iteration_id: str,
    ) -> IterationStatus:
        data = await self._request(
            "GET",
            f"/agents/{quote(agent_id, safe='')}/optimization/iterations/{quote(iteration_id, safe='')}",
        )
        return IterationStatus.from_api(data)

    async def wait_for_update(
        self,
        agent_id: str,
        iteration_id: str,
        after_sequence: int = 0,
        timeout_seconds: int = 30,
    ) -> WaitResult:
        capped = min(max(timeout_seconds, 1), 30)
        data = await self._request(
            "GET",
            f"/agents/{quote(agent_id, safe='')}/optimization/iterations/{quote(iteration_id, safe='')}/events/wait",
            params={"after_sequence": after_sequence, "timeout_seconds": capped},
            timeout=capped + 5,
        )
        return WaitResult.from_api(data)

    async def list_candidates(
        self, agent_id: str, iteration_id: str,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            f"/agents/{quote(agent_id, safe='')}/optimization/iterations/{quote(iteration_id, safe='')}/candidates",
        )

    async def get_report(
        self, agent_id: str, iteration_id: str, report_id: str | None = None,
    ) -> dict[str, Any]:
        if report_id:
            path = f"/agents/{quote(agent_id, safe='')}/optimization/iterations/{quote(iteration_id, safe='')}/reports/{quote(report_id, safe='')}"
        else:
            path = f"/agents/{quote(agent_id, safe='')}/optimization/iterations/{quote(iteration_id, safe='')}/reports"
            data = await self._request("GET", path)
            items = data if isinstance(data, list) else [data]
            return items[0] if items else {}
        return await self._request("GET", path)

    async def get_trend(
        self, agent_id: str, limit: int = 10,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/agents/{quote(agent_id, safe='')}/optimization/optimization/trends",
            params={"limit": limit},
        )
