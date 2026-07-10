from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.user_memory_profile import UserMemoryProfile, UserProfileRebuildJob
from sales_agent.services.memory.contracts import MemoryScope
from sales_agent.services.memory.profile_contracts import ProfileProjectionResult
from sales_agent.services.memory.profile_projection import project_user_profile
from sales_agent.services.memory.repository import AtomicMemoryRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StoredUserMemoryProfile:
    id: str
    tenant_id: str
    agent_id: str
    user_id: str
    version: int
    status: str
    profile: dict
    evidence_map: dict[str, list[str]]
    source_memory_version: str
    generated_at: datetime


class UserMemoryProfileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _to_profile(self, row: UserMemoryProfile) -> StoredUserMemoryProfile:
        return StoredUserMemoryProfile(
            id=row.id,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            version=row.version,
            status=row.status,
            profile=json.loads(row.profile_json),
            evidence_map=json.loads(row.evidence_map_json),
            source_memory_version=row.source_memory_version,
            generated_at=row.generated_at,
        )

    async def get_current_profile(self, scope: MemoryScope) -> StoredUserMemoryProfile | None:
        row = (
            await self.db.execute(
                select(UserMemoryProfile).where(
                    UserMemoryProfile.tenant_id == scope.tenant_id,
                    UserMemoryProfile.agent_id == scope.agent_id,
                    UserMemoryProfile.user_id == scope.user_id,
                    UserMemoryProfile.status.in_(["ready", "rebuilding", "degraded"]),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._to_profile(row)

    async def rebuild_profile_for_scope(
        self,
        scope: MemoryScope,
        *,
        now: datetime | None = None,
    ) -> StoredUserMemoryProfile:
        current = now or utc_now()
        memory_repo = AtomicMemoryRepository(self.db)
        active_records = await memory_repo.list_active_memories(scope, now=current)
        projection: ProfileProjectionResult = project_user_profile(active_records, now=current)
        existing = await self.get_current_profile(scope)

        if existing and existing.source_memory_version == projection.source_memory_version:
            return existing

        if existing is None:
            row = UserMemoryProfile(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                version=1,
                status="ready",
                profile_json=json.dumps(projection.profile, ensure_ascii=False),
                evidence_map_json=json.dumps(projection.evidence_map, ensure_ascii=False),
                source_memory_version=projection.source_memory_version,
                generated_at=current,
            )
            self.db.add(row)
        else:
            row = (
                await self.db.execute(
                    select(UserMemoryProfile).where(UserMemoryProfile.id == existing.id)
                )
            ).scalar_one()
            row.version = existing.version + 1
            row.status = "ready"
            row.profile_json = json.dumps(projection.profile, ensure_ascii=False)
            row.evidence_map_json = json.dumps(projection.evidence_map, ensure_ascii=False)
            row.source_memory_version = projection.source_memory_version
            row.generated_at = current

        await self.db.flush()
        return self._to_profile(row)

    async def enqueue_profile_rebuild(
        self,
        scope: MemoryScope,
        *,
        reason: str,
        source_memory_id: str | None,
        now: datetime | None = None,
    ) -> None:
        current = now or utc_now()
        stmt = insert(UserProfileRebuildJob).values(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            reason=reason,
            source_memory_id=source_memory_id,
            status="pending",
            attempts=0,
            available_at=current,
        ).on_conflict_do_nothing(
            constraint="uq_user_profile_rebuild_scope_reason"
        )
        await self.db.execute(stmt)

    async def list_pending_rebuild_jobs(self, *, limit: int) -> list[UserProfileRebuildJob]:
        return (
            await self.db.execute(
                select(UserProfileRebuildJob)
                .where(UserProfileRebuildJob.status == "pending")
                .where(UserProfileRebuildJob.available_at <= utc_now())
                .order_by(UserProfileRebuildJob.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

    async def list_profile_scopes(self, *, limit: int) -> list[MemoryScope]:
        rows = (
            await self.db.execute(
                select(UserMemoryProfile)
                .where(UserMemoryProfile.status.in_(["ready", "degraded"]))
                .order_by(UserMemoryProfile.updated_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            MemoryScope(tenant_id=row.tenant_id, agent_id=row.agent_id, user_id=row.user_id)
            for row in rows
        ]

    async def _pending_null_source_job_exists(self, scope: MemoryScope, *, reason: str) -> bool:
        """Check if a pending job exists for this scope+reason with NULL source_memory_id.

        PostgreSQL treats NULL as distinct in UNIQUE constraints, so
        ON CONFLICT DO NOTHING cannot deduplicate rows where source_memory_id IS NULL.
        This application-level check fills that gap.
        """
        row = (
            await self.db.execute(
                select(UserProfileRebuildJob).where(
                    UserProfileRebuildJob.tenant_id == scope.tenant_id,
                    UserProfileRebuildJob.agent_id == scope.agent_id,
                    UserProfileRebuildJob.user_id == scope.user_id,
                    UserProfileRebuildJob.reason == reason,
                    UserProfileRebuildJob.source_memory_id.is_(None),
                    UserProfileRebuildJob.status == "pending",
                )
            )
        ).first()
        return row is not None

    async def enqueue_stale_profile_rebuilds(self, *, limit: int) -> int:
        scopes = await self.list_profile_scopes(limit=limit)
        count = 0
        for scope in scopes:
            current_profile = await self.get_current_profile(scope)
            if current_profile is None:
                continue
            memory_repo = AtomicMemoryRepository(self.db)
            records = await memory_repo.list_active_memories(scope)
            projected = project_user_profile(records)
            if projected.source_memory_version != current_profile.source_memory_version:
                if await self._pending_null_source_job_exists(
                    scope, reason="profile_reconciliation"
                ):
                    continue
                await self.enqueue_profile_rebuild(
                    scope,
                    reason="profile_reconciliation",
                    source_memory_id=None,
                )
                count += 1
        return count
