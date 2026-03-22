import dspy


# ============================================================================
# SIGNATURES - OUTCOMES
# ============================================================================





class ExtractIndexTests(dspy.Signature):
    """Extract a list of all index tests evaluated in the study.
    
    Identify all diagnostic methods/tests that are being evaluated against a reference standard.
    Some studies compare multiple tests (e.g., Toluidine Blue vs Acetic Acid).
    """
    
    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    
    index_tests_json: str = dspy.OutputField(
        desc="""JSON list of index test names.
        
        Examples:
        - ["Vital Staining (Toluidine blue)", "Vital Staining (acetic acid)"]
        - ["Clinical Examination", "Velscope"]
        - ["Visual Examination"]
        """
    )


class ExtractOutcomeTargetCondition(dspy.Signature):
    """Extract the target condition for which outcomes are being reported.
    
    Form Question 2: "To which target condition is this outcome related?"
    - Example guidance: "The study population includes patients with OSCC and dysplasia; 
      this outcome is reported for OSCC only."
    - Specifies what disease conditions the diagnostic test is evaluating
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    linking_index_test: str = dspy.InputField(
        desc="The index test name for context"
    )

    outcome_target_condition: str = dspy.OutputField(
        desc="""Description of the target condition being evaluated in the outcomes.
        
        This should specify what conditions are considered "disease positive" vs "disease negative" for the diagnostic accuracy metrics.
        
        Examples:
        - "Premalignant and malignant lesion"
        - "Disease positive are dysplasia, carcinoma in situ and squamous cell carcinoma. Disease negative are hyperplasia, inflammation and normal mucosa"
        - "OPMD (dysplasia - mild, moderate, severe)"
        - "dysplasia or carcinoma"
        - "oral cancer and precancer (dysplasia)"
        
        If the study population includes patients with OSCC and dysplasia but this outcome is reported for OSCC only, specify that clearly.
        Example: "The study population includes patients with OSCC and dysplasia; this outcome is reported for OSCC only."
        
        Be as specific as possible about what constitutes positive vs negative disease status."""
    )


class ExtractConfusionMatrixMetrics(dspy.Signature):
    """Extract confusion matrix metrics: TP, FP, FN, TN.
    
    Form Questions 3-6:
    - Question 3: "How many true positives (TP) were in the study?" - Mention 'NR' if not reported
    - Question 4: "How many false positives (FP) were in the study?" - Mention 'NR' if not reported
    - Question 5: "How many false negatives (FN) were in the study?" - Mention 'NR' if not reported
    - Question 6: "How many true negatives (TN) were in the study?" - Mention 'NR' if not reported
    
    These represent the 2x2 contingency table for diagnostic test performance.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    linking_index_test: str = dspy.InputField(
        desc="The index test name for context"
    )
    outcome_target_condition: str = dspy.InputField(
        desc="The target condition for context"
    )

    confusion_matrix_json: str = dspy.OutputField(
        desc="""JSON string with confusion matrix values.
        
        Structure:
        {
            "true_positives": number_or_"NR",
            "false_positives": number_or_"NR",
            "false_negatives": number_or_"NR",
            "true_negatives": number_or_"NR"
        }
        
        Rules:
        - Extract numeric values where reported
        - Use "NR" (not reported) if a value is not explicitly stated in the study
        - Values should be integers representing counts of patients/lesions
        - TP = Test positive & Disease positive
        - FP = Test positive & Disease negative
        - FN = Test negative & Disease positive
        - TN = Test negative & Disease negative
        
        Examples:
        {"true_positives": 58, "false_positives": 7, "false_negatives": 3, "true_negatives": 32}
        {"true_positives": "NR", "false_positives": "NR", "false_negatives": "NR", "true_negatives": "NR"}
        {"true_positives": 101, "false_positives": 8, "false_negatives": 16, "true_negatives": 110}"""
    )


class ExtractSensitivitySpecificity(dspy.Signature):
    """Extract reported sensitivity and specificity values with confidence intervals.
    
    Form Questions 7-10:
    - Question 7: "What is the reported sensitivity?" - Mention 'NR' if not reported
    - Question 8: "What is the 95% Confidence Interval of sensitivity?" - Mention 'NR' if not reported
    - Question 9: "What is the reported specificity?" - Mention 'NR' if not reported
    - Question 10: "What is the 95% Confidence Interval of specificity?" - Mention 'NR' if not reported
    
    Sensitivity = TP / (TP + FN) - measures ability to correctly identify disease
    Specificity = TN / (TN + FP) - measures ability to correctly identify non-disease
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    linking_index_test: str = dspy.InputField(
        desc="The index test name for context"
    )
    outcome_target_condition: str = dspy.InputField(
        desc="The target condition for context"
    )

    sensitivity_specificity_json: str = dspy.OutputField(
        desc="""JSON string with sensitivity and specificity metrics.
        
        Structure:
        {
            "reported_sensitivity": number_or_"NR",
            "reported_sensitivity_ci": "lower-upper"_or_"NR",
            "reported_specificity": number_or_"NR",
            "reported_specificity_ci": "lower-upper"_or_"NR"
        }
        
        Rules:
        - Sensitivity/specificity should be numeric (percentages as numbers, e.g., 95.08 not "95.08%")
        - Use "NR" if not reported
        - For confidence intervals: use format "lower-upper" (e.g., "85.2-98.4") or "NR"
        - Common CI formats: 95% CI, confidence interval, (CI)
        - If CI is not mentioned or unclear, use "NR"
        
        Examples:
        {"reported_sensitivity": 95.08, "reported_sensitivity_ci": "NR", "reported_specificity": 82.05, "reported_specificity_ci": "NR"}
        {"reported_sensitivity": 100, "reported_sensitivity_ci": "93.5-100", "reported_specificity": 81.8, "reported_specificity_ci": "68.2-91.4"}
        {"reported_sensitivity": "NR", "reported_sensitivity_ci": "NR", "reported_specificity": "NR", "reported_specificity_ci": "NR"}"""
    )


class ExtractOutcomesComments(dspy.Signature):
    """Extract any additional comments or notes about the outcomes.
    
    Form Question 11: "Additional comments:"
    - Free text field for any important methodological notes
    - Population overlap details
    - Special considerations for interpreting results
    - Data extraction clarifications
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    linking_index_test: str = dspy.InputField(
        desc="The index test name for context"
    )
    outcome_target_condition: str = dspy.InputField(
        desc="The target condition for context"
    )
    confusion_matrix_json: str = dspy.InputField(
        desc="Previously extracted confusion matrix for context"
    )
    sensitivity_specificity_json: str = dspy.InputField(
        desc="Previously extracted sensitivity/specificity for context"
    )

    outcomes_comment: str = dspy.OutputField(
        desc="""Additional comments or important notes about the outcomes data.
        
        Include information about:
        - Population overlap between different test arms
        - Special considerations in interpreting the results
        - Data extraction notes or clarifications
        - Subgroup analyses or stratification details
        - Unusual reporting patterns
        - Excluded cases and reasons
        - Important methodological notes
        
        Examples:
        - "Data for 100 patients submitted to toluidine blue (group B). Group A received only clinical examination (100 patients)."
        - "There might be overlap of patients in different arms."
        - "Same population had received the same test. Two pathologists analyzed both populations for all tests."
        - "no OSCC was identified. 2 inadequate tissue sample excluded from table 2x2."
        - "Sensitivity and specificity are reported apart for precancerous and malignancy: The sensitivity, specificity, PPV, NPV of toluidine blue in detecting premalignant and malignant lesions were 66.6%, 87.8%, 28.5%, 97.2% and 94.3%, 100%, 100%, 81.8%"
        
        If no relevant comments are found, return empty string ""."""
    )


class CombineOutcomesData(dspy.Signature):
    """Combine all extracted outcomes components into single comprehensive record."""

    linking_index_test: str = dspy.InputField(
        desc="Index test name from ExtractIndexTests"
    )
    outcome_target_condition: str = dspy.InputField(
        desc="Target condition from ExtractOutcomeTargetCondition"
    )
    confusion_matrix_json: str = dspy.InputField(
        desc="JSON from ExtractConfusionMatrixMetrics"
    )
    sensitivity_specificity_json: str = dspy.InputField(
        desc="JSON from ExtractSensitivitySpecificity"
    )
    outcomes_comment: str = dspy.InputField(
        desc="Comments from ExtractOutcomesComments"
    )

    complete_outcomes_json: str = dspy.OutputField(
        desc="""Merge all input data into a single JSON object with this exact structure:
        {
            "linking_index_test": string,
            "outcome_target_condition": string,
            "true_positives": number_or_"NR",
            "false_positives": number_or_"NR",
            "false_negatives": number_or_"NR",
            "true_negatives": number_or_"NR",
            "reported_sensitivity": number_or_"NR",
            "reported_sensitivity_ci": string_or_"NR",
            "reported_specificity": number_or_"NR",
            "reported_specificity_ci": string_or_"NR",
            "outcomes_comment": string,
        }
        
        Simply merge all fields from the inputs, preserving field names and values exactly as provided.
        Parse the confusion_matrix_json and sensitivity_specificity_json to extract their fields into the top-level structure."""
    )


__all__ = [
    "ExtractIndexTests",
    "ExtractOutcomeTargetCondition",
    "ExtractConfusionMatrixMetrics",
    "ExtractSensitivitySpecificity",
    "ExtractOutcomesComments",
    "CombineOutcomesData",
]