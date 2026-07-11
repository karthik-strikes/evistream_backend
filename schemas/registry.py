"""
Schema Registry

Unified registry for dynamic schemas.
Uses a three-tier cache: in-memory → Redis → Supabase.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional
from .config import DynamicSchemaConfig

logger = logging.getLogger(__name__)

# In-memory cache (L1)
_SCHEMA_REGISTRY: Dict[str, DynamicSchemaConfig] = {}

# Lazy-loaded clients
_supabase_client = None
_redis_client = None

REDIS_SCHEMA_TTL = 3600  # 1 hour TTL for Redis schema cache


def _get_redis_client():
    """Get or create Redis client for schema caching (L2)."""
    global _redis_client

    if _redis_client is None:
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", 6379))
            redis_db = int(os.getenv("REDIS_DB", 0))
            _redis_client = redis.Redis(
                host=redis_host, port=redis_port, db=redis_db,
                decode_responses=True, socket_connect_timeout=3, socket_timeout=3
            )
            _redis_client.ping()
        except Exception as e:
            logger.warning(f"Redis not available for schema cache: {e}")
            _redis_client = None

    return _redis_client


def _get_supabase_client():
    """Get or create Supabase client (L3)."""
    global _supabase_client

    if _supabase_client is None:
        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

            if not supabase_url or not supabase_key:
                return None

            _supabase_client = create_client(supabase_url, supabase_key)
        except Exception as e:
            logger.warning(f"Failed to connect to Supabase: {e}")
            return None

    return _supabase_client


def _schema_to_redis_dict(config: DynamicSchemaConfig) -> str:
    """Serialize a DynamicSchemaConfig to JSON for Redis storage."""
    return json.dumps({
        "schema_name": config.schema_name,
        "task_name": config.task_name,
        "signature_class_names": config.signature_class_names,
        "pipeline_stages": config.pipeline_stages,
        "form_id": str(config.form_id) if config.form_id else "",
        "form_name": config.form_name or "",
        "schema_def": config.schema_def,  # None if not yet populated
    })


def _redis_dict_to_schema(data: str) -> DynamicSchemaConfig:
    """Deserialize a DynamicSchemaConfig from Redis JSON."""
    row = json.loads(data)
    return DynamicSchemaConfig(
        schema_name=row["schema_name"],
        task_name=row["task_name"],
        module_path=f"dspy_components.tasks.{row['task_name']}",
        signatures_path=f"dspy_components.tasks.{row['task_name']}.signatures",
        signature_class_names=row["signature_class_names"],
        pipeline_stages=row["pipeline_stages"],
        project_id=row.get("form_id", ""),
        form_id=row.get("form_id", ""),
        form_name=row.get("form_name", ""),
        schema_def=row.get("schema_def"),
    )


def register_schema(config: DynamicSchemaConfig) -> None:
    """
    Register a dynamic schema in all three tiers: memory → Redis → Supabase.

    Args:
        config: DynamicSchemaConfig to register
    """
    # L1: Update in-memory cache
    _SCHEMA_REGISTRY[config.schema_name] = config

    # L2: Persist to Redis
    redis_client = _get_redis_client()
    if redis_client:
        try:
            redis_key = f"schema:{config.schema_name}"
            redis_client.setex(redis_key, REDIS_SCHEMA_TTL, _schema_to_redis_dict(config))
        except Exception as e:
            logger.warning(f"Failed to cache schema in Redis: {e}")

    # L3: Persist to Supabase
    supabase = _get_supabase_client()
    if supabase:
        try:
            schema_data = {
                "schema_name": config.schema_name,
                "task_name": config.task_name,
                "module_path": config.module_path,
                "signatures_path": config.signatures_path,
                "signature_class_names": config.signature_class_names,
                "pipeline_stages": config.pipeline_stages,
                "form_id": str(config.form_id) if config.form_id else None,
                "form_name": config.form_name or None,
                "schema_def": config.schema_def,
            }
            supabase.table("schemas").upsert(schema_data, on_conflict="schema_name").execute()
        except Exception as e:
            logger.warning(f"Failed to persist schema to database: {e}")


def get_schema(schema_name: str) -> DynamicSchemaConfig:
    """
    Get schema configuration by name.

    Lookup order: L1 (in-memory) → L2 (Redis) → L3 (Supabase).

    Args:
        schema_name: Name of the schema

    Returns:
        DynamicSchemaConfig object

    Raises:
        ValueError: If schema not found
    """
    # L1: Check in-memory cache first
    if schema_name in _SCHEMA_REGISTRY:
        return _SCHEMA_REGISTRY[schema_name]

    # L2: Check Redis
    redis_client = _get_redis_client()
    if redis_client:
        try:
            redis_key = f"schema:{schema_name}"
            cached = redis_client.get(redis_key)
            if cached:
                config = _redis_dict_to_schema(cached)
                _SCHEMA_REGISTRY[schema_name] = config  # Promote to L1
                return config
        except Exception as e:
            logger.warning(f"Failed to read schema from Redis: {e}")

    # L3: Try loading from Supabase
    supabase = _get_supabase_client()
    if supabase:
        try:
            result = supabase.table("schemas")\
                .select("*")\
                .eq("schema_name", schema_name)\
                .execute()

            if result.data and len(result.data) > 0:
                row = result.data[0]

                config = DynamicSchemaConfig(
                    schema_name=row["schema_name"],
                    task_name=row["task_name"],
                    module_path=f"dspy_components.tasks.{row['task_name']}",
                    signatures_path=f"dspy_components.tasks.{row['task_name']}.signatures",
                    signature_class_names=row["signature_class_names"],
                    pipeline_stages=row["pipeline_stages"],
                    project_id=row.get("form_id", ""),
                    form_id=row.get("form_id", ""),
                    form_name=row.get("form_name", ""),
                    schema_def=row.get("schema_def"),
                )

                if config.schema_def is None:
                    logger.warning(
                        "Schema '%s' loaded from L3 with schema_def=NULL — "
                        "extraction will RuntimeError until the form is regenerated.",
                        schema_name,
                    )

                # Promote to L1 + L2
                _SCHEMA_REGISTRY[schema_name] = config
                if redis_client:
                    try:
                        redis_client.setex(
                            f"schema:{schema_name}", REDIS_SCHEMA_TTL,
                            _schema_to_redis_dict(config)
                        )
                    except Exception:
                        pass
                return config

        except Exception as e:
            logger.warning(f"Failed to load schema from database: {e}")

    # Schema not found
    available = sorted(_SCHEMA_REGISTRY.keys())
    raise ValueError(
        f"Unknown schema '{schema_name}'. Available: {', '.join(available)}"
    )


def list_schemas() -> List[str]:
    """
    List all registered schema names (from cache and Supabase).

    Returns:
        Sorted list of schema names
    """
    schema_names = set(_SCHEMA_REGISTRY.keys())

    # Also load from Supabase
    supabase = _get_supabase_client()
    if supabase:
        try:
            result = supabase.table("schemas").select("schema_name").execute()
            if result.data:
                schema_names.update([row["schema_name"] for row in result.data])
        except Exception as e:
            print(f"Warning: Failed to list schemas from database: {e}")

    return sorted(schema_names)


def refresh_registry():
    """
    Refresh registry by loading all schemas from Supabase into memory.

    Returns:
        List of schema names
    """
    supabase = _get_supabase_client()
    if supabase:
        try:
            result = supabase.table("schemas").select("*").execute()

            null_schema_def_names: List[str] = []
            if result.data:
                for row in result.data:
                    # Reconstruct DynamicSchemaConfig
                    config = DynamicSchemaConfig(
                        schema_name=row["schema_name"],
                        task_name=row["task_name"],
                        module_path=f"dspy_components.tasks.{row['task_name']}",
                        signatures_path=f"dspy_components.tasks.{row['task_name']}.signatures",
                        signature_class_names=row["signature_class_names"],
                        pipeline_stages=row["pipeline_stages"],
                        project_id=row.get("form_id", ""),
                        form_id=row.get("form_id", ""),
                        form_name=row.get("form_name", ""),
                        schema_def=row.get("schema_def"),
                    )

                    if config.schema_def is None:
                        null_schema_def_names.append(config.schema_name)

                    # Update cache
                    _SCHEMA_REGISTRY[config.schema_name] = config

            if null_schema_def_names:
                logger.warning(
                    "Loaded %d schema(s) with schema_def=NULL — extraction will "
                    "RuntimeError until each form is regenerated: %s",
                    len(null_schema_def_names),
                    ", ".join(null_schema_def_names),
                )

        except Exception as e:
            logger.warning(f"Failed to refresh registry from database: {e}")

    return list_schemas()


def invalidate_schema(schema_name: str) -> None:
    """Remove a schema from L1 and L2 caches so the next load fetches from Supabase."""
    _SCHEMA_REGISTRY.pop(schema_name, None)
    redis_client = _get_redis_client()
    if redis_client:
        try:
            redis_client.delete(f"schema:{schema_name}")
        except Exception as e:
            logger.warning(f"Redis schema cache invalidation failed: {e}")
    try:
        from dspy_components.runtime_builders import clear_class_cache
        clear_class_cache()
    except Exception:
        pass


def auto_discover_schemas():
    """Load all schemas from the database into the in-memory registry.

    Phase C+: no filesystem scan — schemas are built at runtime from schema_def.

    Returns:
        Number of schemas in registry after refresh
    """
    refresh_registry()
    return len(list_schemas())
