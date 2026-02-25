"""
Log broadcasting utility for Celery workers.

Enables real-time log streaming from Celery tasks to WebSocket clients.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any
from datetime import datetime


logger = logging.getLogger(__name__)


class CeleryLogBroadcaster:
    """
    Broadcasts logs from Celery workers to WebSocket clients.

    Usage in Celery tasks:
        broadcaster = CeleryLogBroadcaster(job_id)
        broadcaster.log("Starting generation...")
        broadcaster.progress(25, "Decomposition complete")
    """

    def __init__(self, job_id: str):
        """
        Initialize log broadcaster.

        Args:
            job_id: Job ID to broadcast logs for
        """
        self.job_id = job_id
        self._loop = None

    def _get_or_create_loop(self):
        """Get or create event loop for async operations."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    def _broadcast_message(self, message: Dict[str, Any]):
        """
        Broadcast a message to WebSocket clients.

        Args:
            message: Message dictionary to broadcast
        """
        try:
            # Import here to avoid circular imports
            from app.api.v1.websocket import manager

            loop = self._get_or_create_loop()

            # Run async broadcast in sync context
            if loop.is_running():
                # If loop is already running, create a task
                asyncio.create_task(manager.broadcast_to_job(self.job_id, message))
            else:
                # Otherwise run until complete
                loop.run_until_complete(manager.broadcast_to_job(self.job_id, message))

        except Exception as e:
            logger.error(f"Failed to broadcast message: {e}")

    def log(self, message: str, level: str = "info"):
        """
        Broadcast a log message.

        Args:
            message: Log message
            level: Log level (info, success, warning, error)
        """
        self._broadcast_message({
            "type": "log",
            "level": level,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "job_id": self.job_id
        })

        # Also log to standard logger
        log_func = getattr(logger, level if level != "success" else "info")
        log_func(f"[Job {self.job_id}] {message}")

    def info(self, message: str):
        """Log info message."""
        self.log(message, "info")

    def success(self, message: str):
        """Log success message."""
        self.log(message, "success")

    def warning(self, message: str):
        """Log warning message."""
        self.log(message, "warning")

    def error(self, message: str):
        """Log error message."""
        self.log(message, "error")

    def progress(self, percentage: int, message: str = ""):
        """
        Broadcast progress update.

        Args:
            percentage: Progress percentage (0-100)
            message: Optional progress message
        """
        self._broadcast_message({
            "type": "progress",
            "progress": percentage,
            "message": message or f"Progress: {percentage}%",
            "timestamp": datetime.utcnow().isoformat(),
            "job_id": self.job_id
        })

    def stage(self, stage_name: str, description: str = ""):
        """
        Broadcast stage change.

        Args:
            stage_name: Name of the stage
            description: Optional stage description
        """
        self._broadcast_message({
            "type": "stage",
            "stage": stage_name,
            "message": description or stage_name,
            "timestamp": datetime.utcnow().isoformat(),
            "job_id": self.job_id
        })

    def data(self, data: Dict[str, Any], message: str = ""):
        """
        Broadcast data update.

        Args:
            data: Data dictionary to broadcast
            message: Optional message
        """
        self._broadcast_message({
            "type": "data",
            "data": data,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "job_id": self.job_id
        })
