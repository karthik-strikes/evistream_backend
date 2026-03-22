"""Service for creating user notifications."""

import logging

from supabase import create_client, Client
from typing import Optional
from uuid import UUID
from app.config import settings

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    """Get Supabase client."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _get_user_notification_prefs(user_id: UUID) -> dict:
    """Fetch notification preferences for a user. Returns defaults if no row exists."""
    try:
        supabase = get_supabase()
        result = supabase.table("user_settings")\
            .select("notify_email, notify_browser, notify_extraction_completed, notify_extraction_failed, notify_code_generation")\
            .eq("user_id", str(user_id))\
            .limit(1)\
            .execute()
        if result.data:
            return result.data[0]
    except Exception:
        logger.debug("Could not load notification prefs for user %s, using defaults", user_id)
    # Defaults — all enabled
    return {
        "notify_email": True,
        "notify_browser": True,
        "notify_extraction_completed": True,
        "notify_extraction_failed": True,
        "notify_code_generation": True,
    }


async def create_notification(
    user_id: UUID,
    type: str,
    title: str,
    message: str,
    action_label: Optional[str] = None,
    action_url: Optional[str] = None,
    related_entity_type: Optional[str] = None,
    related_entity_id: Optional[UUID] = None,
):
    """
    Create a notification for a user.

    Args:
        user_id: User ID to notify
        type: Notification type (success, error, info, warning)
        title: Notification title
        message: Notification message
        action_label: Optional action button label
        action_url: Optional action button URL
        related_entity_type: Optional related entity type (job, extraction, document, etc.)
        related_entity_id: Optional related entity ID
    """
    try:
        supabase = get_supabase()

        supabase.table("notifications").insert({
            "user_id": str(user_id),
            "type": type,
            "title": title,
            "message": message,
            "action_label": action_label,
            "action_url": action_url,
            "related_entity_type": related_entity_type,
            "related_entity_id": str(related_entity_id) if related_entity_id else None,
        }).execute()

    except Exception as e:
        # Log error but don't fail the main operation
        logger.exception("Error creating notification")


async def notify_job_completed(
    user_id: UUID,
    job_id: UUID,
    job_type: str,
    success: bool = True,
    error_message: Optional[str] = None,
):
    """
    Create a notification for a completed job.

    Args:
        user_id: User ID to notify
        job_id: Job ID
        job_type: Type of job
        success: Whether the job succeeded
        error_message: Optional error message if failed
    """
    prefs = _get_user_notification_prefs(user_id)

    if success:
        # Success notifications
        if job_type == "extraction":
            if not prefs.get("notify_extraction_completed", True):
                return
            await create_notification(
                user_id=user_id,
                type="success",
                title="Extraction Complete",
                message=f"Your extraction job completed successfully.",
                action_label="View Results",
                action_url=f"/results?extraction_id={job_id}",
                related_entity_type="job",
                related_entity_id=job_id,
            )
        elif job_type == "pdf_processing":
            await create_notification(
                user_id=user_id,
                type="success",
                title="Document Processed",
                message="Your PDF document has been processed successfully.",
                action_label="View Documents",
                action_url="/documents",
                related_entity_type="job",
                related_entity_id=job_id,
            )
        elif job_type == "form_generation":
            if not prefs.get("notify_code_generation", True):
                return
            await create_notification(
                user_id=user_id,
                type="success",
                title="Code Generation Complete",
                message="Your extraction form code has been generated.",
                action_label="View Form",
                action_url="/forms",
                related_entity_type="job",
                related_entity_id=job_id,
            )
    else:
        # Failure notification
        if job_type == "extraction" and not prefs.get("notify_extraction_failed", True):
            return
        await create_notification(
            user_id=user_id,
            type="error",
            title=f"{job_type.replace('_', ' ').title()} Failed",
            message=error_message or "An error occurred during processing.",
            action_label="View Jobs",
            action_url="/jobs",
            related_entity_type="job",
            related_entity_id=job_id,
        )
