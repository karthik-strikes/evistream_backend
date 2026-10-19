"""
Schema Runtime Builder

Builds runtime components (pipeline) from DynamicSchemaConfig.
"""

from dataclasses import dataclass
from typing import Any
from .config import DynamicSchemaConfig


@dataclass
class SchemaRuntime:
    """
    Runtime components for a schema.

    Contains the pipeline that executes extraction following
    the decomposition pipeline structure.
    """
    config: DynamicSchemaConfig
    pipeline: Any

    def close(self) -> None:
        """Cleanup if needed."""
        pass


def build_runtime(config: DynamicSchemaConfig, pilot_feedback=None) -> SchemaRuntime:
    """
    Build runtime from DynamicSchemaConfig.

    Creates pipeline that follows the pipeline_stages structure
    from decomposition, respecting dependencies and execution order.

    Args:
        config: DynamicSchemaConfig with pipeline structure
        pilot_feedback: Optional dict with 'field_examples' and 'field_instructions'

    Returns:
        SchemaRuntime with configured pipeline
    """
    pipeline = config.build_pipeline(pilot_feedback=pilot_feedback)
    return SchemaRuntime(
        config=config,
        pipeline=pipeline
    )
