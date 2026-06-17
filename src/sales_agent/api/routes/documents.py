"""知识导入路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import IngestRequest, IngestResponse
from sales_agent.core.exceptions import SalesAgentError, TenantNotFoundError
from sales_agent.llm.base import EmbeddingModel
from sales_agent.services.knowledge_ingestor import KnowledgeIngestor
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants", tags=["documents"])


@router.post("/{tenant_id}/documents/ingest", response_model=IngestResponse)
async def ingest_documents(
    tenant_id: str,
    req: IngestRequest,
    db: DbSession,
) -> IngestResponse:
    """导入知识库文档。

    解析指定目录中的所有 Markdown 文件，切分为块，生成向量，并存入数据库。
    """
    # 1. Verify tenant exists and is active.
    resolver = TenantResolver(db)
    try:
        tenant_info = await resolver.resolve(tenant_id)
    except TenantNotFoundError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    except SalesAgentError as exc:
        raise HTTPException(status_code=403, detail=exc.user_message)

    # 2. Build model provider for tenant and extract the embedding model.
    provider = resolver.get_model_provider(tenant_info)
    embedding_model: EmbeddingModel = provider.embedding

    # 3. Run the ingestion pipeline.
    ingestor = KnowledgeIngestor(db=db, embedding_model=embedding_model)
    try:
        result = await ingestor.ingest_directory(
            tenant_id=tenant_id,
            directory=req.path,
            rebuild_index=req.rebuild_index,
        )
    except Exception as exc:
        logger.error("Ingestion failed for tenant %s: %s", tenant_id, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Knowledge ingestion failed: {exc}",
        )

    # 4. Build and return response.
    status = "completed" if not result["errors"] else "completed_with_errors"
    return IngestResponse(
        tenant_id=tenant_id,
        status=status,
        documents_seen=result["documents_seen"],
        documents_ingested=result["documents_ingested"],
        chunks_created=result["chunks_created"],
        warnings=result["warnings"],
        errors=result["errors"],
    )
