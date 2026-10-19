"""
Database connection management.

Provides singleton Supabase client with connection pooling.
"""

import logging
import time
from typing import Optional
from supabase import Client, create_client, ClientOptions
from app.config import settings

logger = logging.getLogger(__name__)

SLOW_QUERY_MS = 500  # warn if query exceeds this threshold


class _LoggingQueryBuilder:
    """Wraps a Supabase QueryBuilder to log every .execute() call."""

    def __init__(self, builder, table: str, operation: str):
        self._builder = builder
        self._table = table
        self._operation = operation

    def __getattr__(self, name):
        attr = getattr(self._builder, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                result = attr(*args, **kwargs)
                # Re-wrap chained builders so execute() is still intercepted
                if hasattr(result, 'execute') and not isinstance(result, _LoggingQueryBuilder):
                    return _LoggingQueryBuilder(result, self._table, self._operation)
                return result
            return wrapper
        return attr

    def execute(self):
        start = time.monotonic()
        try:
            result = self._builder.execute()
            ms = int((time.monotonic() - start) * 1000)
            rows = len(result.data) if result.data else 0
            msg = f"DB {self._operation} {self._table} rows={rows} {ms}ms"
            if ms >= SLOW_QUERY_MS:
                logger.warning(f"SLOW QUERY: {msg}")
            else:
                logger.debug(msg)
            return result
        except Exception as e:
            ms = int((time.monotonic() - start) * 1000)
            logger.error(f"DB {self._operation} {self._table} error={e} {ms}ms")
            raise


class _LoggingClient:
    """Thin proxy over Supabase Client that intercepts table() calls."""

    def __init__(self, client: Client):
        self._client = client

    def table(self, table_name: str):
        builder = self._client.table(table_name)
        return _LoggingTableProxy(builder, table_name)

    def __getattr__(self, name):
        return getattr(self._client, name)


class _LoggingTableProxy:
    """Intercepts select/insert/update/delete to tag the operation name."""

    def __init__(self, builder, table_name: str):
        self._builder = builder
        self._table = table_name

    def select(self, *args, **kwargs):
        return _LoggingQueryBuilder(self._builder.select(*args, **kwargs), self._table, "SELECT")

    def insert(self, *args, **kwargs):
        return _LoggingQueryBuilder(self._builder.insert(*args, **kwargs), self._table, "INSERT")

    def update(self, *args, **kwargs):
        return _LoggingQueryBuilder(self._builder.update(*args, **kwargs), self._table, "UPDATE")

    def delete(self, *args, **kwargs):
        return _LoggingQueryBuilder(self._builder.delete(*args, **kwargs), self._table, "DELETE")

    def upsert(self, *args, **kwargs):
        return _LoggingQueryBuilder(self._builder.upsert(*args, **kwargs), self._table, "UPSERT")


# Singleton Supabase client instance
_supabase_client: Optional[_LoggingClient] = None


def get_supabase_client() -> _LoggingClient:
    """
    Get singleton Supabase client with connection pooling.

    This ensures all requests reuse the same client instance,
    preventing connection exhaustion.

    Returns:
        Supabase client instance
    """
    global _supabase_client

    if _supabase_client is None:
        raw_client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
            options=ClientOptions(
                schema="public",
                auto_refresh_token=True,
                persist_session=True,
            )
        )
        _supabase_client = _LoggingClient(raw_client)

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
