"""
Server-side blinding enforcement.

This is the single authority for what data each user can see based on
their role and assignment status. The frontend respects this, but even
if bypassed, the backend blocks unauthorized access.
"""

import logging
from supabase import Client
from typing import Optional, Dict, Any, List
from uuid import UUID

from app.database import get_supabase_client

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    # Reuse the app-wide singleton (connection pooling + slow-query logging)
    # instead of constructing a fresh client on every call — this function
    # used to call create_client() directly, which built a brand-new client
    # per invocation and compounded badly under _apply_blinding_grouped's
    # per-document loop.
    return get_supabase_client()


async def get_project_review_settings(project_id: UUID) -> Dict[str, Any]:
    """Get review (blinding) settings for a project."""
    supabase = get_supabase()
    result = supabase.table("projects")\
        .select("review_settings")\
        .eq("id", str(project_id))\
        .limit(1)\
        .execute()

    if result.data:
        return result.data[0].get("review_settings") or {}
    return {}


def _pick_assignment(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Adjudicator row wins when a user holds two rows for one document
    (a user can never hold both reviewer_1 and reviewer_2)."""
    if not rows:
        return None
    for row in rows:
        if row["reviewer_role"] == "adjudicator":
            return row
    return rows[0]


async def get_user_assignment(
    user_id: UUID,
    document_id: UUID,
) -> Optional[Dict[str, Any]]:
    """Get user's review assignment for a document (project-scoped; not per-form).

    A user may legitimately hold both an adjudicator row and a reviewer row
    for the same document — the adjudicator row takes precedence when both
    exist (a user can never hold both reviewer_1 and reviewer_2).
    """
    supabase = get_supabase()
    result = supabase.table("review_assignments")\
        .select("*")\
        .eq("reviewer_user_id", str(user_id))\
        .eq("document_id", str(document_id))\
        .execute()

    return _pick_assignment(result.data or [])


async def get_user_assignments_for_documents(
    user_id: UUID,
    document_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Batched version of `get_user_assignment` for many documents at once.

    Returns a document_id -> assignment map (a document with no assignment
    for this user is simply absent from the result). Used by
    `_apply_blinding_grouped` so a result page spanning N documents costs one
    query instead of N.
    """
    if not document_ids:
        return {}

    supabase = get_supabase()
    rows_by_doc: Dict[str, List[Dict[str, Any]]] = {}

    # Supabase .in_() has URL-length limits — chunk like assignment_service.py does.
    chunk_size = 50
    unique_ids = list({str(d) for d in document_ids})
    for i in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[i:i + chunk_size]
        result = supabase.table("review_assignments")\
            .select("*")\
            .eq("reviewer_user_id", str(user_id))\
            .in_("document_id", chunk)\
            .execute()
        for row in (result.data or []):
            rows_by_doc.setdefault(row["document_id"], []).append(row)

    assignments: Dict[str, Dict[str, Any]] = {}
    for doc_id, rows in rows_by_doc.items():
        assignment = _pick_assignment(rows)
        if assignment is not None:
            assignments[doc_id] = assignment
    return assignments


def _decide_visible_rows(
    results: List[Dict[str, Any]],
    user_id: UUID,
    viewer_role: Optional[str],
    review_settings: Dict[str, Any],
    assignment: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Pure blinding decision — no I/O. Callers resolve `review_settings` (project
    config) and `assignment` (the caller's review_assignments row for this
    document, or None if they hold none) once and pass them in, so this can
    run per-document without hitting the database each time.

    Blinding rules:
    - full/partial: an assigned reviewer sees their own results + AI (if not
      hidden), but not the other reviewer's manual entry. An assigned
      adjudicator is never blinded. An unassigned owner/manager/admin sees
      everything; an unassigned member/viewer sees AI only.
    - none: no blinding, everyone sees everything (today's default).

    `viewer_role` is the caller's project role (from `check_project_access`).
    A missing `viewer_role` is treated as an ordinary member — fail closed,
    never assume owner-level visibility.
    """
    blinding = review_settings.get("blinding", "none")
    hide_ai = review_settings.get("hide_ai_results", False)

    if blinding == "none":
        if hide_ai:
            return [r for r in results if r.get("extraction_type") != "ai"]
        return results

    if not assignment:
        if viewer_role in ("owner", "manager", "admin"):
            # Unassigned owner/manager/admin retains full oversight visibility.
            if hide_ai:
                return [r for r in results if r.get("extraction_type") != "ai"]
            return results
        # Unassigned ordinary member/viewer: AI only, no raw reviewer data.
        filtered = [r for r in results if r.get("extraction_type") != "manual"]
        if hide_ai:
            filtered = [r for r in filtered if r.get("extraction_type") != "ai"]
        return filtered

    reviewer_role = assignment["reviewer_role"]

    if reviewer_role == "adjudicator":
        # Adjudicators are never blinded from either side.
        if hide_ai:
            return [r for r in results if r.get("extraction_type") != "ai"]
        return results

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


async def filter_results_for_user(
    results: List[Dict[str, Any]],
    user_id: UUID,
    document_id: UUID,
    project_id: UUID,
    viewer_role: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Filter extraction results for a single document based on blinding rules.

    Thin I/O wrapper around `_decide_visible_rows`: fetches this document's
    settings/assignment, then delegates. Callers handling many documents at
    once (e.g. `_apply_blinding_grouped` in results.py) resolve
    settings/assignments themselves in bulk and call `_decide_visible_rows`
    directly instead of calling this per document.
    """
    review_settings = await get_project_review_settings(project_id)

    # Short-circuit before touching review_assignments — matches the original
    # behavior where the "none" (default) case never queries assignments.
    if review_settings.get("blinding", "none") == "none":
        return _decide_visible_rows(results, user_id, viewer_role, review_settings, None)

    assignment = await get_user_assignment(user_id, document_id)
    return _decide_visible_rows(results, user_id, viewer_role, review_settings, assignment)


async def can_view_adjudication(
    user_id: UUID,
    document_id: UUID,
    project_id: UUID,
    viewer_role: Optional[str] = None,
) -> bool:
    """Check if user can view adjudication data (both R1 and R2 raw) for a document."""
    supabase = get_supabase()

    # Check if user is the assigned adjudicator
    assignment = supabase.table("review_assignments")\
        .select("id")\
        .eq("reviewer_user_id", str(user_id))\
        .eq("document_id", str(document_id))\
        .eq("reviewer_role", "adjudicator")\
        .limit(1)\
        .execute()

    if assignment.data:
        return True

    # Any owner/manager/admin gets adjudicator-equivalent oversight visibility,
    # regardless of whether they hold the legacy `projects.user_id` creator field.
    return viewer_role in ("owner", "manager", "admin")
