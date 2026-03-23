"""
Structured request logging middleware.

Uses structlog for JSON-formatted logs — compatible with Datadog, Grafana Loki,
and AWS CloudWatch Logs Insights out of the box.
"""

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            request_id=getattr(request.state, "request_id", "-"),
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("User-Agent", "-"),
        )

        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        return response