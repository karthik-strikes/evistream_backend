"""
Project member management endpoints.
Allows project owners, managers, and admins to invite, manage, and remove project members.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
from uuid import UUID
from typing import List

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ProjectMemberResponse, ProjectMemberInvite, ProjectMemberUpdate
from app.services.project_access import (
    check_project_access, OWNER_PERMISSIONS, MANAGER_PERMISSIONS, VIEWER_PERMISSIONS,
)

logger = logging.getLogger(__name__)

router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

VALID_PROJECT_ROLES = {"owner", "manager", "member", "viewer"}


# ── Audit log helper ────────────────────────────────────────────────────────

def _log_permission_change(
    project_id: UUID, actor_id: UUID, target_user_id: UUID,
    action: str, old_values: dict | None = None, new_values: dict | None = None,
):
    """Write an entry to the permission_audit_log table."""
    try:
        supabase.table("permission_audit_log").insert({
            "project_id": str(project_id),
            "actor_id": str(actor_id),
            "target_user_id": str(target_user_id),
            "action": action,
            "old_values": old_values,
            "new_values": new_values,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to write permission audit log: {e}")


# ── Permission presets by role ──────────────────────────────────────────────

def _permissions_for_role(role: str) -> dict:
    """Return the permission flags dict for a given project role."""
    if role == "owner":
        return {k: v for k, v in OWNER_PERMISSIONS.items()
                if k.startswith("can_")}
    elif role == "manager":
        return {k: v for k, v in MANAGER_PERMISSIONS.items()
                if k.startswith("can_")}
    elif role == "viewer":
        return {k: v for k, v in VIEWER_PERMISSIONS.items()
                if k.startswith("can_")}
    # member — no preset, use caller-provided flags
    return {}


def _count_owners(project_id: UUID) -> int:
    """Return the number of owner-role members for a project."""
    result = supabase.table("project_members")\
        .select("id", count="exact")\
        .eq("project_id", str(project_id))\
        .eq("role", "owner")\
        .execute()
    return result.count or 0


# Maps each reviewer_role to the project_member flag the assignee must hold
# to actually do that work. Used by cascade cleanup when a member is demoted.
REVIEWER_ROLE_REQUIRED_FLAG = {
    "reviewer_1": "can_run_manual_extractions",
    "reviewer_2": "can_run_manual_extractions",
    "adjudicator": "can_adjudicate",
    "qa_reviewer": "can_qa_review",
}


def _cascade_cleanup_assignments(
    project_id: UUID,
    member_user_id: UUID,
    actor_id: UUID,
    lost_reviewer_roles: list[str] | None = None,
) -> int:
    """
    Delete pending/in-progress review_assignments rows for roles the member
    can no longer perform. Completed/skipped rows are preserved for audit.

    If lost_reviewer_roles is None, all four reviewer roles are cleaned up
    (used when the member is removed entirely).
    """
    roles = lost_reviewer_roles if lost_reviewer_roles is not None else list(
        REVIEWER_ROLE_REQUIRED_FLAG.keys()
    )
    if not roles:
        return 0

    result = supabase.table("review_assignments")\
        .delete()\
        .eq("project_id", str(project_id))\
        .eq("reviewer_user_id", str(member_user_id))\
        .in_("reviewer_role", roles)\
        .in_("status", ["pending", "in_progress"])\
        .execute()

    deleted = len(result.data or [])
    if deleted:
        _log_permission_change(
            project_id, actor_id, member_user_id,
            "assignments_cascade_cleanup",
            old_values={"deleted_roles": roles, "count": deleted},
            new_values=None,
        )
    return deleted


def _build_member_response(row: dict, user_info: dict) -> ProjectMemberResponse:
    """Build a ProjectMemberResponse from a DB row and user info."""
    return ProjectMemberResponse(
        id=row["id"],
        project_id=UUID(row["project_id"]),
        user_id=UUID(row["user_id"]),
        email=user_info.get("email", ""),
        full_name=user_info.get("full_name"),
        role=row.get("role", "member"),
        can_view_docs=row["can_view_docs"],
        can_upload_docs=row["can_upload_docs"],
        can_create_forms=row["can_create_forms"],
        can_run_extractions=row["can_run_extractions"],
        can_run_manual_extractions=row.get("can_run_manual_extractions", False),
        can_view_results=row["can_view_results"],
        can_adjudicate=row.get("can_adjudicate", False),
        can_qa_review=row.get("can_qa_review", False),
        can_manage_assignments=row.get("can_manage_assignments", False),
        can_manage_members=row.get("can_manage_members", False),
        invited_by=UUID(row["invited_by"]) if row.get("invited_by") else None,
        created_at=row["created_at"],
        last_seen_at=user_info.get("last_seen_at"),
    )


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/members", response_model=List[ProjectMemberResponse])
async def list_members(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """List all members of a project including the owner."""
    try:
        # Listing the roster is a basic transparency primitive — anyone who can
        # manage members OR manage assignments needs it. Gating on can_manage_members
        # alone broke the Assignments tab for managers without that flag, which then
        # cascaded a Promise.all rejection that zeroed out every stat card.
        perms = await check_project_access(project_id, user_id)
        if not (perms.get("can_manage_members") or perms.get("can_manage_assignments")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )

        # Fetch the project to get owner + created_at
        project_result = supabase.table("projects")\
            .select("user_id, created_at")\
            .eq("id", str(project_id))\
            .execute()

        owner_user_id = project_result.data[0]["user_id"] if project_result.data else None
        project_created_at = project_result.data[0].get("created_at") if project_result.data else None

        result = supabase.table("project_members")\
            .select("*, users!project_members_user_id_fkey(email, full_name, last_seen_at)")\
            .eq("project_id", str(project_id))\
            .order("created_at")\
            .execute()

        members = []
        member_user_ids = set()

        for row in (result.data or []):
            user_info = row.get("users") or {}
            member_user_ids.add(row["user_id"])
            members.append(_build_member_response(row, user_info))

        # Prepend the owner as a synthetic member if not already in project_members
        if owner_user_id and owner_user_id not in member_user_ids:
            owner_result = supabase.table("users")\
                .select("id, email, full_name, last_seen_at")\
                .eq("id", owner_user_id)\
                .execute()

            if owner_result.data:
                owner_info = owner_result.data[0]
                owner_member = ProjectMemberResponse(
                    id=UUID(owner_user_id),
                    project_id=project_id,
                    user_id=UUID(owner_user_id),
                    email=owner_info.get("email", ""),
                    full_name=owner_info.get("full_name") or "Project Owner",
                    role="owner",
                    can_view_docs=True,
                    can_upload_docs=True,
                    can_create_forms=True,
                    can_run_extractions=True,
                    can_run_manual_extractions=True,
                    can_view_results=True,
                    can_adjudicate=True,
                    can_qa_review=True,
                    can_manage_assignments=True,
                    can_manage_members=True,
                    invited_by=None,
                    created_at=project_created_at,
                    last_seen_at=owner_info.get("last_seen_at"),
                )
                members.insert(0, owner_member)

        return members

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error listing project members")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    project_id: UUID,
    invite: ProjectMemberInvite,
    user_id: UUID = Depends(get_current_user)
):
    """Invite a registered user to the project by email."""
    try:
        await check_project_access(project_id, user_id, "can_manage_members")

        # Validate role
        if invite.role not in VALID_PROJECT_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_PROJECT_ROLES))}")

        # Look up user by email
        user_result = supabase.table("users")\
            .select("id, email, full_name")\
            .eq("email", invite.email)\
            .execute()

        if not user_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        target_user = user_result.data[0]
        target_user_id = target_user["id"]

        # Cannot invite yourself (owner)
        if target_user_id == str(user_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot invite yourself")

        # Check not already a member
        existing = supabase.table("project_members")\
            .select("id")\
            .eq("project_id", str(project_id))\
            .eq("user_id", target_user_id)\
            .execute()

        if existing.data:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already a member of this project")

        # Build member data — apply role preset or use individual flags
        role_presets = _permissions_for_role(invite.role)
        member_data = {
            "project_id": str(project_id),
            "user_id": target_user_id,
            "role": invite.role,
            "invited_by": str(user_id),
        }
        if role_presets:
            # Manager or Viewer — use fixed preset
            member_data.update(role_presets)
        else:
            # Member — use individual flags from invite
            member_data.update({
                "can_view_docs": invite.can_view_docs,
                "can_upload_docs": invite.can_upload_docs,
                "can_create_forms": invite.can_create_forms,
                "can_run_extractions": invite.can_run_extractions,
                "can_run_manual_extractions": invite.can_run_manual_extractions,
                "can_view_results": invite.can_view_results,
                "can_adjudicate": invite.can_adjudicate,
                "can_qa_review": invite.can_qa_review,
                "can_manage_assignments": invite.can_manage_assignments,
                "can_manage_members": invite.can_manage_members,
            })

        result = supabase.table("project_members").insert(member_data).execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add member")

        row = result.data[0]

        # Audit log
        _log_permission_change(
            project_id, user_id, UUID(target_user_id),
            "member_invited",
            old_values=None,
            new_values={"role": invite.role, **{k: v for k, v in member_data.items() if k.startswith("can_")}},
        )

        return _build_member_response(row, target_user)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error inviting project member")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.patch("/{project_id}/members/{member_user_id}", response_model=ProjectMemberResponse)
async def update_member(
    project_id: UUID,
    member_user_id: UUID,
    update: ProjectMemberUpdate,
    user_id: UUID = Depends(get_current_user)
):
    """Update a member's role and/or permissions."""
    try:
        caller_perms = await check_project_access(project_id, user_id, "can_manage_members")

        # Self-edit guard: only project owners and global admins can modify their
        # own row. Otherwise a member granted can_manage_members could self-promote
        # to owner or flip on any capability flag.
        if member_user_id == user_id and not (caller_perms.get("is_admin") or caller_perms.get("is_owner")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot modify your own role or permissions. Ask another owner or admin.",
            )

        # Get existing member
        member_result = supabase.table("project_members")\
            .select("*, users!project_members_user_id_fkey(email, full_name)")\
            .eq("project_id", str(project_id))\
            .eq("user_id", str(member_user_id))\
            .execute()

        if not member_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

        row = member_result.data[0]

        # Privilege escalation guard: only owners/admins can promote to owner or
        # modify owner rows. Managers with can_manage_members can freely edit
        # other managers and members.
        if not (caller_perms.get("is_owner") or caller_perms.get("is_admin")):
            if update.role == "owner":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only owners can promote members to owner.",
                )
            target_role = row.get("role", "member")
            if target_role == "owner":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only owners can modify an owner's role or permissions.",
                )

        # Capture old values for audit
        old_values = {
            "role": row.get("role", "member"),
            **{f: row.get(f, False) for f in [
                "can_view_docs", "can_upload_docs", "can_create_forms",
                "can_run_extractions", "can_run_manual_extractions", "can_view_results",
                "can_adjudicate", "can_qa_review", "can_manage_assignments",
                "can_manage_members",
            ]},
        }

        # Build update data
        update_data = {}

        # Handle role change
        new_role = update.role
        if new_role is not None:
            if new_role not in VALID_PROJECT_ROLES:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_PROJECT_ROLES))}")
            # Min-1-owner invariant: prevent demoting the last owner
            current_role = row.get("role", "member")
            if current_role == "owner" and new_role != "owner":
                if _count_owners(project_id) <= 1:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="A project must have at least one owner. Promote someone else first."
                    )
            update_data["role"] = new_role
            # Apply role preset permissions
            role_presets = _permissions_for_role(new_role)
            if role_presets:
                update_data.update(role_presets)

        # Handle individual permission changes (meaningful for 'manager', 'member', and 'viewer' roles)
        effective_role = new_role or row.get("role", "member")
        if effective_role in ("manager", "member", "viewer"):
            for field in [
                "can_view_docs", "can_upload_docs", "can_create_forms",
                "can_run_extractions", "can_run_manual_extractions", "can_view_results",
                "can_adjudicate", "can_qa_review", "can_manage_assignments",
                "can_manage_members",
            ]:
                value = getattr(update, field, None)
                if value is not None:
                    update_data[field] = value

        # Re-derive role from the merged flag set so the badge stays accurate.
        # Skip when caller explicitly sent a role (already applied) or member is owner.
        if "role" not in update_data and row.get("role") != "owner":
            effective_flags = {
                f: update_data.get(f, row.get(f, False))
                for f in [
                    "can_view_docs", "can_upload_docs", "can_create_forms",
                    "can_run_extractions", "can_run_manual_extractions", "can_view_results",
                    "can_adjudicate", "can_qa_review",
                    "can_manage_assignments", "can_manage_members",
                ]
            }
            viewer_flags = {k: v for k, v in VIEWER_PERMISSIONS.items() if k.startswith("can_")}
            manager_flags = {k: v for k, v in MANAGER_PERMISSIONS.items() if k.startswith("can_")}
            if effective_flags == manager_flags:
                derived_role = "manager"
            elif effective_flags == viewer_flags:
                derived_role = "viewer"
            else:
                derived_role = "member"
            if derived_role != row.get("role"):
                update_data["role"] = derived_role

        if not update_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

        result = supabase.table("project_members")\
            .update(update_data)\
            .eq("id", row["id"])\
            .execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update member")

        updated = result.data[0]
        user_info = row.get("users") or {}

        # Audit log
        new_values = {
            "role": updated.get("role", "member"),
            **{f: updated.get(f, False) for f in [
                "can_view_docs", "can_upload_docs", "can_create_forms",
                "can_run_extractions", "can_run_manual_extractions", "can_view_results",
                "can_adjudicate", "can_qa_review", "can_manage_assignments",
                "can_manage_members",
            ]},
        }
        action = "member_role_changed" if "role" in update_data else "permissions_updated"
        _log_permission_change(project_id, user_id, member_user_id, action, old_values, new_values)

        # Cascade: if any reviewer-capability flag transitioned True→False,
        # drop the member's pending/in-progress assignments for those roles.
        lost_reviewer_roles: list[str] = []
        flag_to_roles = {
            "can_run_manual_extractions": ["reviewer_1", "reviewer_2"],
            "can_adjudicate": ["adjudicator"],
            "can_qa_review": ["qa_reviewer"],
        }
        for flag, roles in flag_to_roles.items():
            if old_values.get(flag) and not new_values.get(flag):
                lost_reviewer_roles.extend(roles)
        if lost_reviewer_roles:
            _cascade_cleanup_assignments(
                project_id, member_user_id, user_id,
                lost_reviewer_roles=lost_reviewer_roles,
            )

        return _build_member_response(updated, user_info)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating project member")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")


@router.delete("/{project_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: UUID,
    member_user_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """Remove a member from the project."""
    try:
        await check_project_access(project_id, user_id, "can_manage_members")

        # Get member for audit log + invariant check
        member_result = supabase.table("project_members")\
            .select("role")\
            .eq("project_id", str(project_id))\
            .eq("user_id", str(member_user_id))\
            .execute()

        old_role = member_result.data[0].get("role", "member") if member_result.data else None

        # Min-1-owner invariant: prevent removing the last owner
        if old_role == "owner" and _count_owners(project_id) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A project must have at least one owner. Promote someone else first."
            )

        supabase.table("project_members")\
            .delete()\
            .eq("project_id", str(project_id))\
            .eq("user_id", str(member_user_id))\
            .execute()

        # Cascade: drop pending/in-progress assignments pointing at this user.
        _cascade_cleanup_assignments(project_id, member_user_id, user_id)

        # Audit log
        _log_permission_change(
            project_id, user_id, member_user_id,
            "member_removed",
            old_values={"role": old_role} if old_role else None,
            new_values=None,
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error removing project member")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")
