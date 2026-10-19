"""
Project access control helper.
Centralizes ownership, membership, and role-based permission checks.

Roles hierarchy:
- Admin (global): Full access to all projects (except deletion)
- Owner: Full access to their project including deletion
- Manager: Full access except project deletion (fixed preset)
- Member: Granular permissions (individual boolean flags)
- Viewer: Read-only access (fixed preset)
"""

import logging
from uuid import UUID
from fastapi import HTTPException, status
from supabase import create_client

from app.config import settings
from app.context import user_role_var

logger = logging.getLogger(__name__)

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# ── Role-based permission presets ────────────────────────────────────────────

ADMIN_PERMISSIONS = {
    "is_owner": False,
    "is_admin": True,
    "role": "admin",
    "can_view_docs": True,
    "can_upload_docs": True,
    "can_create_forms": True,
    "can_run_extractions": True,
    "can_run_manual_extractions": True,
    "can_view_results": True,
    "can_adjudicate": True,
    "can_qa_review": True,
    "can_manage_assignments": True,
    "can_manage_members": True,
}

OWNER_PERMISSIONS = {
    "is_owner": True,
    "is_admin": False,
    "role": "owner",
    "can_view_docs": True,
    "can_upload_docs": True,
    "can_create_forms": True,
    "can_run_extractions": True,
    "can_run_manual_extractions": True,
    "can_view_results": True,
    "can_adjudicate": True,
    "can_qa_review": True,
    "can_manage_assignments": True,
    "can_manage_members": True,
}

MANAGER_PERMISSIONS = {
    "is_owner": False,
    "is_admin": False,
    "role": "manager",
    "can_view_docs": True,
    "can_upload_docs": True,
    "can_create_forms": True,
    "can_run_extractions": True,
    "can_run_manual_extractions": True,
    "can_view_results": True,
    "can_adjudicate": True,
    "can_qa_review": True,
    "can_manage_assignments": True,
    "can_manage_members": True,
}

VIEWER_PERMISSIONS = {
    "is_owner": False,
    "is_admin": False,
    "role": "viewer",
    "can_view_docs": True,
    "can_upload_docs": False,
    "can_create_forms": False,
    "can_run_extractions": False,
    "can_run_manual_extractions": False,
    "can_view_results": True,
    "can_adjudicate": False,
    "can_qa_review": False,
    "can_manage_assignments": False,
    "can_manage_members": False,
}


async def check_project_access(
    project_id: UUID,
    user_id: UUID,
    permission: str = None,
) -> dict:
    """
    Verify that user_id has access to project_id.

    Returns permissions dict if access is granted.
    Raises HTTP 404 if project doesn't exist or user has no access at all.
    Raises HTTP 403 if user is a member but lacks the specified permission.

    Access priority:
    1. Admin (global role) → full access to any project
    2. project_members.role='owner' → full access (supports multiple owners)
    3. Manager → fixed full-access preset (except project deletion)
    4. Member → individual boolean permission flags
    5. Viewer → read-only preset
    6. Legacy fallback: projects.user_id match (unmigrated projects)

    Args:
        project_id: The project UUID
        user_id: The requesting user's UUID
        permission: Optional specific permission flag to check (e.g. "can_create_forms")
    """
    # 1. Admin global bypass — gets full access to any existing project
    global_role = user_role_var.get()
    if global_role == "admin":
        project_result = supabase.table("projects")\
            .select("id")\
            .eq("id", str(project_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        return ADMIN_PERMISSIONS.copy()

    # 2. Check membership row (primary path — supports multi-owner)
    member_result = supabase.table("project_members")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("user_id", str(user_id))\
        .execute()

    if member_result.data:
        member = member_result.data[0]
        member_role = member.get("role", "member")

        if member_role == "owner":
            permissions = OWNER_PERMISSIONS.copy()
        elif member_role == "manager":
            permissions = MANAGER_PERMISSIONS.copy()
        elif member_role == "viewer":
            permissions = {
                "is_owner": False,
                "is_admin": False,
                "role": "viewer",
                "can_view_docs": member["can_view_docs"],
                "can_upload_docs": member["can_upload_docs"],
                "can_create_forms": member["can_create_forms"],
                "can_run_extractions": member["can_run_extractions"],
                "can_run_manual_extractions": member.get("can_run_manual_extractions", False),
                "can_view_results": member["can_view_results"],
                "can_adjudicate": member.get("can_adjudicate", False),
                "can_qa_review": member.get("can_qa_review", False),
                "can_manage_assignments": member.get("can_manage_assignments", False),
                "can_manage_members": member.get("can_manage_members", False),
            }
        else:
            # member — use individual permission flags
            permissions = {
                "is_owner": False,
                "is_admin": False,
                "role": "member",
                "can_view_docs": member["can_view_docs"],
                "can_upload_docs": member["can_upload_docs"],
                "can_create_forms": member["can_create_forms"],
                "can_run_extractions": member["can_run_extractions"],
                "can_run_manual_extractions": member.get("can_run_manual_extractions", False),
                "can_view_results": member["can_view_results"],
                "can_adjudicate": member.get("can_adjudicate", False),
                "can_qa_review": member.get("can_qa_review", False),
                "can_manage_assignments": member.get("can_manage_assignments", False),
                "can_manage_members": member.get("can_manage_members", False),
            }
    else:
        # 3. Legacy fallback: creator via projects.user_id (pre-migration projects)
        project_result = supabase.table("projects")\
            .select("id, user_id")\
            .eq("id", str(project_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        if project_result.data[0]["user_id"] == str(user_id):
            permissions = OWNER_PERMISSIONS.copy()
        else:
            # Project exists but user has no access
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

    # 4. Check specific permission if requested
    if permission and not permissions.get(permission, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action"
        )

    return permissions
