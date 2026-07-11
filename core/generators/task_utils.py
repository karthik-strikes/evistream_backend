"""
Utility Functions for DSPy Code Generation

After Phase C+D, code generation no longer writes per-form Python files to
disk. Only name-sanitization helpers remain — pipeline classes are built
at runtime from forms.schema_def via dspy_components/runtime_builders.py.
"""

import re


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


__all__ = [
    "sanitize_form_name",
    "sanitize_field_key",
]
