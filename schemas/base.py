from dataclasses import dataclass
from typing import Callable, Dict, List, Any


@dataclass(frozen=True)
class SchemaDefinition:
    """Configuration bundle describing a single systematic-review schema."""

    name: str
    description: str
    signature_class: Any
    output_field_name: str
    field_cache_file: str
    pipeline_factory: Callable[[], Any]
