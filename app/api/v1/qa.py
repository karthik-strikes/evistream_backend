"""QA review endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from uuid import UUID
from typing import Optional

from app.dependencies import get_current_user
from app.services.project_access import check_project_access
from app.services import qa_service
from app.models.schemas import QASampleRequest, QAReviewSaveRequest, QAFlagResolveRequest, QAReviewResponse
from supabase import create_client
from app.config import settings

_supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sample")
async def generate_qa_sample(
    data: QASampleRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Generate random QA sample from completed adjudications."""
    await check_project_access(data.project_id, user_id, "can_qa_review")
    result = await qa_service.generate_qa_sample(
        project_id=data.project_id,
        form_id=data.form_id,
        qa_reviewer_id=user_id,
        sample_percentage=data.sample_percentage,
    )
    return {"sampled": len(result), "reviews": result}


@router.get("/queue")
async def get_qa_queue(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get QA review queue."""
    # mutating=False: read-only, so it must keep working on archived projects.
    await check_project_access(project_id, user_id, "can_qa_review", mutating=False)
    return await qa_service.get_qa_queue(project_id, form_id, qa_reviewer_id=user_id)


@router.post("/save", response_model=QAReviewResponse, status_code=status.HTTP_201_CREATED)
async def save_qa_review(
    data: QAReviewSaveRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Save QA review (pass/flag)."""
    await check_project_access(data.project_id, user_id, "can_qa_review")
    result = await qa_service.save_qa_review(
        qa_review_id=None,
        project_id=data.project_id,
        form_id=data.form_id,
        document_id=data.document_id,
        qa_reviewer_id=user_id,
        status=data.status,
        field_comments=data.field_comments,
        overall_comment=data.overall_comment,
        source_result_id=data.source_result_id,
        source_adjudication_id=data.source_adjudication_id,
    )
    return QAReviewResponse(**result)


@router.get("/dashboard")
async def get_qa_dashboard(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get QA metrics dashboard."""
    await check_project_access(project_id, user_id, "can_view_results")
    return await qa_service.get_qa_dashboard(project_id, form_id)


@router.get("/{document_id}", response_model=QAReviewResponse)
async def get_qa_review(
    document_id: UUID,
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get QA review for a document."""
    # mutating=False: read-only, so it must keep working on archived projects.
    await check_project_access(project_id, user_id, "can_qa_review", mutating=False)
    result = await qa_service.get_qa_review(project_id, form_id, document_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No QA review found")
    return QAReviewResponse(**result)


@router.patch("/{qa_id}/resolve-flag")
async def resolve_flag(
    qa_id: UUID,
    data: QAFlagResolveRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Resolve a flagged field in a QA review."""
    # Look up the QA review's project to check access
    qa_result = _supabase.table("qa_reviews").select("project_id").eq("id", str(qa_id)).limit(1).execute()
    if not qa_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QA review not found")
    await check_project_access(UUID(qa_result.data[0]["project_id"]), user_id, "can_qa_review")
    try:
        # Use authenticated user_id instead of client-supplied resolved_by
        result = await qa_service.resolve_flag(qa_id, data.field_name, user_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
