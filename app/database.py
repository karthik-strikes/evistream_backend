"""
Database connection management.

Provides singleton Supabase client with connection pooling.
"""

from typing import Optional
from supabase import Client, create_client
from app.config import settings


# Singleton Supabase client instance
_supabase_client: Optional[Client] = None


def get_supabase_client() -> Client:
    """
    Get singleton Supabase client with connection pooling.

    This ensures all requests reuse the same client instance,
    preventing connection exhaustion.

    Returns:
        Supabase client instance
    """
    global _supabase_client

    if _supabase_client is None:
        _supabase_client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
            options={
                "schema": "public",
                "auto_refresh_token": True,
                "persist_session": True,
            }
        )

    return _supabase_client


def close_supabase_client() -> None:
    """
    Close Supabase client connection.

    Called during application shutdown.
    """
    global _supabase_client

    if _supabase_client is not None:
        # Supabase client doesn't have explicit close method
        # Connection will be cleaned up when object is destroyed
        _supabase_client = None
