"""
Project management endpoints - Full CRUD operations.
"""

import logging
from collections import Counter
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
from uuid import UUID
from typing import List

from app.dependencies import get_current_user
from app.config import settings
from app.context import user_role_var
from app.models.schemas import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    MyPermissionsResponse, OwnershipTransferRequest,
)
from app.services.project_access import check_project_access, OWNER_PERMISSIONS, MANAGER_PERMISSIONS, VIEWER_PERMISSIONS

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    user_id: UUID = Depends(get_current_user)
):
    """
    Create a new project.

    - **name**: Project name (required, 1-255 characters)
    - **description**: Optional project description
    """
    try:
        # Demo account: cap the number of projects it can accumulate
        if str(user_id) == settings.DEMO_USER_ID:
            owned = supabase.table("projects").select("id", count="exact")\
                .eq("user_id", str(user_id)).execute()
            if (owned.count or 0) >= settings.DEMO_MAX_PROJECTS:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"The demo is limited to {settings.DEMO_MAX_PROJECTS} projects. Delete one to make room."
                )

        result = supabase.table("projects").insert({
            "user_id": str(user_id),
            "name": project_data.name,
            "description": project_data.description
        }).execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create project"
            )

        project = result.data[0]

        # Insert owner membership row so multi-owner access checks work
        owner_perms = {k: v for k, v in OWNER_PERMISSIONS.items() if k.startswith("can_")}
        supabase.table("project_members").insert({
            "project_id": project["id"],
            "user_id": str(user_id),
            "role": "owner",
            "invited_by": str(user_id),
            **owner_perms,
        }).execute()

        project["forms_count"] = 0
        project["documents_count"] = 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("", response_model=List[ProjectResponse])
async def list_projects(user_id: UUID = Depends(get_current_user)):
    """
    List all projects for the current user.

    Returns projects sorted by creation date (newest first).
    Includes counts of forms and documents in each project.
    """
    try:
        global_role = user_role_var.get()

        if global_role == "admin":
            # Admin sees ALL projects across the platform
            all_result = supabase.table("projects")\
                .select("*")\
                .order("created_at", desc=True)\
                .execute()
            projects = all_result.data or []
        else:
            # Get owned projects
            owned_result = supabase.table("projects")\
                .select("*")\
                .eq("user_id", str(user_id))\
                .order("created_at", desc=True)\
                .execute()
            owned_projects = owned_result.data or []

            # Get member projects
            member_result = supabase.table("project_members")\
                .select("project_id")\
                .eq("user_id", str(user_id))\
                .execute()
            member_project_ids = [r["project_id"] for r in (member_result.data or [])]

            member_projects = []
            if member_project_ids:
                mp_result = supabase.table("projects")\
                    .select("*")\
                    .in_("id", member_project_ids)\
                    .order("created_at", desc=True)\
                    .execute()
                member_projects = mp_result.data or []

            # Merge, deduplicate by id
            seen = set()
            projects = []
            for p in owned_projects + member_projects:
                if p["id"] not in seen:
                    seen.add(p["id"])
                    projects.append(p)

        # Batch-fetch counts for all projects in 2 queries (not 2N)
        project_ids = [p["id"] for p in projects]
        if project_ids:
            all_forms = supabase.table("forms")\
                .select("project_id")\
                .in_("project_id", project_ids)\
                .execute()
            forms_count_map = Counter(r["project_id"] for r in (all_forms.data or []))

            all_docs = supabase.table("documents")\
                .select("project_id")\
                .in_("project_id", project_ids)\
                .execute()
            docs_count_map = Counter(r["project_id"] for r in (all_docs.data or []))
        else:
            forms_count_map = {}
            docs_count_map = {}

        for project in projects:
            project["forms_count"] = forms_count_map.get(project["id"], 0)
            project["documents_count"] = docs_count_map.get(project["id"], 0)

        return [ProjectResponse(**p) for p in projects]

    except Exception as e:
        logger.exception("Error listing projects")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get a specific project by ID.

    Returns 404 if project doesn't exist or doesn't belong to the user.
    """
    try:
        await check_project_access(project_id, user_id)

        result = supabase.table("projects")\
            .select("*")\
            .eq("id", str(project_id))\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        project = result.data[0]

        forms_count = supabase.table("forms")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["forms_count"] = forms_count.count or 0

        docs_count = supabase.table("documents")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["documents_count"] = docs_count.count or 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting project")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    user_id: UUID = Depends(get_current_user)
):
    """
    Update a project.

    Can update name and/or description.
    Only the project owner or an admin can update it.
    """
    try:
        global_role = user_role_var.get()
        if global_role == "admin":
            existing = supabase.table("projects")\
                .select("id")\
                .eq("id", str(project_id))\
                .execute()
        else:
            existing = supabase.table("projects")\
                .select("id")\
                .eq("id", str(project_id))\
                .eq("user_id", str(user_id))\
                .execute()

        if not existing.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        update_data = {}
        if project_data.name is not None:
            update_data["name"] = project_data.name
        if project_data.description is not None:
            update_data["description"] = project_data.description

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        result = supabase.table("projects")\
            .update(update_data)\
            .eq("id", str(project_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update project"
            )

        project = result.data[0]

        forms_count = supabase.table("forms")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["forms_count"] = forms_count.count or 0

        docs_count = supabase.table("documents")\
            .select("id", count="exact")\
            .eq("project_id", str(project_id))\
            .execute()
        project["documents_count"] = docs_count.count or 0

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """
    Delete a project.

    This will CASCADE delete all related:
    - Documents
    - Forms
    - Jobs
    - Extraction results

    Only project owners (any owner in a multi-owner project) or admins can delete it.
    """
    try:
        # Demo account can't delete the seeded showcase projects
        if str(user_id) == settings.DEMO_USER_ID and str(project_id) in settings.DEMO_SEED_PROJECT_IDS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seeded demo projects can't be deleted."
            )

        permissions = await check_project_access(project_id, user_id)

        if not permissions.get("is_owner") and not permissions.get("is_admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only project owners can delete a project"
            )

        supabase.table("projects")\
            .delete()\
            .eq("id", str(project_id))\
            .execute()

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting project")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.get("/{project_id}/my-permissions", response_model=MyPermissionsResponse)
async def get_my_permissions(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Get the current user's effective permissions for a project."""
    try:
        permissions = await check_project_access(project_id, user_id)
        return MyPermissionsResponse(**permissions)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/{project_id}/transfer-ownership", status_code=status.HTTP_200_OK)
async def transfer_ownership(
    project_id: UUID,
    data: OwnershipTransferRequest,
    user_id: UUID = Depends(get_current_user)
):
    """
    Promote another user to owner and optionally change the caller's own role.

    Any current owner or admin can invoke this. The target becomes an owner;
    the caller's role is set to previous_owner_role (default: manager).
    Use previous_owner_role='none' to leave the project entirely.
    """
    try:
        global_role = user_role_var.get()

        # Verify caller is an owner (or admin)
        if global_role != "admin":
            caller_perms = await check_project_access(project_id, user_id)
            if not caller_perms.get("is_owner"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only project owners or admins can transfer ownership"
                )
        else:
            # Admin: just verify project exists
            proj_check = supabase.table("projects").select("id").eq("id", str(project_id)).execute()
            if not proj_check.data:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        new_owner_id = str(data.new_owner_id)

        # Cannot transfer to self
        if new_owner_id == str(user_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot transfer ownership to yourself")

        # Verify target exists and is active
        new_owner_result = supabase.table("users")\
            .select("id, is_active")\
            .eq("id", new_owner_id)\
            .execute()

        if not new_owner_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if not new_owner_result.data[0].get("is_active", True):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User account is not active")

        # Check if target is already an owner
        target_row = supabase.table("project_members")\
            .select("id, role")\
            .eq("project_id", str(project_id))\
            .eq("user_id", new_owner_id)\
            .execute()

        if target_row.data and target_row.data[0].get("role") == "owner":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already an owner of this project")

        # Promote target to owner (upsert: update existing row or insert new)
        owner_perms = {k: v for k, v in OWNER_PERMISSIONS.items() if k.startswith("can_")}
        if target_row.data:
            supabase.table("project_members")\
                .update({"role": "owner", **owner_perms})\
                .eq("id", target_row.data[0]["id"])\
                .execute()
        else:
            supabase.table("project_members").insert({
                "project_id": str(project_id),
                "user_id": new_owner_id,
                "role": "owner",
                "invited_by": str(user_id),
                **owner_perms,
            }).execute()

        # Handle caller's new role (must happen AFTER promoting target so min-1-owner holds)
        if data.previous_owner_role != "none":
            if data.previous_owner_role == "manager":
                role_perms = {k: v for k, v in MANAGER_PERMISSIONS.items() if k.startswith("can_")}
            elif data.previous_owner_role == "viewer":
                role_perms = {k: v for k, v in VIEWER_PERMISSIONS.items() if k.startswith("can_")}
            else:  # member — all flags off by default
                role_perms = {
                    "can_view_docs": False, "can_upload_docs": False, "can_create_forms": False,
                    "can_run_extractions": False, "can_run_manual_extractions": False,
                    "can_view_results": False, "can_adjudicate": False,
                    "can_qa_review": False, "can_manage_assignments": False, "can_manage_members": False,
                }
            caller_row = supabase.table("project_members")\
                .select("id")\
                .eq("project_id", str(project_id))\
                .eq("user_id", str(user_id))\
                .execute()
            if caller_row.data:
                supabase.table("project_members")\
                    .update({"role": data.previous_owner_role, **role_perms})\
                    .eq("id", caller_row.data[0]["id"])\
                    .execute()
        else:
            # Remove caller from project entirely
            supabase.table("project_members")\
                .delete()\
                .eq("project_id", str(project_id))\
                .eq("user_id", str(user_id))\
                .execute()

        # Keep projects.user_id pointing at the newly promoted owner (legacy compat)
        supabase.table("projects")\
            .update({"user_id": new_owner_id})\
            .eq("id", str(project_id))\
            .execute()

        # Audit log
        try:
            supabase.table("permission_audit_log").insert({
                "project_id": str(project_id),
                "actor_id": str(user_id),
                "target_user_id": new_owner_id,
                "action": "ownership_transferred",
                "old_values": {"caller_role": "owner"},
                "new_values": {"new_owner_id": new_owner_id, "caller_new_role": data.previous_owner_role},
            }).execute()
        except Exception as e:
            logger.error(f"Failed to write ownership transfer audit log: {e}")

        return {
            "message": "Ownership transferred successfully",
            "new_owner_id": new_owner_id,
            "old_owner_role": data.previous_owner_role,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error transferring project ownership")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
