"""
Token Bucket Rate Limiter — Redis-backed with in-memory fallback.

Why Token Bucket over Fixed Window?
=====================================
Fixed window has a burst problem: a user can fire 100 requests at 00:59 and
100 more at 01:00 — 200 requests in 2 seconds straddling the window boundary.
Token bucket prevents this by smoothing the refill rate.

Algorithm:
  - Each user has a bucket with `capacity` tokens
  - Tokens refill at `refill_rate` per second (continuous)
  - Each request consumes 1 token
  - If bucket empty → 429 Too Many Requests

Redis Lua Script:
  Handles the atomic read-modify-write. Critical — without atomicity, two
  concurrent requests both read "1 token left", both pass, bucket goes to -1.

In-Memory Fallback:
  Per-process dict keyed by user_id. Safe because asyncio is single-threaded
  per event loop. Tradeoff: limits are per-process not global during downtime.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import structlog
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.redis_client import get_redis_client, mark_redis_failed

log = structlog.get_logger()

# Atomic Lua script — returns [allowed (0/1), tokens_remaining, retry_after_seconds]
RATE_LIMIT_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now

local elapsed = now - last_refill
local new_tokens = math.min(capacity, tokens + elapsed * refill_rate)

if new_tokens >= requested then
    redis.call('HMSET', key, 'tokens', new_tokens - requested, 'last_refill', now)
    redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) * 2)
    return {1, math.floor(new_tokens - requested), 0}
else
    local deficit = requested - new_tokens
    local retry_after = math.ceil(deficit / refill_rate)
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_refill', now)
    redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) * 2)
    return {0, math.floor(new_tokens), retry_after}
end
"""


@dataclass
class BucketState:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


# In-memory buckets — used when Redis is unavailable
_in_memory_buckets: dict[str, BucketState] = defaultdict(
    lambda: BucketState(tokens=float(settings.RATE_LIMIT_CAPACITY))
)


def _check_in_memory(user_id: str) -> tuple[bool, int, int]:
    """
    Token bucket check using in-memory state.
    Returns (allowed, tokens_remaining, retry_after_seconds).
    """
    bucket = _in_memory_buckets[user_id]
    now = time.monotonic()
    elapsed = now - bucket.last_refill

    bucket.tokens = min(
        settings.RATE_LIMIT_CAPACITY,
        bucket.tokens + elapsed * settings.RATE_LIMIT_REFILL_RATE,
    )
    bucket.last_refill = now

    if bucket.tokens >= 1:
        bucket.tokens -= 1
        return True, int(bucket.tokens), 0
    else:
        deficit = 1 - bucket.tokens
        retry_after = int(deficit / settings.RATE_LIMIT_REFILL_RATE) + 1
        return False, 0, retry_after


async def check_rate_limit(user_id: str) -> dict:
    """
    Main entry point. Tries Redis first, falls back to in-memory.
    Raises HTTP 429 if the request should be rejected.
    Returns rate limit metadata dict for response headers.
    """
    redis = await get_redis_client()
    using_redis = redis is not None

    if using_redis:
        try:
            result = await redis.eval(
                RATE_LIMIT_LUA,
                1,
                f"rate_limit:{user_id}",
                settings.RATE_LIMIT_CAPACITY,
                settings.RATE_LIMIT_REFILL_RATE,
                time.time(),
                1,
            )
            allowed, remaining, retry_after = (
                int(result[0]),
                int(result[1]),
                int(result[2]),
            )
        except Exception as e:
            log.error("redis.rate_limit_error", error=str(e), user_id=user_id)
            await mark_redis_failed()
            # Seamlessly fall through to in-memory
            allowed, remaining, retry_after = _check_in_memory(user_id)
            using_redis = False
    else:
        allowed, remaining, retry_after = _check_in_memory(user_id)

    meta = {
        "X-RateLimit-Limit": str(settings.RATE_LIMIT_CAPACITY),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Backend": "redis" if using_redis else "in-memory",
    }

    if not allowed:
        meta["Retry-After"] = str(retry_after)
        log.warning(
            "rate_limit.exceeded",
            user_id=user_id,
            retry_after=retry_after,
            backend="redis" if using_redis else "in-memory",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Rate limit exceeded",
                "retry_after_seconds": retry_after,
                "limit": settings.RATE_LIMIT_CAPACITY,
            },
            headers=meta,
        )

    return meta