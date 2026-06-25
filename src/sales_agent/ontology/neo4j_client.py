from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neo4j import AsyncDriver, AsyncGraphDatabase

from sales_agent.core.config import Neo4jConfig


class Neo4jClient:
    """Small async Neo4j driver wrapper."""

    def __init__(self, config: Neo4jConfig):
        self.config = config
        self._driver: AsyncDriver | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.uri)

    def driver(self) -> AsyncDriver:
        if not self.enabled:
            raise RuntimeError("Neo4j URI is not configured")
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
                connection_timeout=self.config.connection_timeout_seconds,
            )
        return self._driver

    @asynccontextmanager
    async def session(self) -> AsyncIterator:
        async with self.driver().session(database=self.config.database) as session:
            yield session

    async def verify_connectivity(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Neo4j URI is not configured"
        try:
            await self.driver().verify_connectivity()
            return True, "ok"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
