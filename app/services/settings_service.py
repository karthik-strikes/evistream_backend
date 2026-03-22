"""Service for managing user settings."""

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from app.database import get_supabase_client

logger = logging.getLogger(__name__)

SETTINGS_COLUMNS = (
    "id, user_id, "
    "export_format, export_date_format, export_include_metadata, export_include_confidence, "
    "notify_email, notify_browser, notify_extraction_completed, notify_extraction_failed, notify_code_generation, "
    "created_at, updated_at"
)


async def get_user_settings(user_id: UUID) -> Dict[str, Any]:
    """Fetch user settings, creating a default row if none exists."""
    supabase = get_supabase_client()

    result = supabase.table("user_settings")\
        .select(SETTINGS_COLUMNS)\
        .eq("user_id", str(user_id))\
        .limit(1)\
        .execute()

    if result.data:
        return result.data[0]

    # Create default row
    insert_result = supabase.table("user_settings").insert({
        "user_id": str(user_id),
    }).execute()

    if not insert_result.data:
        raise RuntimeError("Failed to create default user settings")

    return insert_result.data[0]


async def update_user_settings(user_id: UUID, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Partial update of user settings. Creates default row first if needed."""
    supabase = get_supabase_client()

    # Ensure row exists
    await get_user_settings(user_id)

    # Apply update
    result = supabase.table("user_settings")\
        .update({**updates, "updated_at": "now()"})\
        .eq("user_id", str(user_id))\
        .execute()

    if not result.data:
        raise RuntimeError("Failed to update user settings")

    return result.data[0]
