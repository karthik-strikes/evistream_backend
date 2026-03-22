"""Review assignment endpoints (per-project, per-document)."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from uuid import UUID
from typing import Optional, List

from app.dependencies import get_current_user
from app.services.project_access import check_project_access
from app.services import assignment_service
from app.models.schemas import (
    BulkAssignmentCreate, AutoAssignRequest, AssignmentStatusUpdate,
    ReviewAssignmentResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/bulk", response_model=List[ReviewAssignmentResponse], status_code=status.HTTP_201_CREATED)
async def bulk_create_assignments(
    data: BulkAssignmentCreate,
    user_id: UUID = Depends(get_current_user),
):
    """Bulk-create review assignments. Requires can_manage_assignments."""
    await check_project_access(data.project_id, user_id, "can_manage_assignments")
    try:
        assignments_input = [
            {
                "document_id": a.document_id,
                "reviewer_user_id": a.reviewer_user_id,
                "reviewer_role": a.reviewer_role.value,
            }
            for a in data.assignments
        ]
        result = await assignment_service.create_bulk_assignments(
            project_id=data.project_id,
            assignments=assignments_input,
            assigned_by=user_id,
        )
        return [ReviewAssignmentResponse(**r) for r in result]
    except Exception:
        logger.exception("Failed to create bulk assignments")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create assignments")


@router.post("/auto-assign", response_model=List[ReviewAssignmentResponse])
async def auto_assign(
    data: AutoAssignRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Auto-distribute documents across R1, R2, and Adjudicator."""
    await check_project_access(data.project_id, user_id, "can_manage_assignments")
    try:
        result = await assignment_service.auto_assign(
            project_id=data.project_id,
            reviewer_1_id=data.reviewer_1_id,
            reviewer_2_id=data.reviewer_2_id,
            adjudicator_id=data.adjudicator_id,
            document_ids=data.document_ids,
            assigned_by=user_id,
        )
        return [ReviewAssignmentResponse(**r) for r in result]
    except Exception:
        logger.exception("Failed to auto-assign")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to auto-assign")


@router.get("/my", response_model=List[ReviewAssignmentResponse])
async def get_my_assignments(
    project_id: Optional[UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    user_id: UUID = Depends(get_current_user),
):
    """Get my pending review assignments."""
    result = await assignment_service.get_my_assignments(
        user_id=user_id,
        project_id=project_id,
        status_filter=status_filter,
    )
    return [ReviewAssignmentResponse(**r) for r in result]


@router.get("/project/{project_id}", response_model=List[ReviewAssignmentResponse])
async def get_project_assignments(
    project_id: UUID,
    status_filter: Optional[str] = Query(None, alias="status"),
    user_id: UUID = Depends(get_current_user),
):
    """Get all assignments for a project (owner/manager only)."""
    await check_project_access(project_id, user_id, "can_manage_assignments")
    result = await assignment_service.get_project_assignments(
        project_id=project_id,
        status_filter=status_filter,
    )
    return [ReviewAssignmentResponse(**r) for r in result]


@router.patch("/{assignment_id}/status", response_model=ReviewAssignmentResponse)
async def update_assignment_status(
    assignment_id: UUID,
    data: AssignmentStatusUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update assignment status (start/complete/skip)."""
    try:
        result = await assignment_service.update_assignment_status(
            assignment_id=assignment_id,
            new_status=data.status.value,
            user_id=user_id,
        )
        return ReviewAssignmentResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your assignment")


@router.get("/progress")
async def get_assignment_progress(
    project_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get assignment completion progress for a project."""
    await check_project_access(project_id, user_id, "can_view_results")
    return await assignment_service.get_progress(project_id)
