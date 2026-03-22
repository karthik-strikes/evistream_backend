"""Service for QA review management."""

import logging
import random
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def generate_qa_sample(
    project_id: UUID,
    form_id: UUID,
    qa_reviewer_id: UUID,
    sample_percentage: int = 20,
) -> List[Dict[str, Any]]:
    """Generate a random QA sample from completed adjudications."""
    supabase = get_supabase()

    # Get completed adjudications
    adjudications = supabase.table("adjudication_results")\
        .select("id, document_id")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("status", "completed")\
        .execute()

    adj_list = adjudications.data or []
    if not adj_list:
        return []

    # Get already-sampled docs
    existing = supabase.table("qa_reviews")\
        .select("document_id")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .execute()
    existing_doc_ids = set(r["document_id"] for r in (existing.data or []))

    # Filter out already-sampled
    eligible = [a for a in adj_list if a["document_id"] not in existing_doc_ids]

    # Sample
    sample_size = max(1, int(len(eligible) * sample_percentage / 100))
    sampled = random.sample(eligible, min(sample_size, len(eligible)))

    # Create QA review records
    rows = []
    for a in sampled:
        rows.append({
            "project_id": str(project_id),
            "form_id": str(form_id),
            "document_id": a["document_id"],
            "qa_reviewer_id": str(qa_reviewer_id),
            "source_adjudication_id": a["id"],
            "status": "pending",
        })

    if rows:
        result = supabase.table("qa_reviews").insert(rows).execute()
        return result.data or []

    return []


async def get_qa_queue(
    project_id: UUID,
    form_id: UUID,
    qa_reviewer_id: Optional[UUID] = None,
) -> List[Dict[str, Any]]:
    """Get QA review queue for a project+form."""
    supabase = get_supabase()
    query = supabase.table("qa_reviews")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))

    if qa_reviewer_id:
        query = query.eq("qa_reviewer_id", str(qa_reviewer_id))

    result = query.order("created_at").execute()
    reviews = result.data or []

    # Enrich with document filenames
    if reviews:
        doc_ids = list(set(r["document_id"] for r in reviews))
        docs = supabase.table("documents")\
            .select("id, filename")\
            .in_("id", doc_ids)\
            .execute()
        doc_map = {d["id"]: d["filename"] for d in (docs.data or [])}
        for r in reviews:
            r["document_filename"] = doc_map.get(r["document_id"])

    return reviews


async def save_qa_review(
    qa_review_id: Optional[UUID],
    project_id: UUID,
    form_id: UUID,
    document_id: UUID,
    qa_reviewer_id: UUID,
    status: str,
    field_comments: Dict[str, Any],
    overall_comment: Optional[str] = None,
    source_result_id: Optional[UUID] = None,
    source_adjudication_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Save or update a QA review."""
    supabase = get_supabase()

    flagged_count = sum(1 for c in field_comments.values() if not c.get("resolved", False))
    total_reviewed = len(field_comments)

    # Determine status based on flags
    if status == "in_progress" and flagged_count > 0:
        status = "flagged"
    elif status == "in_progress" and total_reviewed > 0 and flagged_count == 0:
        status = "passed"

    payload = {
        "project_id": str(project_id),
        "form_id": str(form_id),
        "document_id": str(document_id),
        "qa_reviewer_id": str(qa_reviewer_id),
        "status": status,
        "field_comments": field_comments,
        "overall_comment": overall_comment,
        "flagged_field_count": flagged_count,
        "total_fields_reviewed": total_reviewed,
        "source_result_id": str(source_result_id) if source_result_id else None,
        "source_adjudication_id": str(source_adjudication_id) if source_adjudication_id else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = supabase.table("qa_reviews")\
        .select("id")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("document_id", str(document_id))\
        .limit(1)\
        .execute()

    if existing.data:
        result = supabase.table("qa_reviews")\
            .update(payload)\
            .eq("id", existing.data[0]["id"])\
            .execute()
    else:
        result = supabase.table("qa_reviews").insert(payload).execute()

    saved = result.data[0] if result.data else {}

    if saved:
        await log_audit(
            user_id=qa_reviewer_id,
            entity_type="qa_review",
            entity_id=UUID(saved["id"]),
            action=f"qa_review_{status}",
            project_id=project_id,
        )

    return saved


async def get_qa_review(
    project_id: UUID,
    form_id: UUID,
    document_id: UUID,
) -> Optional[Dict[str, Any]]:
    """Get QA review for a specific document."""
    supabase = get_supabase()
    result = supabase.table("qa_reviews")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .eq("document_id", str(document_id))\
        .limit(1)\
        .execute()
    return result.data[0] if result.data else None


async def resolve_flag(
    qa_review_id: UUID,
    field_name: str,
    resolved_by: UUID,
) -> Dict[str, Any]:
    """Resolve a flagged field in a QA review."""
    supabase = get_supabase()

    review = supabase.table("qa_reviews")\
        .select("*")\
        .eq("id", str(qa_review_id))\
        .limit(1)\
        .execute()

    if not review.data:
        raise ValueError("QA review not found")

    qa = review.data[0]
    comments = qa.get("field_comments", {})

    if field_name not in comments:
        raise ValueError(f"Field '{field_name}' not found in QA comments")

    comments[field_name]["resolved"] = True
    comments[field_name]["resolved_by"] = str(resolved_by)
    comments[field_name]["resolved_at"] = datetime.now(timezone.utc).isoformat()

    flagged_count = sum(1 for c in comments.values() if not c.get("resolved", False))
    new_status = "passed" if flagged_count == 0 else "flagged"

    result = supabase.table("qa_reviews")\
        .update({
            "field_comments": comments,
            "flagged_field_count": flagged_count,
            "status": new_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })\
        .eq("id", str(qa_review_id))\
        .execute()

    await log_audit(
        user_id=resolved_by,
        entity_type="qa_review",
        entity_id=qa_review_id,
        action="flag_resolved",
        project_id=UUID(qa["project_id"]),
        field_name=field_name,
    )

    return result.data[0] if result.data else qa


async def get_qa_dashboard(
    project_id: UUID,
    form_id: UUID,
) -> Dict[str, Any]:
    """Get QA metrics dashboard data."""
    supabase = get_supabase()

    reviews = supabase.table("qa_reviews")\
        .select("*")\
        .eq("project_id", str(project_id))\
        .eq("form_id", str(form_id))\
        .execute()

    all_reviews = reviews.data or []

    total = len(all_reviews)
    passed = sum(1 for r in all_reviews if r["status"] == "passed")
    flagged = sum(1 for r in all_reviews if r["status"] == "flagged")
    pending = sum(1 for r in all_reviews if r["status"] == "pending")

    # Aggregate error rates by field
    field_errors: Dict[str, int] = {}
    for r in all_reviews:
        for field_name, comment in (r.get("field_comments") or {}).items():
            if not comment.get("resolved", False):
                field_errors[field_name] = field_errors.get(field_name, 0) + 1

    return {
        "total_reviews": total,
        "passed": passed,
        "flagged": flagged,
        "pending": pending,
        "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
        "field_error_rates": dict(sorted(field_errors.items(), key=lambda x: -x[1])),
    }
