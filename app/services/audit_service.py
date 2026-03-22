"""Service for audit trail logging."""

import logging
from supabase import create_client, Client
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def log_audit(
    user_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    project_id: Optional[UUID] = None,
    field_name: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Log an audit trail entry."""
    try:
        supabase = get_supabase()
        supabase.table("audit_trail").insert({
            "user_id": str(user_id),
            "project_id": str(project_id) if project_id else None,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "metadata": metadata or {},
        }).execute()
    except Exception:
        logger.exception("Failed to log audit entry")


async def get_audit_trail(
    project_id: Optional[UUID] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """Query audit trail with filters."""
    supabase = get_supabase()
    query = supabase.table("audit_trail").select("*")

    if project_id:
        query = query.eq("project_id", str(project_id))
    if entity_type:
        query = query.eq("entity_type", entity_type)
    if entity_id:
        query = query.eq("entity_id", str(entity_id))
    if user_id:
        query = query.eq("user_id", str(user_id))

    result = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


async def get_entity_history(entity_type: str, entity_id: UUID) -> list:
    """Get full audit history for a specific entity."""
    supabase = get_supabase()
    result = supabase.table("audit_trail")\
        .select("*")\
        .eq("entity_type", entity_type)\
        .eq("entity_id", str(entity_id))\
        .order("created_at", desc=True)\
        .execute()
    return result.data or []
