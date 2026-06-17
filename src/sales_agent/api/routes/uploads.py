"""知识上传与导入任务 API。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import IngestionJobListResponse, IngestionJobResponse, UploadResponse
from sales_agent.models.document import SourceFile
from sales_agent.models.ingestion import IngestionJob
from sales_agent.models.base import generate_id
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants/{tenant_id}/knowledge", tags=["knowledge"])


async def _verify_tenant(tenant_id: str, db: AsyncSession) -> None:
    """校验租户存在。"""
    resolver = TenantResolver(db)
    try:
        await resolver.resolve(tenant_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")


def _get_upload_dir(tenant_id: str) -> Path:
    """获取租户上传目录。"""
    upload_dir = Path(f"data/{tenant_id}/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _job_to_response(job: IngestionJob) -> IngestionJobResponse:
    """ORM 对象转 Pydantic 响应。"""
    return IngestionJobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        source_file_id=job.source_file_id,
        document_id=job.document_id,
        status=job.status,
        documents_seen=job.documents_seen,
        documents_ingested=job.documents_ingested,
        chunks_created=job.chunks_created,
        warnings=json.loads(job.warnings_json) if job.warnings_json else [],
        errors=json.loads(job.errors_json) if job.errors_json else [],
        error_summary=job.error_summary,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_knowledge_file(
    tenant_id: str,
    db: DbSession,
    file: UploadFile = File(...),
):
    """上传知识库文件（当前仅支持 .md）。

    创建 SourceFile + IngestionJob，然后同步处理导入。
    """
    await _verify_tenant(tenant_id, db)

    # 校验文件类型
    filename = file.filename or "unknown.md"
    if not filename.lower().endswith(".md"):
        raise HTTPException(
            status_code=400,
            detail="Only .md files are supported in Phase A.",
        )

    # 读取文件内容
    content_bytes = await file.read()
    content_text = content_bytes.decode("utf-8")
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    # 保存到磁盘
    upload_dir = _get_upload_dir(tenant_id)
    stored_path = upload_dir / f"{content_hash}.md"
    stored_path.write_text(content_text, encoding="utf-8")

    # 创建 SourceFile 记录
    sf_id = generate_id()
    source_file = SourceFile(
        id=sf_id,
        tenant_id=tenant_id,
        original_filename=filename,
        stored_path=str(stored_path),
        content_hash=content_hash,
        mime_type="text/markdown",
        status="pending",
    )
    db.add(source_file)

    # 创建 IngestionJob
    job_id = generate_id()
    job = IngestionJob(
        id=job_id,
        tenant_id=tenant_id,
        source_file_id=sf_id,
        status="queued",
    )
    db.add(job)
    await db.flush()

    # 同步处理导入
    try:
        job.status = "running"
        await db.flush()

        # 获取 tenant 的 embedding model
        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)

        from sales_agent.services.knowledge_ingestor import KnowledgeIngestor
        ingestor = KnowledgeIngestor(db, model_provider.embedding)

        result = await ingestor.ingest_directory(
            tenant_id=tenant_id,
            directory=upload_dir,
            rebuild_index=False,
        )

        # 更新 job 状态
        job.documents_seen = result["documents_seen"]
        job.documents_ingested = result["documents_ingested"]
        job.chunks_created = result["chunks_created"]
        job.warnings_json = json.dumps(result.get("warnings", []), ensure_ascii=False)
        job.errors_json = json.dumps(result.get("errors", []), ensure_ascii=False)

        if result.get("errors"):
            job.status = "completed_with_errors"
            job.error_summary = f"{len(result['errors'])} error(s) during ingestion"
        else:
            job.status = "completed"

        source_file.status = "active"
        await db.flush()

    except Exception as e:
        logger.error("Ingestion failed for job %s: %s", job_id, e, exc_info=True)
        job.status = "failed"
        job.error_summary = str(e)
        job.errors_json = json.dumps([{"error": str(e)}], ensure_ascii=False)
        source_file.status = "error"
        await db.flush()

    return UploadResponse(
        job_id=job_id,
        source_file_id=sf_id,
        status=job.status,
    )


@router.get("/jobs", response_model=IngestionJobListResponse)
async def list_ingestion_jobs(
    tenant_id: str,
    db: DbSession,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出导入任务。"""
    await _verify_tenant(tenant_id, db)

    conditions = [IngestionJob.tenant_id == tenant_id]
    if status:
        conditions.append(IngestionJob.status == status)

    from sqlalchemy import func
    count_stmt = select(func.count()).select_from(IngestionJob).where(*conditions)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    stmt = (
        select(IngestionJob)
        .where(*conditions)
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    jobs = list(result.scalars().all())

    return IngestionJobListResponse(
        items=[_job_to_response(j) for j in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=IngestionJobResponse)
async def get_ingestion_job(
    tenant_id: str,
    job_id: str,
    db: DbSession,
):
    """获取导入任务详情。"""
    await _verify_tenant(tenant_id, db)

    stmt = select(IngestionJob).where(
        IngestionJob.id == job_id,
        IngestionJob.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return _job_to_response(job)


@router.post("/jobs/{job_id}/retry", response_model=IngestionJobResponse)
async def retry_ingestion_job(
    tenant_id: str,
    job_id: str,
    db: DbSession,
):
    """重试一个失败的导入任务。"""
    await _verify_tenant(tenant_id, db)

    stmt = select(IngestionJob).where(
        IngestionJob.id == job_id,
        IngestionJob.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if job.status not in ("failed", "completed_with_errors"):
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried.")

    # 查找 source file
    sf_stmt = select(SourceFile).where(
        SourceFile.id == job.source_file_id,
        SourceFile.tenant_id == tenant_id,
    )
    sf_result = await db.execute(sf_stmt)
    sf = sf_result.scalar_one_or_none()

    if sf is None:
        raise HTTPException(status_code=404, detail="Source file not found")

    # 创建新的重试 job
    retry_job = IngestionJob(
        id=generate_id(),
        tenant_id=tenant_id,
        source_file_id=sf.id,
        status="queued",
        metadata_json=json.dumps({"retry_of": job_id}, ensure_ascii=False),
    )
    db.add(retry_job)
    await db.flush()

    # 同步重试导入
    try:
        retry_job.status = "running"
        await db.flush()

        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)

        from sales_agent.services.knowledge_ingestor import KnowledgeIngestor
        ingestor = KnowledgeIngestor(db, model_provider.embedding)

        file_dir = Path(sf.stored_path).parent
        result_data = await ingestor.ingest_directory(
            tenant_id=tenant_id,
            directory=file_dir,
            rebuild_index=True,
        )

        retry_job.documents_seen = result_data["documents_seen"]
        retry_job.documents_ingested = result_data["documents_ingested"]
        retry_job.chunks_created = result_data["chunks_created"]
        retry_job.warnings_json = json.dumps(result_data.get("warnings", []), ensure_ascii=False)
        retry_job.errors_json = json.dumps(result_data.get("errors", []), ensure_ascii=False)
        retry_job.status = "completed" if not result_data.get("errors") else "completed_with_errors"

        sf.status = "active"
        await db.flush()

    except Exception as e:
        logger.error("Retry ingestion failed for job %s: %s", retry_job.id, e, exc_info=True)
        retry_job.status = "failed"
        retry_job.error_summary = str(e)
        await db.flush()

    return _job_to_response(retry_job)


@router.post("/documents/{document_id}/reindex", response_model=IngestionJobResponse, status_code=201)
async def reindex_document(
    tenant_id: str,
    document_id: str,
    db: DbSession,
):
    """对已有文档创建重新索引任务。"""
    await _verify_tenant(tenant_id, db)

    from sales_agent.models.document import Document
    stmt = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # 创建 reindex job
    reindex_job = IngestionJob(
        id=generate_id(),
        tenant_id=tenant_id,
        document_id=document_id,
        status="queued",
        metadata_json=json.dumps({"reindex": True, "document_id": document_id}, ensure_ascii=False),
    )
    db.add(reindex_job)
    await db.flush()

    # 同步执行 reindex
    try:
        reindex_job.status = "running"
        await db.flush()

        resolver = TenantResolver(db)
        tenant_info = await resolver.resolve(tenant_id)
        model_provider = resolver.get_model_provider(tenant_info)

        from sales_agent.services.knowledge_ingestor import KnowledgeIngestor
        ingestor = KnowledgeIngestor(db, model_provider.embedding)

        # 使用文档的 source_path 作为目录
        source_path = doc.source_path
        if source_path:
            file_dir = Path(source_path).parent if Path(source_path).is_file() else Path(source_path)
        else:
            raise ValueError("Document has no source_path for reindex")

        result_data = await ingestor.ingest_directory(
            tenant_id=tenant_id,
            directory=file_dir,
            rebuild_index=True,
        )

        reindex_job.documents_seen = result_data["documents_seen"]
        reindex_job.documents_ingested = result_data["documents_ingested"]
        reindex_job.chunks_created = result_data["chunks_created"]
        reindex_job.warnings_json = json.dumps(result_data.get("warnings", []), ensure_ascii=False)
        reindex_job.errors_json = json.dumps(result_data.get("errors", []), ensure_ascii=False)
        reindex_job.status = "completed" if not result_data.get("errors") else "completed_with_errors"
        await db.flush()

    except Exception as e:
        logger.error("Reindex failed for document %s: %s", document_id, e, exc_info=True)
        reindex_job.status = "failed"
        reindex_job.error_summary = str(e)
        await db.flush()

    return _job_to_response(reindex_job)
