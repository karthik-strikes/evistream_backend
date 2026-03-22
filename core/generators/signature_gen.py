"""
DSPy Signature Generator

Handles LLM-powered generation of DSPy signature classes using structured output.
"""

import json
from pathlib import Path
from typing import Dict, Any
from .signature_validator import SignatureValidator
from .models import SignatureGenerationState, SignatureSpec
from utils.lm_config import get_langchain_model
from config.models import CODEGEN_SIGNATURE_MODEL


class SignatureGenerator:
    """
    Generates DSPy Signature classes using LLM with validation.
    """

    def __init__(self, model_name: str = CODEGEN_SIGNATURE_MODEL):
        """
        Initialize signature generator.

        Args:
            model_name: LLM model identifier
        """
        self.model_name = model_name
        self.model = get_langchain_model(
            model_name, temperature=0.3, max_tokens=8000)
        self.validator = SignatureValidator()

    def _generate_spec_from_enriched_sig(
        self, enriched_sig: Dict[str, Any], validation_feedback: str = ""
    ) -> SignatureSpec:
        """
        Generate SignatureSpec using structured output (LLM → validated JSON).

        Args:
            enriched_sig: Enriched signature with name, fields dict, depends_on
            validation_feedback: Optional feedback from previous validation failures

        Returns:
            SignatureSpec Pydantic model
        """
        # Load prompt template
        template_path = Path(__file__).parent / \
            "prompts" / "signature_prompt.md"
        base_prompt = template_path.read_text(encoding="utf-8")

        prompt = base_prompt.replace(
            "[[ENRICHED_SIGNATURE_JSON]]",
            json.dumps(enriched_sig, indent=2)
        )

        if validation_feedback:
            prompt += f"\n\nVALIDATION FEEDBACK (FIX THESE):\n{validation_feedback}\n"

        try:
            structured_model = self.model.with_structured_output(
                SignatureSpec,
                method="json_schema"
            )
            spec = structured_model.invoke(prompt)
            if spec is None:
                raise ValueError("LLM returned None — structured output did not match SignatureSpec schema")
            return spec
        except Exception as e:
            raise ValueError(f"Structured output generation failed: {str(e)}")

    def _generate_code_from_spec(self, spec: SignatureSpec) -> str:
        """
        Generate Python code from SignatureSpec using templates.

        Args:
            spec: Validated SignatureSpec

        Returns:
            Complete Python code string
        """
        def safe_triple_quote_string(text: str) -> str:
            """
            Safely format text for use in triple-quoted Python strings.

            Handles two critical issues:
            1. Triple-quote sequences that would terminate the string early
            2. Trailing quotes that create problems when closing
            """
            # Replace triple-quotes with escaped version
            text = text.replace('"""', '\\"\\"\\"')
            text = text.replace("'''", "\\'\\'\\'")

            # Critical fix: If text ends with a quote, add space after it
            # This prevents four quotes in a row when closing: "..."" + """
            # We use a space instead of escaping for cleaner output
            if text.endswith('"'):
                text = text + ' '

            return text

        # Check if we need typing imports for Dict[str, Any] or List[Dict[str, Any]]
        needs_dict_import = any(
            "Dict[str, Any]" in field.field_type
            for field in spec.input_fields + spec.output_fields
        )
        needs_list_import = any(
            "List[Dict[str, Any]]" in field.field_type
            for field in spec.input_fields + spec.output_fields
        )

        # Start with imports and class definition
        code_lines = ["import dspy"]

        if needs_dict_import or needs_list_import:
            imports = []
            if needs_list_import:
                imports.append("List")
            if needs_dict_import or needs_list_import:
                imports.extend(["Dict", "Any"])
            code_lines.append(f"from typing import {', '.join(imports)}")

        code_lines.extend([
            "",
            "",
            f"class {spec.class_name}(dspy.Signature):",
            f'    """{safe_triple_quote_string(spec.class_docstring)}"""',
            ""
        ])

        # Add input fields
        for input_field in spec.input_fields:
            code_lines.append(
                f"    {input_field.field_name}: {input_field.field_type} = dspy.InputField(")
            code_lines.append(
                f'        desc="""{safe_triple_quote_string(input_field.description)}"""')
            code_lines.append("    )")
            code_lines.append("")

        # Add output fields
        for output_field in spec.output_fields:
            code_lines.append(
                f"    {output_field.field_name}: {output_field.field_type} = dspy.OutputField(")
            code_lines.append(
                f'        desc="""{safe_triple_quote_string(output_field.description)}"""')
            code_lines.append("    )")
            code_lines.append("")

        return "\n".join(code_lines)

    def generate_signature(
        self,
        enriched_sig: Dict[str, Any],
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """
        Generate a single DSPy signature with validation and retry.

        Args:
            enriched_sig: Enriched signature with name, fields dict, depends_on
            max_attempts: Maximum validation retry attempts

        Returns:
            dict with 'code', 'is_valid', 'attempts', 'errors', 'warnings'
        """
        validation_feedback = ""

        for attempt in range(max_attempts):
            try:
                # Generate using structured output approach (LLM → JSON → Code)
                spec = self._generate_spec_from_enriched_sig(
                    enriched_sig=enriched_sig,
                    validation_feedback=validation_feedback
                )
                code = self._generate_code_from_spec(spec)

                if attempt == 0:
                    print("\n--- Generated Signature Code ---")
                    print(code)
                    print("---\n")

                # Validate syntax and structure
                is_valid, errors = self.validator.validate_signature(code)

                questionnaire_spec = {
                    "fields_handled": list(enriched_sig.get("fields", {}).keys()),
                    "output_structure": enriched_sig.get("fields", {})
                }
                metadata_valid, metadata_warnings = self.validator.validate_field_metadata(
                    code, questionnaire_spec
                )

                # Combine warnings
                all_warnings = errors + metadata_warnings

                if is_valid and metadata_valid:
                    return {
                        "code": code,
                        "is_valid": True,
                        "attempts": attempt + 1,
                        "errors": [],
                        "warnings": all_warnings,
                    }

                print(f"  [Attempt {attempt + 1}/{max_attempts}] Validation failed:")
                for error in errors[:3]:
                    print(f"     • {error}")
                validation_feedback = "\n".join(errors)

            except Exception as e:
                error_msg = f"Generation failed: {str(e)}"
                print(f"  [Attempt {attempt + 1}/{max_attempts}] {error_msg}")
                if attempt == max_attempts - 1:
                    import traceback
                    traceback.print_exc()
                    return {
                        "code": "",
                        "is_valid": False,
                        "attempts": attempt + 1,
                        "errors": [error_msg],
                    }
                continue
        return {
            "code": code if 'code' in locals() else "",
            "is_valid": False,
            "attempts": max_attempts,
            "errors": errors if 'errors' in locals() else ["Max attempts reached"],
        }

    def assemble_signatures_file(
        self,
        signatures: list[Dict[str, Any]],
        task_name: str
    ) -> str:
        """
        Assemble complete signatures.py file from generated signature codes.
        Deduplicates imports by removing them from individual signatures.

        Args:
            signatures: List of signature dicts with 'code' and 'class_name' keys
            task_name: Task name for header comment

        Returns:
            Complete signatures.py file content
        """
        # Check if any signature uses Dict[str, Any] or List[Dict[str, Any]]
        needs_dict_import = any(
            "Dict[str, Any]" in sig["code"] for sig in signatures
        )
        needs_list_import = any(
            "List[Dict[str, Any]]" in sig["code"] for sig in signatures
        )

        # Add unified imports at top
        lines = ["import dspy"]

        if needs_dict_import or needs_list_import:
            imports = []
            if needs_list_import:
                imports.append("List")
            if needs_dict_import or needs_list_import:
                imports.extend(["Dict", "Any"])
            lines.append(f"from typing import {', '.join(imports)}")

        lines.extend([
            "",
            "",
            "# " + "=" * 76,
            f"# SIGNATURES - {task_name.upper().replace('_', ' ')}",
            "# " + "=" * 76,
            "",
            "",
        ])

        # Add signatures, stripping their individual imports
        for sig in signatures:
            code = sig["code"]

            # Remove import lines from individual signatures
            code_lines = code.split("\n")
            filtered_lines = []
            for line in code_lines:
                # Skip import lines
                if line.strip().startswith("import ") or line.strip().startswith("from "):
                    continue
                filtered_lines.append(line)

            # Remove leading empty lines
            while filtered_lines and not filtered_lines[0].strip():
                filtered_lines.pop(0)

            cleaned_code = "\n".join(filtered_lines)
            lines.append(cleaned_code)
            lines.append("\n\n")

        # Add __all__ export
        class_names = [sig["class_name"] for sig in signatures]
        lines.extend(["", "", "__all__ = ["])
        for name in class_names:
            lines.append(f'    "{name}",')
        lines.append("]")

        return "\n".join(lines)


__all__ = ["SignatureGenerator"]
