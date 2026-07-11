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


def _grounded_example(ex_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Pilot examples with a blank source_text teach the model to skip
    citations — substitute the grounding-contract placeholder, mirroring
    runtime_builders._example_for_prompt."""
    try:
        from dspy_components.runtime_builders import _example_for_prompt
        return _example_for_prompt(ex_dict)
    except ImportError:
        return ex_dict


def augment_signature_with_feedback(
    sig_class: Type[dspy.Signature],
    field_examples: Dict[str, List[Dict[str, Any]]],
    field_instructions: Dict[str, str],
) -> Type[dspy.Signature]:
    """
    Create a new signature subclass with pilot calibration feedback appended
    to output field descs (runtime overlay — signatures.py on disk unchanged).

    Review-time edits (examples/hints/rules/description) are spliced directly
    into signatures.py at save time via signature_splicer.py, so they are
    already present in the base desc before this function runs.

    Args:
        sig_class: Original DSPy Signature class.
        field_examples: pilot calibration examples — {field_name: [{value, source_text, ...}, ...]}.
        field_instructions: pilot calibration instructions — {field_name: "..."}.

    Returns:
        A dynamically-created subclass with augmented field descriptors,
        or the original class if no feedback applies to its fields.
    """
    output_fields = getattr(sig_class, "output_fields", {})
    if not output_fields:
        return sig_class

    # Separate top-level field feedback from per-column subfield feedback.
    # Subfield keys are formatted as "parent_field.col_name".
    subfield_examples: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    subfield_instructions: Dict[str, Dict[str, str]] = {}
    top_field_examples: Dict[str, List[Dict[str, Any]]] = {}
    top_field_instructions: Dict[str, str] = {}
    for k, v in field_examples.items():
        if "." in k:
            parent, col = k.split(".", 1)
            subfield_examples.setdefault(parent, {})[col] = v
        else:
            top_field_examples[k] = v
    for k, v in field_instructions.items():
        if "." in k:
            parent, col = k.split(".", 1)
            subfield_instructions.setdefault(parent, {})[col] = v
        else:
            top_field_instructions[k] = v

    augmented_fields = {}

    for field_name, field_obj in output_fields.items():
        examples = top_field_examples.get(field_name, [])
        instructions = top_field_instructions.get(field_name, "")
        col_examples = subfield_examples.get(field_name, {})
        col_instructions = subfield_instructions.get(field_name, {})

        if not examples and not instructions and not col_examples and not col_instructions:
            continue

        existing_desc = _get_field_desc(field_obj)
        if existing_desc is None:
            continue

        # This loop only runs when some calibration content exists for the field.
        aug_parts = [
            "\n--- PILOT CALIBRATION (expert review of this project's papers — "
            "takes precedence over the generic guidance and examples above) ---"
        ]

        if instructions:
            aug_parts.append(f"\nAdditional Instructions (from pilot calibration):\n{instructions}")

        if examples:
            trimmed = examples[-MAX_EXAMPLES_PER_FIELD:]
            aug_parts.append("\nCalibration Examples (from pilot review):")
            for ex in trimmed:
                ex_dict = {"value": ex.get("value"), "source_text": ex.get("source_text", "")}
                aug_parts.append(json.dumps(_grounded_example(ex_dict)))

        # Per-column subfield calibration (row_then_columns table fields)
        for col_name, col_exs in col_examples.items():
            if col_exs:
                aug_parts.append(f"\nCalibration Examples — {col_name}:")
                for ex in col_exs[-MAX_EXAMPLES_PER_FIELD:]:
                    ex_dict = {"value": ex.get("value"), "source_text": ex.get("source_text", "")}
                    aug_parts.append(json.dumps(_grounded_example(ex_dict)))
        for col_name, col_instr in col_instructions.items():
            if col_instr:
                aug_parts.append(f"\nAdditional Instructions — {col_name}:\n{col_instr}")

        augmented_desc = existing_desc + "\n".join(aug_parts)

        # Preserve the original json_schema_extra (prefix, format, etc.) and only
        # overwrite the desc. Re-constructing with `dspy.OutputField(desc=...)`
        # alone drops any other metadata the generator put on the field.
        original_extra = getattr(field_obj, "json_schema_extra", None) or {}
        new_extra = {**original_extra, "desc": augmented_desc}
        new_extra.pop("__dspy_field_type", None)  # let OutputField re-stamp this
        augmented_fields[field_name] = dspy.OutputField(**new_extra)

        logger.debug(
            f"Augmented field '{field_name}' on {sig_class.__name__} with "
            f"{len(examples)} pilot ex, {len(instructions)} chars pilot instr"
        )

    if not augmented_fields:
        return sig_class

    new_class = type(
        f"{sig_class.__name__}_Augmented",
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
