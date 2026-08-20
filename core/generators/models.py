"""
Data Models for DSPy Generator

Contains TypedDict state definitions and Pydantic models for:
- Workflow states (LangGraph)
- Cognitive decomposition structures
- Validation schemas
"""

from typing import TypedDict, Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field


ANALYSIS_ROLES = Literal[
    "events_treatment", "total_treatment", "events_comparator", "total_comparator",
    "mean_treatment", "sd_treatment", "n_treatment",
    "mean_comparator", "sd_comparator", "n_comparator",
    "value", "variability", "denominator", "arm", "outcome", "timepoint",
    # A table that reports the effect itself rather than the arms behind it.
    # These are only fillable under verdict "effect"; `app/api/v1/synthesis.py`
    # keeps the same names, and the frontend's mapping.ts mirrors them again.
    "effect_value", "effect_se", "effect_ci_lower", "effect_ci_upper",
    # Single-group shapes: a prevalence, or a correlation with its sample size.
    "prop_events", "prop_total", "corr_r", "corr_n",
]


class MappedSlot(BaseModel):
    """One analysis role filled by one column.

    Deliberately a LIST of these rather than a role -> column dict. A free-form
    ``Dict[str, str]`` compiles to ``additionalProperties`` in the JSON schema,
    and under structured output the model cannot invent keys into an open object
    — it returned ``slots={}`` on every form while naming the right columns in
    its prose. A closed enum of roles is fillable; an open dict is not.
    """
    role: ANALYSIS_ROLES = Field(description="Which analysis input this column provides.")
    column: str = Field(description="Column name, exactly as it appears in the columns list.")
    reasoning: str = Field(
        default="",
        description="One short clause explaining this choice. Shown to the reviewer on hover "
        "while they decide whether to confirm the slot.",
    )


class AnalysisMappingSuggestion(BaseModel):
    """LLM proposal for how a form's table maps onto meta-analysis roles.

    Consumed by ``app/api/v1/synthesis.py``, which validates every returned column
    against the form's real column list before the frontend ever sees it. The
    reviewer then confirms each slot individually, so a wrong suggestion costs a
    click rather than a wrong pooled estimate.
    """
    verdict: Literal[
        "dichotomous", "continuous", "effect", "proportion", "correlation",
        "diagnostic_accuracy", "not_poolable",
    ] = Field(
        description="What kind of outcome data this table holds. `effect` means the table reports an "
        "ALREADY-COMPUTED effect (an adjusted odds ratio, a hazard ratio, a mean difference) together "
        "with its confidence interval or standard error, and no arm-level counts — map effect_value "
        "plus either effect_se or both effect_ci_lower and effect_ci_upper, with layout 'wide'. "
        "Prefer dichotomous or continuous whenever arm-level data IS present, since raw arms can "
        "produce any measure. `proportion` means each row is ONE group's count out of a denominator "
        "with no comparator (a prevalence or event rate); `correlation` means each row is one "
        "correlation coefficient with the sample size it came from. Both are single-group shapes and "
        "are always layout 'wide'. diagnostic_accuracy (tp/fp/fn/tn) and not_poolable (risk of bias, "
        "study characteristics, arm descriptions) both mean no mapping is offered — explain why in "
        "`reasoning`."
    )
    layout: Optional[Literal["wide", "long"]] = Field(
        default=None,
        description="wide = one row per comparison, both arms side by side in paired columns. "
        "long = one row per study arm, with a separate column naming the arm.",
    )
    slots: List[MappedSlot] = Field(
        default_factory=list,
        description="One entry per analysis role you can fill. Omit any role no column genuinely "
        "fills — a short list is better than a wrong one. Leave empty when the verdict is "
        "not_poolable or diagnostic_accuracy.",
    )
    variability_measure_column: Optional[str] = Field(
        default=None,
        description="Column declaring WHICH spread measure the variability column holds "
        "(SD / SE / IQR / 95% CI), when the form has one.",
    )
    comparator_value: Optional[str] = Field(
        default=None,
        description="For a long layout, the value of the arm column that identifies the control or "
        "reference group (placebo, no treatment, standard care).",
    )
    reasoning: str = Field(
        description="One or two plain-English sentences for the reviewer. When the verdict is "
        "not_poolable or diagnostic_accuracy this is shown to them directly, so name the specific "
        "columns that led to it."
    )


class AnchorClassification(BaseModel):
    """LLM split of a table's columns into row-identity (anchor) vs measurement (value).

    Used by the two-stage extractor config: anchor columns identify each row in Stage 1;
    value columns are the measurements filled per-row in Stage 2.
    """
    anchor_columns: List[str] = Field(
        description="Column names whose values IDENTIFY or locate a row — labels, categories, "
        "names, group/arm identity, timepoint, subgroup, comparison. NOT measured numbers."
    )
    value_columns: List[str] = Field(
        description="Column names holding a measured quantity/statistic extracted for an "
        "already-identified row (e.g. mean_arm1, sd_arm2, n_arm1, percentage, p-value)."
    )
    reasoning: str = Field(description="One sentence explaining the split.")


SCOPE_FAMILY = Literal["population", "intervention", "comparator", "outcome", "timepoint"]


class ScopeChip(BaseModel):
    """One entry for the guided review-scope builder, read out of a document.

    A LIST of these rather than a family -> entries dict, for exactly the reason
    ``MappedSlot`` is a list: a free-form ``Dict[str, List[str]]`` compiles to
    ``additionalProperties`` and structured output returns it empty while the
    model names the right things in its prose. A closed enum is fillable.

    ``evidence`` is the load-bearing field. ``utils/scope_suggestion.py`` checks
    it against the document and flags any chip it cannot find, which is what
    keeps an invented population from arriving looking like a read one.
    """
    family: SCOPE_FAMILY = Field(description="Which part of the review question this entry is.")
    value: str = Field(
        description="The entry as it should appear in the builder: a short noun phrase in the "
        "document's own wording, under 80 characters, no bullet, no trailing period."
    )
    evidence: str = Field(
        description="A quote copied VERBATIM from the document that states this entry. Not a "
        "paraphrase and not a section title — the sentence or table cell itself."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="high = stated outright; medium = clear from context; low = inferred.",
    )


class ScopeSuggestion(BaseModel):
    """LLM proposal for a project's review scope, read from its planning documents.

    Consumed by ``app/api/v1/review_scope.py``, which validates every chip before
    the frontend sees it and never saves anything: the reviewer keeps, edits or
    discards each entry in the guided builder, and the existing review-scope
    PATCH is still the only writer. A wrong chip therefore costs a click rather
    than silently degrading every extraction in the project.
    """
    chips: List[ScopeChip] = Field(
        default_factory=list,
        description="Every scope entry you can support with a quote. Omit a family entirely "
        "rather than guessing at it.",
    )
    not_used: List[str] = Field(
        default_factory=list,
        description="Scope-relevant text you read and deliberately left out — exclusion "
        "criteria, study-design limits, methods artefacts. One short line each, so the "
        "reviewer can see nothing was quietly discarded.",
    )
    needs_review: List[str] = Field(
        default_factory=list,
        description="Genuine ambiguities a human has to settle: a term that could be an "
        "intervention or a comparator, a population that might be one group or two, or "
        "documents that appear to describe different reviews.",
    )
    notes: str = Field(
        default="",
        description="At most two sentences for the reviewer, naming which section of which "
        "file the scope came from. Say so plainly here if the documents hold no scope.",
    )


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


class SubfieldEnrichment(BaseModel):
    """
    LLM-provided prose enrichment for a single subform column.

    Structural fields (field_name index, field_type, column count/order) are
    user-owned and NOT present here — the LLM cannot add, rename, retype, or
    reorder columns. Only prose content is carried.
    """
    field_name: str = Field(
        ...,
        description="Must exactly match an input subform column name. Never invent a new column name."
    )
    field_description: str = Field(
        default="",
        description="Enriched description for this column. "
                    "Copy verbatim when user-provided is non-empty; "
                    "enrich only when user left it blank.",
    )
    hints: List[str] = Field(
        default_factory=list,
        description="Extraction hints specific to this column. Empty list when none needed.",
    )
    rules: List[str] = Field(
        default_factory=list,
        description="Validation/format rules specific to this column. Empty list when none needed.",
    )
    examples: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Per-column extraction examples — each {value, source_text}. "
                    "These are merged with any user-provided examples.",
    )
    extraction_role: Optional[str] = Field(
        default=None,
        description="Role in two-stage extraction: 'anchor' (row identifier, Stage 1) or 'value' (measurement, Stage 2).",
    )


class OutputFieldSpec(BaseModel):
    """
    Specification for a single output field in a DSPy signature.

    Used for structured output when LLM designs signature specifications.
    Phase B: description is a clean 1-2 sentence summary; hints/rules/examples
    carry the structured guidance separately.
    """
    field_name: str = Field(
        ...,
        description="Name of the output field, must match key in enriched signature fields dict"
    )
    field_type: str = Field(
        ...,
        description="Python type as string (e.g., 'str', 'int', 'float', 'bool', 'Dict[str, Any]')"
    )
    description: str = Field(
        ...,
        description="Clean 1-2 sentence summary of what this field captures. No embedded hints/rules/examples."
    )
    hints: List[str] = Field(
        default_factory=list,
        description="Soft navigation hints: where/how to locate the value in the document."
    )
    rules: List[str] = Field(
        default_factory=list,
        description="Hard output constraints: format, validation, NR convention (NR = paper silent; NA = field cannot apply, and only when listed in options), must/must-not requirements."
    )
    examples: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Extraction examples as {value, source_text} dicts (include NR case; an NA case only when options list one)."
    )
    options: List[str] = Field(
        default_factory=list,
        description="Allowed values for enum/select fields. Empty list for free-text fields."
    )
    subform_fields: List[SubfieldEnrichment] = Field(
        default_factory=list,
        description="Per-column prose enrichment for array/subform_table fields. "
                    "Structural data (field_type, column count, order) is user-owned — "
                    "do NOT add, remove, rename, or reorder columns. "
                    "Return one entry per column whose prose needs enrichment; "
                    "omit columns that already have substantive user-provided descriptions. "
                    "Empty list for non-table fields.",
    )
    extraction_strategy: Optional[str] = Field(
        default=None,
        description=(
            "'single_call' (default), 'row_then_columns', or 'agentic'. A user "
            "choice made per table field in the form builder — not inferred "
            "from column count."
        ),
    )
    anchor_columns: Optional[List[str]] = Field(
        default=None,
        description="Column names used as row identifiers in Stage 1 (row_then_columns strategy).",
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


class AddFieldSpec(BaseModel):
    """
    Structured output for the add-field LLM call.

    Returned by SignatureGenerator.enrich_new_field(). Contains enrichment
    for exactly ONE field being spliced into an existing signature.
    """
    field_name: str = Field(
        ...,
        description="Must exactly match the user's requested field_name. Never rename."
    )
    description: str = Field(
        ...,
        description="Clean 1-2 sentence summary. Verbatim copy of user input when non-empty."
    )
    hints: List[str] = Field(
        default_factory=list,
        description="Soft navigation hints for locating the value in the document."
    )
    rules: List[str] = Field(
        default_factory=list,
        description="Hard output constraints including the NR convention (NR = paper silent; NA = field cannot apply, only when listed in options)."
    )
    examples: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Extraction examples as {value, source_text} dicts. Always includes the NR case; an NA case only when options list one."
    )
    options: List[str] = Field(
        default_factory=list,
        description="Allowed values for enum/select fields. Empty list for free-text."
    )
    subform_fields: List[SubfieldEnrichment] = Field(
        default_factory=list,
        description="Per-column prose enrichment for array/subform_table fields only."
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
    "Stage1Output",
    "Stage2CombinerAndFlow",
    "InputFieldSpec",
    "OutputFieldSpec",
    "SignatureSpec",
    "AddFieldSpec",
]
