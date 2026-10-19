"""
Client-side log ingestion endpoint.

Accepts frontend log events (errors, warnings) and forwards them
to the server-side logger so they appear in CloudWatch.
"""

import logging
from typing import Any, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger(__name__)


class ClientLogEntry(BaseModel):
    level: str  # "error" | "warn" | "info"
    message: str
    context: Optional[dict[str, Any]] = None


@router.post("/client", status_code=204)
@limiter.limit("60/minute")
async def ingest_client_log(request: Request, entry: ClientLogEntry):
    """
    Receive a log event from the browser and forward it to CloudWatch.
    No auth required — frontend may not have a token yet (e.g. login errors).
    Rate-limited to 60/min per IP.
    """
    ctx = f" context={entry.context}" if entry.context else ""
    msg = f"[frontend] {entry.message}{ctx}"

    if entry.level == "error":
        logger.error(msg)
    elif entry.level == "warn":
        logger.warning(msg)
    else:
        logger.info(msg)
