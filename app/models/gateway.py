"""
Gateway / Proxy Router — the core of the project.

Request flow:
  1. JWT auth → extract user
  2. Rate limit check (Redis or in-memory fallback)
  3. Load balancer picks healthy backend
  4. httpx proxies request with injected headers
  5. Rate limit metadata attached to response headers
  6. Request logged to PostgreSQL (fire-and-forget)

Fire-and-forget logging:
  asyncio.create_task() means the response is returned to the client
  immediately without waiting for the DB write. Risk: log entry lost if
  process crashes between send and commit. Acceptable for this use case —
  a message queue (Kafka/SQS) would be appropriate for audit-critical systems.
"""

import asyncio
import time
from typing import Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.request_log import RequestLog
from app.models.user import User
from app.services.load_balancer import get_load_balancer
from app.services.rate_limiter import check_rate_limit

router = APIRouter()
log = structlog.get_logger()

# Shared httpx client — connection pooling across requests.
# Without this, every proxied request creates a new TCP connection.
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(
                max_connections=100, max_keepalive_connections=20
            ),
            follow_redirects=False,
        )
    return _http_client


# Hop-by-hop headers must not be forwarded to upstream
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}


async def _log_request(
    db: AsyncSession,
    request_id: str,
    user_id: str,
    method: str,
    path: str,
    upstream_url: Optional[str],
    status_code: int,
    duration_ms: float,
    rate_limit_backend: Optional[str],
):
    """Persist request log. Called as background task — does not block response."""
    try:
        entry = RequestLog(
            request_id=request_id,
            user_id=user_id,
            method=method,
            path=path,
            upstream_url=upstream_url,
            status_code=status_code,
            duration_ms=duration_ms,
            rate_limit_backend=rate_limit_backend,
        )
        db.add(entry)
        await db.commit()
    except Exception as e:
        log.error("request_log.write_failed", error=str(e), request_id=request_id)


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    summary="Proxy a request to a registered backend service",
    description="""
Proxies the request after:
1. Validating the Bearer JWT
2. Checking per-user rate limits (token bucket — Redis or in-memory fallback)
3. Selecting the next healthy backend via round-robin

**Rate limit headers on every response:**
- `X-RateLimit-Limit` — bucket capacity
- `X-RateLimit-Remaining` — tokens left
- `X-RateLimit-Backend` — `redis` or `in-memory`
    """,
)
async def proxy(
    path: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = time.monotonic()
    request_id = getattr(request.state, "request_id", "unknown")
    upstream_url = None
    upstream_status = 502

    # ── Step 1: Rate limiting
    rate_meta = await check_rate_limit(current_user.id)

    # ── Step 2: Load balancing
    lb = get_load_balancer()
    backend = lb.get_next()
    if not backend:
        raise HTTPException(status_code=503, detail="No healthy backends available")

    upstream_url = f"{backend}/{path}"
    query = str(request.url.query)
    if query:
        upstream_url += f"?{query}"

    # ── Step 3: Build forwarded headers
    forward_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }
    forward_headers.update({
        "X-Forwarded-For": request.client.host if request.client else "unknown",
        "X-Forwarded-Proto": request.url.scheme,
        "X-Gateway-User-Id": current_user.id,
        "X-Gateway-User-Role": current_user.role,
        "X-Request-ID": request_id,
    })

    # ── Step 4: Proxy
    http_client = get_http_client()
    try:
        body = await request.body()
        upstream_response = await http_client.request(
            method=request.method,
            url=upstream_url,
            headers=forward_headers,
            content=body,
        )
        lb.record_success(backend)
        upstream_status = upstream_response.status_code

    except httpx.ConnectError:
        lb.record_failure(backend)
        log.error("proxy.connect_error", backend=backend, request_id=request_id)
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {backend}")

    except httpx.TimeoutException:
        lb.record_failure(backend)
        log.error("proxy.timeout", backend=backend, request_id=request_id)
        raise HTTPException(status_code=504, detail="Backend request timed out")

    # ── Step 5: Build response
    duration_ms = (time.monotonic() - start) * 1000

    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS | {"content-encoding", "content-length"}
    }
    response_headers.update(rate_meta)

    response = Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )

    # ── Step 6: Fire-and-forget DB log
    asyncio.create_task(
        _log_request(
            db=db,
            request_id=request_id,
            user_id=current_user.id,
            method=request.method,
            path=f"/{path}",
            upstream_url=upstream_url,
            status_code=upstream_status,
            duration_ms=round(duration_ms, 2),
            rate_limit_backend=rate_meta.get("X-RateLimit-Backend"),
        )
    )

    log.info(
        "proxy.forwarded",
        backend=backend,
        path=path,
        upstream_status=upstream_status,
        duration_ms=round(duration_ms, 2),
        request_id=request_id,
    )

    return response