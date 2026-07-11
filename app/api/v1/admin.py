"""
Admin endpoints for user management and system statistics.
All endpoints require admin role.
"""

import logging
import time
from collections import Counter
from fastapi import APIRouter, HTTPException, status, Depends, Query
from supabase import create_client
from uuid import UUID
from typing import Optional

from app.models.schemas import UserResponse, UserAdminUpdate, UserAdminCreate, PermissionAuditLogResponse, AdminAuditLogResponse
from app.dependencies import require_admin, CurrentUser
from app.config import settings
from app.services.auth_service import auth_service
from typing import List

logger = logging.getLogger(__name__)

router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# 60-second module-level cache for storage size
_storage_cache: dict = {"ts": 0.0, "bytes": 0}


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
            "id, email, full_name, is_active, role, created_at, last_seen_at"
        ).order("created_at", desc=True).range(offset, offset + page_size - 1).execute()

        count_result = supabase.table("users").select("id", count="exact").execute()
        total = count_result.count if count_result.count is not None else len(result.data)

        users = result.data or []
        if users:
            ids = [u["id"] for u in users]
            membership_rows = supabase.table("project_members").select("user_id").in_("user_id", ids).execute().data or []
            member_counts = Counter(r["user_id"] for r in membership_rows)
            owner_rows = supabase.table("projects").select("user_id").in_("user_id", ids).execute().data or []
            owner_counts = Counter(r["user_id"] for r in owner_rows)
            for u in users:
                u["project_count"] = member_counts.get(u["id"], 0) + owner_counts.get(u["id"], 0)

        return {
            "users": users,
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


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserAdminCreate,
    _admin: CurrentUser = Depends(require_admin),
):
    """Admin creates a new user account with a specified role."""
    try:
        existing = supabase.table("users").select("id").eq("email", payload.email).execute()
        if existing.data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

        hashed_password = auth_service.hash_password(payload.password)
        role_value = payload.role.value if hasattr(payload.role, "value") else payload.role

        result = supabase.table("users").insert({
            "email": payload.email,
            "hashed_password": hashed_password,
            "full_name": payload.full_name,
            "is_active": True,
            "role": role_value,
        }).execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create user")

        return result.data[0]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error creating user")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


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


def _get_storage_bytes() -> int:
    """Return total storage bytes with a 60-second module-level cache."""
    now = time.time()
    if now - _storage_cache["ts"] < 60:
        return _storage_cache["bytes"]
    try:
        total = 0
        files = supabase.storage.from_("documents").list("", {"limit": 1000}) or []
        for f in files:
            total += f.get("metadata", {}).get("size", 0) if isinstance(f, dict) else 0
        _storage_cache["bytes"] = total
        _storage_cache["ts"] = now
        return total
    except Exception as e:
        logger.warning(f"Could not compute storage size: {e}")
        return _storage_cache.get("bytes", 0)


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
        admins_result = supabase.table("users").select("id", count="exact").eq("role", "admin").execute()
        active_result = supabase.table("users").select("id", count="exact").eq("is_active", True).execute()
        memberships_result = supabase.table("project_members").select("id", count="exact").execute()
        documents_result = supabase.table("documents").select("id", count="exact").execute()
        storage_bytes = _get_storage_bytes()

        return {
            "total_users": users_result.count or 0,
            "total_projects": projects_result.count or 0,
            "total_extractions": extractions_result.count or 0,
            "total_admins": admins_result.count or 0,
            "total_active_users": active_result.count or 0,
            "total_memberships": memberships_result.count or 0,
            "total_documents": documents_result.count or 0,
            "total_storage_bytes": storage_bytes,
        }
    except Exception:
        logger.exception("Error fetching stats")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/projects")
async def list_all_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: CurrentUser = Depends(require_admin),
):
    """List all projects across the platform with owner info."""
    try:
        offset = (page - 1) * page_size
        result = supabase.table("projects")\
            .select("*, users!projects_user_id_fkey(email, full_name)")\
            .order("created_at", desc=True)\
            .range(offset, offset + page_size - 1)\
            .execute()

        count_result = supabase.table("projects").select("id", count="exact").execute()
        total = count_result.count or 0

        projects = []
        for p in (result.data or []):
            owner_info = p.pop("users", {}) or {}
            p["owner_email"] = owner_info.get("email", "")
            p["owner_name"] = owner_info.get("full_name", "")
            # Count members
            member_count = supabase.table("project_members")\
                .select("id", count="exact")\
                .eq("project_id", p["id"])\
                .execute()
            p["member_count"] = member_count.count or 0
            projects.append(p)

        return {
            "projects": projects,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception:
        logger.exception("Error listing all projects")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/audit-log", response_model=AdminAuditLogResponse)
async def get_global_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: CurrentUser = Depends(require_admin),
):
    """Get global permission audit log across all projects."""
    try:
        offset = (page - 1) * page_size
        result = supabase.table("permission_audit_log")\
            .select("*")\
            .order("created_at", desc=True)\
            .range(offset, offset + page_size - 1)\
            .execute()

        count_result = supabase.table("permission_audit_log").select("id", count="exact").execute()
        total = count_result.count or 0

        rows = result.data or []

        # Collect ids for batch lookups
        actor_ids = list({r["actor_id"] for r in rows if r.get("actor_id")})
        target_ids = list({r["target_user_id"] for r in rows if r.get("target_user_id")})
        project_ids = list({r["project_id"] for r in rows if r.get("project_id")})
        user_ids = list(set(actor_ids + target_ids))

        users_map: dict = {}
        if user_ids:
            user_rows = supabase.table("users").select("id, email, full_name").in_("id", user_ids).execute().data or []
            users_map = {u["id"]: u for u in user_rows}

        projects_map: dict = {}
        if project_ids:
            proj_rows = supabase.table("projects").select("id, name").in_("id", project_ids).execute().data or []
            projects_map = {p["id"]: p for p in proj_rows}

        entries = []
        for r in rows:
            actor = users_map.get(r.get("actor_id"), {})
            target = users_map.get(r.get("target_user_id"), {})
            proj = projects_map.get(r.get("project_id"), {})
            entries.append({
                **r,
                "actor_name": actor.get("full_name"),
                "actor_email": actor.get("email", ""),
                "target_name": target.get("full_name"),
                "target_email": target.get("email", ""),
                "project_name": proj.get("name", ""),
            })

        return {"entries": entries, "total": total, "page": page, "page_size": page_size}
    except Exception:
        logger.exception("Error fetching global audit log")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/projects/{project_id}/audit-log", response_model=List[PermissionAuditLogResponse])
async def get_permission_audit_log(
    project_id: UUID,
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    _admin: CurrentUser = Depends(require_admin),
):
    """Get permission change audit log for a project."""
    try:
        result = supabase.table("permission_audit_log")\
            .select("*")\
            .eq("project_id", str(project_id))\
            .order("created_at", desc=True)\
            .range(offset, offset + limit - 1)\
            .execute()

        return [PermissionAuditLogResponse(**r) for r in (result.data or [])]
    except Exception:
        logger.exception("Error fetching permission audit log")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
