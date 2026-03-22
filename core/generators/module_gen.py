"""
DSPy Module Generator

Handles template-based generation of DSPy Module classes that wrap signatures.
Modules are pure boilerplate - no LLM needed!
"""

import json
from typing import Dict, Any
from .module_validator import ModuleValidator
from config.models import CODEGEN_MODULE_MODEL


class ModuleGenerator:
    """
    Generates async DSPy Module classes using deterministic templates.
    Fast, reliable, and cost-free!
    """

    def __init__(self, model_name: str = CODEGEN_MODULE_MODEL):
        """
        Initialize module generator.

        Args:
            model_name: Kept for backward compatibility (not used)
        """
        self.validator = ModuleValidator()

    def generate_module_code(
        self,
        signature_class_name: str,
        fallback_structure: Dict[str, Any],
        requires_fields: list = None,
    ) -> str:
        """Generate async DSPy Module using deterministic template (no LLM needed).

        Args:
            signature_class_name: DSPy signature class name
            fallback_structure: Default return value structure on error
            requires_fields: Upstream field names this module depends on.
                If non-empty, generates explicit field extraction code so that
                field names are correctly remapped from accumulated pipeline results.
        """
        if requires_fields is None:
            requires_fields = []

        module_class_name = f"Async{signature_class_name}Extractor"
        field_names = list(fallback_structure.keys())
        fallback_json = json.dumps(fallback_structure, indent=12)

        field_extraction = "{\n"
        for field in field_names:
            if fallback_structure[field] == []:
                field_extraction += f'            "{field}": outputs.get("{field}", []),\n'
            else:
                field_extraction += f'            "{field}": outputs.get("{field}", {{"value": "NR", "source_text": "NR"}}),\n'
        field_extraction += "        }"

        if requires_fields:
            # Downstream module: explicitly extract and remap upstream fields.
            # This ensures field names match DSPy InputField declarations exactly.
            upstream_extractions = ""
            for field in requires_fields:
                upstream_extractions += f'        {field} = kwargs.get("{field}", "NR")\n'

            upstream_call_args = ""
            for field in requires_fields:
                upstream_call_args += (
                    f"                {field}=json.dumps({field}) "
                    f"if isinstance({field}, dict) else str({field}),\n"
                )

            call_body = f"""            outputs = await async_dspy_forward(
                self.extract,
                markdown_content=markdown_content,
{upstream_call_args}            )
"""
        else:
            # Independent module: pass kwargs through directly
            upstream_extractions = ""
            call_body = """            outputs = await async_dspy_forward(self.extract, markdown_content=markdown_content, **kwargs)
"""

        code = f'''


class {module_class_name}(dspy.Module):
    """Async module to extract data using {signature_class_name} signature."""

    def __init__(self):
        super().__init__()
        self.extract = dspy.ChainOfThought({signature_class_name})

    async def __call__(self, markdown_content: str, **kwargs) -> Dict[str, Any]:
        """
        Extract data from markdown content.

        Args:
            markdown_content: Full markdown text to extract from
            **kwargs: Additional context fields from upstream pipeline stages

        Returns:
            Dict with extracted field values
        """
{upstream_extractions}
        try:
{call_body}
            # Extract all output fields from result
            return {field_extraction}
        except Exception as e:
            print(f"Error in {module_class_name}: {{e}}")
            # Return fallback structure with default values
            return {fallback_json}
'''

        return code

    def generate_module(
        self,
        signature_class_name: str,
        output_field_name: str,
        fallback_structure: Dict[str, Any],
        max_attempts: int = 1,
        requires_fields: list = None,
    ) -> Dict[str, Any]:
        """Generate async DSPy module using deterministic template."""
        try:
            code = self.generate_module_code(
                signature_class_name=signature_class_name,
                fallback_structure=fallback_structure,
                requires_fields=requires_fields or [],
            )

            print("\n--- Generated Module Code ---")
            print(code)
            print("---\n")

            is_valid, issues = self.validator.validate_module(code)

            import logging
            _logger = logging.getLogger(__name__)
            _logger.info(f"Module validation for {signature_class_name}: is_valid={is_valid}, issues={issues}")
            if not is_valid:
                _logger.error(f"Module validation FAILED for {signature_class_name}: {issues}")
                _logger.error(f"Generated code:\n{code}")

            return {
                "code": code,
                "is_valid": is_valid,
                "attempts": 1,
                "errors": [] if is_valid else issues,
                "warnings": issues if is_valid else [],
            }

        except Exception as e:
            return {
                "code": "",
                "is_valid": False,
                "attempts": 1,
                "errors": [f"Template generation failed: {str(e)}"],
            }

    def create_fallback_structure(self, enriched_sig: Dict[str, Any]) -> Dict[str, Any]:
        """Create fallback structure for error recovery."""
        fields_metadata = enriched_sig.get("fields", {})

        if isinstance(fields_metadata, dict):
            fallback = {}
            for field_name, field_meta in fields_metadata.items():
                field_type = field_meta.get("field_type", "text")
                if field_type == "array":
                    fallback[field_name] = []
                else:
                    fallback[field_name] = {"value": "NR", "source_text": "NR"}
            return fallback
        return {"value": "NR", "source_text": "NR"}

    def assemble_modules_file(
        self,
        modules: list[str],
        task_name: str,
        signature_class_names: list[str] = None
    ) -> str:
        """
        Assemble complete modules.py file from generated module codes.

        Args:
            modules: List of module code strings
            task_name: Task name for imports and header
            signature_class_names: List of signature class names to import

        Returns:
            Complete modules.py file content
        """
        lines = [
            "import json",
            "from typing import Dict, Any, List",
            "",
            "import dspy",
            "",
            "from utils.dspy_async import async_dspy_forward",
            "from utils.json_parser import safe_json_parse",
        ]

        # Add signature imports
        if signature_class_names:
            lines.append(
                f"from dspy_components.tasks.{task_name}.signatures import (")
            for sig_name in signature_class_names:
                lines.append(f"    {sig_name},")
            lines.append(")")
        else:
            # Fallback to placeholder if no names provided
            lines.extend([
                f"from dspy_components.tasks.{task_name}.signatures import (",
                "    # Import signatures here",
                ")",
            ])

        lines.extend([
            "",
            "",
            "# " + "=" * 76,
            f"# MODULES - {task_name.upper().replace('_', ' ')}",
            "# " + "=" * 76,
            "",
            "",
        ])

        for module_code in modules:
            lines.append(module_code)
            lines.append("\n\n")

        # Extract module class names and add __all__ export
        import re
        module_class_names = []
        full_modules_code = "\n".join(modules)
        class_pattern = r'class\s+(\w+)\s*\('
        for match in re.finditer(class_pattern, full_modules_code):
            class_name = match.group(1)
            module_class_names.append(class_name)

        if module_class_names:
            lines.extend(["", "", "__all__ = ["])
            for name in module_class_names:
                lines.append(f'    "{name}",')
            lines.append("]")

        return "\n".join(lines)


__all__ = ["ModuleGenerator"]
