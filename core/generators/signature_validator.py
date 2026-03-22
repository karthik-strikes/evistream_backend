"""
Signature Validation Module

Comprehensive validation for DSPy Signature code generation.
Performs checks on:
- Python syntax correctness
- DSPy signature structure compliance
- Field definitions and type hints
- Documentation completeness
- Field metadata alignment with specifications
"""

import ast
import logging
from typing import Tuple, List, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS - SYNTAX VALIDATION
# ============================================================================


def validate_python_syntax(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that code is syntactically correct Python.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    try:
        compile(code, "<string>", "exec")
        ast.parse(code)
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
    except Exception as e:
        errors.append(f"Parse error: {str(e)}")

    return len(errors) == 0, errors


def validate_imports(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that required imports are present.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    if "import dspy" not in code:
        errors.append("Missing required import: 'import dspy'")

    # If Dict[str, Any] is used, check for typing import
    if "Dict[str, Any]" in code and "from typing import" not in code and "import typing" not in code:
        errors.append(
            "Missing required import: 'from typing import Dict, Any' (needed for Dict[str, Any] type hints)")
    
    # If List[Dict[str, Any]] is used, check for List import
    if "List[Dict[str, Any]]" in code and "from typing import" not in code and "import typing" not in code:
        errors.append(
            "Missing required import: 'from typing import List, Dict, Any' (needed for List[Dict[str, Any]] type hints)")

    return len(errors) == 0, errors


# ============================================================================
# HELPER FUNCTIONS - STRUCTURE VALIDATION
# ============================================================================


def validate_class_structure(code: str) -> Tuple[bool, List[str]]:
    """
    Validate basic DSPy Signature class structure.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check class definition exists
    if "class " not in code:
        errors.append("Missing class definition")
        return False, errors

    # Check inheritance from dspy.Signature
    if "dspy.Signature" not in code:
        errors.append("Class must inherit from dspy.Signature")

    # Check for docstring
    if '"""' not in code and "'''" not in code:
        errors.append("Missing class docstring")

    return len(errors) == 0, errors


def validate_field_definitions(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that signature has proper field definitions.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    warnings = []

    # Check for InputField
    if "dspy.InputField" not in code:
        errors.append("Must have at least one dspy.InputField")

    # Check for OutputField
    if "dspy.OutputField" not in code:
        errors.append("Must have at least one dspy.OutputField")

    # Check for type hints (including Dict[str, Any] for source grounding and List[Dict[str, Any]] for subforms)
    has_type_hints = (
        ": str = dspy.InputField" in code or
        ": str = dspy.OutputField" in code or
        ": Dict[str, Any] = dspy.OutputField" in code or
        ": List[Dict[str, Any]] = dspy.OutputField" in code or
        ": int = dspy." in code or
        ": float = dspy." in code or
        ": bool = dspy." in code
    )
    if not has_type_hints:
        warnings.append(
            "Consider adding type hints (e.g., field_name: str = dspy.InputField(...) or field_name: Dict[str, Any] = dspy.OutputField(...))"
        )

    return len(errors) == 0, errors + warnings


def validate_field_descriptions(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that fields have proper descriptions with structure.
    Checks for source grounding documentation.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for output field documentation sections
    if "dspy.OutputField" in code:
        has_rules = "Rules:" in code or "rules" in code.lower()
        has_examples = "Examples:" in code or "examples" in code.lower()
        has_source_grounding = "Source Grounding:" in code or "source_text" in code.lower()

        if not has_rules:
            warnings.append(
                "Output field should include 'Rules:' section for validation"
            )

        if not has_examples:
            warnings.append(
                "Output field should include 'Examples:' section for clarity"
            )

        # Check for source grounding (new requirement)
        if "Dict[str, Any]" in code and not has_source_grounding:
            warnings.append(
                "Output fields with Dict[str, Any] type should include 'Source Grounding:' section"
            )

    return True, warnings


def validate_best_practices(code: str) -> Tuple[bool, List[str]]:
    """
    Check for DSPy best practices and conventions.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for "NR" convention
    if '"NR"' not in code and "'NR'" not in code:
        warnings.append(
            "Consider using 'NR' (Not Reported) convention for missing values"
        )

    # Check field descriptions use desc parameter
    if "dspy.InputField" in code or "dspy.OutputField" in code:
        if "desc=" not in code and "description=" not in code:
            warnings.append(
                "Field definitions should use desc= parameter for descriptions"
            )

    return True, warnings


# ============================================================================
# HELPER FUNCTIONS - METADATA VALIDATION
# ============================================================================


def validate_field_coverage(
    code: str,
    expected_fields: List[str]
) -> Tuple[bool, List[str]]:
    """
    Validate that all expected fields are present in generated code.

    Args:
        code: Generated signature code
        expected_fields: List of field names that should be in the signature

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    for field_name in expected_fields:
        if field_name not in code:
            errors.append(
                f"Expected field '{field_name}' not found in generated code"
            )

    return len(errors) == 0, errors


def validate_field_options(
    code: str,
    field_name: str,
    options: List[str]
) -> Tuple[bool, List[str]]:
    """
    Validate that field options are properly documented.

    Args:
        code: Generated signature code
        field_name: Name of the field
        options: List of valid options for the field

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    if not options:
        return True, warnings

    # Count how many options are mentioned
    options_found = sum(1 for opt in options if opt in code)

    if options_found == 0:
        warnings.append(
            f"Field '{field_name}' has options {options} but NONE are mentioned in code"
        )
    elif options_found < len(options):
        warnings.append(
            f"Field '{field_name}' has {len(options)} options but only {options_found} mentioned"
        )

    # Check for validation rule
    if "Must be one of" not in code and "one of:" not in code.lower():
        warnings.append(
            f"Field '{field_name}' with options should have 'Must be one of' validation rule"
        )

    return True, warnings


def validate_field_description_coverage(
    code: str,
    field_name: str,
    description: str
) -> Tuple[bool, List[str]]:
    """
    Validate that field description is represented in generated code.

    Args:
        code: Generated signature code
        field_name: Name of the field
        description: Expected description content

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    if not description:
        return True, warnings

    # Check if significant words from description appear in code
    desc_words = [w for w in description.lower().split() if len(w) > 4][:5]
    code_lower = code.lower()
    words_found = sum(1 for word in desc_words if word in code_lower)

    if desc_words and words_found < len(desc_words) // 2:
        warnings.append(
            f"Field '{field_name}' description not well represented in generated code"
        )

    return True, warnings


def validate_field_examples(
    code: str,
    field_name: str,
    example: str
) -> Tuple[bool, List[str]]:
    """
    Validate that field examples are included.
    Checks for both old format and new source grounding format.

    Args:
        code: Generated signature code
        field_name: Name of the field
        example: Expected example value

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    if not example:
        return True, warnings

    # Check for Examples section
    if "Examples:" not in code:
        warnings.append(
            f"Field '{field_name}' should have an 'Examples:' section"
        )
        return True, warnings

    # For source grounding format, check for "value" and "source_text" keys in examples
    if "Dict[str, Any]" in code:
        if '"value":' not in code and "'value':" not in code:
            warnings.append(
                f"Field '{field_name}' with source grounding should have examples with 'value' key"
            )
        if '"source_text":' not in code and "'source_text':" not in code:
            warnings.append(
                f"Field '{field_name}' with source grounding should have examples with 'source_text' key"
            )

    return True, warnings


def validate_extraction_hints(
    code: str,
    field_name: str,
    hints: str
) -> Tuple[bool, List[str]]:
    """
    Validate that extraction hints are mentioned in code.

    Args:
        code: Generated signature code
        field_name: Name of the field
        hints: Extraction hints text

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    if not hints:
        return True, warnings

    # Check if hints are mentioned
    hint_words = [w for w in hints.lower().split() if len(w) > 4][:3]
    code_lower = code.lower()
    hints_found = sum(1 for word in hint_words if word in code_lower)

    if hint_words and hints_found == 0:
        warnings.append(
            f"Field '{field_name}' has extraction hints but they're not mentioned in code"
        )

    return True, warnings


# ============================================================================
# MAIN VALIDATOR CLASS
# ============================================================================


class SignatureValidator:
    """
    Comprehensive validator for DSPy Signature code generation.

    Validates:
    - Python syntax correctness
    - DSPy signature structure
    - Field definitions and types
    - Documentation quality
    - Metadata compliance
    """

    def validate_signature(self, code: str) -> Tuple[bool, List[str]]:
        """
        Run comprehensive validation on generated signature code.

        Args:
            code: Generated DSPy signature code

        Returns:
            Tuple of (is_valid, list_of_errors_and_warnings)
        """
        all_issues = []

        # ====================================================================
        # VALIDATION 1: INPUT VALIDATION
        # ====================================================================
        if not isinstance(code, str):
            return False, [
                f"Invalid code type: expected str, got {type(code).__name__}"
            ]

        if not code or not code.strip():
            return False, ["Empty code generated"]

        # ====================================================================
        # VALIDATION 2: SYNTAX VALIDATION
        # ====================================================================
        syntax_valid, syntax_errors = validate_python_syntax(code)
        if not syntax_valid:
            all_issues.extend(syntax_errors)
            # If syntax is invalid, can't proceed with further checks
            return False, all_issues

        # ====================================================================
        # VALIDATION 3: IMPORT VALIDATION
        # ====================================================================
        imports_valid, import_errors = validate_imports(code)
        all_issues.extend(import_errors)

        # ====================================================================
        # VALIDATION 4: CLASS STRUCTURE VALIDATION
        # ====================================================================
        structure_valid, structure_errors = validate_class_structure(code)
        all_issues.extend(structure_errors)

        # ====================================================================
        # VALIDATION 5: FIELD DEFINITIONS VALIDATION
        # ====================================================================
        fields_valid, field_issues = validate_field_definitions(code)
        all_issues.extend(field_issues)

        # ====================================================================
        # VALIDATION 6: DOCUMENTATION VALIDATION (WARNINGS)
        # ====================================================================
        _, doc_warnings = validate_field_descriptions(code)
        all_issues.extend(doc_warnings)

        # ====================================================================
        # VALIDATION 7: BEST PRACTICES (WARNINGS)
        # ====================================================================
        _, practice_warnings = validate_best_practices(code)
        all_issues.extend(practice_warnings)

        # ====================================================================
        # FINAL RESULT
        # ====================================================================
        # Only critical errors make it invalid (not warnings)
        is_valid = (
            syntax_valid and
            imports_valid and
            structure_valid and
            fields_valid
        )

        if is_valid:
            logger.debug("✓ Signature validation passed")
        else:
            logger.warning(
                f"✗ Signature validation failed with {len(all_issues)} issues")

        return is_valid, all_issues

    def validate_field_metadata(
        self,
        code: str,
        questionnaire_spec: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        Validate that generated signature includes field metadata from spec.

        Args:
            code: Generated signature code
            questionnaire_spec: Original enriched questionnaire specification

        Returns:
            Tuple of (is_valid, list_of_warnings)
        """
        all_warnings = []

        output_structure = questionnaire_spec.get("output_structure", {})

        # Get list of expected fields
        expected_fields = list(output_structure.keys())

        # ====================================================================
        # VALIDATION 1: FIELD COVERAGE
        # ====================================================================
        coverage_valid, coverage_errors = validate_field_coverage(
            code, expected_fields
        )
        if not coverage_valid:
            all_warnings.extend(coverage_errors)

        # ====================================================================
        # VALIDATION 2: FIELD-SPECIFIC METADATA
        # ====================================================================
        for field_name, field_info in output_structure.items():
            # Skip if field_info is just a string (not enriched)
            if isinstance(field_info, str):
                continue

            # Skip if field not in code (already reported in coverage)
            if field_name not in code:
                continue

            # Check options
            if "options" in field_info and field_info["options"]:
                _, option_warnings = validate_field_options(
                    code, field_name, field_info["options"]
                )
                all_warnings.extend(option_warnings)

            # Check description
            if "description" in field_info and field_info["description"]:
                _, desc_warnings = validate_field_description_coverage(
                    code, field_name, field_info["description"]
                )
                all_warnings.extend(desc_warnings)

            # Check examples
            if "example" in field_info and field_info["example"]:
                _, example_warnings = validate_field_examples(
                    code, field_name, field_info["example"]
                )
                all_warnings.extend(example_warnings)

            # Check extraction hints
            if "extraction_hints" in field_info and field_info["extraction_hints"]:
                _, hint_warnings = validate_extraction_hints(
                    code, field_name, field_info["extraction_hints"]
                )
                all_warnings.extend(hint_warnings)

        # ====================================================================
        # FINAL RESULT
        # ====================================================================
        # Metadata validation produces warnings, not errors
        is_valid = True

        if all_warnings:
            logger.debug(
                f"Signature metadata validation: {len(all_warnings)} warnings")
        else:
            logger.debug("✓ Signature metadata validation passed")

        return is_valid, all_warnings


__all__ = [
    "SignatureValidator",
    "validate_python_syntax",
    "validate_class_structure",
    "validate_field_definitions",
]
