"""Ingestion Job 单元测试。"""

from __future__ import annotations

import json
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.ingestion import IngestionJob
from sales_agent.models.document import SourceFile


class TestIngestionJob:
    @pytest.mark.asyncio
    async def test_create_job(self, db_session: AsyncSession, sample_tenant: str):
        """创建 IngestionJob 记录。"""
        job = IngestionJob(
            tenant_id=sample_tenant,
            status="queued",
        )
        db_session.add(job)
        await db_session.flush()

        assert job.id is not None
        assert job.status == "queued"
        assert job.documents_seen == 0
        assert job.chunks_created == 0

    @pytest.mark.asyncio
    async def test_job_status_transitions(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """任务状态转换：queued → running → completed。"""
        job = IngestionJob(
            tenant_id=sample_tenant,
            status="queued",
        )
        db_session.add(job)
        await db_session.flush()

        # queued → running
        job.status = "running"
        await db_session.flush()

        # running → completed
        job.status = "completed"
        job.documents_seen = 1
        job.documents_ingested = 1
        job.chunks_created = 5
        await db_session.flush()

        stmt = select(IngestionJob).where(IngestionJob.id == job.id)
        refreshed = (await db_session.execute(stmt)).scalar_one()
        assert refreshed.status == "completed"
        assert refreshed.chunks_created == 5

    @pytest.mark.asyncio
    async def test_failed_job_records_errors(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """失败的任务应记录错误信息。"""
        job = IngestionJob(
            tenant_id=sample_tenant,
            status="running",
        )
        db_session.add(job)
        await db_session.flush()

        job.status = "failed"
        job.error_summary = "Embedding model timeout"
        job.errors_json = json.dumps(
            [{"error": "Connection timeout after 30s"}],
            ensure_ascii=False,
        )
        await db_session.flush()

        stmt = select(IngestionJob).where(IngestionJob.id == job.id)
        refreshed = (await db_session.execute(stmt)).scalar_one()
        assert refreshed.status == "failed"
        assert refreshed.error_summary == "Embedding model timeout"
        errors = json.loads(refreshed.errors_json)
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_completed_with_errors(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """部分成功的任务应标记 completed_with_errors。"""
        job = IngestionJob(
            tenant_id=sample_tenant,
            status="running",
        )
        db_session.add(job)
        await db_session.flush()

        job.status = "completed_with_errors"
        job.documents_seen = 2
        job.documents_ingested = 1
        job.chunks_created = 3
        job.warnings_json = json.dumps(["File B had encoding issues"])
        job.errors_json = json.dumps([{"file": "b.md", "error": "Invalid encoding"}])
        await db_session.flush()

        stmt = select(IngestionJob).where(IngestionJob.id == job.id)
        refreshed = (await db_session.execute(stmt)).scalar_one()
        assert refreshed.status == "completed_with_errors"
        assert refreshed.documents_seen == 2
        warnings = json.loads(refreshed.warnings_json)
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_job_with_source_file(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """任务应关联 SourceFile。"""
        sf = SourceFile(
            tenant_id=sample_tenant,
            original_filename="test.md",
            stored_path="/data/test/test.md",
            content_hash="abc123",
            mime_type="text/markdown",
            status="pending",
        )
        db_session.add(sf)
        await db_session.flush()

        job = IngestionJob(
            tenant_id=sample_tenant,
            source_file_id=sf.id,
            status="queued",
        )
        db_session.add(job)
        await db_session.flush()

        assert job.source_file_id == sf.id

        # 查询关联
        stmt = select(IngestionJob).where(IngestionJob.source_file_id == sf.id)
        result = await db_session.execute(stmt)
        found = result.scalar_one()
        assert found.id == job.id
