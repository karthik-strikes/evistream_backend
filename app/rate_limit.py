"""
Rate limiter configuration — extracted to avoid circular imports.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def get_user_identifier(request: Request) -> str:
    """
    Get user identifier for rate limiting.

    Uses JWT user_id if authenticated, otherwise falls back to IP address.
    """
    if hasattr(request.state, "user_id"):
        return f"user:{request.state.user_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=get_user_identifier)
