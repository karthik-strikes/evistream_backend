"""
Signature Splicer — updates DSPy signature field definitions in schema_def JSON.

Editable: description, hints, rules, examples, options.
None = leave as-is.  [] = remove the key entirely.
"""

import copy
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def update_schema_def_field(
    schema_def: dict,
    *,
    signature_name: str,
    field_name: str,
    description: Optional[str] = None,
    hints: Optional[list] = None,
    rules: Optional[list] = None,
    examples: Optional[list] = None,
    options: Optional[list] = None,
) -> dict:
    """Update a single output field in schema_def and return the updated copy.

    signature_name must match schema_def["signatures"][i]["class_name"].
    None = leave the key as-is.  [] = remove the key.

    Raises ValueError if signature_name or field_name is not found.
    Bumps schema_def["version"] on success.
    """
    schema_def = copy.deepcopy(schema_def)
    for sig_def in schema_def.get("signatures", []):
        if sig_def.get("class_name") != signature_name:
            continue
        for out_field in sig_def.get("output_fields", []):
            if out_field.get("name") != field_name:
                continue
            _apply_update(out_field, "description", description)
            _apply_update(out_field, "hints", hints)
            _apply_update(out_field, "rules", rules)
            _apply_update(out_field, "examples", examples)
            _apply_update(out_field, "options", options)
            schema_def["version"] = schema_def.get("version", 1) + 1
            return schema_def
        raise ValueError(f"Field '{field_name}' not found in signature '{signature_name}'")
    raise ValueError(f"Signature '{signature_name}' not found in schema_def")


def signatures_consuming_field(schema_def: dict, field_name: str) -> list:
    """Return class_names of signatures that consume field_name as an input dependency."""
    producer = schema_def.get("field_to_signature_map", {}).get(field_name)
    consumers = []
    for stage in schema_def.get("pipeline_stages", []):
        if field_name in (stage.get("requires_fields") or []):
            consumers.extend(stage.get("signatures", []))
    return [s for s in consumers if s != producer]


def add_schema_def_field(
    schema_def: dict,
    *,
    target_signature_class: str,
    field_name: str,
    field_type_str: str,
    description: str,
    hints: list,
    rules: list,
    examples: list,
    options: list,
    source_grounded: bool = False,
) -> dict:
    """Add a new output_field to an existing signature in schema_def.

    Raises ValueError if the signature is not found or the field_name already exists.
    Bumps schema_def["version"] on success.
    """
    schema_def = copy.deepcopy(schema_def)

    target_sig = None
    for sig_def in schema_def.get("signatures", []):
        if sig_def.get("class_name") == target_signature_class:
            target_sig = sig_def
            break
    if target_sig is None:
        raise ValueError(f"Signature '{target_signature_class}' not found in schema_def")

    existing_names = {f.get("name") for f in target_sig.get("output_fields", [])}
    if field_name in existing_names:
        raise ValueError(f"Field '{field_name}' already exists in '{target_signature_class}'")

    out_field: dict = {
        "name": field_name,
        "type": field_type_str,
        "description": description,
        "source_grounded": source_grounded,
    }
    if hints:
        out_field["hints"] = hints
    if rules:
        out_field["rules"] = rules
    if examples:
        out_field["examples"] = examples
    if options:
        out_field["options"] = options

    target_sig.setdefault("output_fields", []).append(out_field)

    schema_def.setdefault("field_to_signature_map", {})[field_name] = target_signature_class

    default_fallback = {"value": "NR", "source_text": "NR"} if source_grounded else "NR"
    schema_def.setdefault("fallback_structures", {}).setdefault(
        target_signature_class, {}
    )[field_name] = default_fallback

    for stage in schema_def.get("pipeline_stages", []):
        if target_signature_class in stage.get("signatures", []):
            stage.setdefault("provides_fields", []).append(field_name)
            break

    schema_def["version"] = schema_def.get("version", 1) + 1
    return schema_def


def remove_schema_def_field(schema_def: dict, *, field_name: str) -> dict:
    """Remove an output_field from schema_def.

    Raises ValueError if the field has downstream consumers or is not found.
    Bumps schema_def["version"] on success.
    """
    consumers = signatures_consuming_field(schema_def, field_name)
    if consumers:
        raise ValueError(
            f"Field '{field_name}' is consumed by: {', '.join(consumers)}"
        )

    schema_def = copy.deepcopy(schema_def)
    ftm = schema_def.get("field_to_signature_map", {})
    producer_class = ftm.get(field_name)
    if not producer_class:
        raise ValueError(f"Field '{field_name}' not found in field_to_signature_map")

    for sig_def in schema_def.get("signatures", []):
        if sig_def.get("class_name") == producer_class:
            sig_def["output_fields"] = [
                f for f in sig_def.get("output_fields", []) if f.get("name") != field_name
            ]
            break

    ftm.pop(field_name, None)

    fs = schema_def.get("fallback_structures", {})
    if producer_class in fs and isinstance(fs[producer_class], dict):
        fs[producer_class].pop(field_name, None)

    for stage in schema_def.get("pipeline_stages", []):
        if field_name in stage.get("provides_fields", []):
            stage["provides_fields"] = [
                f for f in stage["provides_fields"] if f != field_name
            ]

    schema_def["version"] = schema_def.get("version", 1) + 1
    return schema_def


def update_schema_def_subfield(
    schema_def: dict,
    *,
    signature_name: str,
    field_name: str,
    subform_fields: list,
) -> dict:
    """Replace the entire subform_fields array for one output_field in schema_def.

    Finds the output field via signature_name + field_name. Replaces its
    subform_fields key with the provided list (removes key when list is empty).
    Bumps schema_def['version'] on success.

    Raises ValueError if signature_name or field_name is not found.

    Note: No per-column structural validation is performed here — callers must
    validate column names/types before calling. This function is a dumb array swap.
    Concurrent edit safety: caller should use OCC (schema_def->>version check) on
    the DB update; last-write-wins if OCC is skipped.
    """
    schema_def = copy.deepcopy(schema_def)
    for sig_def in schema_def.get("signatures", []):
        if sig_def.get("class_name") != signature_name:
            continue
        for out_field in sig_def.get("output_fields", []):
            if out_field.get("name") != field_name:
                continue
            if subform_fields:
                out_field["subform_fields"] = subform_fields
            else:
                out_field.pop("subform_fields", None)

            # Reconcile two-stage config with the new column set: a renamed or
            # deleted anchor column would otherwise leave anchor_columns
            # pointing at nothing and Stage 1 with zero columns to discover
            # rows with.
            if out_field.get("anchor_columns") is not None:
                new_names = [
                    sf.get("field_name") for sf in (subform_fields or [])
                    if isinstance(sf, dict) and sf.get("field_name")
                ]
                kept = [a for a in out_field["anchor_columns"] if a in set(new_names)]
                if not kept and new_names:
                    from core.generators.signature_gen import _auto_detect_anchors
                    kept = sorted(_auto_detect_anchors(subform_fields))
                    logger.warning(
                        "All anchor columns of '%s' were removed/renamed — "
                        "re-detected anchors: %s", field_name, kept,
                    )
                if not new_names or not kept or len(kept) >= len(new_names):
                    # Degenerate split (no columns, no anchors, or no value
                    # columns left) — two-stage cannot run; fall back.
                    out_field.pop("anchor_columns", None)
                    if out_field.get("extraction_strategy") == "row_then_columns":
                        out_field["extraction_strategy"] = "single_call"
                        logger.warning(
                            "Demoted '%s' to single_call — column edit left no "
                            "valid anchor/value split.", field_name,
                        )
                else:
                    out_field["anchor_columns"] = kept
                anchor_set = set(out_field.get("anchor_columns") or [])
                for sf in (subform_fields or []):
                    if isinstance(sf, dict):
                        sf["extraction_role"] = (
                            "anchor" if sf.get("field_name") in anchor_set else "value"
                        )

            schema_def["version"] = schema_def.get("version", 1) + 1
            return schema_def
        raise ValueError(f"Field '{field_name}' not found in signature '{signature_name}'")
    raise ValueError(f"Signature '{signature_name}' not found in schema_def")


def _apply_update(field_dict: dict, key: str, value) -> None:
    if value is None:
        return
    if value == [] or value == "":
        field_dict.pop(key, None)
    else:
        field_dict[key] = value


def add_decomposition_field(
    decomposition: dict,
    target_signature_class: str,
    field_name: str,
) -> dict:
    """Sync metadata.decomposition after add_schema_def_field.

    Appends field_name to decomposition.signatures[target].fields (dict key)
    and to the matching pipeline stage's provides_fields. Idempotent.
    """
    decomposition = copy.deepcopy(decomposition)
    for sig in decomposition.get("signatures", []):
        if sig.get("name") == target_signature_class or sig.get("class_name") == target_signature_class:
            fields = sig.setdefault("fields", {})
            if field_name not in fields:
                fields[field_name] = {"field_name": field_name}
            break
    for stage in decomposition.get("pipeline", []):
        if target_signature_class in (stage.get("signatures") or []):
            pf = stage.setdefault("provides_fields", [])
            if field_name not in pf:
                pf.append(field_name)
            break
    return decomposition


def remove_decomposition_field(decomposition: dict, field_name: str) -> dict:
    """Sync metadata.decomposition after remove_schema_def_field.

    Removes field_name from all signatures' fields dicts and from all
    pipeline stages' provides_fields lists. Idempotent.
    """
    decomposition = copy.deepcopy(decomposition)
    for sig in decomposition.get("signatures", []):
        sig.get("fields", {}).pop(field_name, None)
    for stage in decomposition.get("pipeline", []):
        pf = stage.get("provides_fields", [])
        if field_name in pf:
            stage["provides_fields"] = [f for f in pf if f != field_name]
    return decomposition
