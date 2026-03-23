"""
JWT authentication utilities.

Token structure:
  {
    "sub": "<user_id>",
    "email": "user@example.com",
    "role": "user" | "admin",
    "exp": <unix timestamp>,
    "iat": <unix timestamp>,
    "jti": "<uuid>"
  }

Using HS256 (HMAC-SHA256). For multi-service deployments where downstream
services also verify tokens, RS256 is preferred — it's listed in v1.1 roadmap.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.user_service import get_user_by_id

log = structlog.get_logger()

security = HTTPBearer()


def create_access_token(user_id: str, email: str, role: str = "user") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT.
    We don't distinguish between 'expired' vs 'invalid' in the public error —
    revealing which occurred is an information leak.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        if payload.get("sub") is None:
            raise JWTError("Missing subject claim")
        return payload
    except JWTError as e:
        log.warning("jwt.decode_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency — resolves the current authenticated user."""
    payload = decode_token(credentials.credentials)
    user = await get_user_by_id(db, payload["sub"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        )
    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency that additionally asserts admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user