"""Admin endpoints — protected, require role='admin'."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_admin
from app.models.request_log import RequestLog
from app.models.user import User
from app.services.load_balancer import get_load_balancer

router = APIRouter()


@router.get("/logs", summary="List recent proxied requests (admin only)")
async def list_logs(
    limit: int = Query(50, ge=1, le=500),
    user_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = select(RequestLog).order_by(RequestLog.created_at.desc()).limit(limit)
    if user_id:
        q = q.where(RequestLog.user_id == user_id)
    result = await db.execute(q)
    logs = result.scalars().all()
    return [
        {
            "id": l.id,
            "request_id": l.request_id,
            "user_id": l.user_id,
            "method": l.method,
            "path": l.path,
            "upstream_url": l.upstream_url,
            "status_code": l.status_code,
            "duration_ms": l.duration_ms,
            "rate_limit_backend": l.rate_limit_backend,
            "created_at": l.created_at.isoformat(),
        }
        for l in logs
    ]


@router.get("/stats", summary="Aggregate request stats (admin only)")
async def stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    total = await db.execute(select(func.count()).select_from(RequestLog))
    avg_duration = await db.execute(
        select(func.avg(RequestLog.duration_ms))
    )
    lb = get_load_balancer()
    return {
        "total_requests": total.scalar(),
        "avg_duration_ms": round(avg_duration.scalar() or 0, 2),
        "load_balancer": lb.status(),
    }