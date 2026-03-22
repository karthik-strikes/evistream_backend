"""
Project member management endpoints.
Allows project owners to invite, manage, and remove project members.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import create_client
from uuid import UUID
from typing import List

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import ProjectMemberResponse, ProjectMemberInvite, ProjectMemberUpdate
from app.services.project_access import check_project_access

logger = logging.getLogger(__name__)

router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _require_owner(project_id: UUID, user_id: UUID):
    """Raise 403 if user is not the project owner."""
    result = supabase.table("projects")\
        .select("id, user_id")\
        .eq("id", str(project_id))\
        .execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if result.data[0]["user_id"] != str(user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the project owner can manage members")


@router.get("/{project_id}/members", response_model=List[ProjectMemberResponse])
async def list_members(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user)
):
    """List all members of a project including the owner. Only the owner can see the full list."""
    try:
        _require_owner(project_id, user_id)

        # Fetch the project to get the owner
        project_result = supabase.table("projects")\
            .select("user_id")\
            .eq("id", str(project_id))\
            .execute()

        owner_user_id = project_result.data[0]["user_id"] if project_result.data else None

        result = supabase.table("project_members")\
            .select("*, users!project_members_user_id_fkey(email, full_name)")\
            .eq("project_id", str(project_id))\
            .order("created_at")\
            .execute()

        members = []
        member_user_ids = set()

        for row in (result.data or []):
            user_info = row.get("users") or {}
            member_user_ids.add(row["user_id"])
            members.append(ProjectMemberResponse(
                id=row["id"],
                project_id=UUID(row["project_id"]),
                user_id=UUID(row["user_id"]),
                email=user_info.get("email", ""),
                full_name=user_info.get("full_name"),
                can_view_docs=row["can_view_docs"],
                can_upload_docs=row["can_upload_docs"],
                can_create_forms=row["can_create_forms"],
                can_run_extractions=row["can_run_extractions"],
                can_view_results=row["can_view_results"],
                can_adjudicate=row.get("can_adjudicate", False),
                can_qa_review=row.get("can_qa_review", False),
                can_manage_assignments=row.get("can_manage_assignments", False),
                invited_by=UUID(row["invited_by"]) if row.get("invited_by") else None,
                created_at=row["created_at"],
            ))

        # Prepend the owner as a synthetic member if not already in project_members
        if owner_user_id and owner_user_id not in member_user_ids:
            owner_result = supabase.table("users")\
                .select("id, email, full_name")\
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
                    can_view_docs=True,
                    can_upload_docs=True,
                    can_create_forms=True,
                    can_run_extractions=True,
                    can_view_results=True,
                    can_adjudicate=True,
                    can_qa_review=True,
                    can_manage_assignments=True,
                    invited_by=None,
                    created_at=None,
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
        _require_owner(project_id, user_id)

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

        # Create member record
        member_data = {
            "project_id": str(project_id),
            "user_id": target_user_id,
            "can_view_docs": invite.can_view_docs,
            "can_upload_docs": invite.can_upload_docs,
            "can_create_forms": invite.can_create_forms,
            "can_run_extractions": invite.can_run_extractions,
            "can_view_results": invite.can_view_results,
            "can_adjudicate": invite.can_adjudicate,
            "can_qa_review": invite.can_qa_review,
            "can_manage_assignments": invite.can_manage_assignments,
            "invited_by": str(user_id),
        }

        result = supabase.table("project_members").insert(member_data).execute()

        if not result.data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add member")

        row = result.data[0]

        return ProjectMemberResponse(
            id=row["id"],
            project_id=UUID(row["project_id"]),
            user_id=UUID(row["user_id"]),
            email=target_user["email"],
            full_name=target_user.get("full_name"),
            can_view_docs=row["can_view_docs"],
            can_upload_docs=row["can_upload_docs"],
            can_create_forms=row["can_create_forms"],
            can_run_extractions=row["can_run_extractions"],
            can_view_results=row["can_view_results"],
            can_adjudicate=row.get("can_adjudicate", False),
            can_qa_review=row.get("can_qa_review", False),
            can_manage_assignments=row.get("can_manage_assignments", False),
            invited_by=UUID(row["invited_by"]) if row.get("invited_by") else None,
            created_at=row["created_at"],
        )

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
    """Update a member's permissions."""
    try:
        _require_owner(project_id, user_id)

        # Get existing member
        member_result = supabase.table("project_members")\
            .select("*, users!project_members_user_id_fkey(email, full_name)")\
            .eq("project_id", str(project_id))\
            .eq("user_id", str(member_user_id))\
            .execute()

        if not member_result.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

        row = member_result.data[0]

        # Build update data from non-None fields
        update_data = {}
        for field in [
            "can_view_docs", "can_upload_docs", "can_create_forms",
            "can_run_extractions", "can_view_results",
            "can_adjudicate", "can_qa_review", "can_manage_assignments",
        ]:
            value = getattr(update, field, None)
            if value is not None:
                update_data[field] = value

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

        return ProjectMemberResponse(
            id=updated["id"],
            project_id=UUID(updated["project_id"]),
            user_id=UUID(updated["user_id"]),
            email=user_info.get("email", ""),
            full_name=user_info.get("full_name"),
            can_view_docs=updated["can_view_docs"],
            can_upload_docs=updated["can_upload_docs"],
            can_create_forms=updated["can_create_forms"],
            can_run_extractions=updated["can_run_extractions"],
            can_view_results=updated["can_view_results"],
            can_adjudicate=updated.get("can_adjudicate", False),
            can_qa_review=updated.get("can_qa_review", False),
            can_manage_assignments=updated.get("can_manage_assignments", False),
            invited_by=UUID(updated["invited_by"]) if updated.get("invited_by") else None,
            created_at=updated["created_at"],
        )

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
        _require_owner(project_id, user_id)

        result = supabase.table("project_members")\
            .delete()\
            .eq("project_id", str(project_id))\
            .eq("user_id", str(member_user_id))\
            .execute()

        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error removing project member")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred")
