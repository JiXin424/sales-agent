from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.atomic_memory import AtomicMemory, MemoryAuditEvent, MemoryOutboxJob
from sales_agent.services.memory.contracts import (
    AtomicMemoryRecord,
    MemoryCandidate,
    MemoryOperationResult,
    MemoryScope,
)
from sales_agent.services.memory.normalization import build_search_text
from sales_agent.services.memory.policy import default_expires_at


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExpiryResult:
    expired_count: int
    memory_ids: list[str]


class AtomicMemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _to_record(self, row: AtomicMemory) -> AtomicMemoryRecord:
        scope = MemoryScope(
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            user_id=row.subject_id,
        )
        return AtomicMemoryRecord(
            id=row.id,
            scope=scope,
            memory_type=row.memory_type,
            normalized_key=row.normalized_key,
            content=json.loads(row.content_json),
            search_text=row.search_text,
            status=row.status,
            source_kind=row.source_kind,
            source_conversation_id=row.source_conversation_id,
            source_message_ids=json.loads(row.source_message_ids_json),
            evidence_count=row.evidence_count,
            confidence_band=row.confidence_band,
            sensitivity=row.sensitivity,
            supersedes_id=row.supersedes_id,
            observed_at=row.observed_at,
            last_confirmed_at=row.last_confirmed_at,
            expires_at=row.expires_at,
        )

    async def _audit(
        self,
        scope: MemoryScope,
        *,
        operation: str,
        status: str,
        reason_code: str,
        memory_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            MemoryAuditEvent(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                memory_id=memory_id,
                operation=operation,
                status=status,
                reason_code=reason_code,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            )
        )

    # ------------------------------------------------------------------
    # Active reads
    # ------------------------------------------------------------------

    async def list_active_memories(
        self,
        scope: MemoryScope,
        *,
        normalized_key: str | None = None,
        now: datetime | None = None,
    ) -> list[AtomicMemoryRecord]:
        current = now or _now()
        conditions = [
            AtomicMemory.tenant_id == scope.tenant_id,
            AtomicMemory.agent_id == scope.agent_id,
            AtomicMemory.subject_type == scope.subject_type,
            AtomicMemory.subject_id == scope.subject_id,
            AtomicMemory.status == "active",
        ]
        if normalized_key:
            conditions.append(AtomicMemory.normalized_key == normalized_key)

        rows = (
            (
                await self.db.execute(
                    select(AtomicMemory)
                    .where(*conditions)
                    .where(
                        (AtomicMemory.expires_at.is_(None))
                        | (AtomicMemory.expires_at > current)
                    )
                    .order_by(AtomicMemory.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [self._to_record(row) for row in rows]

    async def get_memory_with_provenance(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> AtomicMemoryRecord | None:
        row = (
            (
                await self.db.execute(
                    select(AtomicMemory).where(
                        AtomicMemory.id == memory_id,
                        AtomicMemory.tenant_id == scope.tenant_id,
                        AtomicMemory.agent_id == scope.agent_id,
                        AtomicMemory.subject_type == scope.subject_type,
                        AtomicMemory.subject_id == scope.subject_id,
                        AtomicMemory.status != "deleted",
                    )
                )
            )
            .scalars()
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_record(row)

    # ------------------------------------------------------------------
    # Explicit activation
    # ------------------------------------------------------------------

    async def activate_explicit(
        self,
        scope: MemoryScope,
        candidate: MemoryCandidate,
        *,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        try:
            current = now or _now()
            existing = await self.list_active_memories(
                scope, normalized_key=candidate.normalized_key, now=current
            )
            supersedes_id = None
            if existing:
                supersedes_id = existing[0].id
                await self.db.execute(
                    update(AtomicMemory)
                    .where(AtomicMemory.id == existing[0].id)
                    .values(status="superseded", updated_at=current.isoformat())
                )

            row = AtomicMemory(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                subject_type=scope.subject_type,
                subject_id=scope.subject_id,
                memory_type=candidate.memory_type,
                normalized_key=candidate.normalized_key,
                content_json=json.dumps(candidate.content, ensure_ascii=False),
                search_text=build_search_text(candidate.content),
                status="active",
                source_kind=candidate.source_kind,
                source_conversation_id=conversation_id,
                source_message_ids_json=json.dumps([message_id], ensure_ascii=False),
                evidence_count=1,
                confidence_band="confirmed",
                sensitivity=candidate.sensitivity,
                supersedes_id=supersedes_id,
                observed_at=current,
                last_confirmed_at=current,
                expires_at=default_expires_at(candidate.memory_type, current),
            )
            self.db.add(row)
            await self.db.flush()
            await self._audit(
                scope,
                operation="remember",
                status="success",
                reason_code="explicit_confirmed",
                memory_id=row.id,
            )
            return MemoryOperationResult(
                operation="remember",
                status="success",
                response_text=f"已记住：{candidate.content.get('value', candidate.evidence_text)}",
                memory_ids=[row.id],
                reason_code="explicit_confirmed",
            )
        except Exception:
            logger.exception("activate_explicit failed")
            return MemoryOperationResult(
                operation="remember",
                status="failed",
                response_text="这条记忆没有保存成功，请稍后重试。",
                reason_code="write_failed",
            )

    # ------------------------------------------------------------------
    # Correction
    # ------------------------------------------------------------------

    async def correct_memory(
        self,
        scope: MemoryScope,
        *,
        normalized_key: str,
        new_candidate: MemoryCandidate,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        try:
            active = await self.list_active_memories(
                scope, normalized_key=normalized_key, now=now
            )
            result = await self.activate_explicit(
                scope,
                new_candidate,
                conversation_id=conversation_id,
                message_id=message_id,
                now=now,
            )
            if active:
                await self._audit(
                    scope,
                    operation="correct",
                    status="success",
                    reason_code="superseded_existing",
                    memory_id=active[0].id,
                )
                result.operation = "correct"
                result.reason_code = "superseded_existing"
                result.response_text = f"已更新记忆：{new_candidate.content.get('value', new_candidate.evidence_text)}"
            return result
        except Exception:
            logger.exception("correct_memory failed")
            return MemoryOperationResult(
                operation="correct",
                status="failed",
                response_text="这条记忆没有保存成功，请稍后重试。",
                reason_code="write_failed",
            )

    # ------------------------------------------------------------------
    # Forget
    # ------------------------------------------------------------------

    async def forget_memory(
        self,
        scope: MemoryScope,
        *,
        normalized_key: str | None,
        confirm_broad: bool,
    ) -> MemoryOperationResult:
        try:
            active = await self.list_active_memories(
                scope, normalized_key=normalized_key
            )
            if not active:
                return MemoryOperationResult(
                    operation="forget",
                    status="noop",
                    response_text="没有找到需要忘记的记忆。",
                    reason_code="no_matching_memory",
                )
            if normalized_key is None and not confirm_broad:
                return MemoryOperationResult(
                    operation="forget",
                    status="clarify",
                    response_text="你是想忘记所有关于你的长期记忆吗？请回复“确认忘记全部”。",
                    reason_code="broad_forget_requires_confirmation",
                )

            ids = [row.id for row in active]
            await self.db.execute(
                update(AtomicMemory)
                .where(AtomicMemory.id.in_(ids))
                .values(status="deleted", updated_at=_now().isoformat())
            )
            for memory_id in ids:
                await self._audit(
                    scope,
                    operation="forget",
                    status="success",
                    reason_code="user_requested",
                    memory_id=memory_id,
                )
            return MemoryOperationResult(
                operation="forget",
                status="success",
                response_text=f"已忘记 {len(ids)} 条相关记忆。",
                memory_ids=ids,
                reason_code="user_requested",
            )
        except Exception:
            logger.exception("forget_memory failed")
            return MemoryOperationResult(
                operation="forget",
                status="failed",
                response_text="没有成功忘记，请稍后重试。",
                reason_code="write_failed",
            )

    # ------------------------------------------------------------------
    # Candidate storage & corroboration
    # ------------------------------------------------------------------

    async def store_candidate(
        self,
        scope: MemoryScope,
        candidate: MemoryCandidate,
        *,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        current = now or _now()
        row = AtomicMemory(
            tenant_id=scope.tenant_id,
            agent_id=scope.agent_id,
            subject_type=scope.subject_type,
            subject_id=scope.subject_id,
            memory_type=candidate.memory_type,
            normalized_key=candidate.normalized_key,
            content_json=json.dumps(candidate.content, ensure_ascii=False),
            search_text=build_search_text(candidate.content),
            status="candidate",
            source_kind=candidate.source_kind,
            source_conversation_id=conversation_id,
            source_message_ids_json=json.dumps([message_id], ensure_ascii=False),
            evidence_count=1,
            confidence_band="candidate",
            sensitivity=candidate.sensitivity,
            observed_at=current,
            expires_at=default_expires_at(candidate.memory_type, current),
        )
        self.db.add(row)
        await self.db.flush()
        await self._audit(
            scope,
            operation="candidate",
            status="success",
            reason_code="stored_candidate",
            memory_id=row.id,
        )
        return MemoryOperationResult(
            operation="candidate",
            status="success",
            response_text="已记录为候选记忆，等待后续确认。",
            memory_ids=[row.id],
            reason_code="stored_candidate",
            candidate_count=1,
        )

    async def corroborate_candidate(
        self,
        scope: MemoryScope,
        candidate: MemoryCandidate,
        *,
        conversation_id: str,
        message_id: str,
        now: datetime | None = None,
    ) -> MemoryOperationResult:
        current = now or _now()
        content_json = json.dumps(candidate.content, ensure_ascii=False)
        existing = (
            (
                await self.db.execute(
                    select(AtomicMemory)
                    .where(
                        AtomicMemory.tenant_id == scope.tenant_id,
                        AtomicMemory.agent_id == scope.agent_id,
                        AtomicMemory.subject_type == scope.subject_type,
                        AtomicMemory.subject_id == scope.subject_id,
                        AtomicMemory.normalized_key == candidate.normalized_key,
                        AtomicMemory.status == "candidate",
                        AtomicMemory.content_json == content_json,
                    )
                    .order_by(AtomicMemory.created_at.asc())
                    .limit(1)
                )
            )
            .scalars()
            .one_or_none()
        )
        if existing is None:
            return await self.store_candidate(
                scope,
                candidate,
                conversation_id=conversation_id,
                message_id=message_id,
                now=current,
            )

        source_ids = list(
            dict.fromkeys(
                json.loads(existing.source_message_ids_json) + [message_id]
            )
        )
        existing.source_message_ids_json = json.dumps(source_ids, ensure_ascii=False)
        existing.evidence_count = len(source_ids)
        existing.updated_at = current.isoformat()
        if existing.evidence_count >= 2:
            active = await self.list_active_memories(
                scope, normalized_key=candidate.normalized_key, now=current
            )
            if active:
                await self.db.execute(
                    update(AtomicMemory)
                    .where(AtomicMemory.id == active[0].id)
                    .values(status="superseded", updated_at=current.isoformat())
                )
                existing.supersedes_id = active[0].id
            existing.status = "active"
            existing.confidence_band = "corroborated"
            existing.last_confirmed_at = current
            existing.expires_at = default_expires_at(candidate.memory_type, current)
            await self.db.flush()
            await self._audit(
                scope,
                operation="candidate",
                status="success",
                reason_code="corroborated_two_evidence",
                memory_id=existing.id,
            )
            return MemoryOperationResult(
                operation="candidate",
                status="success",
                response_text="候选记忆已通过两次用户证据确认。",
                memory_ids=[existing.id],
                reason_code="corroborated_two_evidence",
                candidate_count=0,
            )

        await self.db.flush()
        return MemoryOperationResult(
            operation="candidate",
            status="success",
            response_text="已补充候选记忆证据。",
            memory_ids=[existing.id],
            reason_code="stored_candidate",
            candidate_count=1,
        )

    # ------------------------------------------------------------------
    # Outbox
    # ------------------------------------------------------------------

    async def enqueue_inferred_job(
        self,
        scope: MemoryScope,
        *,
        conversation_id: str,
        event_id: str,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> None:
        current = now or _now()
        self.db.add(
            MemoryOutboxJob(
                tenant_id=scope.tenant_id,
                agent_id=scope.agent_id,
                user_id=scope.user_id,
                conversation_id=conversation_id,
                event_id=event_id,
                operation="infer_candidates",
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
                attempts=0,
                available_at=current,
            )
        )
        await self.db.flush()

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    async def expire_due_memories(
        self, now: datetime | None = None
    ) -> ExpiryResult:
        current = now or _now()
        rows = (
            (
                await self.db.execute(
                    select(AtomicMemory)
                    .where(AtomicMemory.status == "active")
                    .where(AtomicMemory.expires_at.is_not(None))
                    .where(AtomicMemory.expires_at <= current)
                )
            )
            .scalars()
            .all()
        )
        ids = [row.id for row in rows]
        if ids:
            await self.db.execute(
                update(AtomicMemory)
                .where(AtomicMemory.id.in_(ids))
                .values(status="expired", updated_at=current.isoformat())
            )
        return ExpiryResult(expired_count=len(ids), memory_ids=ids)
