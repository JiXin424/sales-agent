"""Prompt 管理 API：版本 CRUD、激活、归档、预览。"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import (
    BuiltinPromptResponse,
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
    try:
        placeholders = json.loads(pv.required_placeholders_json or "[]")
    except (ValueError, TypeError):
        placeholders = []
    return PromptVersionResponse(
        id=pv.id,
        tenant_id=pv.tenant_id,
        task_type=pv.task_type,
        prompt_category=pv.prompt_category,
        prompt_key=pv.prompt_key,
        required_placeholders=placeholders,
        version=pv.version,
        status=pv.status,
        template_text=pv.template_text,
        description=pv.description or "",
        created_at=pv.created_at,
        updated_at=pv.updated_at,
    )


class _SafeFormatDict(dict):
    """``format_map`` 用的宽容 dict：缺失占位符返回空串而非 KeyError。"""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


def _render_template_for_category(
    category: str,
    key: str,
    template_text: str,
    message: str,
    context: dict[str, Any] | None,
    sample_variables: dict[str, str] | None,
) -> str:
    """按 category 渲染 prompt 模板用于预览。

    task 类沿用 agent_executor 的渲染变量（message/context_block/retrieval_*）；
    其他类（router/risk/coach）用 ``sample_variables`` 提供占位符值，message 兜底。
    缺失占位符渲染为空串（预览场景宽容，避免 500）。
    """
    values: dict[str, Any] = dict(sample_variables or {})
    values.setdefault("message", message)

    if category == "task":
        from sales_agent.services.agent_executor import _build_context_block

        values["context_block"] = _build_context_block(context)
        values.setdefault("retrieval_block", "")
        values.setdefault("retrieval_content", "（预览模式，不执行检索）")

    return template_text.format_map(_SafeFormatDict(values))


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
            prompt_category=req.prompt_category,
            prompt_key=req.prompt_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _version_to_response(pv)


@router.get("/builtin", response_model=list[BuiltinPromptResponse])
async def list_builtin_prompts(
    tenant_id: str,
    db: DbSession,
):
    """列出所有内置 prompt（供前端展示默认模板与占位符，只读）。"""
    await _verify_tenant(tenant_id, db)
    from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS

    return [
        BuiltinPromptResponse(
            prompt_category=b.category,
            prompt_key=b.key,
            template=b.template,
            required_placeholders=list(b.required_placeholders),
            description=b.description,
        )
        for b in BUILTIN_PROMPTS
    ]


@router.get("", response_model=PromptVersionListResponse)
async def list_prompt_versions(
    tenant_id: str,
    db: DbSession,
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    prompt_category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出租户的 prompt 版本。可按 task_type 或 prompt_category 过滤。"""
    await _verify_tenant(tenant_id, db)
    registry = PromptRegistry(db)
    try:
        versions, total = await registry.list_versions(
            tenant_id=tenant_id,
            task_type=task_type,
            status=status,
            prompt_category=prompt_category,
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

    category = req.prompt_category or "task"
    key = req.prompt_key or req.task_type
    if not key:
        raise HTTPException(status_code=400, detail="prompt_key or task_type is required")

    # 解析 prompt 文本
    version_id = req.version_id
    template_text: str | None = None

    if version_id:
        pv = await registry.get_version(tenant_id, version_id)
        if pv is None:
            raise HTTPException(status_code=404, detail="Prompt version not found")
        template_text = pv.template_text
        category = pv.prompt_category or category
        key = pv.prompt_key or pv.task_type or key
    else:
        # 使用当前 active 或内置默认
        try:
            template_text = await registry.resolve_prompt(category, key, tenant_id)
        except ValueError:
            pass

    if not template_text:
        raise HTTPException(status_code=404, detail="No prompt found for preview")

    rendered = _render_template_for_category(
        category,
        key,
        template_text,
        message=req.sample_message,
        context=req.sample_context,
        sample_variables=req.sample_variables,
    )

    # 可选：执行模型生成（system 消息走 registry 解析，不再硬编码 SYSTEM_CONSTRAINT）
    model_output: str | None = None
    if req.run_generation:
        try:
            resolver = TenantResolver(db)
            tenant_info = await resolver.resolve(tenant_id)
            model_provider = resolver.get_model_provider(tenant_info)
            system_prompt = await registry.resolve_prompt(
                "system", "system_constraint", tenant_id
            )
            messages = [
                {"role": "system", "content": system_prompt},
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
        prompt_category=category,
        prompt_key=key,
        task_type=req.task_type,
    )
