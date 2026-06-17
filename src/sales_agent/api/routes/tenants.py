"""租户管理路由。"""

import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.api.deps import DbSession
from sales_agent.api.schemas import CreateTenantRequest, TenantResponse
from sales_agent.core.exceptions import ERROR_HTTP_STATUS, TenantNotFoundError
from sales_agent.models.tenant import Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("")
async def list_tenants(
    db: DbSession,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出所有租户。"""
    conditions = []
    if status:
        conditions.append(Tenant.status == status)

    count_stmt = select(func.count()).select_from(Tenant)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
    if conditions:
        stmt = stmt.where(*conditions)
    rows = (await db.execute(stmt)).scalars().all()

    return {
        "items": [
            {
                "tenant_id": t.id,
                "name": t.name,
                "status": t.status,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", response_model=TenantResponse)
async def create_or_update_tenant(
    req: CreateTenantRequest,
    db: DbSession,
):
    """创建或更新租户。"""
    # 检查是否已存在
    stmt = select(Tenant).where(Tenant.id == req.tenant_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    config_dict = req.config.model_dump()
    config_json = json.dumps(config_dict, ensure_ascii=False)

    if existing:
        existing.name = req.name
        existing.config_json = config_json
        existing.status = "active"
        await db.flush()
        tenant = existing
    else:
        tenant = Tenant(
            id=req.tenant_id,
            name=req.name,
            config_json=config_json,
            status="active",
        )
        db.add(tenant)
        await db.flush()

    return TenantResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        status=tenant.status,
        config=config_dict,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: str,
    db: DbSession,
):
    """获取租户信息。"""
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TENANT_NOT_FOUND",
                "message": "当前企业未开通或配置不存在",
                "detail": f"tenant_id={tenant_id} was not found",
            },
        )

    config = json.loads(tenant.config_json) if tenant.config_json else {}
    return TenantResponse(
        tenant_id=tenant.id,
        name=tenant.name,
        status=tenant.status,
        config=config,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )
