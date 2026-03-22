import dspy


# ============================================================================
# SIGNATURES - PATIENT POPULATION
# ============================================================================


class ExtractPatientPopulation(dspy.Signature):
    """Extract patient population categories from medical research paper."""

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    patient_population_json: str = dspy.OutputField(
        desc="""JSON string with this exact nested structure:
        {
            "population": {
                "innocuous_lesions": {
                    "selected": true/false,
                    "comment": "specific details if selected, otherwise empty string"
                },
                "suspicious_or_malignant_lesions": {
                    "selected": true/false,
                    "comment": "specific details if selected, otherwise empty string"
                },
                "healthy_without_lesions": {
                    "selected": true/false,
                    "comment": "specific details if selected, otherwise empty string"
                },
                "other": {
                    "selected": true/false,
                    "comment": "specific details if selected, otherwise empty string"
                },
                "unclear": {
                    "selected": true/false,
                    "comment": "specific details if selected, otherwise empty string"
                },
                "statement": "If selected innocuous_lesions then statement = Patients with clinically evident, innocuous, or nonsuspicious lesions in the oral cavity or lips if selected suspicious_or_malignant_lesions then statement = Patients with clinically evident, suspicious, or malignant lesions in the oral cavity or lips if selected healthy_without_lesions then statement = Healthy patients without lesions if selected other then statement = Other if selected unclear then statement = Unclear or not reported"
            }
        }

        Example: {"population": {"innocuous_lesions": {"selected": false, "comment": ""}, "suspicious_or_malignant_lesions": {"selected": true, "comment": "patients presenting with oral lesions persistent for more than 3 weeks"}, "healthy_without_lesions": {"selected": false, "comment": ""}, "other": {"selected": false, "comment": ""}, "unclear": {"selected": false, "comment": ""}, "statement": "Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips"}}"""
    )


class ExtractPatientSelectionAndDemographics(dspy.Signature):
    """Extract patient selection method and demographic characteristics."""

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    selection_demographics_json: str = dspy.OutputField(
        desc="""JSON string with exactly these fields:
        - patient_selection_method: Full description of selection method. If not clearly reported, describe what is mentioned or use "NR"
        - population_ses: Socioeconomic status description, or "NR" if not reported
        - population_ethnicity: Ethnicity distribution description, or "NR" if not reported
        - population_risk_factors: Risk factors with types/percentages if available, or "NR" if not reported

        Example: {"patient_selection_method": "Randomization. An oral medicine specialist recorded clinical findings, took pictures of the lesions, and picked the areas to be examined.", "population_ses": "NR", "population_ethnicity": "NR", "population_risk_factors": "NR"}"""
    )


class ExtractAgeCharacteristics(dspy.Signature):
    """Extract age-related statistics and measures."""

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    age_characteristics_json: str = dspy.OutputField(
        desc="""JSON string with this exact nested structure:
        {
            "age_central_tendency": {
                "mean": {"selected": true/false, "value": numeric_value or empty string},
                "median": {"selected": true/false, "value": numeric_value or empty string},
                "not_reported": always false
            },
            "age_variability": {
                "sd": {"selected": true/false, "value": numeric_value or empty string},
                "range": {"selected": true/false, "value": "min-max" or empty string},
                "not_reported": true/false
            }
        }

        Rules:
        - If mean is reported: set mean.selected=true, mean.value=<number>, not_reported=false
        - If median is reported: set median.selected=true, median.value=<number>, not_reported=false
        - If SD is reported: set sd.selected=true, sd.value=<number>, not_reported=false
        - If range is reported: set range.selected=true, range.value="25-86", not_reported=false
        - If nothing is reported: set not_reported=true for that section

        Example: {"age_central_tendency": {"mean": {"selected": true, "value": 61.5}, "median": {"selected": false, "value": ""}, "not_reported": false}, "age_variability": {"sd": {"selected": true, "value": 12.38}, "range": {"selected": true, "value": "25-86"}, "not_reported": false}}"""
    )


class ExtractBaselineCharacteristics(dspy.Signature):
    """Extract baseline participant counts and gender distribution."""

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    baseline_json: str = dspy.OutputField(
        desc="""JSON string with this exact nested structure:
        {
            "baseline_participants": {
                "total": {"selected": true/false, "value": number_or_string_or_empty},
                "female_n": {"selected": true/false, "value": number_or_empty},
                "female_percent": {"selected": true/false, "value": number_or_empty},
                "male_n": {"selected": true/false, "value": number_or_empty},
                "male_percent": {"selected": true/false, "value": number_or_empty},
                "not_reported": {"selected": true/false, "value": ""},
                "other": {"selected": true/false, "value": ""}
            }
        }

        Rules:
        - Always set total.selected=true if total count is available
        - Set selected=true for any gender metric that is reported
        - Use numeric values where available
        - Set selected=false and value="" for unreported metrics

        Example: {"baseline_participants": {"total": {"selected": true, "value": 87}, "female_n": {"selected": true, "value": 38}, "female_percent": {"selected": true, "value": 43.7}, "male_n": {"selected": true, "value": 49}, "male_percent": {"selected": true, "value": 56.3}, "not_reported": {"selected": false, "value": ""}, "other": {"selected": false, "value": ""}}}"""
    )


class ExtractTargetCondition(dspy.Signature):
    """Extract target condition details including type, severity, and anatomical site."""

    markdown_content: str = dspy.InputField(
        desc="Full markdown content of the medical research paper"
    )

    target_condition_json: str = dspy.OutputField(
        desc="""JSON string with this exact nested structure:
        {
            "target_condition": {
                "opmd": {"selected": true/false, "comment": "specific OPMD types or empty"},
                "oral_cancer": {"selected": true/false, "comment": "specific cancer info or empty"},
                "other": {"selected": true/false, "comment": "other conditions or empty"}
            },
            "target_condition_severity": "Description of severity/dysplasia grades or NR",
            "target_condition_site": "Description of anatomical sites or NR",
            "filename": "LastAuthor_Year format"
        }

        Rules:
        - Set opmd.selected=true if study includes premalignant lesions
        - Set oral_cancer.selected=true if study includes carcinoma/cancer
        - Include specific condition names in comments when available

        Example: {"target_condition": {"opmd": {"selected": true, "comment": "Leukoplakia, oral submucosal fibrosis, pemphigus vulgaris"}, "oral_cancer": {"selected": true, "comment": "Carcinoma"}, "other": {"selected": false, "comment": ""}}, "target_condition_severity": "Hyperplasia and mild dysplasia, carcinoma in situ, squamous cell carcinoma (well differentiated, moderately differentiated, poorly differentiated, invasive, microinvasive, infiltrating)", "target_condition_site": "Gingivolabial sulcus, Lateral border of tongue, Buccal mucosa, Retromolar trigone", "filename": "Agrawal_2024"}"""
    )


class CombinePatientPopulationCharacteristics(dspy.Signature):
    """Combine all extracted patient population components into single comprehensive record."""

    patient_population_json: str = dspy.InputField(
        desc="JSON from ExtractPatientPopulation"
    )
    selection_demographics_json: str = dspy.InputField(
        desc="JSON from ExtractPatientSelectionAndDemographics"
    )
    age_characteristics_json: str = dspy.InputField(
        desc="JSON from ExtractAgeCharacteristics"
    )
    baseline_json: str = dspy.InputField(
        desc="JSON from ExtractBaselineCharacteristics"
    )
    target_condition_json: str = dspy.InputField(
        desc="JSON from ExtractTargetCondition"
    )

    complete_characteristics_json: str = dspy.OutputField(
        desc="""Merge all input JSONs into a single JSON object with this structure:
        {
            "population": { nested structure from patient_population_json },
            "patient_selection_method": string,
            "population_ses": string,
            "population_ethnicity": string,
            "population_risk_factors": string,
            "age_central_tendency": { nested structure from age_characteristics_json },
            "age_variability": { nested structure from age_characteristics_json },
            "baseline_participants": { nested structure from baseline_json },
            "target_condition": { nested structure from target_condition_json },
            "target_condition_severity": string,
            "target_condition_site": string,
            "filename": string
        }

        Simply merge all fields from the 5 input JSONs, preserving all field names and nested structures exactly as provided."""
    )


__all__ = [
    "ExtractPatientPopulation",
    "ExtractPatientSelectionAndDemographics",
    "ExtractAgeCharacteristics",
    "ExtractBaselineCharacteristics",
    "ExtractTargetCondition",
    "CombinePatientPopulationCharacteristics",
]
