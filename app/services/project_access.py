"""
Project access control helper.
Centralizes ownership and membership permission checks.
"""

import logging
from uuid import UUID
from fastapi import HTTPException, status
from supabase import create_client

from app.config import settings

logger = logging.getLogger(__name__)

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# Full permissions dict for project owners
OWNER_PERMISSIONS = {
    "is_owner": True,
    "can_view_docs": True,
    "can_upload_docs": True,
    "can_create_forms": True,
    "can_run_extractions": True,
    "can_view_results": True,
    "can_adjudicate": True,
    "can_qa_review": True,
    "can_manage_assignments": True,
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

    Args:
        project_id: The project UUID
        user_id: The requesting user's UUID
        permission: Optional specific permission flag to check (e.g. "can_create_forms")
    """
    # Fetch project
    project_result = supabase.table("projects")\
        .select("id, user_id")\
        .eq("id", str(project_id))\
        .execute()

    if not project_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    project = project_result.data[0]

    # Owner has all permissions
    if project["user_id"] == str(user_id):
        return OWNER_PERMISSIONS.copy()

    # Check membership
    member_result = supabase.table("project_members")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("user_id", str(user_id))\
        .execute()

    if not member_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    member = member_result.data[0]
    permissions = {
        "is_owner": False,
        "can_view_docs": member["can_view_docs"],
        "can_upload_docs": member["can_upload_docs"],
        "can_create_forms": member["can_create_forms"],
        "can_run_extractions": member["can_run_extractions"],
        "can_view_results": member["can_view_results"],
        "can_adjudicate": member.get("can_adjudicate", False),
        "can_qa_review": member.get("can_qa_review", False),
        "can_manage_assignments": member.get("can_manage_assignments", False),
    }

    if permission and not permissions.get(permission, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have permission to perform this action"
        )

    return permissions
