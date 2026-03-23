"""
Unit tests for the token bucket rate limiter.

TC-07: 100 requests pass, 101st returns 429
TC-08: Token refill after sleep — request passes again
TC-09: Redis error → in-memory fallback, request still succeeds
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.rate_limiter import (
    BucketState,
    _check_in_memory,
    _in_memory_buckets,
    check_rate_limit,
)


@pytest.fixture(autouse=True)
def clear_buckets():
    """Reset in-memory buckets before each test to prevent state leakage."""
    _in_memory_buckets.clear()
    yield
    _in_memory_buckets.clear()


class TestInMemoryBucket:
    """Tests for the pure in-memory token bucket logic."""

    def test_fresh_bucket_allows_request(self):
        """A new user starts with a full bucket and can make a request."""
        allowed, remaining, retry_after = _check_in_memory("user-fresh")
        assert allowed is True
        assert remaining == 99  # started at 100, used 1
        assert retry_after == 0

    def test_bucket_decrements_on_each_request(self):
        """Each request consumes one token."""
        for i in range(10):
            allowed, remaining, _ = _check_in_memory("user-decrement")
            assert allowed is True
            assert remaining == 99 - i

    def test_bucket_empty_returns_429(self):
        """After capacity requests, the next one is rejected."""
        from app.core.config import settings

        user_id = "user-exhaust"
        # Exhaust the bucket
        for _ in range(settings.RATE_LIMIT_CAPACITY):
            allowed, _, _ = _check_in_memory(user_id)
            assert allowed is True

        # 101st request should be denied
        allowed, remaining, retry_after = _check_in_memory(user_id)
        assert allowed is False
        assert remaining == 0
        assert retry_after > 0

    def test_tokens_refill_over_time(self):
        """After waiting, tokens refill and requests are allowed again."""
        from app.core.config import settings

        user_id = "user-refill"

        # Exhaust the bucket
        for _ in range(settings.RATE_LIMIT_CAPACITY):
            _check_in_memory(user_id)

        # Manually backdate the last_refill to simulate time passing
        # (10 tokens at refill rate 10/s = 1 second of wait)
        _in_memory_buckets[user_id].last_refill = time.monotonic() - 1.0

        allowed, remaining, retry_after = _check_in_memory(user_id)
        assert allowed is True
        assert remaining > 0
        assert retry_after == 0

    def test_different_users_have_independent_buckets(self):
        """Rate limiting is per-user — one user exhausting their bucket doesn't affect others."""
        from app.core.config import settings

        # Exhaust user A
        for _ in range(settings.RATE_LIMIT_CAPACITY):
            _check_in_memory("user-a")
        allowed_a, _, _ = _check_in_memory("user-a")
        assert allowed_a is False

        # User B should be unaffected
        allowed_b, remaining_b, _ = _check_in_memory("user-b")
        assert allowed_b is True
        assert remaining_b == 99

    def test_retry_after_is_positive_when_denied(self):
        """Retry-After header value should always be a positive integer when denied."""
        from app.core.config import settings

        user_id = "user-retry"
        for _ in range(settings.RATE_LIMIT_CAPACITY):
            _check_in_memory(user_id)

        _, _, retry_after = _check_in_memory(user_id)
        assert retry_after >= 1


class TestCheckRateLimitRedisPath:
    """Tests for the full check_rate_limit() function with Redis mocked."""

    @pytest.mark.asyncio
    async def test_redis_available_request_allowed(self):
        """When Redis returns allowed=1, request goes through with correct headers."""
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[1, 95, 0])

        with patch(
            "app.services.rate_limiter.get_redis_client", return_value=mock_redis
        ):
            meta = await check_rate_limit("user-redis-ok")

        assert meta["X-RateLimit-Backend"] == "redis"
        assert meta["X-RateLimit-Remaining"] == "95"
        assert "Retry-After" not in meta

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_in_memory(self):
        """
        TC-09: When Redis raises an exception mid-request, the gateway
        silently falls back to in-memory rate limiting. The request succeeds.
        """
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=Exception("Redis connection lost"))

        with (
            patch("app.services.rate_limiter.get_redis_client", return_value=mock_redis),
            patch("app.services.rate_limiter.mark_redis_failed") as mock_mark_failed,
        ):
            meta = await check_rate_limit("user-redis-fail")

        # Request should still succeed via in-memory fallback
        assert meta["X-RateLimit-Backend"] == "in-memory"
        assert "Retry-After" not in meta
        mock_mark_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_none_uses_in_memory(self):
        """When get_redis_client() returns None (Redis down), in-memory is used."""
        with patch(
            "app.services.rate_limiter.get_redis_client", return_value=None
        ):
            meta = await check_rate_limit("user-no-redis")

        assert meta["X-RateLimit-Backend"] == "in-memory"

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_raises_429(self):
        """When bucket is empty, check_rate_limit raises HTTP 429."""
        from fastapi import HTTPException

        with patch(
            "app.services.rate_limiter.get_redis_client", return_value=None
        ):
            from app.core.config import settings

            user_id = "user-429-test"
            # Exhaust bucket via direct in-memory calls
            for _ in range(settings.RATE_LIMIT_CAPACITY):
                _check_in_memory(user_id)

            with pytest.raises(HTTPException) as exc_info:
                await check_rate_limit(user_id)

        assert exc_info.value.status_code == 429
        assert "retry_after_seconds" in exc_info.value.detail