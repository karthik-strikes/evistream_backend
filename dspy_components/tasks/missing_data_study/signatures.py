import dspy


# ============================================================================
# SIGNATURES - MISSING DATA / PARTIAL VERIFICATION
# ============================================================================


class ExtractPatientsPartialVerification(dspy.Signature):
    """Extract patient-level partial verification data.
    
    Form Questions 32-33:
    - Question 32: "How many patients received index test but not reference standard?" - Mention 'NR' if not reported
    - Question 33: "How many patients received reference standard but not index test?" - Mention 'NR' if not reported
    
    These questions assess whether all patients who received the index test also received 
    the reference standard (gold standard diagnostic test), which is important for assessing 
    verification bias.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    patients_partial_verification_json: str = dspy.OutputField(
        desc="""JSON string with patient-level partial verification data.
        
        Structure:
        {
            "num_patients_received_index_test_but_not_reference_standard": number_or_string,
            "num_patients_received_reference_standard_but_not_index_test": number_or_string
        }
        
        Rules:
        - Extract numeric values (e.g., 0, 4, 5) where explicitly reported
        - Use "NR" if not reported
        - Can use qualified descriptions like "assumed 0", "NR (probably 5)", "0" for zero patients
        - Index test = the diagnostic test being evaluated (e.g., toluidine blue staining)
        - Reference standard = gold standard test (typically histopathological biopsy)
        
        Examples:
        {"num_patients_received_index_test_but_not_reference_standard": "0", "num_patients_received_reference_standard_but_not_index_test": "0"}
        {"num_patients_received_index_test_but_not_reference_standard": "4", "num_patients_received_reference_standard_but_not_index_test": "0"}
        {"num_patients_received_index_test_but_not_reference_standard": "assumed 0", "num_patients_received_reference_standard_but_not_index_test": "assumed 0"}
        {"num_patients_received_index_test_but_not_reference_standard": "NR", "num_patients_received_reference_standard_but_not_index_test": "NR (probably 5)"}"""
    )


class ExtractLesionsPartialVerification(dspy.Signature):
    """Extract lesion-level partial verification data.
    
    Form Questions 34-35:
    - Question 34: "How many lesions received index test but not reference standard?" - Mention 'NR' if not reported
    - Question 35: "How many lesions received reference standard but not index test?" - Mention 'NR' if not reported
    
    These questions are similar to questions 32-33 but at the lesion level rather than 
    patient level. This is relevant for studies where multiple lesions per patient may 
    be examined.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    lesions_partial_verification_json: str = dspy.OutputField(
        desc="""JSON string with lesion-level partial verification data.
        
        Structure:
        {
            "num_lesions_received_index_test_but_not_reference_standard": number_or_string,
            "num_lesions_received_reference_standard_but_not_index_test": number_or_string
        }
        
        Rules:
        - Extract numeric values (e.g., 0, 7, 9) where explicitly reported
        - Use "NR" if not reported
        - Can use detailed descriptions for complex scenarios
        - Some studies report at lesion level, others at patient level
        - If study is patient-level only, this may be "NR"
        
        Examples:
        {"num_lesions_received_index_test_but_not_reference_standard": "0", "num_lesions_received_reference_standard_but_not_index_test": "0"}
        {"num_lesions_received_index_test_but_not_reference_standard": "9", "num_lesions_received_reference_standard_but_not_index_test": "0"}
        {"num_lesions_received_index_test_but_not_reference_standard": "NR", "num_lesions_received_reference_standard_but_not_index_test": "NR"}
        {"num_lesions_received_index_test_but_not_reference_standard": "patient data not given, of 145 lesions 86 (or 87 in abstract) received biopsy, 59. Not reported how biopsies were selected", "num_lesions_received_reference_standard_but_not_index_test": "patient data not given, of 145 lesions 86 (or 87 in abstract) received biopsy, 59. Not reported how biopsies were selected"}"""
    )


class ExtractTimeInterval(dspy.Signature):
    """Extract time interval between index test and reference standard.
    
    Form Question 36: "What is the time interval between the index test(s) and reference standard?" - Mention 'NR' if not reported
    
    This assesses whether the reference standard was performed close in time to the index test, 
    which is important because disease status may change over time (disease progression bias).
    The ideal scenario is when both tests are performed simultaneously or very close together.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    time_interval: str = dspy.OutputField(
        desc="""Description of time interval between index test and reference standard.
        
        This should capture:
        - Exact time intervals if specified (e.g., "same day", "within 2 weeks", "immediately after")
        - Implicit/assumed timing from study description
        - Uncertainty or lack of reporting
        - Any relevant contextual notes about timing
        
        Rules:
        - Extract explicit time intervals where stated
        - Use "NR" if not reported and cannot be reasonably inferred
        - Can include qualified descriptions like "assumed same day", "not reported but assumed following index tests"
        - Can include detailed notes about timing ambiguities
        
        Examples:
        - "NR"
        - "The exams were performed in the same day."
        - "not more than 2 weeks between the 3 methods of investigation"
        - "description implied biopsy taken immediately after rinsing"
        - "simulteneous biospy"
        - "index test followed by reference standard"
        - "assumed reference standard biopsy immediately followed the index test"
        - "not reported but assumed at the same appointment"
        - "Unclear, perhaps in the same day, although it is not clear. \\"Biopsy site was selected based on the retention of MB and the absence of staining of LI.\\""
        - "NR. If there were 3 vital staining tests, they could be hardly be conducted in the same day."
        - "biospy consecutive to index text"
        
        Note: Can be a detailed free-text description if timing is complex or uncertain."""
    )


class CombineMissingData(dspy.Signature):
    """Combine all extracted missing data components into single comprehensive record."""

    patients_partial_verification_json: str = dspy.InputField(
        desc="JSON from ExtractPatientsPartialVerification"
    )
    lesions_partial_verification_json: str = dspy.InputField(
        desc="JSON from ExtractLesionsPartialVerification"
    )
    time_interval: str = dspy.InputField(
        desc="Time interval from ExtractTimeInterval"
    )

    complete_missing_data_json: str = dspy.OutputField(
        desc="""Merge all input data into a single JSON object with this exact structure:
        {
            "num_patients_received_index_test_but_not_reference_standard": number_or_string,
            "num_patients_received_reference_standard_but_not_index_test": number_or_string,
            "num_lesions_received_index_test_but_not_reference_standard": number_or_string,
            "num_lesions_received_reference_standard_but_not_index_test": number_or_string,
            "time_interval_between_index_test_and_reference_standard": string
        }
        
        Simply merge all fields from the inputs, preserving field names and values exactly as provided.
        Parse the patients_partial_verification_json and lesions_partial_verification_json to extract their fields into the top-level structure."""
    )


__all__ = [
    "ExtractPatientsPartialVerification",
    "ExtractLesionsPartialVerification",
    "ExtractTimeInterval",
    "CombineMissingData",
]