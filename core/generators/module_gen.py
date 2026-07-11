"""
DSPy Module Generator — create_fallback_structure retained for schema_def building.
Module code generation was eliminated in Phase D; runtime_builders constructs
extractor classes at runtime from schema_def JSON.
"""

from typing import Dict, Any


class ModuleGenerator:
    """Provides create_fallback_structure for use in _build_schema_def."""

    def __init__(self, model_name: str = ""):
        pass

    def create_fallback_structure(self, enriched_sig: Dict[str, Any]) -> Dict[str, Any]:
        """Build fallback return value for a signature based on its field types."""
        fields_metadata = enriched_sig.get("fields", {})

        if isinstance(fields_metadata, dict):
            fallback = {}
            for field_name, field_meta in fields_metadata.items():
                field_type = field_meta.get("field_type", "text")
                if field_type == "array":
                    fallback[field_name] = []
                else:
                    fallback[field_name] = {"value": "NR", "source_text": "NR"}
            return fallback
        return {"value": "NR", "source_text": "NR"}


__all__ = ["ModuleGenerator"]
