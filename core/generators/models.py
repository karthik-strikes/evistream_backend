"""
Data Models for DSPy Generator

Contains TypedDict state definitions and Pydantic models for:
- Workflow states (LangGraph)
- Cognitive decomposition structures
- Validation schemas
"""

from typing import TypedDict, Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field


# ============================================================================
# STATE DEFINITIONS FOR LANGGRAPH WORKFLOWS
# ============================================================================


class SignatureGenerationState(TypedDict):
    """State for the signature generation workflow"""

    # Input
    questionnaire_spec: Dict[str, Any]
    max_attempts: int

    # Workflow state
    code: str
    validation_feedback: str
    attempt: int
    errors: List[str]
    warnings: List[str]
    is_valid: bool

    # Human-in-the-loop
    human_feedback: Optional[str]
    needs_human_review: bool

    # Output
    result: Optional[Dict[str, Any]]
    status: str  # "in_progress", "completed", "failed", "needs_review"


class CompleteTaskGenerationState(TypedDict):
    """State for the complete task generation workflow with cognitive decomposition"""

    # Input
    form_data: Dict[str, Any]
    task_name: str
    thread_id: str
    max_attempts: int

    # Decomposition stage
    decomposition: Optional[Dict[str, Any]]
    decomposition_valid: bool
    decomposition_feedback: str

    # Generation stage
    signatures_code: List[Dict[str, Any]]
    modules_code: List[str]

    # Field mapping
    field_to_signature_map: Dict[str, Dict[str, str]]

    # Workflow control
    current_stage: str
    attempt: int
    errors: List[str]
    warnings: List[str]

    # Human-in-the-loop fields
    human_review_enabled: bool
    human_feedback: Optional[str]
    human_approved: bool
    decomposition_summary: Optional[str]

    # Output
    result: Optional[Dict[str, Any]]
    status: str  # "in_progress", "completed", "failed", "needs_refinement"


# ============================================================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUT (LangChain Integration)
# ============================================================================
# Reference: https://docs.langchain.com/oss/python/langchain/structured-output


class CognitiveBehavior(BaseModel):
    """Cognitive behavior definition for a signature"""
    reasoning_pattern: Literal[
        "classification", "extraction", "transformation",
        "interpretation", "validation", "aggregation"
    ] = Field(description="The type of reasoning this signature performs")
    output_schema_type: Literal[
        "text", "number", "json", "list", "boolean", "enum"] = Field(description="The type of output schema")
    processing_rules: List[str] = Field(
        description="List of processing rules applied (e.g., 'direct_lookup', 'conditional_logic')"
    )


class QuestionnaireSpec(BaseModel):
    """DSPy questionnaire specification"""
    class_name: str = Field(description="Python class name for the signature")
    form_question: str = Field(
        description="The question this signature answers")
    description: str = Field(
        description="Detailed description of what this extracts")
    output_structure: Dict[str, Any] = Field(
        description="Output schema structure")
    output_field_name: str = Field(description="Name of the output field")
    requires_context: bool = Field(
        default=False, description="Whether this requires context from other signatures")
    context_fields: List[str] = Field(
        default_factory=list, description="List of context field names required")


class FieldMapping(BaseModel):
    """Mapping of form field to signature output"""
    signature: str = Field(
        description="Name of the signature handling this field")
    output_field: str = Field(description="Output field name in the signature")
    json_path: str = Field(description="JSON path in the output structure")
    cognitive_behavior: str = Field(
        description="Brief description of the cognitive behavior")


class AtomicSignature(BaseModel):
    """Specification for an atomic DSPy signature"""
    signature_name: str = Field(
        description="Unique name for this atomic signature")
    cognitive_behavior: CognitiveBehavior = Field(
        description="The cognitive behavior this signature implements")
    fields_handled: List[str] = Field(
        description="List of form field names this signature handles")
    field_mapping: Dict[str, str] = Field(
        description="Mapping of form field names to their output paths in this signature"
    )
    questionnaire_spec: QuestionnaireSpec = Field(
        description="Complete questionnaire specification for generate_signature()")
    reasoning_explanation: str = Field(
        description="Explanation of why these fields are grouped or separated")


class PipelineStage(BaseModel):
    """A stage in the pipeline execution flow"""
    stage_name: str = Field(description="Name of this pipeline stage")
    stage_number: int = Field(
        description="Order number of this stage in execution")
    signatures: List[str] = Field(
        description="List of signature names in this stage")
    execution: Literal["sequential", "parallel"] = Field(
        description="How signatures in this stage execute")
    dependencies: List[str] = Field(
        default_factory=list,
        description="List of stage names this stage depends on"
    )
    provides_context: List[str] = Field(
        default_factory=list,
        description="List of context field names this stage provides"
    )
    requires_context: List[str] = Field(
        default_factory=list,
        description="List of context field names this stage requires"
    )
    description: str = Field(description="Purpose of this stage")


class PipelineFlow(BaseModel):
    """Pipeline flow with stages and execution order"""
    stages: List[PipelineStage] = Field(description="List of pipeline stages")


class CombinerSignature(BaseModel):
    """Specification for the final combiner signature"""
    signature_name: str = Field(description="Name of the combiner signature")
    questionnaire_spec: QuestionnaireSpec = Field(
        description="Questionnaire spec for the combiner")


class DecompositionValidation(BaseModel):
    """Validation results for the decomposition"""
    total_form_fields: int = Field(
        description="Total number of fields in the form")
    fields_covered: int = Field(
        description="Number of fields covered by atomic signatures")
    coverage_map: Dict[str, str] = Field(
        description="Map of field_name to signature_name that handles it"
    )
    all_fields_covered: bool = Field(
        description="Whether all fields are covered (MUST be true)")
    dependency_graph_valid: bool = Field(
        default=True, description="Whether dependency graph is valid (no cycles)")
    no_circular_dependencies: bool = Field(
        default=True, description="Whether there are no circular dependencies")


class FormDecomposition(BaseModel):
    """
    Complete form decomposition with atomic signatures, pipeline flow, and validation.

    This is the structured output format for cognitive decomposition using LangChain's
    structured output feature. All validations are enforced by Pydantic.

    Reference: https://docs.langchain.com/oss/python/langchain/structured-output
    """
    reasoning_trace: str = Field(
        description="Step-by-step reasoning about decomposition decisions")
    atomic_signatures: List[AtomicSignature] = Field(
        description="List of atomic signature specifications")
    pipeline_flow: PipelineFlow = Field(
        description="Pipeline flow with stages and dependencies")
    combiner_signature: CombinerSignature = Field(
        description="Final combiner signature specification")
    field_to_signature_map: Dict[str, FieldMapping] = Field(
        description="Complete mapping of each form field to its handling signature"
    )
    validation: DecompositionValidation = Field(
        description="Validation of completeness and correctness")


class Signature(BaseModel):
    """
    A single atomic signature that groups fields with same cognitive behavior.
    """
    name: str = Field(
        ...,
        description="Descriptive signature name (e.g., 'ExtractTextualFields', 'ClassifyEnumFields')"
    )
    field_names: List[str] = Field(
        ...,
        min_length=1,
        description="List of field names from form_data that this signature handles. Must have at least one field."
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description="List of field names this signature needs as input. Empty list [] for independent signatures."
    )


class Stage1Output(BaseModel):
    """
    Stage 1: Field grouping into atomic signatures.

    The LLM only outputs field grouping decisions. Field metadata will be
    enriched by code from form_data.
    """
    reasoning_trace: Optional[str] = Field(
        None,
        description="Step-by-step reasoning about field analysis and grouping decisions (optional for debugging)"
    )
    signatures: List[Signature] = Field(
        ...,
        min_length=1,
        description="List of atomic signatures with grouped fields. Must have at least one signature."
    )


# ============================================================================
# SIGNATURE SPECIFICATION MODELS (FOR STRUCTURED CODE GENERATION)
# ============================================================================


class InputFieldSpec(BaseModel):
    """
    Specification for a single input field in a DSPy signature.

    Used for structured output when LLM designs signature specifications.
    """
    field_name: str = Field(
        ...,
        description="Name of the input field (e.g., 'markdown_content', 'diagnosis')"
    )
    field_type: str = Field(
        ...,
        description="Python type as string (e.g., 'str', 'int', 'float', 'bool')"
    )
    description: str = Field(
        ...,
        description="Clear description of what this input field contains"
    )


class OutputFieldSpec(BaseModel):
    """
    Specification for a single output field in a DSPy signature.

    Used for structured output when LLM designs signature specifications.
    """
    field_name: str = Field(
        ...,
        description="Name of the output field, must match key in enriched signature fields dict"
    )
    field_type: str = Field(
        ...,
        description="Python type as string (e.g., 'str', 'int', 'float', 'bool')"
    )
    description: str = Field(
        ...,
        description="Complete field description including extraction rules, examples, options (if enum), and 'NR' convention"
    )


class SignatureSpec(BaseModel):
    """
    Complete specification for a DSPy signature class.

    This is the structured output format that the LLM returns when designing
    a signature. The specification is then converted to Python code using templates.

    Benefits:
    - LLM only decides WHAT to extract (field names, types, descriptions)
    - Template generates HOW to extract (Python code structure)
    - Automatic validation via Pydantic
    - Deterministic code generation from spec
    """
    class_name: str = Field(
        ...,
        description="Name of the DSPy signature class (e.g., 'ExtractClinicalDetails')"
    )
    class_docstring: str = Field(
        ...,
        description="Complete docstring including purpose, form questions, and domain context"
    )
    input_fields: List[InputFieldSpec] = Field(
        ...,
        min_length=1,
        description="List of input fields (at least document input, plus context fields if dependent)"
    )
    output_fields: List[OutputFieldSpec] = Field(
        ...,
        min_length=1,
        description="List of output fields, one per key in enriched signature's fields dict"
    )


class Stage2CombinerAndFlow(BaseModel):
    """
    Stage 2: Generate combiner signature and pipeline flow.

    Takes the atomic signatures from Stage 1 and creates the combiner
    and execution flow.
    """
    reasoning_trace: str = Field(
        description="Step-by-step reasoning about pipeline flow and combiner design"
    )
    pipeline_flow: PipelineFlow = Field(
        description="Pipeline flow with stages, dependencies, and execution order"
    )
    combiner_signature: CombinerSignature = Field(
        description="Final combiner signature that merges all atomic outputs"
    )
    field_to_signature_map: Dict[str, FieldMapping] = Field(
        default_factory=dict,
        description="Complete mapping of each form field to its handling signature with JSON paths (auto-generated if empty)"
    )


__all__ = [
    "SignatureGenerationState",
    "CompleteTaskGenerationState",
    "CognitiveBehavior",
    "QuestionnaireSpec",
    "FieldMapping",
    "AtomicSignature",
    "PipelineStage",
    "PipelineFlow",
    "CombinerSignature",
    "DecompositionValidation",
    "FormDecomposition",
    "Stage1AtomicSignatures",
    "Stage2CombinerAndFlow",
    "InputFieldSpec",
    "OutputFieldSpec",
    "SignatureSpec",
]
