import dspy

# ======================================================================
# ExtractFieldsFromSchema Signature
# ======================================================================


class ExtractFieldsFromSchema(dspy.Signature):
    """Extract and classify all fields from a medical data extraction schema description."""

    schema_description: str = dspy.InputField(
        desc="The complete schema description from a DSPy signature's OutputField"
    )

    ground_truth_json: str = dspy.InputField(
        desc="The ground truth JSON String from a DSPy signature's OutputField"
    )

    reasoning: str = dspy.OutputField(
        desc="Explain your logic for classifying fields as semantic vs exact vs groupable"
    )

    all_required_fields: str = dspy.OutputField(
        desc="""JSON array of ALL field names from the schema (complete list, all 85 fields) and also for now neglect Ref_ID & filename fields.
        Example: ["First_Author", "Year", ..., "Intervention_6_Comment"]"""
    )

    semantic_fields: str = dspy.OutputField(
        desc="""JSON array of fields that should use SEMANTIC matching (free-text, descriptions, comments, outcomes).
        These are fields where exact string match doesn't matter - meaning matters.
        Example: ["Outcomes_Studied", "Outcomes_Reported", "Comments", "Intervention_1_Name","Intervention_1_Description", ...]"""
    )

    exact_fields: str = dspy.OutputField(
        desc="""JSON array of fields that should use EXACT matching (IDs, names, numbers, codes, dates) and also for now neglect Ref_ID & filename fields.
        These are structured fields where exact value matters.
        Example: ["First_Author", "Year", "Design", "Funding",  ...]"""
    )

    groupable_field_patterns: str = dspy.OutputField(
        desc="""JSON object mapping repeating field groups where ORDER doesn't matter.
        These are fields with numbered patterns (like Intervention_1_*, Intervention_2_*) where we need to match 
        by CONTENT, not by position number.
        
        Example: {
            "interventions": {
                "pattern": "Intervention_{i}_*",
                "key_matching_fields": ["Name"],
                "all_fields": ["Name", "Description", "N_Randomized", "Age_Central_Tendency", "Female_N", "Female_Percent", "Comment"],
                "max_slots": 6
            }
        }
        
        If no repeating patterns exist, return empty object: {}
        """
    )


# ======================================================================
# SemanticMatcher Signature
# ======================================================================

class SemanticMatcher(dspy.Signature):
    """
    Expert medical data extraction evaluator. Determine if two extracted medical text values 
    represent the same clinical information, accounting for:
    - Paraphrasing and rewording
    - Different but equivalent terminology (e.g., "aspirin" vs "acetylsalicylic acid")
    - Minor formatting differences
    - Abbreviations vs full terms

    IMPORTANT: Focus on semantic meaning, not exact wording. Values are equivalent if they 
    convey the same medical information in the context of the field being compared.
    """

    text1: str = dspy.InputField(
        desc="First extracted value from medical document"
    )

    text2: str = dspy.InputField(
        desc="Second extracted value (ground truth) to compare against"
    )

    field_context: str = dspy.InputField(
        desc="The medical field being compared (e.g., 'Intervention_Description', 'Outcomes_Studied'). "
             "Consider field-specific standards when evaluating equivalence."
    )

    reasoning: str = dspy.OutputField(
        desc="Brief explanation of why the texts are or are not equivalent (1-2 sentences)"
    )

    is_equivalent: bool = dspy.OutputField(
        desc="True if texts are semantically equivalent or partially equivalent for data extraction purposes, False only if they are completely different"
    )


# ============================================================================
# CLASS 2: Evaluator
# ============================================================================
