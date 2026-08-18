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


# ── Archived-project read-only guard ─────────────────────────────────────────
# The backend raises 409 in 14 other places for unrelated state conflicts (form
# lifecycle, duplicate rows, …), so the status code alone is not a reliable
# signal for the frontend. The header is the machine-readable discriminator;
# the body keeps the codebase's plain {"detail": "<string>"} shape.
ARCHIVED_DETAIL = (
    "This project is archived and is read-only. Restore the project to make changes."
)
ARCHIVED_ERROR_HEADERS = {"X-EviStream-Error": "project_archived"}

# Permissions that usually accompany a mutation. Used only as a DEFAULT when a
# caller doesn't pass `mutating=` explicitly — the mapping is imperfect in both
# directions (several GET endpoints request can_adjudicate / can_qa_review /
# can_create_forms just to gate visibility), so read endpoints holding a write
# permission must opt out with mutating=False.
WRITE_PERMISSIONS = frozenset({
    "can_upload_docs",
    "can_create_forms",
    "can_run_extractions",
    "can_run_manual_extractions",
    "can_adjudicate",
    "can_qa_review",
    "can_manage_assignments",
    "can_manage_members",
    "can_manage_project",
})


async def assert_project_writable(project_id: UUID) -> None:
    """Raise 409 if the project is archived.

    For the two mutations that cannot route through check_project_access:
    accepting an invitation (the caller isn't a member yet, so there is no role
    to resolve) and updating one's own assignment status (authorized by
    reviewer identity rather than by project role).
    """
    result = supabase.table("projects")\
        .select("id, archived_at")\
        .eq("id", str(project_id))\
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    if result.data[0].get("archived_at"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ARCHIVED_DETAIL,
            headers=ARCHIVED_ERROR_HEADERS,
        )


async def check_project_access(
    project_id: UUID,
    user_id: UUID,
    permission: str = None,
    mutating: bool = None,
) -> dict:
    """
    Verify that user_id has access to project_id.

    Returns permissions dict if access is granted.
    Raises HTTP 404 if project doesn't exist or user has no access at all.
    Raises HTTP 403 if user is a member but lacks the specified permission.
    Raises HTTP 409 if the project is archived and this call is a mutation
    (see the `mutating` arg).

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
        mutating: Whether this call mutates project data, which decides the
            archived read-only guard.
            - None (default): infer from `permission` via WRITE_PERMISSIONS.
              Fail-closed, so a new endpoint passing a write permission is
              guarded automatically.
            - False: never guard. Required for read endpoints that request a
              write permission purely to gate visibility (e.g. GET
              /adjudication/compare, GET /qa/queue), for archive/restore
              itself, and for cancelling work already in flight.
            - True: always guard, even when `permission` is None. For mutations
              authorized by something other than a project permission flag.
    """
    project_row = None  # populated lazily; carries archived_at when fetched

    # 1. Admin global bypass — gets full access to any existing project
    global_role = user_role_var.get()
    if global_role == "admin":
        project_result = supabase.table("projects")\
            .select("id, archived_at")\
            .eq("id", str(project_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        project_row = project_result.data[0]
        permissions = ADMIN_PERMISSIONS.copy()
        return _finalize(permissions, project_id, project_row, permission, mutating)

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
            .select("id, user_id, archived_at")\
            .eq("id", str(project_id))\
            .execute()

        if not project_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        project_row = project_result.data[0]

        if project_row["user_id"] == str(user_id):
            permissions = OWNER_PERMISSIONS.copy()
        else:
            # Project exists but user has no access
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

    return _finalize(permissions, project_id, project_row, permission, mutating)


def _finalize(
    permissions: dict,
    project_id: UUID,
    project_row: dict | None,
    permission: str | None,
    mutating: bool | None,
) -> dict:
    """Attach derived flags, then enforce the permission and archive gates."""
    # Derived: who may rename / archive / restore a project. Deliberately NOT
    # part of the role presets — the member/invite/transfer endpoints splat
    # every `can_*` preset key straight into project_members rows, and there is
    # no such column there. Managers get it; members and viewers never do.
    permissions["can_manage_project"] = bool(
        permissions.get("is_owner")
        or permissions.get("is_admin")
        or permissions.get("role") == "manager"
    )

    # Check specific permission if requested
    if permission and not permissions.get(permission, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action"
        )

    # Archived projects are read-only. Reads pass through so archived projects
    # stay viewable and exportable. 403 is raised above before this 409, so an
    # unauthorized caller learns nothing about the project's archive state.
    should_guard = (permission in WRITE_PERMISSIONS) if mutating is None else mutating
    if should_guard:
        if project_row is None:
            # The project_members path never touches `projects`; fetch only
            # when a write is actually being attempted so reads keep their
            # current query count.
            project_result = supabase.table("projects")\
                .select("id, archived_at")\
                .eq("id", str(project_id))\
                .execute()
            if not project_result.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Project not found"
                )
            project_row = project_result.data[0]

        if project_row.get("archived_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ARCHIVED_DETAIL,
                headers=ARCHIVED_ERROR_HEADERS,
            )

    return permissions
