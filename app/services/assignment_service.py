"""Service for managing review assignments (per-project, per-document)."""

import logging
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# ---------------------------------------------------------------------------
# Helpers – form completion enrichment
# ---------------------------------------------------------------------------

def _get_active_form_ids(supabase: Client, project_id: str) -> List[str]:
    """Return IDs of active forms for a project."""
    forms = supabase.table("forms")\
        .select("id, form_name")\
        .eq("project_id", project_id)\
        .eq("status", "active")\
        .execute()
    return forms.data or []


def _enrich_with_form_status(
    supabase: Client,
    assignments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add forms_completed, forms_total, and form_details to each assignment."""
    if not assignments:
        return assignments

    # Collect unique project IDs
    project_ids = list(set(a["project_id"] for a in assignments))

    # Fetch active forms per project
    active_forms_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for pid in project_ids:
        active_forms_by_project[pid] = _get_active_form_ids(supabase, pid)

    # Collect all (document_id, reviewer_role) pairs to batch-query results
    doc_ids = list(set(a["document_id"] for a in assignments))

    # Fetch all manual extraction results for these documents
    results_data = []
    # Supabase .in_() has limits, so chunk if needed
    chunk_size = 50
    for i in range(0, len(doc_ids), chunk_size):
        chunk = doc_ids[i:i + chunk_size]
        res = supabase.table("extraction_results")\
            .select("document_id, form_id, reviewer_role, extracted_data")\
            .in_("document_id", chunk)\
            .eq("extraction_type", "manual")\
            .execute()
        results_data.extend(res.data or [])

    # Build lookup: (document_id, reviewer_role) -> set of completed form_ids.
    # Partial saves carry _partial=true in extracted_data — exclude them.
    completed_lookup: Dict[tuple, set] = {}
    for r in results_data:
        if (r.get("extracted_data") or {}).get("_partial") is True:
            continue
        key = (r["document_id"], r.get("reviewer_role"))
        if key not in completed_lookup:
            completed_lookup[key] = set()
        completed_lookup[key].add(r["form_id"])

    # Enrich each assignment
    for a in assignments:
        forms = active_forms_by_project.get(a["project_id"], [])
        form_ids = {f["id"] for f in forms}
        completed = completed_lookup.get((a["document_id"], a["reviewer_role"]), set())
        completed_for_active = completed & form_ids

        a["forms_total"] = len(form_ids)
        a["forms_completed"] = len(completed_for_active)

        # Per-form detail for My Queue UI
        form_details = []
        for f in forms:
            form_details.append({
                "form_id": f["id"],
                "form_name": f.get("form_name", "Untitled"),
                "completed": f["id"] in completed_for_active,
            })
        a["form_details"] = form_details

    return assignments


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def _find_r1_r2_conflicts(assignments: List[Dict[str, Any]]) -> int:
    """Return the number of documents where the same user is both R1 and R2 within a single payload."""
    r1_map: Dict[str, str] = {}
    for a in assignments:
        if a["reviewer_role"] == "reviewer_1":
            r1_map[str(a["document_id"])] = str(a["reviewer_user_id"])
    return sum(
        1 for a in assignments
        if a["reviewer_role"] == "reviewer_2"
        and r1_map.get(str(a["document_id"])) == str(a["reviewer_user_id"])
    )


def _find_post_upsert_blind_conflicts(
    supabase: Client,
    project_id: UUID,
    assignments: List[Dict[str, Any]],
) -> int:
    """
    Find R1==R2 conflicts on the *post-upsert* state — i.e. merging the incoming
    payload over what's already in the DB for the same (project, doc, role) keys.
    Catches the case where Run #1 sets Wenrui as R2 doc-X and Run #2 sets Wenrui
    as R1 doc-X; the in-payload check alone misses this.
    """
    doc_ids = list({str(a["document_id"]) for a in assignments
                    if a.get("reviewer_role") in ("reviewer_1", "reviewer_2")})
    if not doc_ids:
        return 0

    existing = supabase.table("review_assignments")\
        .select("document_id, reviewer_role, reviewer_user_id")\
        .eq("project_id", str(project_id))\
        .in_("document_id", doc_ids)\
        .in_("reviewer_role", ["reviewer_1", "reviewer_2"])\
        .execute()

    # post[doc_id][role] = user_id, with incoming overriding existing per upsert key.
    post: Dict[str, Dict[str, str]] = {}
    for row in (existing.data or []):
        post.setdefault(row["document_id"], {})[row["reviewer_role"]] = row["reviewer_user_id"]
    for a in assignments:
        if a["reviewer_role"] in ("reviewer_1", "reviewer_2"):
            post.setdefault(str(a["document_id"]), {})[a["reviewer_role"]] = str(a["reviewer_user_id"])

    return sum(
        1 for roles in post.values()
        if roles.get("reviewer_1") and roles.get("reviewer_2")
        and roles["reviewer_1"] == roles["reviewer_2"]
    )


# reviewer_role -> project_members flag the assignee must hold to do the work.
_REVIEWER_ROLE_REQUIRED_FLAG = {
    "reviewer_1": "can_run_manual_extractions",
    "reviewer_2": "can_run_manual_extractions",
    "adjudicator": "can_adjudicate",
    "qa_reviewer": "can_qa_review",
}


def _validate_assignee_capabilities(
    supabase: Client,
    project_id: UUID,
    pairs: List[tuple],
) -> None:
    """
    Verify each (reviewer_user_id, reviewer_role) assignee has the required
    project_members flag. Project owner (projects.user_id) and role='owner'
    members bypass. Raises ValueError listing skipped users on failure.

    pairs: list of (reviewer_user_id: str, reviewer_role: str).
    """
    if not pairs:
        return

    user_ids = list({uid for uid, _ in pairs})

    # Project legacy owner — always bypasses.
    proj = supabase.table("projects")\
        .select("user_id")\
        .eq("id", str(project_id))\
        .limit(1)\
        .execute()
    legacy_owner_id = proj.data[0]["user_id"] if proj.data else None

    members = supabase.table("project_members")\
        .select("user_id, role, can_run_extractions, can_run_manual_extractions, can_adjudicate, can_qa_review")\
        .eq("project_id", str(project_id))\
        .in_("user_id", user_ids)\
        .execute()
    member_by_uid = {m["user_id"]: m for m in (members.data or [])}

    # Resolve user names for a clearer error message.
    users = supabase.table("users")\
        .select("id, full_name, email")\
        .in_("id", user_ids)\
        .execute()
    name_by_uid = {
        u["id"]: (u.get("full_name") or u.get("email") or u["id"])
        for u in (users.data or [])
    }

    skipped: List[str] = []
    for uid, role in pairs:
        if uid == legacy_owner_id:
            continue
        member = member_by_uid.get(uid)
        if member is None:
            skipped.append(f"{name_by_uid.get(uid, uid)} (not a project member)")
            continue
        if member.get("role") == "owner":
            continue
        required_flag = _REVIEWER_ROLE_REQUIRED_FLAG.get(role)
        if required_flag and not member.get(required_flag):
            skipped.append(
                f"{name_by_uid.get(uid, uid)} lacks {required_flag} for role {role}"
            )

    if skipped:
        # Dedupe but keep order.
        seen = set()
        unique = [s for s in skipped if not (s in seen or seen.add(s))]
        preview = "; ".join(unique[:5])
        more = f" (+{len(unique) - 5} more)" if len(unique) > 5 else ""
        raise ValueError(
            f"Cannot assign — {len(unique)} user(s) lack required permissions: {preview}{more}"
        )


async def create_bulk_assignments(
    project_id: UUID,
    assignments: List[Dict[str, Any]],
    assigned_by: UUID,
) -> List[Dict[str, Any]]:
    """Create multiple assignments at once (per-document, no form_id)."""
    supabase = get_supabase()

    # In-payload check (fast, no DB roundtrip).
    conflicts = _find_r1_r2_conflicts(assignments)
    if conflicts:
        raise ValueError(
            f"Same reviewer is assigned as both R1 and R2 for {conflicts} document(s). "
            "This breaks blind review — R1 and R2 must be different people for every paper."
        )

    # Cross-payload check — catches R1=R2 collisions against rows already in the DB.
    post_conflicts = _find_post_upsert_blind_conflicts(supabase, project_id, assignments)
    if post_conflicts:
        raise ValueError(
            f"Blind-review conflict on {post_conflicts} document(s): the incoming assignments "
            "would put the same person as both R1 and R2 for those papers (against existing rows). "
            "Reassign one of the roles to a different reviewer."
        )

    _validate_assignee_capabilities(
        supabase, project_id,
        [(str(a["reviewer_user_id"]), a["reviewer_role"]) for a in assignments],
    )
    rows = []
    for a in assignments:
        rows.append({
            "project_id": str(project_id),
            "document_id": str(a["document_id"]),
            "reviewer_user_id": str(a["reviewer_user_id"]),
            "reviewer_role": a["reviewer_role"],
            "status": "pending",
            "assigned_by": str(assigned_by),
        })

    result = supabase.table("review_assignments").upsert(
        rows,
        on_conflict="project_id,document_id,reviewer_role"
    ).execute()

    created = result.data or []

    for row in created:
        await log_audit(
            user_id=assigned_by,
            entity_type="review_assignment",
            entity_id=UUID(row["id"]),
            action="created",
            project_id=project_id,
        )

    return _enrich_with_form_status(supabase, created)


async def auto_assign(
    project_id: UUID,
    reviewer_1_id: UUID,
    reviewer_2_id: UUID,
    adjudicator_id: UUID,
    document_ids: Optional[List[UUID]],
    assigned_by: UUID,
) -> List[Dict[str, Any]]:
    """Auto-assign documents to reviewers (per-document, no form_id)."""
    if str(reviewer_1_id) == str(reviewer_2_id):
        raise ValueError(
            "Reviewer 1 and Reviewer 2 cannot be the same person. "
            "Blind review requires two independent reviewers per paper."
        )

    supabase = get_supabase()

    _validate_assignee_capabilities(
        supabase, project_id,
        [
            (str(reviewer_1_id), "reviewer_1"),
            (str(reviewer_2_id), "reviewer_2"),
            (str(adjudicator_id), "adjudicator"),
        ],
    )

    if document_ids:
        doc_ids = [str(d) for d in document_ids]
    else:
        docs = supabase.table("documents")\
            .select("id")\
            .eq("project_id", str(project_id))\
            .eq("processing_status", "completed")\
            .execute()
        doc_ids = [d["id"] for d in (docs.data or [])]

    if not doc_ids:
        return []

    rows = []
    for doc_id in doc_ids:
        rows.append({
            "project_id": str(project_id),
            "document_id": doc_id,
            "reviewer_user_id": str(reviewer_1_id),
            "reviewer_role": "reviewer_1",
            "status": "pending",
            "assigned_by": str(assigned_by),
        })
        rows.append({
            "project_id": str(project_id),
            "document_id": doc_id,
            "reviewer_user_id": str(reviewer_2_id),
            "reviewer_role": "reviewer_2",
            "status": "pending",
            "assigned_by": str(assigned_by),
        })
        rows.append({
            "project_id": str(project_id),
            "document_id": doc_id,
            "reviewer_user_id": str(adjudicator_id),
            "reviewer_role": "adjudicator",
            "status": "pending",
            "assigned_by": str(assigned_by),
        })

    result = supabase.table("review_assignments").upsert(
        rows,
        on_conflict="project_id,document_id,reviewer_role"
    ).execute()

    return _enrich_with_form_status(supabase, result.data or [])


async def delete_project_assignments(
    project_id: UUID,
    reviewer_user_id: Optional[UUID] = None,
) -> int:
    """Delete assignments for a project. If reviewer_user_id given, only that reviewer's rows."""
    supabase = get_supabase()
    query = supabase.table("review_assignments").delete().eq("project_id", str(project_id))
    if reviewer_user_id:
        query = query.eq("reviewer_user_id", str(reviewer_user_id))
    result = query.execute()
    return len(result.data or [])


async def get_my_assignments(
    user_id: UUID,
    project_id: Optional[UUID] = None,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get assignments for a specific user, enriched with form completion status."""
    supabase = get_supabase()
    query = supabase.table("review_assignments")\
        .select("*")\
        .eq("reviewer_user_id", str(user_id))

    if project_id:
        query = query.eq("project_id", str(project_id))
    if status_filter:
        query = query.eq("status", status_filter)

    result = query.order("assigned_at", desc=True).execute()
    assignments = result.data or []

    # Enrich with document filenames
    if assignments:
        doc_ids = list(set(a["document_id"] for a in assignments))
        docs = supabase.table("documents")\
            .select("id, filename")\
            .in_("id", doc_ids)\
            .execute()
        doc_map = {d["id"]: d["filename"] for d in (docs.data or [])}
        for a in assignments:
            a["document_filename"] = doc_map.get(a["document_id"])

    return _enrich_with_form_status(supabase, assignments)


async def get_project_assignments(
    project_id: UUID,
    status_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get all assignments for a project."""
    supabase = get_supabase()
    query = supabase.table("review_assignments")\
        .select("*")\
        .eq("project_id", str(project_id))

    if status_filter:
        query = query.eq("status", status_filter)

    result = query.order("assigned_at", desc=True).execute()
    assignments = result.data or []

    # Enrich with document filenames and reviewer names
    if assignments:
        doc_ids = list(set(a["document_id"] for a in assignments))
        user_ids = list(set(a["reviewer_user_id"] for a in assignments))

        docs = supabase.table("documents")\
            .select("id, filename")\
            .in_("id", doc_ids)\
            .execute()
        doc_map = {d["id"]: d["filename"] for d in (docs.data or [])}

        users = supabase.table("users")\
            .select("id, full_name, email")\
            .in_("id", user_ids)\
            .execute()
        user_map = {u["id"]: u.get("full_name") or u["email"] for u in (users.data or [])}

        for a in assignments:
            a["document_filename"] = doc_map.get(a["document_id"])
            a["reviewer_name"] = user_map.get(a["reviewer_user_id"])

    return _enrich_with_form_status(supabase, assignments)


async def update_assignment_status(
    assignment_id: UUID,
    new_status: str,
    user_id: UUID,
) -> Dict[str, Any]:
    """Update assignment status with validation."""
    supabase = get_supabase()

    # Fetch current assignment
    current = supabase.table("review_assignments")\
        .select("*")\
        .eq("id", str(assignment_id))\
        .limit(1)\
        .execute()

    if not current.data:
        raise ValueError("Assignment not found")

    assignment = current.data[0]

    # Validate user owns this assignment
    if assignment["reviewer_user_id"] != str(user_id):
        raise PermissionError("Not your assignment")

    # Validate status transition
    valid_transitions = {
        "pending": ["in_progress", "skipped"],
        "in_progress": ["completed", "skipped"],
    }
    allowed = valid_transitions.get(assignment["status"], [])
    if new_status not in allowed:
        raise ValueError(f"Cannot transition from {assignment['status']} to {new_status}")

    update_data = {"status": new_status}
    if new_status == "in_progress":
        update_data["started_at"] = datetime.now(timezone.utc).isoformat()
    elif new_status in ("completed", "skipped"):
        update_data["completed_at"] = datetime.now(timezone.utc).isoformat()

    result = supabase.table("review_assignments")\
        .update(update_data)\
        .eq("id", str(assignment_id))\
        .execute()

    await log_audit(
        user_id=user_id,
        entity_type="review_assignment",
        entity_id=assignment_id,
        action=f"status_changed_to_{new_status}",
        project_id=UUID(assignment["project_id"]),
    )

    updated = result.data[0] if result.data else assignment
    enriched = _enrich_with_form_status(supabase, [updated])
    return enriched[0]


async def get_progress(
    project_id: UUID,
) -> Dict[str, Any]:
    """Get completion progress for a project."""
    supabase = get_supabase()

    assignments = supabase.table("review_assignments")\
        .select("reviewer_role, status")\
        .eq("project_id", str(project_id))\
        .execute()

    all_assignments = assignments.data or []

    total = len(all_assignments)
    by_role = {}
    for a in all_assignments:
        role = a["reviewer_role"]
        if role not in by_role:
            by_role[role] = {"total": 0, "pending": 0, "in_progress": 0, "completed": 0, "skipped": 0}
        by_role[role]["total"] += 1
        by_role[role][a["status"]] += 1

    completed = sum(1 for a in all_assignments if a["status"] == "completed")

    return {
        "total_assignments": total,
        "completed": completed,
        "completion_pct": round(completed / total * 100) if total > 0 else 0,
        "by_role": by_role,
    }


# ---------------------------------------------------------------------------
# Auto-completion: call after each manual extraction save
# ---------------------------------------------------------------------------

async def check_and_auto_complete_assignment(
    project_id: str,
    document_id: str,
    reviewer_role: str,
) -> Optional[Dict[str, Any]]:
    """
    Check if all active forms have manual extraction results for this
    document + reviewer_role. If so, auto-complete the assignment.
    Also auto-transition pending -> in_progress on first extraction.
    """
    supabase = get_supabase()

    # Find the assignment
    assignment = supabase.table("review_assignments")\
        .select("id, status")\
        .eq("project_id", project_id)\
        .eq("document_id", document_id)\
        .eq("reviewer_role", reviewer_role)\
        .limit(1)\
        .execute()

    if not assignment.data:
        return None

    current = assignment.data[0]

    # Never reopen a skipped assignment
    if current["status"] == "skipped":
        return None

    status_was = current["status"]

    # Auto-transition pending -> in_progress on first extraction
    if status_was == "pending":
        supabase.table("review_assignments")\
            .update({
                "status": "in_progress",
                "started_at": datetime.now(timezone.utc).isoformat(),
            })\
            .eq("id", current["id"])\
            .execute()

    # Get active forms for this project
    active_forms = _get_active_form_ids(supabase, project_id)
    active_form_ids = {f["id"] for f in active_forms}

    if not active_form_ids:
        return None

    # Count completed extraction results for this doc+role across active forms.
    # Partial saves (_partial=true) don't count toward completion.
    results = supabase.table("extraction_results")\
        .select("form_id, extracted_data")\
        .eq("document_id", document_id)\
        .eq("reviewer_role", reviewer_role)\
        .eq("extraction_type", "manual")\
        .in_("form_id", list(active_form_ids))\
        .execute()

    completed_form_ids = {
        r["form_id"] for r in (results.data or [])
        if not (r.get("extracted_data") or {}).get("_partial")
    }

    if completed_form_ids >= active_form_ids:
        # All active forms done — promote to completed (if not already)
        if status_was != "completed":
            result = supabase.table("review_assignments")\
                .update({
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })\
                .eq("id", current["id"])\
                .execute()
            return result.data[0] if result.data else None
        return None
    else:
        # Not all forms done — demote back to in_progress if a new form was added
        # after this assignment was previously completed.
        if status_was == "completed":
            supabase.table("review_assignments")\
                .update({"status": "in_progress", "completed_at": None})\
                .eq("id", current["id"])\
                .execute()
        return None
