"""
Server-side blinding enforcement.

This is the single authority for what data each user can see based on
their role and assignment status. The frontend respects this, but even
if bypassed, the backend blocks unauthorized access.
"""

import logging
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID

from app.config import settings

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def get_form_review_settings(form_id: UUID) -> Dict[str, Any]:
    """Get review settings for a form."""
    supabase = get_supabase()
    result = supabase.table("forms")\
        .select("review_settings")\
        .eq("id", str(form_id))\
        .limit(1)\
        .execute()

    if result.data:
        return result.data[0].get("review_settings") or {}
    return {}


async def get_user_assignment(
    user_id: UUID,
    document_id: UUID,
    form_id: UUID,
) -> Optional[Dict[str, Any]]:
    """Get user's assignment for a specific document+form."""
    supabase = get_supabase()
    result = supabase.table("review_assignments")\
        .select("*")\
        .eq("reviewer_user_id", str(user_id))\
        .eq("document_id", str(document_id))\
        .eq("form_id", str(form_id))\
        .limit(1)\
        .execute()

    return result.data[0] if result.data else None


async def filter_results_for_user(
    results: List[Dict[str, Any]],
    user_id: UUID,
    document_id: UUID,
    form_id: UUID,
) -> List[Dict[str, Any]]:
    """
    Filter extraction results based on blinding rules.

    Blinding rules:
    - full: Reviewer can only see their own results + AI results (if not hidden)
    - partial: Reviewer can see their own + AI, but not the other reviewer's
    - none: No blinding, everyone sees everything
    """
    review_settings = await get_form_review_settings(form_id)
    blinding = review_settings.get("blinding", "none")
    hide_ai = review_settings.get("hide_ai_results", False)

    if blinding == "none":
        if hide_ai:
            return [r for r in results if r.get("extraction_type") != "ai"]
        return results

    # Check if user has an assignment (is a reviewer)
    assignment = await get_user_assignment(user_id, document_id, form_id)

    if not assignment:
        # User has no assignment — might be an owner/adjudicator
        # Check if they're the adjudicator
        adj_assignment = get_supabase().table("review_assignments")\
            .select("reviewer_role")\
            .eq("reviewer_user_id", str(user_id))\
            .eq("document_id", str(document_id))\
            .eq("form_id", str(form_id))\
            .eq("reviewer_role", "adjudicator")\
            .limit(1)\
            .execute()

        if adj_assignment.data:
            # Adjudicator can see all reviewer results
            return results
        # Non-assigned user (owner etc.) sees everything
        return results

    reviewer_role = assignment["reviewer_role"]

    filtered = []
    for r in results:
        ext_type = r.get("extraction_type", "ai")
        r_role = r.get("reviewer_role")

        # Always show user's own results
        if r.get("extracted_by") == str(user_id):
            filtered.append(r)
            continue

        # AI results based on settings
        if ext_type == "ai" and not hide_ai:
            filtered.append(r)
            continue

        # In full/partial blinding, hide other reviewer's manual results
        if blinding in ("full", "partial") and ext_type == "manual":
            if r_role and r_role != reviewer_role:
                continue  # Hide other reviewer's data
            filtered.append(r)
            continue

    return filtered


async def can_view_adjudication(
    user_id: UUID,
    document_id: UUID,
    form_id: UUID,
    project_id: UUID,
) -> bool:
    """Check if user can view adjudication data for a document."""
    supabase = get_supabase()

    # Check if user is the adjudicator
    assignment = supabase.table("review_assignments")\
        .select("id")\
        .eq("reviewer_user_id", str(user_id))\
        .eq("document_id", str(document_id))\
        .eq("form_id", str(form_id))\
        .eq("reviewer_role", "adjudicator")\
        .limit(1)\
        .execute()

    if assignment.data:
        return True

    # Check if user is project owner
    project = supabase.table("projects")\
        .select("user_id")\
        .eq("id", str(project_id))\
        .limit(1)\
        .execute()

    if project.data and project.data[0]["user_id"] == str(user_id):
        return True

    return False
