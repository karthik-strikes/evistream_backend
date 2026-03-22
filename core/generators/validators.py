"""
Input validation for code generation.

Validates all external inputs (form_data from API) before processing to ensure
data quality and fail fast with clear error messages.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator


class FormFieldDefinition(BaseModel):
    """
    Validates a single form field definition from API.

    Ensures each field has required attributes and valid values.
    """

    field_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique name for this field"
    )

    field_description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Clear description of what this field extracts"
    )

    field_type: str = Field(
        ...,
        pattern="^(text|number|enum|object|array|boolean)$",
        description="Data type of this field"
    )

    field_control_type: Optional[str] = Field(
        None,
        description="UI control type (dropdown, checkbox_group_with_text, etc.)"
    )

    options: Optional[List[str]] = Field(
        None,
        description="Valid options for enum fields"
    )

    example: Optional[str] = Field(
        None,
        max_length=500,
        description="Example value for this field"
    )

    extraction_hints: Optional[str] = Field(
        None,
        max_length=1000,
        description="Hints for LLM about how to extract this field"
    )

    subform_fields: Optional[List['FormFieldDefinition']] = Field(
        None,
        description="Nested fields for object/array types"
    )

    @field_validator("options")
    @classmethod
    def validate_enum_options(cls, v: Optional[List[str]], info) -> Optional[List[str]]:
        """Validate that enum fields have options."""
        field_type = info.data.get("field_type")
        if field_type == "enum" and not v:
            raise ValueError("Enum fields must have at least one option")
        if v and len(v) < 1:
            raise ValueError("Enum options cannot be empty")
        return v

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, v: str) -> str:
        """Validate field name format."""
        # Check for valid Python identifier (will be used in generated code)
        if not v.replace("_", "").isalnum():
            raise ValueError(
                f"Field name '{v}' must be alphanumeric with underscores only"
            )
        if v[0].isdigit():
            raise ValueError(
                f"Field name '{v}' cannot start with a digit"
            )
        return v


class FormDataInput(BaseModel):
    """
    Validates complete form_data input from API before code generation.

    This is the main validation entry point for all form submissions.
    """

    form_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Name of the form"
    )

    form_description: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Description of what this form extracts"
    )

    fields: List[FormFieldDefinition] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of fields to extract"
    )

    @field_validator("fields")
    @classmethod
    def validate_unique_field_names(cls, v: List[FormFieldDefinition]) -> List[FormFieldDefinition]:
        """Ensure field names are unique."""
        field_names = [f.field_name for f in v]
        if len(field_names) != len(set(field_names)):
            # Find duplicates
            seen = set()
            duplicates = set()
            for name in field_names:
                if name in seen:
                    duplicates.add(name)
                seen.add(name)
            raise ValueError(
                f"Field names must be unique. Duplicates found: {', '.join(duplicates)}"
            )
        return v

    @field_validator("form_name")
    @classmethod
    def validate_form_name(cls, v: str) -> str:
        """Validate form name format."""
        # Remove leading/trailing whitespace
        v = v.strip()
        if not v:
            raise ValueError("Form name cannot be empty")
        return v


def validate_form_data(form_data: Dict[str, Any]) -> FormDataInput:
    """
    Validate form_data dictionary and return validated Pydantic model.

    Args:
        form_data: Raw form data from API

    Returns:
        Validated FormDataInput model

    Raises:
        ValidationError: If validation fails with detailed error messages
    """
    return FormDataInput(**form_data)


__all__ = [
    "FormFieldDefinition",
    "FormDataInput",
    "validate_form_data",
]
