from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func

from sales_agent.api.deps import DbSession
from sales_agent.core.config import get_settings
from sales_agent.models.ingestion import IngestionJob
from sales_agent.ontology.neo4j_client import Neo4jClient
from sales_agent.services.agent_service import AgentService, AgentNotFoundError

router = APIRouter(prefix="/agents", tags=["ontology"])


async def _load_agent_or_404(agent_id: str, db: DbSession):
    try:
        return await AgentService(db).get_agent(agent_id)
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/ontology/status")
async def get_ontology_status(agent_id: str, db: DbSession):
    await _load_agent_or_404(agent_id, db)
    settings = get_settings()
    client = Neo4jClient(settings.neo4j)
    ready = False
    if client.enabled:
        ready, _detail = await client.verify_connectivity()
        await client.close()
    return {
        "knowledge_engine": settings.ontology.knowledge_engine,
        "ontology_status": "ready" if ready else ("not_configured" if not client.enabled else "failed"),
        "neo4j_configured": client.enabled,
        "neo4j_ready": ready,
        "visual_url": settings.neo4j.visual_url,
    }


@router.post("/{agent_id}/ontology/ingest", status_code=202)
async def start_ontology_ingest(agent_id: str, body: dict, db: DbSession):
    agent = await _load_agent_or_404(agent_id, db)
    path = body.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    job = IngestionJob(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        engine="ontology_neo4j",
        status="queued",
        stage="queued",
        metadata_json=json.dumps({"path": path}, ensure_ascii=False),
    )
    db.add(job)
    await db.flush()
    return _job_to_dict(job)


@router.get("/{agent_id}/ontology/jobs")
async def list_ontology_jobs(agent_id: str, db: DbSession, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    agent = await _load_agent_or_404(agent_id, db)
    conds = [IngestionJob.tenant_id == agent.tenant_id, IngestionJob.agent_id == agent.id, IngestionJob.engine == "ontology_neo4j"]
    total = (await db.execute(select(func.count()).select_from(IngestionJob).where(*conds))).scalar() or 0
    rows = (await db.execute(
        select(IngestionJob).where(*conds).order_by(IngestionJob.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return {"items": [_job_to_dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def _job_to_dict(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "tenant_id": job.tenant_id,
        "agent_id": job.agent_id,
        "engine": job.engine,
        "status": job.status,
        "stage": job.stage,
        "documents_seen": job.documents_seen,
        "documents_ingested": job.documents_ingested,
        "entities_created": job.entities_created,
        "entities_merged": job.entities_merged,
        "facts_created": job.facts_created,
        "facts_active": job.facts_active,
        "facts_pending_review": job.facts_pending_review,
        "facts_rejected": job.facts_rejected,
        "conflicts_created": job.conflicts_created,
        "warnings": json.loads(job.warnings_json or "[]"),
        "errors": json.loads(job.errors_json or "[]"),
        "error_summary": job.error_summary,
        "metadata": json.loads(job.metadata_json or "{}"),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
