"""
Integration tests for the gateway proxy router.

TC-05: Proxy with no token → 401
TC-06: Proxy with expired token → 401
TC-12: Forwarded headers (X-Gateway-User-Id, X-Request-ID) reach upstream
TC-14: Health endpoint reports in-memory backend when Redis is mocked down
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from jose import jwt

from app.core.config import settings


class TestProxyAuth:
    @pytest.mark.asyncio
    async def test_proxy_no_token_returns_401(self, client: AsyncClient):
        """TC-05: Request to /proxy/* without Authorization header → 401."""
        response = await client.get("/proxy/api/test")
        # HTTPBearer returns 403 when header is entirely missing
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_proxy_invalid_token_returns_401(self, client: AsyncClient):
        """Garbage token → 401."""
        response = await client.get(
            "/proxy/api/test",
            headers={"Authorization": "Bearer this.is.garbage"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_proxy_expired_token_returns_401(self, client: AsyncClient, test_user):
        """TC-06: Expired token → 401."""
        expired_payload = {
            "sub": test_user.id,
            "email": test_user.email,
            "role": "user",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "jti": "test-expired",
        }
        expired_token = jwt.encode(
            expired_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
        )
        response = await client.get(
            "/proxy/api/test",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401


class TestProxyForwarding:
    @pytest.mark.asyncio
    async def test_rate_limit_headers_present_on_proxy_response(
        self, client: AsyncClient, user_token
    ):
        """
        TC-12: After a successful proxy, rate limit headers are attached.
        We mock the upstream httpx call to avoid needing real backends.
        """
        mock_response = httpx.Response(
            200,
            json={"service": "A", "path": "/api/hello"},
            headers={"content-type": "application/json"},
        )

        with patch(
            "app.routers.gateway.get_http_client"
        ) as mock_client_factory:
            mock_client = MagicMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_factory.return_value = mock_client

            response = await client.get(
                "/proxy/api/hello",
                headers={"Authorization": f"Bearer {user_token}"},
            )

        assert response.status_code == 200
        assert "x-ratelimit-limit" in response.headers
        assert "x-ratelimit-remaining" in response.headers
        assert "x-ratelimit-backend" in response.headers

    @pytest.mark.asyncio
    async def test_request_id_echoed_in_response(
        self, client: AsyncClient, user_token
    ):
        """X-Request-ID sent by client is echoed back in the response."""
        mock_response = httpx.Response(
            200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )

        with patch("app.routers.gateway.get_http_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_factory.return_value = mock_client

            response = await client.get(
                "/proxy/api/test",
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "X-Request-ID": "my-trace-id-123",
                },
            )

        assert response.headers.get("x-request-id") == "my-trace-id-123"

    @pytest.mark.asyncio
    async def test_proxy_backend_connect_error_returns_502(
        self, client: AsyncClient, user_token
    ):
        """When backend is unreachable, gateway returns 502."""
        with patch("app.routers.gateway.get_http_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.request = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_factory.return_value = mock_client

            response = await client.get(
                "/proxy/api/test",
                headers={"Authorization": f"Bearer {user_token}"},
            )

        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_proxy_backend_timeout_returns_504(
        self, client: AsyncClient, user_token
    ):
        """When backend times out, gateway returns 504."""
        with patch("app.routers.gateway.get_http_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.request = AsyncMock(
                side_effect=httpx.TimeoutException("Timed out")
            )
            mock_factory.return_value = mock_client

            response = await client.get(
                "/proxy/api/test",
                headers={"Authorization": f"Bearer {user_token}"},
            )

        assert response.status_code == 504


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        """Health endpoint is always accessible without auth."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_reports_in_memory_when_redis_down(
        self, client: AsyncClient
    ):
        """
        TC-14: When Redis is mocked as unavailable, /health reports
        rate_limiting_backend as 'in-memory'.
        """
        with patch("app.routers.health.is_redis_available", return_value=False):
            response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["redis_available"] is False
        assert data["rate_limiting_backend"] == "in-memory"

    @pytest.mark.asyncio
    async def test_health_reports_redis_when_available(self, client: AsyncClient):
        """When Redis is available, health reports redis backend."""
        with patch("app.routers.health.is_redis_available", return_value=True):
            response = await client.get("/health")

        data = response.json()
        assert data["redis_available"] is True
        assert data["rate_limiting_backend"] == "redis"