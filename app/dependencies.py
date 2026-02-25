"""
Dependency injection functions for FastAPI endpoints.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from uuid import UUID
from supabase import Client

from app.services.auth_service import auth_service
from app.config import settings
from app.database import get_supabase_client


security = HTTPBearer(auto_error=False)


def get_db() -> Client:
    """
    Get Supabase client (dependency injection).

    Returns singleton client to avoid connection exhaustion.
    """
    return get_supabase_client()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> UUID:
    """
    Dependency to get current authenticated user from JWT token.

    Usage:
        @router.get("/me")
        async def get_me(user_id: UUID = Depends(get_current_user)):
            return {"user_id": user_id}
    """
    token = credentials.credentials
    user_id = auth_service.verify_token(token)
    return user_id


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[UUID]:
    """
    Dependency to get current user if authenticated, None otherwise.
    """
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        user_id = auth_service.verify_token(token)
        return user_id
    except HTTPException:
        return None
