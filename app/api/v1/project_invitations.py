"""
Project invitation management endpoints.

Supports inviting any email address (registered or not) to a project.
On acceptance the invitee must be authenticated and their email must match.
"""

import logging
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from supabase import create_client
from uuid import UUID
from typing import List, Optional

from app.dependencies import get_current_user
from app.config import settings
from app.models.schemas import (
    ProjectInvitationCreate, ProjectInvitationResponse,
    InvitationPreview, AcceptInvitationRequest,
)
from app.services.project_access import check_project_access, MANAGER_PERMISSIONS, VIEWER_PERMISSIONS

logger = logging.getLogger(__name__)

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# Router included under /projects prefix (handles /{project_id}/invitations/...)
project_router = APIRouter()

# Router included under /invitations prefix (handles /{token} and /accept)
router = APIRouter()

INVITATION_TTL_DAYS = 14
VALID_PROJECT_ROLES = {"manager", "member", "viewer"}


def _make_accept_url(token: str) -> str:
    base = (settings.FRONTEND_URL or "http://localhost:3000").rstrip("/")
    return f"{base}/invitations/{token}"


def _permissions_for_role(role: str) -> dict:
    if role == "manager":
        return {k: v for k, v in MANAGER_PERMISSIONS.items() if k.startswith("can_")}
    if role == "viewer":
        return {k: v for k, v in VIEWER_PERMISSIONS.items() if k.startswith("can_")}
    return {}


def _build_response(row: dict, accept_url: Optional[str] = None) -> ProjectInvitationResponse:
    return ProjectInvitationResponse(
        id=row["id"],
        project_id=UUID(row["project_id"]),
        email=row["email"],
        role=row["role"],
        can_view_docs=row.get("can_view_docs", True),
        can_upload_docs=row.get("can_upload_docs", False),
        can_create_forms=row.get("can_create_forms", False),
        can_run_extractions=row.get("can_run_extractions", False),
        can_run_manual_extractions=row.get("can_run_manual_extractions", False),
        can_view_results=row.get("can_view_results", True),
        can_adjudicate=row.get("can_adjudicate", False),
        can_qa_review=row.get("can_qa_review", False),
        can_manage_assignments=row.get("can_manage_assignments", False),
        can_manage_members=row.get("can_manage_members", False),
        invited_by=UUID(row["invited_by"]) if row.get("invited_by") else None,
        invited_by_name=row.get("_inviter_name"),
        expires_at=row["expires_at"],
        accepted_at=row.get("accepted_at"),
        revoked_at=row.get("revoked_at"),
        created_at=row["created_at"],
        accept_url=accept_url or _make_accept_url(row["token"]),
    )


# ── Project-scoped endpoints ────────────────────────────────────────────────

@project_router.get("/{project_id}/invitations", response_model=List[ProjectInvitationResponse])
async def list_invitations(
    project_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """List pending invitations for a project."""
    try:
        await check_project_access(project_id, user_id, "can_manage_members")

        now_iso = datetime.now(timezone.utc).isoformat()
        result = supabase.table("project_invitations") \
            .select("*, users!project_invitations_invited_by_fkey(full_name, email)") \
            .eq("project_id", str(project_id)) \
            .is_("accepted_at", "null") \
            .is_("revoked_at", "null") \
            .gt("expires_at", now_iso) \
            .order("created_at", desc=True) \
            .execute()

        out = []
        for row in (result.data or []):
            inviter = row.pop("users", None) or {}
            row["_inviter_name"] = inviter.get("full_name") or inviter.get("email")
            out.append(_build_response(row))
        return out

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error listing invitations")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@project_router.post(
    "/{project_id}/invitations",
    response_model=ProjectInvitationResponse,
    status_code=201,
)
async def create_invitation(
    project_id: UUID,
    invite: ProjectInvitationCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Create an invitation for any email address (user need not be registered)."""
    try:
        await check_project_access(project_id, user_id, "can_manage_members")

        if invite.role not in VALID_PROJECT_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role. Must be one of: {', '.join(VALID_PROJECT_ROLES)}",
            )

        email_lower = invite.email.lower()

        # If registered, confirm not already a member
        user_res = supabase.table("users").select("id").eq("email", email_lower).execute()
        if user_res.data:
            existing = supabase.table("project_members") \
                .select("id") \
                .eq("project_id", str(project_id)) \
                .eq("user_id", user_res.data[0]["id"]) \
                .execute()
            if existing.data:
                raise HTTPException(status_code=409, detail="User is already a member of this project")

        # Revoke any existing pending invitation for this email
        supabase.table("project_invitations") \
            .update({"revoked_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("project_id", str(project_id)) \
            .eq("email", email_lower) \
            .is_("accepted_at", "null") \
            .is_("revoked_at", "null") \
            .execute()

        # Determine permissions
        perms = _permissions_for_role(invite.role)
        if not perms:
            perms = {
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
            }

        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=INVITATION_TTL_DAYS)).isoformat()

        row_data = {
            "project_id": str(project_id),
            "email": email_lower,
            "role": invite.role,
            "invited_by": str(user_id),
            "token": token,
            "expires_at": expires_at,
            **perms,
        }

        result = supabase.table("project_invitations").insert(row_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create invitation")

        row = result.data[0]
        row["_inviter_name"] = None
        return _build_response(row, accept_url=_make_accept_url(token))

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error creating invitation")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@project_router.post(
    "/{project_id}/invitations/{invitation_id}/resend",
    response_model=ProjectInvitationResponse,
)
async def resend_invitation(
    project_id: UUID,
    invitation_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Refresh the expiry and return a fresh accept URL."""
    try:
        await check_project_access(project_id, user_id, "can_manage_members")

        existing = supabase.table("project_invitations") \
            .select("id") \
            .eq("id", str(invitation_id)) \
            .eq("project_id", str(project_id)) \
            .is_("accepted_at", "null") \
            .is_("revoked_at", "null") \
            .execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Invitation not found or already accepted/revoked")

        new_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=INVITATION_TTL_DAYS)).isoformat()

        result = supabase.table("project_invitations") \
            .update({"token": new_token, "expires_at": expires_at}) \
            .eq("id", str(invitation_id)) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to resend invitation")

        row = result.data[0]
        row["_inviter_name"] = None
        return _build_response(row, accept_url=_make_accept_url(new_token))

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error resending invitation")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@project_router.delete("/{project_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    project_id: UUID,
    invitation_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Revoke a pending invitation."""
    try:
        await check_project_access(project_id, user_id, "can_manage_members")

        result = supabase.table("project_invitations") \
            .update({"revoked_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("id", str(invitation_id)) \
            .eq("project_id", str(project_id)) \
            .is_("revoked_at", "null") \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Invitation not found or already revoked")

        return None

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error revoking invitation")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


# ── Standalone invitation endpoints ─────────────────────────────────────────

@router.get("/{token}", response_model=InvitationPreview)
async def preview_invitation(token: str):
    """Public endpoint: returns project/role info for the accept page."""
    try:
        result = supabase.table("project_invitations") \
            .select("*, projects(name), users!project_invitations_invited_by_fkey(full_name, email)") \
            .eq("token", token) \
            .is_("revoked_at", "null") \
            .is_("accepted_at", "null") \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Invitation not found or has already been used")

        row = result.data[0]
        expires_at_str = row["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Invitation has expired")

        project = row.get("projects") or {}
        inviter = row.get("users") or {}

        return InvitationPreview(
            project_id=UUID(row["project_id"]),
            project_name=project.get("name", "Unknown Project"),
            role=row["role"],
            invited_by_name=inviter.get("full_name") or inviter.get("email"),
            expires_at=expires_at,
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error previewing invitation")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/accept")
async def accept_invitation(
    body: AcceptInvitationRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Accept an invitation. Caller must be authenticated and email must match the invite."""
    try:
        result = supabase.table("project_invitations") \
            .select("*") \
            .eq("token", body.token) \
            .is_("revoked_at", "null") \
            .is_("accepted_at", "null") \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Invitation not found, already accepted, or revoked")

        inv = result.data[0]
        expires_at = datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00"))
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Invitation has expired")

        user_res = supabase.table("users").select("id, email").eq("id", str(user_id)).execute()
        if not user_res.data:
            raise HTTPException(status_code=401, detail="User not found")

        user = user_res.data[0]
        if user["email"].lower() != inv["email"].lower():
            raise HTTPException(
                status_code=403,
                detail="This invitation was sent to a different email address",
            )

        # Idempotent: only insert if not already a member
        existing = supabase.table("project_members") \
            .select("id") \
            .eq("project_id", inv["project_id"]) \
            .eq("user_id", str(user_id)) \
            .execute()

        if not existing.data:
            perm_keys = [
                "can_view_docs", "can_upload_docs", "can_create_forms",
                "can_run_extractions", "can_run_manual_extractions",
                "can_view_results", "can_adjudicate",
                "can_qa_review", "can_manage_assignments", "can_manage_members",
            ]
            supabase.table("project_members").insert({
                "project_id": inv["project_id"],
                "user_id": str(user_id),
                "role": inv["role"],
                "invited_by": inv.get("invited_by"),
                **{k: inv.get(k, False) for k in perm_keys},
            }).execute()

        supabase.table("project_invitations") \
            .update({"accepted_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("id", inv["id"]) \
            .execute()

        return {"project_id": inv["project_id"], "role": inv["role"]}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error accepting invitation")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
