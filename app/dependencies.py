"""
Dependency injection functions for FastAPI endpoints.
"""

from dataclasses import dataclass
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from uuid import UUID
from supabase import Client

from app.services.auth_service import auth_service
from app.config import settings
from app.database import get_supabase_client
from app.context import user_id_var


security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """Authenticated user identity including role."""
    user_id: UUID
    role: str


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

    Raises 401 if no valid token is provided.
    Returns user_id (UUID) for backward compatibility.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    token = credentials.credentials
    user_id, _role = auth_service.verify_token(token)
    user_id_var.set(str(user_id))
    return user_id


async def get_current_user_with_role(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> CurrentUser:
    """
    Dependency to get current authenticated user including role.

    Raises 401 if no valid token is provided.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    token = credentials.credentials
    user_id, role = auth_service.verify_token(token)
    user_id_var.set(str(user_id))
    return CurrentUser(user_id=user_id, role=role)


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user_with_role)
) -> CurrentUser:
    """
    Dependency that requires the current user to have admin role.

    Raises 403 if the user is not an admin.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user
