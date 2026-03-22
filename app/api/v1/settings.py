"""User settings endpoints — export and notification preferences."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from uuid import UUID

from app.dependencies import get_current_user
from app.models.schemas import UserSettingsResponse, UserSettingsUpdate
from app.services.settings_service import get_user_settings, update_user_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=UserSettingsResponse)
async def get_settings(user_id: UUID = Depends(get_current_user)):
    """Get current user's settings (creates defaults if first call)."""
    try:
        return await get_user_settings(user_id)
    except Exception:
        logger.exception("Failed to get user settings")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load settings",
        )


@router.patch("", response_model=UserSettingsResponse)
async def patch_settings(
    body: UserSettingsUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update current user's settings (partial update)."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return await get_user_settings(user_id)

    try:
        return await update_user_settings(user_id, updates)
    except Exception:
        logger.exception("Failed to update user settings")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save settings",
        )
