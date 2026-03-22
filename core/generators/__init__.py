"""
DSPy Signature & Module Generator Package

Modular architecture for generating DSPy signatures, modules, and pipelines
from form specifications using LLM-powered cognitive decomposition.

Architecture:
- models: Pydantic models and TypedDict states
- validators: Code validation utilities (signature/module)
- decomposition_validator: Decomposition validation utilities
- workflow_validator: Generated code validation (syntax/semantic/flow)
- human_review: Human-in-the-loop review and approval
- signature_gen: Signature generation with LLM
- module_gen: Module generation with LLM
- decomposition: Cognitive form decomposition
- workflow: LangGraph workflow orchestration
- utils: Helper functions and entry points

Usage:
    from core.generators import DSPySignatureGenerator, generate_task_from_form
    
    generator = DSPySignatureGenerator()
    result = generator.generate_complete_task(form_data, task_name)
"""

from .models import (
    SignatureGenerationState,
    CompleteTaskGenerationState,
    FormDecomposition,
    CognitiveBehavior,
    QuestionnaireSpec,
    FieldMapping,
    AtomicSignature,
    PipelineStage,
    PipelineFlow,
    CombinerSignature,
    DecompositionValidation,
    InputFieldSpec,
    OutputFieldSpec,
    SignatureSpec,
)

from .signature_validator import SignatureValidator
from .module_validator import ModuleValidator
from .decomposition_validator import DecompositionValidator
from .human_review import HumanReviewHandler

from .signature_gen import SignatureGenerator

from .module_gen import ModuleGenerator

from .workflow import WorkflowOrchestrator

from .task_utils import (
    generate_task_from_form,
    create_task_directory,
    load_dynamic_schemas,
    register_dynamic_schema,
    sanitize_form_name,
    sanitize_field_key,
)

from config.models import CODEGEN_DECOMPOSITION_MODEL


class DSPySignatureGenerator:
    """
    Main class for DSPy signature and module generation.

    This is the primary entry point that coordinates all generation components.
    It maintains backward compatibility with the original monolithic class.
    """

    def __init__(
        self,
        model_name: str = CODEGEN_DECOMPOSITION_MODEL,
        enable_human_review: bool = False,
    ):
        """
        Initialize DSPy signature generator.

        Args:
            model_name: LLM model identifier
            enable_human_review: Enable human-in-the-loop review step
        """
        self.model_name = model_name
        self.enable_human_review = enable_human_review

        # Initialize component generators
        self.sig_gen = SignatureGenerator(model_name)
        self.mod_gen = ModuleGenerator(model_name)

        # Initialize workflow orchestrator
        self.workflow = WorkflowOrchestrator(
            signature_gen=self.sig_gen,
            module_gen=self.mod_gen,
            model_name=model_name,
            human_review_enabled=enable_human_review,
        )

    def generate_signature(
        self,
        questionnaire_spec: dict,
        max_attempts: int = 3,
        thread_id: str = "default",
    ) -> dict:
        """
        Generate a single DSPy signature using LLM.

        Args:
            questionnaire_spec: Specification with class_name, fields, output_structure, etc.
            max_attempts: Maximum validation retry attempts
            thread_id: Thread ID for workflow state persistence

        Returns:
            dict with 'code', 'is_valid', 'attempts', 'errors', 'warnings'
        """
        return self.sig_gen.generate_signature(questionnaire_spec, max_attempts)

    def generate_module(
        self,
        signature_class_name: str,
        output_field_name: str,
        fallback_structure: dict,
        max_attempts: int = 3,
    ) -> dict:
        """
        Generate an async DSPy module that wraps a signature.

        Args:
            signature_class_name: Name of the signature class
            output_field_name: Name of output field in signature
            fallback_structure: Default structure for error recovery
            max_attempts: Maximum validation attempts

        Returns:
            dict with 'code', 'is_valid', 'attempts', 'errors'
        """
        return self.mod_gen.generate_module(
            signature_class_name, output_field_name, fallback_structure, max_attempts
        )

    def generate_complete_task(
        self, form_data: dict, task_name: str = None
    ) -> dict:
        """
        Generate complete task with cognitive decomposition workflow.

        Args:
            form_data: Form specification with name, description, fields
            task_name: Optional task name for the generated code

        Returns:
            dict with 'success', 'signatures_file', 'modules_file', 'field_mapping', etc.
        """
        return self.workflow.generate_complete_task(form_data, task_name)

    def approve_decomposition(self, thread_id: str = "default") -> dict:
        """
        Approve decomposition and continue workflow.

        Args:
            thread_id: Thread ID of the paused workflow

        Returns:
            Final result dict
        """
        return self.workflow.approve_decomposition(thread_id)

    def reject_decomposition(self, feedback: str, thread_id: str = "default") -> dict:
        """
        Reject decomposition with feedback for refinement.

        Args:
            feedback: Human feedback explaining what needs to change
            thread_id: Thread ID of the paused workflow

        Returns:
            Result dict (may be paused again for another review)
        """
        return self.workflow.reject_decomposition(feedback, thread_id)


__all__ = [
    # Main class
    "DSPySignatureGenerator",

    # Component classes
    "SignatureGenerator",
    "ModuleGenerator",
    "WorkflowOrchestrator",
    "SignatureValidator",
    "ModuleValidator",
    "DecompositionValidator",
    "HumanReviewHandler",

    # Functions
    "generate_task_from_form",
    "load_dynamic_schemas",

    # Models (for structured output / Pydantic validation)
    "SignatureGenerationState",
    "CompleteTaskGenerationState",
    "FormDecomposition",
    "CognitiveBehavior",
    "QuestionnaireSpec",
    "FieldMapping",
    "AtomicSignature",
    "PipelineStage",
    "PipelineFlow",
    "CombinerSignature",
    "DecompositionValidation",
    "InputFieldSpec",
    "OutputFieldSpec",
    "SignatureSpec",
]
