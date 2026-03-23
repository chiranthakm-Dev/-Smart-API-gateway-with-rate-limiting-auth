"""
Shared test fixtures.

Key decisions:
  - SQLite in-memory replaces PostgreSQL → no external DB needed to run tests
  - Redis is mocked via unittest.mock.patch → tests control availability
  - FastAPI dependency_overrides swaps real DB for test DB session
  - Per-test rollback keeps tests hermetic (no state leaks between tests)
"""

import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.user import User
from app.services.user_service import create_user

# ── In-memory SQLite — no external database required
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    """Create all tables once per test session, drop at end."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test DB session with rollback — prevents state leaking between tests."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Test HTTP client with:
      - In-memory DB injected via dependency override
      - Redis mocked as unavailable → exercises in-memory rate limit path
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.services.rate_limiter.get_redis_client", return_value=None):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_with_redis(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Test client where Redis is available (mocked to return a fake redis client
    that executes the Lua script via in-memory logic for testing).
    Used for tests that explicitly test the Redis code path.
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Mock Redis client that simulates successful eval
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=[1, 99, 0])  # allowed, 99 remaining, 0 retry

    with patch(
        "app.services.rate_limiter.get_redis_client",
        return_value=mock_redis,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = await create_user(db_session, "test@example.com", "password123")
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = await create_user(
        db_session, "admin@example.com", "password123", role="admin"
    )
    await db_session.commit()
    return user


@pytest_asyncio.fixture
def user_token(test_user: User) -> str:
    return create_access_token(test_user.id, test_user.email, test_user.role)


@pytest_asyncio.fixture
def admin_token(admin_user: User) -> str:
    return create_access_token(admin_user.id, admin_user.email, admin_user.role)