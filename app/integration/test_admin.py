"""
Integration tests for admin endpoints.

TC-13: Admin logs endpoint returns 403 for non-admin users
"""

import pytest
from httpx import AsyncClient


class TestAdminLogs:
    @pytest.mark.asyncio
    async def test_admin_logs_requires_auth(self, client: AsyncClient):
        """No token → 403 (HTTPBearer)."""
        response = await client.get("/admin/logs")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_logs_rejects_regular_user(
        self, client: AsyncClient, user_token
    ):
        """TC-13: Regular user token → 403 Forbidden."""
        response = await client.get(
            "/admin/logs",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert response.status_code == 403
        assert "Admin access required" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_admin_logs_accessible_with_admin_token(
        self, client: AsyncClient, admin_token
    ):
        """Admin token → 200 with list response."""
        response = await client.get(
            "/admin/logs",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_admin_stats_rejects_regular_user(
        self, client: AsyncClient, user_token
    ):
        """Stats endpoint also requires admin role."""
        response = await client.get(
            "/admin/stats",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_stats_returns_expected_shape(
        self, client: AsyncClient, admin_token
    ):
        """Stats response contains required keys."""
        response = await client.get(
            "/admin/stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_requests" in data
        assert "avg_duration_ms" in data
        assert "load_balancer" in data

    @pytest.mark.asyncio
    async def test_admin_logs_limit_parameter(
        self, client: AsyncClient, admin_token
    ):
        """limit query param is respected."""
        response = await client.get(
            "/admin/logs?limit=5",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert len(response.json()) <= 5

    @pytest.mark.asyncio
    async def test_admin_logs_invalid_limit_returns_422(
        self, client: AsyncClient, admin_token
    ):
        """limit=0 violates ge=1 constraint → 422."""
        response = await client.get(
            "/admin/logs?limit=0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 422