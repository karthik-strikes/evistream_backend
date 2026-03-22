"""
Authentication service handling user registration, login, and JWT tokens.
"""

import logging
import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from jose import JWTError, jwt
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)


# Dummy hash for timing-safe comparison when user is not found
_DUMMY_HASH = bcrypt.hashpw(b"timing-safe-dummy-password", bcrypt.gensalt(rounds=12))


class AuthService:
    """Service for authentication operations."""

    def hash_password(self, password: str) -> str:
        """Hash a password with bcrypt."""
        password_bytes = password.encode('utf-8')[:72]
        hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=12))
        return hashed.decode('utf-8')

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash."""
        return bcrypt.checkpw(
            plain_password.encode('utf-8')[:72],
            hashed_password.encode('utf-8')
        )

    def verify_password_timing_safe(self, plain_password: str, hashed_password: Optional[str]) -> bool:
        """
        Verify a password with constant-time behavior.

        If hashed_password is None (user not found), still runs bcrypt
        against a dummy hash to prevent timing-based user enumeration.
        """
        pw_bytes = plain_password.encode('utf-8')[:72]
        if hashed_password is None:
            bcrypt.checkpw(pw_bytes, _DUMMY_HASH)
            return False
        return bcrypt.checkpw(pw_bytes, hashed_password.encode('utf-8'))

    def create_access_token(self, user_id: UUID, role: str = "user", expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token."""
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

        now = datetime.now(timezone.utc)
        expire = now + expires_delta

        to_encode = {
            "sub": str(user_id),
            "exp": expire,
            "iat": now,
            "type": "access",
            "role": role,
        }

        return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    def create_refresh_token(self, user_id: UUID) -> str:
        """Create JWT refresh token with longer expiry."""
        now = datetime.now(timezone.utc)
        expire = now + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)

        refresh_secret = settings.REFRESH_SECRET_KEY or settings.SECRET_KEY

        to_encode = {
            "sub": str(user_id),
            "exp": expire,
            "iat": now,
            "type": "refresh",
            "jti": secrets.token_hex(16),
        }

        return jwt.encode(to_encode, refresh_secret, algorithm=settings.ALGORITHM)

    def verify_token(self, token: str) -> tuple[UUID, str]:
        """Verify JWT access token and return (user_id, role)."""
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )

            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type"
                )

            user_id: str = payload.get("sub")

            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication credentials"
                )

            role: str = payload.get("role", "user")
            return UUID(user_id), role

        except JWTError:
            logger.warning("Access token verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )

    def verify_refresh_token(self, token: str) -> UUID:
        """Verify JWT refresh token and return user_id."""
        refresh_secret = settings.REFRESH_SECRET_KEY or settings.SECRET_KEY
        try:
            payload = jwt.decode(
                token,
                refresh_secret,
                algorithms=[settings.ALGORITHM]
            )

            if payload.get("type") != "refresh":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type"
                )

            user_id: str = payload.get("sub")

            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication credentials"
                )

            return UUID(user_id)

        except JWTError:
            logger.warning("Refresh token verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )


# Global auth service instance
auth_service = AuthService()
