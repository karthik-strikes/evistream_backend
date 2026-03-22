"""
Helpers for building the canonical JSON structures used for form definitions.

Keeping this logic in one place makes it easier to debug and test how
forms are represented before they are sent to the DSPy code generator
or saved in the project store.
"""

from typing import Any, Dict, List, Optional


def build_field_definition(
    name: str,
    data_type: str,
    control_type: str,
    description: str,
    options: Optional[List[str]] = None,
    example: Optional[str] = None,
    extraction_hints: Optional[str] = None,
    subform_fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build the canonical JSON object for a single form field.

    Centralising this makes it easier to log / validate field structures.
    
    Args:
        name: Field name
        data_type: Field data type (text, number, enum, array, etc.)
        control_type: UI control type (text, dropdown, subform_table, etc.)
        description: Field description
        options: Optional list of options (for dropdowns/checkboxes)
        example: Optional example value
        extraction_hints: Optional hints for extraction
        subform_fields: Optional nested fields for subform/hierarchical data
    """
    cleaned_name = name.strip()
    cleaned_description = description.strip()

    field: Dict[str, Any] = {
        "field_name": cleaned_name,
        "field_type": data_type,
        "field_control_type": control_type,
        "field_description": cleaned_description,
    }
    if options:
        field["options"] = options
    if example:
        field["example"] = example.strip()
    if extraction_hints:
        field["extraction_hints"] = extraction_hints.strip()
    if subform_fields:
        field["subform_fields"] = subform_fields
    return field


def build_form_definition(
    name: str, description: str, fields: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Build the minimal form definition JSON passed into generate_task_from_form.
    """
    return {
        "form_name": name,
        "form_description": description,
        "fields": fields,
    }


def build_form_payload(
    name: str,
    description: str,
    fields: List[Dict[str, Any]],
    schema_name: str,
    task_dir: str,
) -> Dict[str, Any]:
    """
    Build the payload JSON that is stored with the project (Supabase or JSON).
    """
    return {
        "form_name": name,
        "form_description": description,
        "fields": fields,
        "schema_name": schema_name,
        "task_dir": task_dir,
    }
