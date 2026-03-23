"""
Redis client with graceful degradation support.

Design Decision — Why Graceful Degradation?
============================================
A rate limiter that takes down the entire gateway when Redis goes offline is
worse than no rate limiter at all. Transient network blips, rolling restarts,
and memory pressure can all cause brief unavailability.

Tradeoff accepted: during Redis downtime, per-user rate limiting falls back
to per-process in-memory buckets. This means:
  - Rate limits are enforced per gateway instance, not globally
  - On restart, the in-memory state is lost (users get fresh buckets)
  - Acceptable for short outages, not indefinite ones

Alternatives rejected:
  - Fail-open (no rate limiting): could be exploited during intentional outage
  - Fail-closed (reject all requests): availability > perfect enforcement
"""

from typing import Optional

import redis.asyncio as aioredis
import structlog

from app.core.config import settings

log = structlog.get_logger()

_redis_client: Optional[aioredis.Redis] = None
_redis_available: bool = False
_connection_failures: int = 0


async def get_redis_client() -> Optional[aioredis.Redis]:
    """
    Returns a Redis client if available, None if Redis is down.
    Callers must handle the None case — this is the graceful degradation contract.
    """
    global _redis_client, _redis_available, _connection_failures

    if _redis_client is None:
        try:
            _redis_client = aioredis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD or None,
                db=settings.REDIS_DB,
                socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_MS / 1000,
                socket_timeout=0.5,
                decode_responses=True,
            )
            await _redis_client.ping()
            _redis_available = True
            _connection_failures = 0
            log.info("redis.connection_established")
        except (aioredis.RedisError, ConnectionRefusedError, OSError) as e:
            _redis_client = None
            _redis_available = False
            log.warning("redis.connection_failed", error=str(e), fallback="in_memory")
            return None

    if not _redis_available:
        try:
            await _redis_client.ping()
            _redis_available = True
            _connection_failures = 0
            log.info("redis.reconnected")
        except Exception:
            _connection_failures += 1
            _redis_client = None
            return None

    return _redis_client


async def mark_redis_failed():
    """Called by rate limiter when a Redis operation fails mid-request."""
    global _redis_available, _redis_client, _connection_failures
    _redis_available = False
    _connection_failures += 1
    _redis_client = None
    log.warning(
        "redis.marked_failed",
        failures=_connection_failures,
        action="switched_to_in_memory_rate_limiting",
    )


def is_redis_available() -> bool:
    return _redis_available


def get_failure_count() -> int:
    return _connection_failures