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
                "form_name": config.form_name or None
            }
            supabase.table("schemas").upsert(schema_data).execute()
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
                    form_name=row.get("form_name", "")
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
                        form_name=row.get("form_name", "")
                    )

                    # Update cache
                    _SCHEMA_REGISTRY[config.schema_name] = config

        except Exception as e:
            print(f"Warning: Failed to refresh registry from database: {e}")

    return list_schemas()


def auto_discover_schemas():
    """
    Auto-discover and register dynamic schemas from filesystem.

    Scans dspy_components/tasks/ for dynamic_* directories and registers them
    by loading their field_mapping.json files.

    Returns:
        Number of schemas discovered and registered
    """
    # Find project root (schemas is at project_root/schemas)
    project_root = Path(__file__).parent.parent
    tasks_dir = project_root / "dspy_components" / "tasks"

    if not tasks_dir.exists():
        return 0

    count = 0
    for task_dir in tasks_dir.iterdir():
        if not task_dir.is_dir():
            continue

        # Only process dynamic schemas
        if not task_dir.name.startswith("dynamic_"):
            continue

        # Check if field_mapping.json exists
        mapping_file = task_dir / "field_mapping.json"
        if not mapping_file.exists():
            continue

        try:
            # Load field mapping to get signature names
            with open(mapping_file, "r") as f:
                field_mapping = json.load(f)

            # Extract signature names from field mapping
            signature_names = sorted(set(field_mapping.values()))

            # Load pipeline stages from disk if available, otherwise fall back to
            # a single parallel stage (all signatures in stage 0)
            pipeline_file = task_dir / "pipeline_stages.json"
            if pipeline_file.exists():
                with open(pipeline_file, "r") as pf:
                    pipeline_stages = json.load(pf)
            else:
                pipeline_stages = [
                    {
                        "stage": 0,
                        "signatures": signature_names,
                        "execution": "parallel"
                    }
                ]

            # Create schema config
            task_name = task_dir.name
            schema_config = DynamicSchemaConfig(
                schema_name=task_name,
                task_name=task_name,
                module_path=f"dspy_components.tasks.{task_name}",
                signatures_path=f"dspy_components.tasks.{task_name}.signatures",
                signature_class_names=signature_names,
                pipeline_stages=pipeline_stages,
                project_id="",
                form_id="",
                form_name=""
            )

            register_schema(schema_config)
            count += 1

        except Exception as e:
            # Skip schemas that fail to load
            print(f"Warning: Failed to auto-register schema {task_dir.name}: {e}")
            continue

    # Restore any ACTIVE forms whose task directories are missing
    supabase = _get_supabase_client()
    if supabase:
        try:
            forms_result = supabase.table("forms")\
                .select("schema_name, task_dir, signatures_code, modules_code, form_name")\
                .eq("status", "active")\
                .not_.is_("schema_name", "null")\
                .not_.is_("signatures_code", "null")\
                .execute()

            for form in (forms_result.data or []):
                schema_name = form.get("schema_name")
                if not schema_name:
                    continue

                task_dir_path = tasks_dir / schema_name
                if task_dir_path.exists():
                    continue  # Already on disk, skip

                # Restore files from DB
                try:
                    task_dir_path.mkdir(parents=True, exist_ok=True)
                    (task_dir_path / "signatures.py").write_text(form["signatures_code"], encoding="utf-8")
                    (task_dir_path / "modules.py").write_text(form["modules_code"], encoding="utf-8")
                    (task_dir_path / "__init__.py").write_text(
                        f'"""\nGenerated task: {schema_name}\n"""\n', encoding="utf-8"
                    )
                    print(f"Restored missing schema from DB: {schema_name}")
                    count += 1
                except Exception as e:
                    print(f"Warning: Failed to restore schema {schema_name} from DB: {e}")

        except Exception as e:
            print(f"Warning: Failed to query forms for schema restoration: {e}")

    # Load all registered schemas from the DB into memory (covers restored schemas
    # and any schemas registered in a previous process that are not on disk yet)
    refresh_registry()

    return count
