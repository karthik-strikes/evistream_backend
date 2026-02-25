"""
WebSocket endpoints for real-time job updates.

Provides real-time progress updates for:
- PDF processing jobs
- Code generation jobs
- Extraction jobs
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status
from typing import Dict, Set, Optional, Any
import json
import asyncio
import logging
from uuid import UUID

from app.services.cache_service import cache_service


router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        # Map job_id -> set of active WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, job_id: str, websocket: WebSocket):
        """
        Register a new WebSocket connection for a job.

        Args:
            job_id: Job ID to subscribe to
            websocket: WebSocket connection
        """
        await websocket.accept()

        async with self._lock:
            if job_id not in self.active_connections:
                self.active_connections[job_id] = set()
            self.active_connections[job_id].add(websocket)

        logger.info(f"WebSocket connected for job {job_id}. Active connections: {len(self.active_connections[job_id])}")

    async def disconnect(self, job_id: str, websocket: WebSocket):
        """
        Remove a WebSocket connection.

        Args:
            job_id: Job ID
            websocket: WebSocket connection
        """
        async with self._lock:
            if job_id in self.active_connections:
                self.active_connections[job_id].discard(websocket)
                if not self.active_connections[job_id]:
                    del self.active_connections[job_id]

        logger.info(f"WebSocket disconnected for job {job_id}")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """
        Send a message to a specific WebSocket connection.

        Args:
            message: Message to send
            websocket: WebSocket connection
        """
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")

    async def broadcast_to_job(self, job_id: str, message: dict):
        """
        Broadcast a message to all connections subscribed to a job.

        Args:
            job_id: Job ID
            message: Message to broadcast
        """
        async with self._lock:
            if job_id not in self.active_connections:
                # No active connections, store in cache for later retrieval
                self._cache_message(job_id, message)
                return

            connections = list(self.active_connections[job_id])

        # Send to all connections (outside lock to avoid blocking)
        disconnected = []
        for websocket in connections:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to job {job_id}: {e}")
                disconnected.append(websocket)

        # Remove disconnected websockets
        if disconnected:
            async with self._lock:
                if job_id in self.active_connections:
                    for ws in disconnected:
                        self.active_connections[job_id].discard(ws)
                    if not self.active_connections[job_id]:
                        del self.active_connections[job_id]

    def _cache_message(self, job_id: str, message: dict):
        """
        Cache message in Redis for later retrieval.

        Args:
            job_id: Job ID
            message: Message to cache
        """
        try:
            key = f"ws_messages:{job_id}"
            # Store as list in Redis
            cache_service.lpush(key, message)
            # Keep only last 100 messages
            if cache_service.llen(key) > 100:
                cache_service.redis_client.ltrim(key, 0, 99)
            # Expire after 1 hour
            cache_service.expire(key, 3600)
        except Exception as e:
            logger.error(f"Error caching WebSocket message: {e}")

    async def get_cached_messages(self, job_id: str) -> list:
        """
        Get cached messages for a job.

        Args:
            job_id: Job ID

        Returns:
            List of cached messages
        """
        try:
            key = f"ws_messages:{job_id}"
            messages = []
            length = cache_service.llen(key)
            if length > 0:
                # Get all messages (they're stored in reverse order)
                for i in range(length):
                    msg = cache_service.redis_client.lindex(key, i)
                    if msg:
                        try:
                            messages.append(json.loads(msg.decode('utf-8')))
                        except:
                            pass
            return list(reversed(messages))  # Return in chronological order
        except Exception as e:
            logger.error(f"Error retrieving cached messages: {e}")
            return []


# Global connection manager
manager = ConnectionManager()


@router.websocket("/jobs/{job_id}")
async def websocket_job_updates(
    websocket: WebSocket,
    job_id: str,
    token: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for real-time job progress updates.

    Connect to this endpoint to receive real-time updates for a specific job.

    **Connection:**
    ```javascript
    const ws = new WebSocket(`ws://localhost:8000/api/v1/ws/jobs/${jobId}?token=${accessToken}`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('Job update:', data);
    };
    ```

    **Message Format:**
    ```json
    {
        "type": "progress|status|error|complete",
        "job_id": "uuid",
        "status": "pending|processing|completed|failed",
        "progress": 0-100,
        "message": "Human-readable message",
        "data": {},
        "timestamp": "ISO-8601 timestamp"
    }
    ```

    **Message Types:**
    - `progress`: Progress update (includes progress percentage)
    - `status`: Status change (pending -> processing -> completed/failed)
    - `error`: Error occurred
    - `complete`: Job completed successfully
    - `heartbeat`: Keep-alive ping (every 30 seconds)

    **Example Messages:**

    Progress Update:
    ```json
    {
        "type": "progress",
        "job_id": "123e4567-e89b-12d3-a456-426614174000",
        "status": "processing",
        "progress": 45,
        "message": "Processing document 3 of 10",
        "data": {
            "current": 3,
            "total": 10,
            "document_name": "research_paper.pdf"
        },
        "timestamp": "2026-01-28T12:00:00Z"
    }
    ```

    Completion:
    ```json
    {
        "type": "complete",
        "job_id": "123e4567-e89b-12d3-a456-426614174000",
        "status": "completed",
        "progress": 100,
        "message": "Extraction completed successfully",
        "data": {
            "extraction_id": "uuid",
            "results_count": 10
        },
        "timestamp": "2026-01-28T12:05:00Z"
    }
    ```

    Error:
    ```json
    {
        "type": "error",
        "job_id": "123e4567-e89b-12d3-a456-426614174000",
        "status": "failed",
        "progress": 45,
        "message": "Failed to process document",
        "data": {
            "error": "FileNotFoundError: Document not found"
        },
        "timestamp": "2026-01-28T12:03:00Z"
    }
    ```

    Args:
        job_id: Job ID to subscribe to
        token: Optional JWT token for authentication
    """
    # Validate job_id format
    try:
        UUID(job_id)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # TODO: Validate token if provided
    # For now, we allow unauthenticated connections for development
    # In production, validate JWT token here

    await manager.connect(job_id, websocket)

    try:
        # Send initial connection message
        await manager.send_personal_message({
            "type": "connected",
            "job_id": job_id,
            "message": "Connected to job updates",
            "timestamp": asyncio.get_event_loop().time()
        }, websocket)

        # Send any cached messages
        cached_messages = await manager.get_cached_messages(job_id)
        for msg in cached_messages:
            await manager.send_personal_message(msg, websocket)

        # Keep connection alive and handle incoming messages
        heartbeat_interval = 30  # seconds
        last_heartbeat = asyncio.get_event_loop().time()

        while True:
            try:
                # Wait for message with timeout for heartbeat
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=heartbeat_interval
                )

                # Handle client messages (e.g., subscription updates, pings)
                try:
                    data = json.loads(message)
                    if data.get("type") == "ping":
                        await manager.send_personal_message({
                            "type": "pong",
                            "timestamp": asyncio.get_event_loop().time()
                        }, websocket)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from client: {message}")

            except asyncio.TimeoutError:
                # Send heartbeat
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    await manager.send_personal_message({
                        "type": "heartbeat",
                        "timestamp": current_time
                    }, websocket)
                    last_heartbeat = current_time

    except WebSocketDisconnect:
        await manager.disconnect(job_id, websocket)
        logger.info(f"Client disconnected from job {job_id}")

    except Exception as e:
        logger.error(f"WebSocket error for job {job_id}: {e}")
        await manager.disconnect(job_id, websocket)


@router.get("/jobs/{job_id}/messages")
async def get_job_messages(job_id: str):
    """
    Get cached messages for a job (REST endpoint alternative to WebSocket).

    This is useful for:
    - Polling-based updates
    - Checking status without WebSocket connection
    - Retrieving message history

    Args:
        job_id: Job ID

    Returns:
        List of cached messages
    """
    try:
        UUID(job_id)
    except ValueError:
        return {"error": "Invalid job_id format"}

    messages = await manager.get_cached_messages(job_id)
    return {
        "job_id": job_id,
        "messages": messages,
        "count": len(messages)
    }


# Helper function for Celery workers to send updates
async def send_job_update(
    job_id: str,
    message_type: str,
    status: str,
    progress: int,
    message: str,
    data: Optional[dict] = None
):
    """
    Send a job update to all connected WebSocket clients.

    This function should be called from Celery workers to broadcast updates.

    Args:
        job_id: Job ID
        message_type: Message type (progress, status, error, complete)
        status: Job status (pending, processing, completed, failed)
        progress: Progress percentage (0-100)
        message: Human-readable message
        data: Optional additional data
    """
    update_message = {
        "type": message_type,
        "job_id": job_id,
        "status": status,
        "progress": progress,
        "message": message,
        "data": data or {},
        "timestamp": asyncio.get_event_loop().time()
    }

    await manager.broadcast_to_job(job_id, update_message)


# Export for use in workers
__all__ = ["router", "manager", "send_job_update"]
