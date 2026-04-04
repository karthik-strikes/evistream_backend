"""
Pilot feedback injection for extraction signatures.

Dynamically subclasses DSPy signature classes to append calibration
examples and additional instructions from pilot review feedback into
field descriptors. The original signature files on disk are never modified.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Type

import dspy

logger = logging.getLogger(__name__)

# Maximum examples per field to keep prompts from getting too long
MAX_EXAMPLES_PER_FIELD = 5


def augment_signature_with_feedback(
    sig_class: Type[dspy.Signature],
    field_examples: Dict[str, List[Dict[str, Any]]],
    field_instructions: Dict[str, str],
) -> Type[dspy.Signature]:
    """
    Create a new signature subclass with pilot feedback appended to output field descs.

    Args:
        sig_class: Original DSPy Signature class
        field_examples: {field_name: [{value, source_text, note?, ...}, ...]}
        field_instructions: {field_name: "additional instruction text"}

    Returns:
        A dynamically-created subclass with augmented field descriptors,
        or the original class if no feedback applies to its fields.
    """
    output_fields = getattr(sig_class, "output_fields", {})
    if not output_fields:
        return sig_class

    augmented_fields = {}

    for field_name, field_obj in output_fields.items():
        examples = field_examples.get(field_name, [])
        instructions = field_instructions.get(field_name, "")

        if not examples and not instructions:
            continue

        # Get the existing desc from the field
        existing_desc = _get_field_desc(field_obj)
        if existing_desc is None:
            continue

        # Build augmentation text
        aug_parts = []

        if instructions:
            aug_parts.append(f"\nAdditional Instructions (from pilot calibration):\n{instructions}")

        if examples:
            # Keep only the most recent examples, capped at MAX_EXAMPLES_PER_FIELD
            trimmed = examples[-MAX_EXAMPLES_PER_FIELD:]
            aug_parts.append("\nCalibration Examples (from pilot review):")
            for ex in trimmed:
                ex_dict = {"value": ex.get("value"), "source_text": ex.get("source_text", "")}
                aug_parts.append(json.dumps(ex_dict))

        augmented_desc = existing_desc + "\n".join(aug_parts)
        augmented_fields[field_name] = dspy.OutputField(desc=augmented_desc)

        logger.debug(
            f"Augmented field '{field_name}' on {sig_class.__name__} with "
            f"{len(examples)} examples, {len(instructions)} chars of instructions"
        )

    if not augmented_fields:
        return sig_class

    # Create a subclass with the augmented fields
    new_class = type(
        f"{sig_class.__name__}_PilotAugmented",
        (sig_class,),
        augmented_fields,
    )

    logger.info(
        f"Created augmented signature {new_class.__name__} with "
        f"{len(augmented_fields)} augmented fields"
    )
    return new_class


def _get_field_desc(field_obj: Any) -> Optional[str]:
    """Extract the desc string from a DSPy field object."""
    # DSPy stores field metadata in json_schema_extra
    if hasattr(field_obj, "json_schema_extra") and isinstance(field_obj.json_schema_extra, dict):
        return field_obj.json_schema_extra.get("desc")
    return None


def build_signature_feedback_map(
    sig_class: Type[dspy.Signature],
    field_examples: Dict[str, List[Dict[str, Any]]],
    field_instructions: Dict[str, str],
) -> bool:
    """Check if a signature class has any output fields that match the feedback."""
    output_fields = getattr(sig_class, "output_fields", {})
    for field_name in output_fields:
        if field_name in field_examples or field_name in field_instructions:
            return True
    return False
