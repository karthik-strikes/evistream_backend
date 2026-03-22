"""
Admin endpoints for user management and system statistics.
All endpoints require admin role.
"""

import logging
from fastapi import APIRouter, HTTPException, status, Depends, Query
from supabase import create_client
from uuid import UUID
from typing import Optional

from app.models.schemas import UserResponse, UserAdminUpdate
from app.dependencies import require_admin, CurrentUser
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: CurrentUser = Depends(require_admin),
):
    """
    List all users with pagination.
    """
    try:
        offset = (page - 1) * page_size
        result = supabase.table("users").select(
            "id, email, full_name, is_active, role, created_at"
        ).order("created_at", desc=True).range(offset, offset + page_size - 1).execute()

        count_result = supabase.table("users").select("id", count="exact").execute()
        total = count_result.count if count_result.count is not None else len(result.data)

        return {
            "users": result.data,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception:
        logger.exception("Error listing users")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/users/{user_id}")
async def get_user(
    user_id: UUID,
    _admin: CurrentUser = Depends(require_admin),
):
    """
    Get a single user's details.
    """
    try:
        result = supabase.table("users").select(
            "id, email, full_name, is_active, role, created_at"
        ).eq("id", str(user_id)).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        return result.data[0]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.patch("/users/{user_id}")
async def update_user(
    user_id: UUID,
    updates: UserAdminUpdate,
    admin: CurrentUser = Depends(require_admin),
):
    """
    Update a user's is_active or role.
    Blocks self-demotion.
    """
    try:
        if str(user_id) == str(admin.user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify your own account"
            )

        patch = {k: v for k, v in updates.model_dump().items() if v is not None}
        if not patch:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        # Serialize enum values to strings
        if "role" in patch and hasattr(patch["role"], "value"):
            patch["role"] = patch["role"].value

        result = supabase.table("users").update(patch).eq("id", str(user_id)).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        return result.data[0]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error updating user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    admin: CurrentUser = Depends(require_admin),
):
    """
    Delete a user. Blocks self-deletion.
    """
    try:
        if str(user_id) == str(admin.user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete your own account"
            )

        result = supabase.table("users").delete().eq("id", str(user_id)).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error deleting user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/stats")
async def get_stats(
    _admin: CurrentUser = Depends(require_admin),
):
    """
    Get system-wide statistics.
    """
    try:
        users_result = supabase.table("users").select("id", count="exact").execute()
        projects_result = supabase.table("projects").select("id", count="exact").execute()
        extractions_result = supabase.table("extractions").select("id", count="exact").execute()

        return {
            "total_users": users_result.count or 0,
            "total_projects": projects_result.count or 0,
            "total_extractions": extractions_result.count or 0,
        }
    except Exception:
        logger.exception("Error fetching stats")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
