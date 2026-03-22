"""
Module Validation Module

Comprehensive validation for DSPy Module code generation.
Performs checks on:
- Python syntax correctness
- DSPy module structure compliance
- Method definitions (forward/__call__)
- Error handling and async patterns
- Proper use of DSPy wrappers (ChainOfThought, Predict)
"""

import ast
import logging
from typing import Tuple, List

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
    Validate that required imports are present and correct.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors_and_warnings)
    """
    errors = []
    warnings = []

    # Check for dspy import
    # if "import dspy" not in code:
    #     errors.append("Missing required import: 'import dspy'")

    # Check for async imports if using async
    if "async def" in code and "asyncio" not in code:
        warnings.append(
            "Module uses async methods but doesn't import asyncio"
        )

    # Check for asyncio import without async usage
    if "asyncio" in code and "async def" not in code:
        errors.append("Imports asyncio but has no async methods")

    return len(errors) == 0, errors + warnings


# ============================================================================
# HELPER FUNCTIONS - STRUCTURE VALIDATION
# ============================================================================


def validate_class_structure(code: str) -> Tuple[bool, List[str]]:
    """
    Validate basic DSPy Module class structure.

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

    # Check inheritance from dspy.Module
    if "dspy.Module" not in code:
        errors.append("Class must inherit from dspy.Module")

    # Check for docstring
    if '"""' not in code and "'''" not in code:
        errors.append("Missing class docstring")

    return len(errors) == 0, errors


def validate_method_definitions(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that module has proper method definitions.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check for __call__ or forward method
    has_call = "def __call__" in code or "async def __call__" in code
    has_forward = "def forward" in code or "async def forward" in code

    if not (has_call or has_forward):
        errors.append(
            "Module must implement either __call__ or forward method"
        )

    return len(errors) == 0, errors


def validate_dspy_wrappers(code: str) -> Tuple[bool, List[str]]:
    """
    Validate proper use of DSPy prediction wrappers.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check for ChainOfThought or Predict
    has_cot = "ChainOfThought" in code
    has_predict = "Predict" in code

    if not (has_cot or has_predict):
        errors.append(
            "Module must use either ChainOfThought or Predict wrapper"
        )

    return len(errors) == 0, errors


# ============================================================================
# HELPER FUNCTIONS - BEST PRACTICES VALIDATION
# ============================================================================


def validate_error_handling(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that module has proper error handling.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for try/except blocks
    has_try = "try:" in code
    has_except = "except" in code

    if not (has_try and has_except):
        warnings.append(
            "Module should include error handling with try/except blocks"
        )

    return True, warnings


def validate_fallback_handling(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that module has fallback structure for errors.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for fallback mention
    if "fallback" not in code.lower():
        warnings.append(
            "Module should define fallback values for error cases"
        )

    return True, warnings


def validate_async_patterns(code: str) -> Tuple[bool, List[str]]:
    """
    Validate proper async/await patterns if used.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    errors = []
    warnings = []

    # Reject deprecated run_in_executor pattern
    if "run_in_executor" in code:
        errors.append(
            "CRITICAL: Module uses deprecated run_in_executor — use async_dspy_forward instead"
        )

    # Require async_dspy_forward for async modules
    if "async def __call__" in code and "async_dspy_forward" not in code:
        errors.append(
            "Async __call__ must use async_dspy_forward for DSPy calls"
        )

    # Note: import check (from utils.dspy_async import async_dspy_forward) is skipped here
    # because individual module snippets don't include imports — they're added during
    # file assembly in module_gen.assemble_modules_file()

    # If using async def, check for await
    if "async def" in code:
        if "await" not in code:
            warnings.append(
                "Async method defined but no await statements found"
            )

    # If using await, check for async def
    if "await" in code and "async def" not in code:
        warnings.append(
            "Using await but method is not defined as async"
        )

    is_valid = len(errors) == 0
    return is_valid, errors + warnings


def validate_initialization(code: str) -> Tuple[bool, List[str]]:
    """
    Validate proper module initialization.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for __init__ method
    if "__init__" not in code:
        warnings.append(
            "Module should have __init__ method to initialize signature"
        )
    else:
        # Check if signature is assigned
        if "self.signature" not in code and "self.predictor" not in code:
            warnings.append(
                "Module should assign signature/predictor in __init__"
            )

    return True, warnings


def validate_return_statement(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that methods have return statements.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for return statement in __call__ or forward
    if ("def __call__" in code or "def forward" in code):
        if "return" not in code:
            warnings.append(
                "Module method should have return statement"
            )

    return True, warnings


def validate_logging(code: str) -> Tuple[bool, List[str]]:
    """
    Validate that module includes logging for debugging.

    Args:
        code: Python code string

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []

    # Check for logging usage
    has_logger = "logger" in code or "logging" in code
    has_print = "print(" in code

    if not (has_logger or has_print):
        warnings.append(
            "Module should include logging or print statements for debugging"
        )

    return True, warnings


# ============================================================================
# MAIN VALIDATOR CLASS
# ============================================================================


class ModuleValidator:
    """
    Comprehensive validator for DSPy Module code generation.

    Validates:
    - Python syntax correctness
    - DSPy module structure
    - Method definitions
    - Error handling patterns
    - Async usage
    - Best practices
    """

    def validate_module(self, code: str) -> Tuple[bool, List[str]]:
        """
        Run comprehensive validation on generated module code.

        Args:
            code: Generated DSPy module code

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
        imports_valid, import_issues = validate_imports(code)
        all_issues.extend(import_issues)

        # ====================================================================
        # VALIDATION 4: CLASS STRUCTURE VALIDATION
        # ====================================================================
        structure_valid, structure_errors = validate_class_structure(code)
        all_issues.extend(structure_errors)

        # ====================================================================
        # VALIDATION 5: METHOD DEFINITIONS VALIDATION
        # ====================================================================
        methods_valid, method_errors = validate_method_definitions(code)
        all_issues.extend(method_errors)

        # ====================================================================
        # VALIDATION 6: DSPY WRAPPERS VALIDATION
        # ====================================================================
        wrappers_valid, wrapper_errors = validate_dspy_wrappers(code)
        all_issues.extend(wrapper_errors)

        # ====================================================================
        # VALIDATION 7: ERROR HANDLING (WARNINGS)
        # ====================================================================
        _, error_warnings = validate_error_handling(code)
        all_issues.extend(error_warnings)

        # ====================================================================
        # VALIDATION 8: FALLBACK HANDLING (WARNINGS)
        # ====================================================================
        _, fallback_warnings = validate_fallback_handling(code)
        all_issues.extend(fallback_warnings)

        # ====================================================================
        # VALIDATION 9: ASYNC PATTERNS (ERRORS + WARNINGS)
        # ====================================================================
        async_valid, async_issues = validate_async_patterns(code)
        all_issues.extend(async_issues)

        # ====================================================================
        # VALIDATION 10: INITIALIZATION (WARNINGS)
        # ====================================================================
        _, init_warnings = validate_initialization(code)
        all_issues.extend(init_warnings)

        # ====================================================================
        # VALIDATION 11: RETURN STATEMENT (WARNINGS)
        # ====================================================================
        _, return_warnings = validate_return_statement(code)
        all_issues.extend(return_warnings)

        # ====================================================================
        # VALIDATION 12: LOGGING (WARNINGS)
        # ====================================================================
        _, logging_warnings = validate_logging(code)
        all_issues.extend(logging_warnings)

        # ====================================================================
        # FINAL RESULT
        # ====================================================================
        # Only critical errors make it invalid (not warnings)
        is_valid = (
            syntax_valid and
            imports_valid and
            structure_valid and
            methods_valid and
            wrappers_valid and
            async_valid
        )

        if is_valid:
            logger.debug("✓ Module validation passed")
        else:
            logger.warning(
                f"✗ Module validation failed with {len(all_issues)} issues")

        return is_valid, all_issues


__all__ = [
    "ModuleValidator",
    "validate_python_syntax",
    "validate_class_structure",
    "validate_method_definitions",
    "validate_dspy_wrappers",
]
