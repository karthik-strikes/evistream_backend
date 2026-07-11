"""Audit trail endpoints."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from uuid import UUID
from typing import List

from app.dependencies import get_current_user
from app.services import audit_service
from app.services.project_access import check_project_access
from app.models.schemas import AuditTrailEntryResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=List[AuditTrailEntryResponse])
async def get_audit_trail(
    project_id: UUID = Query(...),
    entity_type: str | None = Query(None),
    user_filter: UUID | None = Query(None, alias="user_id"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    user_id: UUID = Depends(get_current_user),
):
    """Query audit trail with filters. Requires project access."""
    await check_project_access(project_id, user_id, "can_view_results")
    result = await audit_service.get_audit_trail(
        project_id=project_id,
        entity_type=entity_type,
        user_id=user_filter,
        limit=limit,
        offset=offset,
    )
    return [AuditTrailEntryResponse(**r) for r in result]


@router.get("/entity/{entity_type}/{entity_id}", response_model=List[AuditTrailEntryResponse])
async def get_entity_history(
    entity_type: str,
    entity_id: UUID,
    project_id: UUID = Query(...),
    user_id: UUID = Depends(get_current_user),
):
    """Get full audit history for an entity. Requires project access."""
    await check_project_access(project_id, user_id, "can_view_results")
    result = await audit_service.get_entity_history(entity_type, entity_id)
    return [AuditTrailEntryResponse(**r) for r in result]
