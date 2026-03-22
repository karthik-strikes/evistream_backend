"""
Sync wrappers for async service functions, for use in Celery workers.
"""

import asyncio
import logging

from typing import Optional, Dict, Any
from uuid import UUID

from app.services.activity_service import log_activity
from app.services.notification_service import notify_job_completed

logger = logging.getLogger(__name__)


def sync_log_activity(
    user_id: UUID,
    action_type: str,
    action: str,
    description: str,
    project_id: Optional[UUID] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: Optional[str] = "success",
):
    """Sync wrapper for log_activity, for use in Celery workers."""
    try:
        asyncio.run(log_activity(
            user_id=user_id,
            action_type=action_type,
            action=action,
            description=description,
            project_id=project_id,
            metadata=metadata or {},
            status=status,
        ))
    except Exception:
        logger.debug("sync_log_activity failed", exc_info=True)


def sync_notify(
    user_id: UUID,
    job_id: UUID,
    job_type: str,
    success: bool = True,
    error_message: Optional[str] = None,
):
    """Sync wrapper for notify_job_completed, for use in Celery workers."""
    try:
        asyncio.run(notify_job_completed(
            user_id=user_id,
            job_id=job_id,
            job_type=job_type,
            success=success,
            error_message=error_message,
        ))
    except Exception:
        logger.debug("sync_notify failed", exc_info=True)
