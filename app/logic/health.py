"""Health check endpoint — used by load balancers, monitoring, and the /health page."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.redis_client import get_failure_count, is_redis_available
from app.services.load_balancer import get_load_balancer

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    redis_available: bool
    redis_failure_count: int
    rate_limiting_backend: str
    backends: list


@router.get("/health", response_model=HealthResponse, summary="Gateway health check")
async def health():
    redis_up = is_redis_available()
    lb = get_load_balancer()
    return HealthResponse(
        status="ok",
        redis_available=redis_up,
        redis_failure_count=get_failure_count(),
        rate_limiting_backend="redis" if redis_up else "in-memory",
        backends=lb.status()["backends"],
    )