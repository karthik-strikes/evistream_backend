"""
Log broadcasting utility for Celery workers.

Enables real-time log streaming from Celery tasks to WebSocket clients
via Redis pub/sub. The Celery worker and FastAPI run in separate processes,
so this broadcaster publishes to a Redis channel that the FastAPI WebSocket
handler subscribes to and forwards to connected clients.
"""

import json
import logging
from typing import Dict, Any
from datetime import datetime


logger = logging.getLogger(__name__)


class CeleryLogBroadcaster:
    """
    Broadcasts logs from Celery workers to WebSocket clients via Redis pub/sub.

    Usage in Celery tasks:
        broadcaster = CeleryLogBroadcaster(job_id)
        broadcaster.log("Starting generation...")
        broadcaster.progress(25, "Decomposition complete")
    """

    def __init__(self, job_id: str):
        self.job_id = job_id

    def _broadcast_message(self, message: Dict[str, Any]):
        """
        Publish a message to Redis so the FastAPI WebSocket handler
        can forward it to any connected clients.

        Also appends to the Redis cache list so clients that connect
        after the job completes can replay the message history.
        """
        try:
            from app.services.cache_service import cache_service

            payload = json.dumps(message)
            channel = f"ws_jobs:{self.job_id}"

            # Live path: pub/sub delivers to currently connected clients
            cache_service.redis_client.publish(channel, payload)

            # Late-join path: cache list for clients that connect after job ends
            key = f"ws_messages:{self.job_id}"
            cache_service.redis_client.lpush(key, payload.encode())
            cache_service.redis_client.ltrim(key, 0, 99)
            cache_service.redis_client.expire(key, 3600)

        except Exception as e:
            logger.error(f"Failed to publish message: {e}")

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
