"""
Utility Functions for DSPy Code Generation

Provides helper functions for:
- Name sanitization
- Task directory creation and management
- Dynamic schema loading and registration
- Main entry point for form-to-task generation
"""

import re
import json
import importlib
import sys
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
import dspy


def sanitize_form_name(form_name: str) -> str:
    """
    Sanitize form name to create valid Python class names.

    Examples:
        " TrialCharacteristics" -> "TrialCharacteristics"
        "Trial Characteristics" -> "TrialCharacteristics"
        "trial-characteristics" -> "TrialCharacteristics"
    """
    name = form_name.strip()
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    words = name.split()

    sanitized_words = []
    for word in words:
        if word:
            sanitized_words.append(
                word[0].upper() + word[1:] if len(word) > 1 else word.upper()
            )

    sanitized = "".join(sanitized_words)

    if sanitized and not sanitized[0].isalpha():
        sanitized = "Form" + sanitized

    if not sanitized:
        sanitized = "CustomForm"

    return sanitized


def sanitize_field_key(field_name: str) -> str:
    """
    Convert field name to valid Python/JSON key (snake_case).

    Examples:
        "Study Design" -> "study_design"
        "Patient Age (years)" -> "patient_age_years"
        "Female (%)" -> "female_percent"
    """
    name = field_name.lower()
    name = name.replace("(%)", "_percent")
    name = name.replace("(n)", "_n")
    name = name.replace("%", "_percent")
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")

    if name and not name[0].isalpha():
        name = "field_" + name

    if not name:
        name = "custom_field"

    return name


def create_task_name_from_ids(project_id: str, form_id: str) -> str:
    """
    Create a consistent task name using hash of project_id and form_id.

    Args:
        project_id: Project identifier
        form_id: Form identifier

    Returns:
        Task name in format: task_{8_char_hash}
    """
    hash_input = f"{project_id}_{form_id}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    return f"task_{short_hash}"


def create_task_directory(
    project_id: str,
    form_id: str,
    form_data: Dict[str, Any],
    generator: "DSPySignatureGenerator",  # type: ignore
) -> Path:
    """
    Create complete task directory with LangGraph-generated code.

    Args:
        project_id: Project identifier
        form_id: Form identifier
        form_data: Form specification with name, description, fields
        generator: DSPySignatureGenerator instance for code generation

    Returns:
        Path to created task directory
    """
    # Create a short, readable task name using first 8 chars of hash
    task_name = create_task_name_from_ids(project_id, form_id)

    # Tasks live under the top-level dspy_components/tasks package
    task_dir = Path(__file__).parent.parent.parent / \
        "dspy_components" / "tasks" / task_name

    task_dir.mkdir(parents=True, exist_ok=True)

    form_name = form_data.get("form_name") or form_data.get("name")
    form_description = form_data.get(
        "description", f"Extract {form_name} data")

    # Use the generator to create complete task directly from form_data
    result = generator.generate_complete_task(form_data, task_name=task_name)

    if not result.get("success"):
        error_msg = f"Task generation failed: {result.get('error')}"
        raise Exception(error_msg)

    # Write files
    signature_file = task_dir / "signatures.py"
    signature_file.write_text(result["signatures_file"])

    module_file = task_dir / "modules.py"
    module_file.write_text(result["modules_file"])

    init_file = task_dir / "__init__.py"
    init_file.write_text("")

    metadata = {
        "project_id": project_id,
        "form_id": form_id,
        "form_data": form_data,
        "field_mapping": result.get("field_mapping", {}),
        "decomposition": result.get("decomposition", {}),
        "generation_stats": result.get("statistics", {}),
    }
    metadata_file = task_dir / "metadata.json"
    metadata_file.write_text(json.dumps(metadata, indent=2))

    # Task directory created successfully

    return task_dir


def load_dynamic_schemas():
    """
    Load all dynamic schemas on startup by reading metadata and registering them.

    Note:
        The dynamically generated DSPy task packages live under the
        top-level ``dspy_components/tasks`` directory at the project root,
        not inside ``core/``. We therefore resolve the tasks directory
        relative to the project root (parent of ``core``).
    """
    # project_root / "dspy_components" / "tasks"
    tasks_dir = Path(__file__).parent.parent.parent / \
        "dspy_components" / "tasks"
    if not tasks_dir.exists():
        return

    # Find all dynamically generated task packages
    task_packages = [d for d in tasks_dir.iterdir() if d.is_dir(
    ) and d.name.startswith("task_") and (d / "__init__.py").exists()]

    loaded_count = 0
    for task_pkg_dir in task_packages:
        task_pkg_name = task_pkg_dir.name
        metadata_file = task_pkg_dir / "metadata.json"

        # Try to load metadata and register schema
        if metadata_file.exists():
            try:
                metadata = json.loads(metadata_file.read_text())
                project_id = metadata.get("project_id")
                form_id = metadata.get("form_id")
                form_data = metadata.get("form_data")

                if project_id and form_id and form_data:
                    # Check if schema is already registered to avoid duplicate work
                    from schemas.registry import list_schemas
                    form_name = form_data.get("form_name") or form_data.get("name", "Form")
                    schema_name = sanitize_form_name(form_name)
                    
                    # Skip if already registered
                    if schema_name in list_schemas():
                        continue
                    
                    # Register the schema with decomposition from metadata
                    try:
                        decomposition = metadata.get("decomposition", {})
                        register_dynamic_schema(
                            project_id, form_id, form_data, decomposition=decomposition)
                        loaded_count += 1
                    except Exception as e:
                        # Schema might already be registered or registration failed
                        # That's okay, continue with other schemas
                        pass
            except Exception:
                # If metadata is invalid, skip this task
                pass


def register_dynamic_schema(
    project_id: str, form_id: str, form_data: Dict[str, Any],
    decomposition: Optional[Dict[str, Any]] = None
) -> str:
    """
    Register a dynamically generated schema in the schema registry.

    Args:
        project_id: Project identifier
        form_id: Form identifier
        form_data: Form specification
        decomposition: Optional decomposition dict (if not provided, loads from metadata.json)

    Returns:
        Schema name for the registered schema
    """
    from schemas.config import DynamicSchemaConfig
    from schemas.registry import register_schema

    # Create a short, readable task name using first 8 chars of hash
    task_name = create_task_name_from_ids(project_id, form_id)

    form_name = form_data.get("form_name") or form_data.get("name", "Form")
    sanitized_form_name = sanitize_form_name(form_name)
    schema_name = f"{sanitized_form_name}"

    # Module path
    module_path = f"dspy_components.tasks.{task_name}"
    signatures_path = f"{module_path}.signatures"

    # Load decomposition if not provided
    if decomposition is None:
        # Try to load from metadata.json
        task_dir = Path(__file__).parent.parent.parent / \
            "dspy_components" / "tasks" / task_name
        metadata_file = task_dir / "metadata.json"
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text())
            decomposition = metadata.get("decomposition", {})
        else:
            raise ValueError(
                f"Decomposition not provided and metadata.json not found for {task_name}")

    # Extract pipeline structure and signatures from decomposition
    pipeline_stages = decomposition.get("pipeline", [])
    signatures = decomposition.get("signatures", [])

    if not signatures:
        raise ValueError(
            f"No signatures found in decomposition for {task_name}")

    # Get signature class names
    signature_class_names = [sig.get("name")
                             for sig in signatures if sig.get("name")]

    if not signature_class_names:
        raise ValueError(
            f"No signature names found in decomposition for {task_name}")

    # Create DynamicSchemaConfig
    config = DynamicSchemaConfig(
        schema_name=schema_name,
        task_name=task_name,
        module_path=module_path,
        signatures_path=signatures_path,
        signature_class_names=signature_class_names,
        pipeline_stages=pipeline_stages,
        project_id=project_id,
        form_id=form_id,
        form_name=form_name
    )

    # Register the schema
    register_schema(config)

    return schema_name


def generate_task_from_form(
    project_id: str,
    form_id: str,
    form_data: Dict[str, Any],
    generator: Optional["DSPySignatureGenerator"] = None,  # type: ignore
) -> Dict[str, Any]:
    """
    Complete workflow: Generate task directory and register schema.

    Args:
        project_id: Project identifier
        form_id: Form identifier
        form_data: Form specification with name, description, fields
        generator: Optional DSPySignatureGenerator instance (will create if not provided)

    Returns:
        Dictionary with status, task_dir, schema_name, and form_name
    """
    # Write to file IMMEDIATELY to confirm function was called
    from datetime import datetime
    try:
        debug_file = Path("debug_utils_function.log")
        with open(debug_file, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(
                f"[{datetime.now()}] FUNCTION CALLED: generate_task_from_form()\n")
            f.write(f"Project ID: {project_id}\n")
            f.write(f"Form ID: {form_id}\n")
            f.write(f"Form Name: {form_data.get('form_name', 'N/A')}\n")
            f.write(f"{'='*60}\n")
    except Exception as write_error:
        # If file write fails, write to main debug log
        main_debug = Path("debug_form_generation.log")
        with open(main_debug, "a") as f:
            f.write(
                f"\n[{datetime.now()}] ERROR: Could not write to debug_utils_function.log: {write_error}\n")

    try:

        # Import here to avoid circular dependency
        from core.generators import DSPySignatureGenerator

        # Create generator if not provided
        if generator is None:
            generator = DSPySignatureGenerator()

        task_dir = create_task_directory(
            project_id, form_id, form_data, generator)

        # Get decomposition from metadata.json (just written by create_task_directory)
        task_name = create_task_name_from_ids(project_id, form_id)
        metadata_file = task_dir / "metadata.json"
        decomposition = {}
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text())
            decomposition = metadata.get("decomposition", {})

        schema_name = register_dynamic_schema(
            project_id, form_id, form_data, decomposition=decomposition)

        return {
            "status": "success",
            "task_dir": str(task_dir),
            "schema_name": schema_name,
            "form_name": form_data.get("form_name") or form_data.get("name"),
        }

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()

        return {"status": "error", "error": str(e), "traceback": error_trace}


__all__ = [
    "sanitize_form_name",
    "sanitize_field_key",
    "create_task_name_from_ids",
    "create_task_directory",
    "load_dynamic_schemas",
    "register_dynamic_schema",
    "generate_task_from_form",
]
