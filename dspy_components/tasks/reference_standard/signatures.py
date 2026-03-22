import dspy


# ============================================================================
# SIGNATURES - REFERENCE STANDARD
# ============================================================================


class ExtractReferenceStandardType(dspy.Signature):
    """Extract the reference standard type and biopsy details.
    
    Form Question 23: "Mention the reference standard and type of biopsy."
    - Options: "Biopsy and histopathological assessment" or "Other"
    - Note: Copy and paste the type of biopsy in the text box from the study.
    
    The reference standard is the gold standard diagnostic test against which the 
    index test is being compared. In oral cancer studies, this is typically 
    histopathological examination of biopsy tissue.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    reference_standard_type_json: str = dspy.OutputField(
        desc="""JSON string with reference standard type information.
        
        Structure:
        {
            "biopsy_and_histopathological_assessment": {
                "selected": true/false,
                "comment": "specific biopsy type description or empty string"
            },
            "other": {
                "selected": true/false,
                "comment": "description if other type or empty string"
            }
        }
        
        Rules:
        - Set biopsy_and_histopathological_assessment.selected=true if standard histopathological biopsy is used
        - Include specific biopsy type in comment (e.g., "Incisional biopsy", "Punch or wedge biopsy", "Surgical incisional biopsy")
        - Set other.selected=true only if a non-standard reference is used
        - Extract exact biopsy type descriptions from the paper
        
        Examples:
        {"biopsy_and_histopathological_assessment": {"selected": true, "comment": "Punch or wedge biopsy"}, "other": {"selected": false, "comment": ""}}
        {"biopsy_and_histopathological_assessment": {"selected": true, "comment": "Incisional biopsy"}, "other": {"selected": false, "comment": ""}}
        {"biopsy_and_histopathological_assessment": {"selected": true, "comment": "NR"}, "other": {"selected": false, "comment": ""}}"""
    )


class ExtractBiopsySite(dspy.Signature):
    """Extract the anatomical site where biopsy was performed.
    
    Form Question 24: "What is the site of biopsy?"
    - Note: Copy and paste the site from the study. Mention 'NR' if it is not reported.
    
    This identifies which anatomical locations in the oral cavity were biopsied.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    site_of_biopsy_json: str = dspy.OutputField(
        desc="""JSON string with biopsy site information.
        
        Structure:
        {
            "site_of_biopsy": "brief location or NR",
            "site_of_biopsy_full_description": "detailed description with context"
        }
        
        Rules:
        - site_of_biopsy: Concise anatomical location(s) or "NR"
        - site_of_biopsy_full_description: Full description including biopsy selection criteria, multiple sites, or procedural details
        - Include "NR" prefix in full description if basic site is not reported
        - Preserve study language and terminology
        
        Common anatomical sites:
        - buccal mucosa, tongue, gums, floor of mouth, lip, retromolar area, palate
        
        Examples:
        {"site_of_biopsy": "buccal mucosa, tongue, gums, floor of mouth, lip", "site_of_biopsy_full_description": "buccal mucosa, tongue, gums, floor of mouth, lip."}
        {"site_of_biopsy": "NR", "site_of_biopsy_full_description": "NR\\nAn incisional biopsy was performed at the blue-stained area"}
        {"site_of_biopsy": "NR", "site_of_biopsy_full_description": "NR\\nThe biopsy was done from the area stained maximum with Toluidine Blue in Group B, whereas in Group A, biopsies were taken from the area with most clinical suspicion."}"""
    )


class ExtractPatientsLesionsReferenceStandard(dspy.Signature):
    """Extract counts of patients and lesions that received and were analyzed in the reference standard.
    
    Form Questions 25-28:
    - Question 25: "How many patients received the reference standard?" - Mention 'NR' if not reported
    - Question 26: "How many patients were analyzed in the reference standard?" - Mention 'NR' if not reported
    - Question 27: "How many lesions received the reference standard?" - Mention 'NR' if not reported
    - Question 28: "How many lesions were analyzed in the reference standard?" - Mention 'NR' if not reported
    
    "Received" means patients/lesions that underwent the reference standard test.
    "Analyzed" means those whose results were included in the final analysis.
    The difference indicates exclusions or dropouts.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    patients_lesions_reference_standard_json: str = dspy.OutputField(
        desc="""JSON string with patient and lesion counts.
        
        Structure:
        {
            "num_patients_received_reference_standard": number_or_string,
            "num_patients_analyzed_reference_standard": number_or_string,
            "num_lesions_received_reference_standard": number_or_string,
            "num_lesions_analyzed_reference_standard": number_or_string
        }
        
        Rules:
        - Extract numeric values where reported (e.g., "100", "87", "122")
        - Use "NR" if not reported
        - Can include descriptive text (e.g., "100 (100 were directly biopsied, not included here)")
        - Typically: received >= analyzed (due to exclusions)
        - Some studies report only patient-level or only lesion-level data
        
        Examples:
        {"num_patients_received_reference_standard": "100", "num_patients_analyzed_reference_standard": "100", "num_lesions_received_reference_standard": "NR", "num_lesions_analyzed_reference_standard": "NR"}
        {"num_patients_received_reference_standard": "87", "num_patients_analyzed_reference_standard": "87", "num_lesions_received_reference_standard": "122", "num_lesions_analyzed_reference_standard": "122"}
        {"num_patients_received_reference_standard": "65", "num_patients_analyzed_reference_standard": "60", "num_lesions_received_reference_standard": "NR", "num_lesions_analyzed_reference_standard": "NR"}"""
    )


class ExtractPositivityThreshold(dspy.Signature):
    """Extract the positivity threshold criteria for the reference standard.
    
    Form Question 29: "How many positivity thresholds does the reference standard have?"
    - Options with text boxes: oral cavity cancer, potentially malignant disorder, squamous cell carcinoma, Other
    - Note: Add the statement from the study that describes the positive diagnosis threshold for reference standard. 
      Mention 'NR' if it is not stated or if the study has reported multiple thresholds.
    
    This defines what histopathological findings are classified as "disease positive" vs "disease negative".
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    positivity_threshold_json: str = dspy.OutputField(
        desc="""JSON string with positivity threshold information.
        
        Structure:
        {
            "oral_cavity_cancer": {
                "selected": true/false,
                "comment": "specific cancer types/grades or NA"
            },
            "potentially_malignant_disorder": {
                "selected": true/false,
                "comment": "specific dysplasia grades or NA"
            },
            "squamous_cell_carcinoma": {
                "selected": true/false,
                "comment": "specific SCC details or NA"
            },
            "other": {
                "selected": true/false,
                "comment": "other conditions or NA"
            },
            "positivity_threshold_summary": "brief summary",
            "final_diagnosis_categories": "brief category list"
        }
        
        Rules:
        - Set selected=true for categories used as positive threshold
        - Include specific grades/types in comments (e.g., "mild, moderate, severe dysplasia")
        - Use "NA" for categories not applicable
        - positivity_threshold_summary: Brief overall summary (e.g., "dysplasia and carcinoma")
        - final_diagnosis_categories: Same or similar to summary
        
        Common descriptions:
        - Dysplasia: mild, moderate, severe, epithelial dysplasia
        - Carcinoma: carcinoma in situ, squamous cell carcinoma, well/moderately/poorly differentiated
        
        Examples:
        {"oral_cavity_cancer": {"selected": true, "comment": "well-differentiated, moderately differentiated, and poorly differentiated"}, "potentially_malignant_disorder": {"selected": true, "comment": "mild, moderate and severe grades of dysplasia and carcinoma-in-situ"}, "squamous_cell_carcinoma": {"selected": false, "comment": "NA"}, "other": {"selected": false, "comment": "NA"}, "positivity_threshold_summary": "dysplasia and carcinoma", "final_diagnosis_categories": "dysplasia and carcinoma"}"""
    )


class ExtractTrainingCalibration(dspy.Signature):
    """Extract information about training/calibration of reference standard examiners.
    
    Form Question 30: "Mention the training/calibration of person carrying out reference standard."
    - Note: Copy and paste statement(s) from the study. Mention 'NR' if it is not reported.
    
    This assesses whether pathologists or other examiners were trained or calibrated to ensure 
    consistent diagnosis, which affects the reliability of the reference standard.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    training_calibration: str = dspy.OutputField(
        desc="""Description of training/calibration for reference standard examiners.
        
        Extract information about:
        - Training of pathologists or examiners
        - Calibration procedures
        - Experience level or qualifications
        - Inter-rater reliability measures
        - Standardization procedures
        
        Rules:
        - Copy relevant statements from the study
        - Use "NR" if not reported
        - Preserve exact language when possible
        
        Examples:
        - "NR"
        - "All biopsies were examined by board-certified oral pathologists with at least 10 years of experience"
        - "Two pathologists independently examined all specimens. Discrepancies were resolved by consensus."
        - "Pathologists underwent calibration training using 20 reference cases before study commencement"
        - "Histopathological examination was performed by experienced pathologists at the Department of Pathology"
        
        Note: Most studies report "NR" for this field."""
    )


class ExtractBlindingReferenceStandard(dspy.Signature):
    """Extract information about blinding of reference standard examiners to index test results.
    
    Form Question 31: "Mention the blinding of reference standard examiners to results of index testing."
    - Note: Copy and paste statement(s) from the study. Mention 'NR' if it is not reported.
    
    This is critical for assessing review bias. Ideally, pathologists examining biopsies 
    should be blinded to the index test results to prevent biased interpretation.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    blinding_reference_standard: str = dspy.OutputField(
        desc="""Description of blinding of reference standard examiners.
        
        Extract information about:
        - Whether pathologists were blinded to index test results
        - Whether examiners were blinded to clinical information
        - Specific blinding procedures
        - Any statement about independent evaluation
        
        Rules:
        - Copy relevant statements verbatim from the study
        - Use "NR" if not reported
        - Include quotes when available
        - Note both explicit blinding statements and implicit procedures
        
        Examples:
        - "NR"
        - "\\"The pathologists examining the biopsy specimens were not informed regarding the staining information of the samples.\\""
        - "The pathologists who examined the biopsies were not informed about the clinical or staining evaluations of the samples."
        - "Histopathological examination was performed independently without knowledge of clinical findings"
        - "Pathologists were blinded to all clinical and index test information"
        - "Blinding was not mentioned in the study"
        
        Note: This is a critical quality indicator for diagnostic accuracy studies."""
    )


class CombineReferenceStandardData(dspy.Signature):
    """Combine all extracted reference standard components into single comprehensive record."""

    reference_standard_type_json: str = dspy.InputField(
        desc="JSON from ExtractReferenceStandardType"
    )
    site_of_biopsy_json: str = dspy.InputField(
        desc="JSON from ExtractBiopsySite"
    )
    patients_lesions_reference_standard_json: str = dspy.InputField(
        desc="JSON from ExtractPatientsLesionsReferenceStandard"
    )
    positivity_threshold_json: str = dspy.InputField(
        desc="JSON from ExtractPositivityThreshold"
    )
    training_calibration: str = dspy.InputField(
        desc="Training/calibration from ExtractTrainingCalibration"
    )
    blinding_reference_standard: str = dspy.InputField(
        desc="Blinding info from ExtractBlindingReferenceStandard"
    )

    complete_reference_standard_json: str = dspy.OutputField(
        desc="""Merge all input data into a single JSON object with this exact structure:
        {
            "reference_standard_type": {
                "biopsy_and_histopathological_assessment": {"selected": bool, "comment": string},
                "other": {"selected": bool, "comment": string}
            },
            "site_of_biopsy": string,
            "site_of_biopsy_full_description": string,
            "num_patients_received_reference_standard": string,
            "num_patients_analyzed_reference_standard": string,
            "num_lesions_received_reference_standard": string,
            "num_lesions_analyzed_reference_standard": string,
            "positivity_threshold": {
                "oral_cavity_cancer": {"selected": bool, "comment": string},
                "potentially_malignant_disorder": {"selected": bool, "comment": string},
                "squamous_cell_carcinoma": {"selected": bool, "comment": string},
                "other": {"selected": bool, "comment": string}
            },
            "positivity_threshold_summary": string,
            "final_diagnosis_categories": string,
            "training_calibration_reference_standard_examiners": string,
            "blinding_reference_standard_examiners_to_index_test": string
        }
        
        Simply merge all fields from the inputs, preserving all nested structures and field names exactly as provided.
        Parse JSON inputs to extract their fields into the appropriate nested structures."""
    )


__all__ = [
    "ExtractReferenceStandardType",
    "ExtractBiopsySite",
    "ExtractPatientsLesionsReferenceStandard",
    "ExtractPositivityThreshold",
    "ExtractTrainingCalibration",
    "ExtractBlindingReferenceStandard",
    "CombineReferenceStandardData",
]