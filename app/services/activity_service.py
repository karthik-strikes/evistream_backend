"""Service for logging user activities."""

from supabase import create_client, Client
from typing import Optional, Dict, Any
from uuid import UUID
from app.config import settings


def get_supabase() -> Client:
    """Get Supabase client."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


async def log_activity(
    user_id: UUID,
    action_type: str,
    action: str,
    description: str,
    project_id: Optional[UUID] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: Optional[str] = "success",
):
    """
    Log a user activity to the database.

    Args:
        user_id: User ID performing the action
        action_type: Type of action (upload, extraction, export, code_generation, form_create, project_create)
        action: Short action title
        description: Detailed description
        project_id: Optional project ID
        metadata: Optional additional metadata as JSON
        status: Status of the action (success, failed, pending)
    """
    try:
        supabase = get_supabase()

        supabase.table("activities").insert({
            "user_id": str(user_id),
            "project_id": str(project_id) if project_id else None,
            "action_type": action_type,
            "action": action,
            "description": description,
            "metadata": metadata or {},
            "status": status,
        }).execute()

    except Exception as e:
        # Log error but don't fail the main operation
        print(f"Error logging activity: {e}")
