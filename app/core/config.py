"""
Centralised configuration — all env vars live here.

Using Pydantic Settings means:
  1. Type validation at startup (misconfigured env = immediate crash, not silent bug)
  2. .env file support for local dev without touching environment
  3. IDE autocomplete across the codebase
"""

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Application
    VERSION: str = "1.0.0"
    ENV: str = "development"
    DEBUG: bool = False

    # ── Security
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    )

    # ── Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    # How long to wait for Redis before deciding it's down (ms)
    REDIS_CONNECT_TIMEOUT_MS: int = 200

    # ── Rate Limiting (Token Bucket)
    RATE_LIMIT_CAPACITY: int = 100        # max tokens in bucket
    RATE_LIMIT_REFILL_RATE: float = 10.0  # tokens added per second

    # ── Load Balancer
    BACKEND_SERVICES: List[str] = [
        "http://service_a:8001",
        "http://service_b:8002",
    ]

    # ── CORS
    CORS_ORIGINS: List[str] = ["*"]

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_db_url(cls, v: str) -> str:
        if not v.startswith(("postgresql", "sqlite")):
            raise ValueError("DATABASE_URL must be PostgreSQL or SQLite")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()