"""Adjudication endpoints for resolving reviewer disagreements."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from uuid import UUID

from app.dependencies import get_current_user
from app.services.project_access import check_project_access
from app.services import adjudication_service
from app.services.blinding_service import can_view_adjudication
from app.models.schemas import AdjudicationResolveRequest, AdjudicationResultResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/compare")
async def compare_reviewers(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    document_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Compare R1 vs R2 extraction results field-by-field."""
    await check_project_access(project_id, user_id, "can_adjudicate")
    if not await can_view_adjudication(user_id, document_id, form_id, project_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view adjudication")
    return await adjudication_service.compare_reviewers(project_id, form_id, document_id)


@router.post("/resolve", response_model=AdjudicationResultResponse, status_code=status.HTTP_201_CREATED)
async def resolve_adjudication(
    data: AdjudicationResolveRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Save adjudication decisions."""
    await check_project_access(data.project_id, user_id, "can_adjudicate")
    try:
        result = await adjudication_service.save_adjudication(
            project_id=data.project_id,
            form_id=data.form_id,
            document_id=data.document_id,
            adjudicator_id=user_id,
            field_resolutions=data.field_resolutions,
            reviewer_1_result_id=data.reviewer_1_result_id,
            reviewer_2_result_id=data.reviewer_2_result_id,
            status=data.status,
        )
        return AdjudicationResultResponse(**result)
    except Exception:
        logger.exception("Failed to save adjudication")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save adjudication")


@router.get("/summary")
async def get_adjudication_summary(
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get adjudication progress dashboard data."""
    await check_project_access(project_id, user_id, "can_view_results")
    return await adjudication_service.get_adjudication_summary(project_id, form_id)


@router.get("/{document_id}", response_model=AdjudicationResultResponse)
async def get_adjudication(
    document_id: UUID,
    project_id: UUID = Query(...),
    form_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get existing adjudication for a document."""
    await check_project_access(project_id, user_id, "can_adjudicate")
    result = await adjudication_service.get_adjudication(project_id, form_id, document_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No adjudication found")
    return AdjudicationResultResponse(**result)
