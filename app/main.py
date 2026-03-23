"""
API Gateway - Main Application Entry Point

Architecture Decision:
  FastAPI was chosen over Flask for its native async support, automatic OpenAPI
  generation, and Pydantic-based validation. This matters for a gateway because
  every millisecond of overhead compounds across all proxied requests.
"""

import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import engine, Base
from app.core.redis_client import get_redis_client
from app.middleware.logging import RequestLoggingMiddleware
from app.routers import auth, gateway, health, admin

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup/shutdown lifecycle manager.
    We pre-warm the Redis connection and create DB tables here so the first
    real request doesn't pay that cost.
    """
    log.info("gateway.starting", version=settings.VERSION)

    # Create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database.tables_ready")

    # Test Redis connection — if it fails, we fall back to in-memory limiting
    redis = await get_redis_client()
    if redis:
        log.info("redis.connected", host=settings.REDIS_HOST)
    else:
        log.warning(
            "redis.unavailable",
            fallback="in_memory_rate_limiting",
            reason="Redis unreachable at startup — graceful degradation active",
        )

    yield

    log.info("gateway.shutting_down")
    if redis:
        await redis.aclose()


app = FastAPI(
    title="API Gateway",
    description="""
## Production-Grade API Gateway

Handles JWT authentication, per-user rate limiting (token bucket),
request logging, and load balancing across backend services.

### Key Features
- **JWT Auth** — HS256 signed tokens, configurable expiry
- **Rate Limiting** — Token bucket algorithm, per-user, backed by Redis
- **Graceful Degradation** — Falls back to in-memory limiting if Redis is down
- **Load Balancing** — Round-robin across registered backend services
- **Observability** — Structured JSON logs with request tracing via `X-Request-ID`
    """,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── Middleware (order matters — outermost runs first on request, last on response)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)


@app.middleware("http")
async def add_request_id(request: Request, call_next) -> Response:
    """
    Attach a unique request ID to every request/response pair.
    Downstream services receive this via X-Request-ID header so logs
    can be correlated across the full call chain.
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    request.state.start_time = time.monotonic()

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ── Routers
app.include_router(health.router, tags=["Health"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(gateway.router, prefix="/proxy", tags=["Gateway / Proxy"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(
        "unhandled_exception",
        path=request.url.path,
        error=str(exc),
        request_id=getattr(request.state, "request_id", "unknown"),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": getattr(request.state, "request_id", None),
        },
    )