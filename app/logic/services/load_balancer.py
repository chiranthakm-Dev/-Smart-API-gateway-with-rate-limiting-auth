"""
Load Balancer — Round-Robin with passive health checking.

Strategy: Round-Robin
  Simple, fair, O(1). Suitable when backends are homogeneous.
  For heterogeneous capacity, weighted round-robin would be better (v2.0 roadmap).

Health Checking:
  Passive — we track failures on real traffic, not synthetic probes.
  A backend is marked unhealthy after FAILURE_THRESHOLD consecutive failures.
  A background task re-checks every HEALTH_CHECK_INTERVAL seconds.

Fallback:
  If all backends are unhealthy, we fall back to trying all of them rather
  than returning 503 — better to attempt than to fail every request.
"""

import asyncio
import time
from typing import Optional

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger()

HEALTH_CHECK_INTERVAL = 15  # seconds between background health checks
FAILURE_THRESHOLD = 3       # consecutive failures before marking unhealthy


class LoadBalancer:
    def __init__(self, backends: list[str]):
        self.all_backends = backends
        self.healthy: set[str] = set(backends)
        self._failure_counts: dict[str, int] = {b: 0 for b in backends}
        self._index = 0

    def get_next(self) -> Optional[str]:
        """Returns next healthy backend, falls back to all if all are unhealthy."""
        pool = list(self.healthy) if self.healthy else self.all_backends
        if not pool:
            return None
        backend = pool[self._index % len(pool)]
        self._index += 1
        return backend

    def record_success(self, backend: str):
        self._failure_counts[backend] = 0
        self.healthy.add(backend)

    def record_failure(self, backend: str):
        self._failure_counts[backend] = self._failure_counts.get(backend, 0) + 1
        if self._failure_counts[backend] >= FAILURE_THRESHOLD:
            if backend in self.healthy:
                self.healthy.discard(backend)
                log.warning("lb.backend_marked_unhealthy", backend=backend)

    async def health_check(self, backend: str) -> bool:
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                resp = await client.get(f"{backend}/health")
                return resp.status_code == 200
            except Exception:
                return False

    async def run_health_checks(self):
        """Background task — periodically re-checks all backends."""
        while True:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            for backend in self.all_backends:
                healthy = await self.health_check(backend)
                if healthy:
                    if backend not in self.healthy:
                        log.info("lb.backend_recovered", backend=backend)
                    self.healthy.add(backend)
                    self._failure_counts[backend] = 0
                else:
                    self.record_failure(backend)

    def status(self) -> dict:
        return {
            "backends": [
                {
                    "url": b,
                    "healthy": b in self.healthy,
                    "failure_count": self._failure_counts.get(b, 0),
                }
                for b in self.all_backends
            ]
        }


# Singleton — shared across all requests in the process
_load_balancer: Optional[LoadBalancer] = None


def get_load_balancer() -> LoadBalancer:
    global _load_balancer
    if _load_balancer is None:
        _load_balancer = LoadBalancer(settings.BACKEND_SERVICES)
    return _load_balancer