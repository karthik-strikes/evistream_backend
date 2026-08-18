"""
Project management endpoints - Full CRUD operations.
"""

import logging
from collections import Counter
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import create_client
from uuid import UUID
from typing import List

from app.dependencies import get_current_user
from app.config import settings
from app.context import user_role_var
from app.models.schemas import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    MyPermissionsResponse, OwnershipTransferRequest, ReviewSettingsUpdate,
    ReviewScopeUpdate,
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
async def list_projects(
    user_id: UUID = Depends(get_current_user),
    include_archived: bool = Query(
        False,
        description="Include archived projects. Off by default so the project "
                    "selector and every project dropdown stay clean.",
    ),
):
    """
    List all projects for the current user.

    Returns projects sorted by creation date (newest first).
    Includes counts of forms and documents in each project.

    Archived projects are excluded unless include_archived=true.
    """
    try:
        global_role = user_role_var.get()

        def _visible(query):
            """Hide archived projects unless the caller asked for them."""
            return query if include_archived else query.is_("archived_at", "null")

        if global_role == "admin":
            # Admin sees ALL projects across the platform
            all_result = _visible(
                supabase.table("projects")
                .select("*")
            )\
                .order("created_at", desc=True)\
                .execute()
            projects = all_result.data or []
            for project in projects:
                project["my_role"] = "admin"
        else:
            # Get owned projects
            owned_result = _visible(
                supabase.table("projects")
                .select("*")
                .eq("user_id", str(user_id))
            )\
                .order("created_at", desc=True)\
                .execute()
            owned_projects = owned_result.data or []

            # Get member projects + this user's role on each
            member_result = supabase.table("project_members")\
                .select("project_id, role")\
                .eq("user_id", str(user_id))\
                .execute()
            member_rows = member_result.data or []
            member_role_map = {r["project_id"]: r["role"] for r in member_rows}
            member_project_ids = list(member_role_map.keys())

            member_projects = []
            if member_project_ids:
                mp_result = _visible(
                    supabase.table("projects")
                    .select("*")
                    .in_("id", member_project_ids)
                )\
                    .order("created_at", desc=True)\
                    .execute()
                member_projects = mp_result.data or []

            # Merge, deduplicate by id, and attach the caller's role.
            # Legacy creator (projects.user_id == caller) is treated as owner
            # even without a project_members row.
            seen = set()
            projects = []
            for p in owned_projects + member_projects:
                if p["id"] not in seen:
                    seen.add(p["id"])
                    if p["id"] in member_role_map:
                        p["my_role"] = member_role_map[p["id"]]
                    elif p.get("user_id") == str(user_id):
                        p["my_role"] = "owner"
                    projects.append(p)

            # Re-sort: the two source queries are each ordered, but concatenating
            # them puts every owned project ahead of every member-of project
            # regardless of date. Sort the merged list so it is globally
            # newest-first, matching the admin path above.
            projects.sort(key=lambda p: p.get("created_at") or "", reverse=True)

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
    Requires can_manage_project — owner, global admin, or manager.

    Routed through check_project_access (rather than matching projects.user_id
    directly) so that co-owners and managers who aren't the original creator
    can rename, and so an archived project rejects the write with 409.
    """
    try:
        await check_project_access(project_id, user_id, "can_manage_project")

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

        # mutating=False: an archived project must still be deletable.
        permissions = await check_project_access(project_id, user_id, mutating=False)

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


@router.patch("/{project_id}/review-scope")
async def update_review_scope(
    project_id: UUID,
    body: ReviewScopeUpdate,
    user_id: UUID = Depends(get_current_user)
):
    """Update a project's review scope.

    Free text describing what the review is about. It is injected into every
    extraction prompt at runtime as CONTEXT — it helps the model pick the right
    arm, population, timepoint or measure — and never filters rows.

    Requires can_create_forms: this is extraction-design authority, the same
    power as editing a form's field prompts. That permission is in
    WRITE_PERMISSIONS, so archived projects are rejected automatically.
    """
    try:
        await check_project_access(project_id, user_id, "can_create_forms")

        # Blank clears the scope rather than storing "" — extraction treats
        # None and "" alike, but a null keeps the column honest.
        scope = (body.review_scope or "").strip() or None
        updated = supabase.table("projects")\
            .update({"review_scope": scope})\
            .eq("id", str(project_id))\
            .execute()

        if not updated.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        return {"review_scope": updated.data[0].get("review_scope")}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating review scope")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update review scope: {str(e)}"
        )


@router.patch("/{project_id}/review-settings")
async def update_review_settings(
    project_id: UUID,
    body: ReviewSettingsUpdate,
    user_id: UUID = Depends(get_current_user)
):
    """Update a project's manual-review blinding settings.

    Governs whether R1/R2 can see each other's manual extractions across
    every form in the project. Requires can_manage_assignments — the same
    permission that gates the Assignments UI this setting lives in.
    """
    try:
        await check_project_access(project_id, user_id, "can_manage_assignments")

        new_settings = {"blinding": body.blinding.value, "hide_ai_results": body.hide_ai_results}
        updated = supabase.table("projects")\
            .update({"review_settings": new_settings})\
            .eq("id", str(project_id))\
            .execute()

        if not updated.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        return {"review_settings": updated.data[0]["review_settings"]}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating review settings")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Archive a project.

    Archived projects are hidden from the default project list — and therefore
    from the project selector and every project dropdown — and become
    read-only: write endpoints return 409 until the project is restored.
    Results, forms, and documents stay fully viewable and exportable.

    Requires can_manage_project (owner, global admin, or manager). Idempotent.
    """
    return await _set_archived(project_id, user_id, archived=True)


@router.post("/{project_id}/unarchive", response_model=ProjectResponse)
async def unarchive_project(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Restore an archived project, making it writable again.

    Requires can_manage_project (owner, global admin, or manager). Idempotent.
    """
    return await _set_archived(project_id, user_id, archived=False)


async def _set_archived(project_id: UUID, user_id: UUID, archived: bool) -> ProjectResponse:
    """Shared archive/restore body: permission check, update, audit log."""
    try:
        # mutating=False: the guard must not block the very call that restores
        # the project, nor a redundant re-archive.
        permissions = await check_project_access(
            project_id, user_id, "can_manage_project", mutating=False
        )

        archived_at = datetime.now(timezone.utc).isoformat() if archived else None
        result = supabase.table("projects")\
            .update({
                "archived_at": archived_at,
                "archived_by": str(user_id) if archived else None,
            })\
            .eq("id", str(project_id))\
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        project = result.data[0]
        # Preserve my_role so the frontend keeps its owner/manager gating when
        # it swaps this row into its cached project list.
        project["my_role"] = permissions.get("role")

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

        try:
            supabase.table("permission_audit_log").insert({
                "project_id": str(project_id),
                "actor_id": str(user_id),
                "action": "project_archived" if archived else "project_restored",
                "new_values": {"archived_at": archived_at},
            }).execute()
        except Exception as e:
            logger.error(f"Failed to write archive audit log: {e}")

        return ProjectResponse(**project)

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error changing project archive state")
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
        permissions = await check_project_access(project_id, user_id, mutating=False)
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
            # mutating=False: reassigning ownership of an archived project is
            # legitimate (e.g. the owner leaves the org), so it isn't blocked.
            caller_perms = await check_project_access(project_id, user_id, mutating=False)
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
