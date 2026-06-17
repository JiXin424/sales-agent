"""Prompt 管理 API：版本 CRUD、激活、归档、预览。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import (
    PromptPreviewRequest,
    PromptPreviewResponse,
    PromptVersionCreate,
    PromptVersionListResponse,
    PromptVersionResponse,
    PromptVersionUpdate,
)
from sales_agent.services.prompt_registry import PromptRegistry
from sales_agent.services.tenant_resolver import TenantResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants/{tenant_id}/prompts", tags=["prompts"])


async def _verify_tenant(tenant_id: str, db: AsyncSession) -> None:
    """校验租户存在。"""
    resolver = TenantResolver(db)
    try:
        await resolver.resolve(tenant_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")


def _version_to_response(pv: Any) -> PromptVersionResponse:
    """ORM 对象转 Pydantic 响应。"""
    return PromptVersionResponse(
        id=pv.id,
        tenant_id=pv.tenant_id,
        task_type=pv.task_type,
        version=pv.version,
        status=pv.status,
        template_text=pv.template_text,
        description=pv.description or "",
        created_at=pv.created_at,
        updated_at=pv.updated_at,
    )


@router.post("", response_model=PromptVersionResponse, status_code=201)
async def create_prompt_version(
    tenant_id: str,
    req: PromptVersionCreate,
    db: DbSession,
):
    """创建一个 draft 版本的 prompt。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    try:
        pv = await registry.create_version(
            tenant_id=tenant_id,
            task_type=req.task_type,
            template_text=req.template_text,
            description=req.description,
            version=req.version,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _version_to_response(pv)


@router.get("", response_model=PromptVersionListResponse)
async def list_prompt_versions(
    tenant_id: str,
    db: DbSession,
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出租户的 prompt 版本。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    try:
        versions, total = await registry.list_versions(
            tenant_id=tenant_id,
            task_type=task_type,
            status=status,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PromptVersionListResponse(
        items=[_version_to_response(v) for v in versions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{version_id}", response_model=PromptVersionResponse)
async def get_prompt_version(
    tenant_id: str,
    version_id: str,
    db: DbSession,
):
    """获取特定 prompt 版本。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    pv = await registry.get_version(tenant_id, version_id)
    if pv is None:
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return _version_to_response(pv)


@router.put("/{version_id}", response_model=PromptVersionResponse)
async def update_prompt_version(
    tenant_id: str,
    version_id: str,
    req: PromptVersionUpdate,
    db: DbSession,
):
    """更新 draft 版本的 prompt 内容。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    try:
        pv = await registry.update_draft(
            tenant_id=tenant_id,
            version_id=version_id,
            template_text=req.template_text,
            description=req.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _version_to_response(pv)


@router.post("/{version_id}/activate", response_model=PromptVersionResponse)
async def activate_prompt_version(
    tenant_id: str,
    version_id: str,
    db: DbSession,
):
    """激活一个 draft 版本（同 tenant+task_type 的当前 active 版本自动归档）。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    try:
        pv = await registry.activate_version(tenant_id, version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _version_to_response(pv)


@router.post("/{version_id}/archive", response_model=PromptVersionResponse)
async def archive_prompt_version(
    tenant_id: str,
    version_id: str,
    db: DbSession,
):
    """归档一个 prompt 版本。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    try:
        pv = await registry.archive_version(tenant_id, version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _version_to_response(pv)


@router.post("/preview", response_model=PromptPreviewResponse)
async def preview_prompt(
    tenant_id: str,
    req: PromptPreviewRequest,
    db: DbSession,
):
    """预览 prompt：渲染模板，可选执行模型生成。

    不会创建 Conversation 或 ConversationMessage 记录。
    """
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)

    # 解析 prompt 文本
    version_id = req.version_id
    template_text: str | None = None

    if version_id:
        pv = await registry.get_version(tenant_id, version_id)
        if pv is None:
            raise HTTPException(status_code=404, detail="Prompt version not found")
        template_text = pv.template_text
    else:
        # 使用当前 active 或默认
        try:
            template_text = await registry.resolve(tenant_id, req.task_type)
        except ValueError:
            pass

    if not template_text:
        raise HTTPException(status_code=404, detail="No prompt found for preview")

    # 渲染 prompt（使用 agent_executor 的 _build_messages 辅助）
    from sales_agent.services.agent_executor import (
        _build_context_block,
        _build_retrieval_block,
    )

    context_block = _build_context_block(req.sample_context)
    retrieval_block = ""  # 预览不执行真实检索

    rendered = template_text.format(
        message=req.sample_message,
        context_block=context_block,
        retrieval_block=retrieval_block,
        retrieval_content="（预览模式，不执行检索）",
    )

    # 可选：执行模型生成
    model_output: str | None = None
    if req.run_generation:
        try:
            resolver = TenantResolver(db)
            tenant_info = await resolver.resolve(tenant_id)
            model_provider = resolver.get_model_provider(tenant_info)

            from sales_agent.prompts.system import SYSTEM_CONSTRAINT
            messages = [
                {"role": "system", "content": SYSTEM_CONSTRAINT},
                {"role": "user", "content": rendered},
            ]
            model_output = await model_provider.chat.generate(
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
            )
        except Exception as e:
            logger.warning("Preview generation failed: %s", e)
            model_output = f"[Generation failed: {e}]"

    return PromptPreviewResponse(
        rendered_prompt=rendered,
        model_output=model_output,
        version_id=version_id,
        task_type=req.task_type,
    )
