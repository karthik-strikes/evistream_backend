import dspy


# ============================================================================
# SIGNATURES - INDEX TEST
# ============================================================================


class ExtractIndexTestType(dspy.Signature):
    """Extract the type of index test used in the study.
    
    Form Question 1: "Select the index test used in the study."
    - Options with text boxes: Cytology, Vital Staining, Light-based test - Autofluorescence, 
      Light-based test - Tissue reflectance, Other
    - "Clear Response" option available
    
    The index test is the diagnostic test being evaluated for its ability to detect 
    oral cancer or premalignant lesions.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    index_test_type_json: str = dspy.OutputField(
        desc="""JSON string with index test type information.
        
        Structure:
        {
            "cytology": {"selected": true/false, "comment": "specific test name"},
            "vital_staining": {"selected": true/false, "comment": "specific test name"},
            "autofluorescence": {"selected": true/false, "comment": "specific test name"},
            "tissue_reflectance": {"selected": true/false, "comment": "specific test name"},
            "other": {"selected": true/false, "comment": "specific test name"}
        }
        
        Rules:
        - Set selected=true for the appropriate test category
        - Include specific test details in comment (e.g., "1% Toluidine Blue", "5% Acetic acid")
        - Only one category should typically be selected=true (unless combined tests)
        - Comment field contains the specific test name/concentration
        
        Examples:
        {"cytology": {"selected": false, "comment": "1% Toluidine Blue"}, "vital_staining": {"selected": true, "comment": "1% Toluidine Blue"}, "autofluorescence": {"selected": false, "comment": "1% Toluidine Blue"}, "tissue_reflectance": {"selected": false, "comment": "1% Toluidine Blue"}, "other": {"selected": false, "comment": "1% Toluidine Blue"}}
        {"cytology": {"selected": true, "comment": "Oral brush biopsy"}, "vital_staining": {"selected": false, "comment": ""}, "autofluorescence": {"selected": false, "comment": ""}, "tissue_reflectance": {"selected": false, "comment": ""}, "other": {"selected": false, "comment": ""}}"""
    )


class ExtractIndexTestBrandAndSite(dspy.Signature):
    """Extract commercial brand name and site selection information.
    
    Form Questions 2-3:
    - Question 2: "Mention the commercial (brand) name of the index test, if stated." - For eg: "OralCDx"
    - Question 3: "How was the site selected for index testing?" - Copy and paste the description from the study.
      Example: "Brushing samples were collected from each participant. For participants with no lesions, 
      brushings were collected from the buccal mucosa."
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    brand_and_site_json: str = dspy.OutputField(
        desc="""JSON string with brand name and site selection information.
        
        Structure:
        {
            "brand_name": "commercial name or NR",
            "site_selection": "description of how sites were selected"
        }
        
        Rules:
        - brand_name: Extract commercial/brand name if mentioned (e.g., "OralCDx", "VELscope", "ViziLite")
        - brand_name: Use "NR" if not reported
        - site_selection: Copy description from study about how lesion sites were selected for testing
        - site_selection: Include any prefixes like "NR\\n" if basic info not reported but procedure described
        
        Examples:
        {"brand_name": "NR", "site_selection": "NR\\nThe dye was applied over the oral lesion identified in clinical examination."}
        {"brand_name": "OralCDx", "site_selection": "Brushing samples were collected from each participant. For participants with no lesions, brushings were collected from the buccal mucosa."}
        {"brand_name": "VELscope", "site_selection": "All clinically visible lesions were examined using the device"}"""
    )


class ExtractSpecimenCollection(dspy.Signature):
    """Extract specimen collection methodology.
    
    Form Question 4: "How was the specimen collected for index tests?"
    - Note: Copy and paste the description from the study. If the index test doesn't collect samples 
      (for example: vital staining), mention 'NA'.
    - Example: "Cytobrush heads were rotated on the lesional surface several times and transferred into 
      a methanolbased preservative solution (ThinPrep Solution, Hologic Suisse, Lausanne, Switzerland)."
    
    This is relevant for cytology and similar tests that collect physical samples, but not for 
    visual inspection tests like vital staining.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    index_test_type: str = dspy.InputField(
        desc="The type of index test for context"
    )

    specimen_collection: str = dspy.OutputField(
        desc="""Description of specimen collection methodology.
        
        Extract information about:
        - How samples were collected (brushing, swabbing, scraping)
        - Collection devices used
        - Preservation methods
        - Transfer procedures
        
        Rules:
        - Copy relevant procedural descriptions from study
        - Use "NA" if the test doesn't collect physical specimens (e.g., vital staining, visual inspection)
        - Use empty string "" if not clearly described
        - Include specific device names and preservation solutions when mentioned
        
        Examples:
        - "NA" (for vital staining tests)
        - "" (not described)
        - "Cytobrush heads were rotated on the lesional surface several times and transferred into a methanolbased preservative solution (ThinPrep Solution, Hologic Suisse, Lausanne, Switzerland)."
        - "Oral brushing was performed using a sterile cytobrush rotated 10 times on the lesion surface"
        
        Note: This field applies primarily to cytology and similar sample-collecting tests."""
    )


class ExtractTechniqueAndAnalysis(dspy.Signature):
    """Extract the technique and analysis methods used to perform the index test.
    
    Form Question 5: "What was the technique and analysis used to perform index tests?"
    - Note: Copy and paste the description from the study.
    - Example: "The samples were transferred into a methanol-based preservative solution (ThinPrep Solution, 
      Hologic Suisse, Lausanne, Switzerland). An aliquot of each sample was processed with liquid-based 
      technology (ThinPrep 5000 Processor, Hologic), stained using the Papanicolaou method, and assessed 
      under microscopy."
    
    This describes the complete procedure from test application through result interpretation.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    index_test_type: str = dspy.InputField(
        desc="The type of index test for context"
    )

    technique: str = dspy.OutputField(
        desc="""Detailed description of technique and analysis methods.
        
        Extract comprehensive procedural information:
        - Preparation steps (mouth rinsing, debris removal)
        - Application technique (timing, concentration, method)
        - Analysis method (visual inspection, microscopy, device readings)
        - Interpretation criteria
        - Complete procedural sequences
        
        Rules:
        - Copy detailed procedural descriptions verbatim from study
        - Include specific concentrations, timing, devices
        - Preserve quoted text with proper formatting
        - Include multi-step procedures in full
        
        Examples:
        - "One% Toluidine Blue solution was prepared from 1 gm Toluidine Blue dye powder, 10 ml 1% Acetic Acid, 4.19 ml Absolute Alcohol, and 86 ml of Distilled Water. Firstly, the mouth was rinsed with water to remove all the debris. After that 1% Acetic Acid rinse was given for 20 s to remove saliva, and the area was dried with gauze. Then the dye was applied over the oral lesion, with a cotton swab, very gently, for 1 min, covering approximately 2 cm margin all around the lesion. 1% Acetic Acid rinse was given thereafter for 1 min and the dye uptake pattern was observed under proper light and exposure."
        - "The samples were transferred into a methanol-based preservative solution (ThinPrep Solution, Hologic Suisse, Lausanne, Switzerland). An aliquot of each sample was processed with liquid-based technology (ThinPrep 5000 Processor, Hologic), stained using the Papanicolaou method, and assessed under microscopy."
        
        Note: This is typically the most detailed field in the index test form."""
    )


class ExtractPatientsLesionsIndexTest(dspy.Signature):
    """Extract counts of patients and lesions that received and were analyzed in the index test.
    
    Form Questions 6-9:
    - Question 6: "How many patients received the index test?" - Mention 'NR' if not reported
    - Question 7: "How many patients were analyzed in the index test?" - Mention 'NR' if not reported
    - Question 8: "How many lesions received the index test?" - Mention 'NR' if not reported
    - Question 9: "How many lesions were analyzed in the index test?" - Mention 'NR' if not reported
    
    "Received" means patients/lesions that underwent the index test.
    "Analyzed" means those whose results were included in the final analysis.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    patients_lesions_index_test_json: str = dspy.OutputField(
        desc="""JSON string with patient and lesion counts.
        
        Structure:
        {
            "patients_received_n": number_or_string,
            "patients_analyzed_n": number_or_string,
            "lesions_received_n": number_or_string,
            "lesions_analyzed_n": number_or_string
        }
        
        Rules:
        - Extract numeric values where reported (can be string or number type)
        - Use "NR" if not reported
        - Typically: received >= analyzed (due to exclusions)
        - Some studies report only patient-level or only lesion-level data
        
        Examples:
        {"patients_received_n": "100", "patients_analyzed_n": "100", "lesions_received_n": "NR", "lesions_analyzed_n": "NR"}
        {"patients_received_n": 33, "patients_analyzed_n": 33, "lesions_received_n": 67, "lesions_analyzed_n": 67}
        {"patients_received_n": "NR", "patients_analyzed_n": "NR", "lesions_received_n": "145", "lesions_analyzed_n": "145"}"""
    )


class ExtractPositivityThreshold(dspy.Signature):
    """Extract the positivity threshold criteria for the index test.
    
    Form Question 10: "What is a positive diagnosis for index test (positivity threshold)?"
    - Note: Add statement from the study in the text box. Mention 'NR' if it is not stated or if the 
      study has reported multiple thresholds.
    
    This defines what index test results are classified as "positive" (suspicious/abnormal) vs 
    "negative" (normal).
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )
    index_test_type: str = dspy.InputField(
        desc="The type of index test for context"
    )

    positivity_threshold: str = dspy.OutputField(
        desc="""Description of positivity threshold criteria.
        
        Extract information defining positive test results:
        - Visual criteria (e.g., "dark blue staining", "retained dye")
        - Cytological criteria (e.g., "atypical cells", "nuclear abnormalities")
        - Device readings (e.g., "loss of fluorescence", "increased reflectance")
        - Classification schemes
        - Specific thresholds or cutoffs
        
        Rules:
        - Copy relevant statements verbatim from study
        - Include quoted definitions when available
        - Use "NR" if not clearly stated or if multiple thresholds reported
        - Preserve multi-line descriptions
        
        Examples:
        - "A stain was considered as positive for malignancy if the lesion diffusely or partly stained dark blue (royal or navy blue), or had stippled appearance."
        - "Lesions that exhibited dark blue staining were deemed positive for premalignant or malignant tissue, while those with light staining or no color were considered negative and were scheduled for follow up."
        - "A positive lesion was the one which color changed to blue, while a negative lesion was with no change."
        - "Cytology positive: presence of atypical squamous cells or worse"
        - "NR"
        
        Note: This is critical for understanding how the index test classifies lesions."""
    )


class ExtractAssessorTrainingAndBlinding(dspy.Signature):
    """Extract information about assessor training and blinding procedures.
    
    Form Questions 11-13:
    - Question 11: "Mention the training/calibration of index test assessors." - Copy and paste statement(s) 
      from the study. Mention 'NR' if it is not reported in the study.
    - Question 12: "Mention the blinding of index test assessment to the sample collection process." - 
      Copy and paste statement(s) from the study. Mention 'NR' if it is not reported in the study.
    - Question 13: "Mention the blinding of the index test examiner to the reference standard." - 
      Copy and paste the statement(s) from the study. Mention 'NR' if it is not reported in the study.
    
    These assess potential sources of bias in index test interpretation.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    assessor_training_blinding_json: str = dspy.OutputField(
        desc="""JSON string with assessor training and blinding information.
        
        Structure:
        {
            "assessor_training": "training/calibration description or NR",
            "assessor_blinding": "blinding to sample collection description or NR",
            "examiner_blinding": "blinding to reference standard description or NR"
        }
        
        Rules:
        - assessor_training: Training, experience, or calibration of those performing index test
        - assessor_blinding: Whether assessors were blinded to how samples were collected
        - examiner_blinding: Whether examiners were blinded to reference standard (biopsy) results
        - Copy relevant statements verbatim
        - Use "NR" for each field not reported
        
        Examples:
        {"assessor_training": "NR", "assessor_blinding": "NR", "examiner_blinding": "The pathologists examining the biopsy specimens were not informed regarding the staining information of the samples."}
        {"assessor_training": "All assessors underwent standardized training using 20 reference cases", "assessor_blinding": "Assessors were blinded to clinical information", "examiner_blinding": "NR"}
        {"assessor_training": "NR", "assessor_blinding": "NR", "examiner_blinding": "NR"}
        
        Note: examiner_blinding refers to whether the index test examiner knew the reference standard results."""
    )


class ExtractAdditionalComments(dspy.Signature):
    """Extract additional comments about the index test.
    
    Form Question 14: "Additional comments:"
    - Free text field for any relevant notes about the index test procedures, 
      interpretations, or methodological considerations.
    """

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    additional_comments: str = dspy.OutputField(
        desc="""Additional comments or notes about the index test.
        
        Include information about:
        - Study design notes affecting index test interpretation
        - Group assignments or randomization
        - Unusual procedures or modifications
        - Quality concerns or limitations
        - Clarifications about blinding or methodology
        
        Rules:
        - Extract relevant methodological notes
        - Include comments about study groups if applicable
        - Note any concerns about bias or quality
        - Use empty string "" if no relevant comments
        
        Examples:
        - "Only Group B received index test.\\nBiopsy was done from the site stained maximum - blinding is under question (affects RoB judgement)"
        - "Check the # of patients as is described in Table 2."
        - "Two different thresholds were used for analysis"
        - ""
        
        Note: This field captures important contextual information not covered in other fields."""
    )


class CombineIndexTestData(dspy.Signature):
    """Combine all extracted index test components into single comprehensive record."""

    index_test_type_json: str = dspy.InputField(
        desc="JSON from ExtractIndexTestType"
    )
    brand_and_site_json: str = dspy.InputField(
        desc="JSON from ExtractIndexTestBrandAndSite"
    )
    specimen_collection: str = dspy.InputField(
        desc="Specimen collection from ExtractSpecimenCollection"
    )
    technique: str = dspy.InputField(
        desc="Technique from ExtractTechniqueAndAnalysis"
    )
    patients_lesions_index_test_json: str = dspy.InputField(
        desc="JSON from ExtractPatientsLesionsIndexTest"
    )
    positivity_threshold: str = dspy.InputField(
        desc="Positivity threshold from ExtractPositivityThreshold"
    )
    assessor_training_blinding_json: str = dspy.InputField(
        desc="JSON from ExtractAssessorTrainingAndBlinding"
    )
    additional_comments: str = dspy.InputField(
        desc="Comments from ExtractAdditionalComments"
    )

    complete_index_test_json: str = dspy.OutputField(
        desc="""Merge all input data into a single JSON object with this exact structure:
        {
            "type": {
                "cytology": {"selected": bool, "comment": string},
                "vital_staining": {"selected": bool, "comment": string},
                "autofluorescence": {"selected": bool, "comment": string},
                "tissue_reflectance": {"selected": bool, "comment": string},
                "other": {"selected": bool, "comment": string}
            },
            "brand_name": string,
            "site_selection": string,
            "specimen_collection": string,
            "technique": string,
            "patients_received_n": number_or_string,
            "patients_analyzed_n": number_or_string,
            "lesions_received_n": number_or_string,
            "lesions_analyzed_n": number_or_string,
            "positivity_threshold": string,
            "assessor_training": string,
            "assessor_blinding": string,
            "examiner_blinding": string,
            "additional_comments": string
        }
        
        Simply merge all fields from the inputs, preserving all nested structures and field names exactly as provided.
        Parse JSON inputs to extract their fields into the appropriate nested structures."""
    )


__all__ = [
    "ExtractIndexTestType",
    "ExtractIndexTestBrandAndSite",
    "ExtractSpecimenCollection",
    "ExtractTechniqueAndAnalysis",
    "ExtractPatientsLesionsIndexTest",
    "ExtractPositivityThreshold",
    "ExtractAssessorTrainingAndBlinding",
    "ExtractAdditionalComments",
    "CombineIndexTestData",
]