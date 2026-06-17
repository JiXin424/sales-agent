"""反馈路由 — 使用独立反馈表。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import (
    FeedbackDetailResponse,
    FeedbackListResponse,
    FeedbackRequest,
    FeedbackResponse,
    ReviewStatusUpdate,
)
from sales_agent.services.feedback_service import FeedbackService
from sales_agent.services.tenant_resolver import TenantResolver

router = APIRouter(tags=["feedback"])


async def _verify_tenant(tenant_id: str, db: AsyncSession) -> None:
    """校验租户存在。"""
    resolver = TenantResolver(db)
    try:
        await resolver.resolve(tenant_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Tenant not found: {tenant_id}")


def _feedback_to_response(fb: any) -> FeedbackDetailResponse:
    """ORM 对象转 Pydantic 响应。"""
    import json
    return FeedbackDetailResponse(
        id=fb.id,
        tenant_id=fb.tenant_id,
        conversation_id=fb.conversation_id,
        user_id=fb.user_id,
        rating=fb.rating,
        feedback_text=fb.feedback_text or "",
        labels=json.loads(fb.labels_json) if fb.labels_json else [],
        review_status=getattr(fb, "review_status", "open"),
        created_at=fb.created_at,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest, db: DbSession):
    """提交反馈（点赞/点踩 + 文字）。

    使用独立 Feedback 表存储。
    """
    svc = FeedbackService(db)
    try:
        await svc.submit(
            tenant_id=req.tenant_id or "",
            conversation_id=req.conversation_id,
            user_id=req.user_id or "anonymous",
            rating=req.rating,
            feedback_text=req.feedback_text,
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    return FeedbackResponse(
        status="ok",
        conversation_id=req.conversation_id,
    )


# --- 租户级反馈查询 ---


@router.get(
    "/tenants/{tenant_id}/feedback",
    response_model=FeedbackListResponse,
    tags=["feedback"],
)
async def list_tenant_feedback(
    tenant_id: str,
    db: DbSession,
    rating: str | None = Query(None),
    review_status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出租户的反馈。"""
    await _verify_tenant(tenant_id, db)
    svc = FeedbackService(db)
    items, total = await svc.list_by_tenant(
        tenant_id=tenant_id,
        rating=rating,
        review_status=review_status,
        limit=limit,
        offset=offset,
    )
    return FeedbackListResponse(
        items=[_feedback_to_response(fb) for fb in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tenants/{tenant_id}/feedback/{feedback_id}",
    response_model=FeedbackDetailResponse,
    tags=["feedback"],
)
async def get_feedback_detail(
    tenant_id: str,
    feedback_id: str,
    db: DbSession,
):
    """获取单条反馈详情。"""
    await _verify_tenant(tenant_id, db)
    svc = FeedbackService(db)
    fb = await svc.get_by_id(tenant_id, feedback_id)
    if fb is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return _feedback_to_response(fb)


@router.patch(
    "/tenants/{tenant_id}/feedback/{feedback_id}/review",
    response_model=FeedbackDetailResponse,
    tags=["feedback"],
)
async def update_feedback_review(
    tenant_id: str,
    feedback_id: str,
    req: ReviewStatusUpdate,
    db: DbSession,
):
    """更新反馈的处理状态（open / reviewed / ignored）。"""
    await _verify_tenant(tenant_id, db)
    svc = FeedbackService(db)
    try:
        fb = await svc.update_review_status(
            tenant_id=tenant_id,
            feedback_id=feedback_id,
            review_status=req.review_status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _feedback_to_response(fb)
