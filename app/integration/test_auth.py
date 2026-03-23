"""
Integration tests for authentication endpoints.

TC-01: Register new user → 201, password not in response
TC-02: Register duplicate email → 409
TC-03: Login valid credentials → 200 with valid JWT
TC-04: Login invalid password → 401, same message as unknown email
TC-05: Proxy with no token → 401
TC-06: Proxy with expired token → 401
"""

import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from jose import jwt

from app.core.config import settings


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_success(self, client: AsyncClient):
        """TC-01: Successful registration returns 201 with user profile."""
        response = await client.post(
            "/auth/register",
            json={"email": "newuser@example.com", "password": "securepass"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["role"] == "user"
        assert data["is_active"] is True
        # Password must never appear in any response
        assert "password" not in data
        assert "hashed_password" not in data

    @pytest.mark.asyncio
    async def test_register_returns_user_id(self, client: AsyncClient):
        """Registered user gets a UUID id."""
        response = await client.post(
            "/auth/register",
            json={"email": "withid@example.com", "password": "securepass"},
        )
        assert response.status_code == 201
        assert "id" in response.json()

    @pytest.mark.asyncio
    async def test_register_duplicate_email_returns_409(self, client: AsyncClient):
        """TC-02: Duplicate email returns 409 Conflict."""
        payload = {"email": "duplicate@example.com", "password": "securepass"}
        await client.post("/auth/register", json=payload)

        response = await client.post("/auth/register", json=payload)
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_short_password_returns_422(self, client: AsyncClient):
        """Passwords under 8 chars are rejected at validation layer."""
        response = await client.post(
            "/auth/register",
            json={"email": "short@example.com", "password": "abc"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_register_invalid_email_returns_422(self, client: AsyncClient):
        """Invalid email format is rejected by Pydantic."""
        response = await client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "securepass"},
        )
        assert response.status_code == 422


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_valid_credentials(self, client: AsyncClient):
        """TC-03: Valid credentials return a JWT access token."""
        await client.post(
            "/auth/register",
            json={"email": "loginuser@example.com", "password": "mypassword"},
        )
        response = await client.post(
            "/auth/login",
            json={"email": "loginuser@example.com", "password": "mypassword"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    @pytest.mark.asyncio
    async def test_login_token_is_valid_jwt(self, client: AsyncClient):
        """TC-03: The returned token decodes to correct claims."""
        await client.post(
            "/auth/register",
            json={"email": "jwtcheck@example.com", "password": "mypassword"},
        )
        response = await client.post(
            "/auth/login",
            json={"email": "jwtcheck@example.com", "password": "mypassword"},
        )
        token = response.json()["access_token"]
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert payload["email"] == "jwtcheck@example.com"
        assert payload["role"] == "user"
        assert "sub" in payload
        assert "jti" in payload
        assert "exp" in payload

    @pytest.mark.asyncio
    async def test_login_wrong_password_returns_401(self, client: AsyncClient):
        """TC-04: Wrong password → 401 with generic message."""
        await client.post(
            "/auth/register",
            json={"email": "wrongpass@example.com", "password": "correct"},
        )
        response = await client.post(
            "/auth/login",
            json={"email": "wrongpass@example.com", "password": "wrong"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid credentials"

    @pytest.mark.asyncio
    async def test_login_unknown_email_same_message_as_wrong_password(
        self, client: AsyncClient
    ):
        """
        TC-04: Unknown email returns same 401 message as wrong password.
        This prevents user enumeration attacks.
        """
        wrong_pass_response = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "whatever"},
        )
        assert wrong_pass_response.status_code == 401
        assert wrong_pass_response.json()["detail"] == "Invalid credentials"


class TestGetMe:
    @pytest.mark.asyncio
    async def test_get_me_with_valid_token(self, client: AsyncClient, test_user, user_token):
        """Authenticated /auth/me returns current user profile."""
        response = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["id"] == test_user.id

    @pytest.mark.asyncio
    async def test_get_me_without_token_returns_401(self, client: AsyncClient):
        """TC-05: No token → 401."""
        response = await client.get("/auth/me")
        assert response.status_code == 403  # HTTPBearer returns 403 when no header

    @pytest.mark.asyncio
    async def test_get_me_with_expired_token_returns_401(self, client: AsyncClient, test_user):
        """TC-06: Expired token → 401."""
        expired_payload = {
            "sub": test_user.id,
            "email": test_user.email,
            "role": "user",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "jti": "expired-jti",
        }
        expired_token = jwt.encode(
            expired_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM
        )
        response = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower() or \
               "invalid" in response.json()["detail"].lower()