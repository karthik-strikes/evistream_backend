# Single-call extraction — Patient Population (periodontitis)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `age_arm_a`  _(source-grounded)_
Mean age (years) of the intervention arm (arm A). Use 'NR' if not reported.
Hints:
- Look in the baseline characteristics table, often labelled 'Table 1' or 'Baseline Demographics'
- The intervention arm may be labelled as 'treatment group', 'experimental arm', 'arm A', or the name of the active intervention
Rules:
- Extract the numeric mean age value only
- Return as a float or integer (e.g., 54.4 or 54)
- Do not include units or SD in this field
- Use "NR" if not reported
Examples:
- {'value': '54.4', 'source_text': ''}

### `age_arm_a_sd`  _(source-grounded)_
Standard deviation of age in the intervention arm (arm A). Use 'NR' if not reported.
Hints:
- Typically reported alongside the mean as 'mean ± SD' in the baseline characteristics table
- May appear as 'SD', 'std dev', or in parentheses after the mean value
Rules:
- Extract the numeric SD value only
- Return as a float or integer (e.g., 5.8)
- Do not include the mean or units in this field
- Use "NR" if not reported
Examples:
- {'value': '5.8', 'source_text': ''}

### `age_arm_b`  _(source-grounded)_
Mean age (years) of the control / usual-care arm (arm B). Use 'NR' if not reported.
Hints:
- Look in the baseline characteristics table for the control, placebo, or usual-care arm
- The control arm may be labelled 'arm B', 'control group', 'placebo', or 'usual care'
Rules:
- Extract the numeric mean age value only
- Return as a float or integer (e.g., 52 or 52.0)
- Do not include units or SD in this field
- Use "NR" if not reported
Examples:
- {'value': '52', 'source_text': ''}

### `age_arm_b_sd`  _(source-grounded)_
Standard deviation of age in the control arm (arm B). Use 'NR' if not reported.
Hints:
- Typically reported alongside the mean as 'mean ± SD' in the baseline characteristics table
- May appear as 'SD', 'std dev', or in parentheses after the mean value for the control arm
Rules:
- Extract the numeric SD value only
- Return as a float or integer (e.g., 3.3)
- Do not include the mean or units in this field
- Use "NR" if not reported
Examples:
- {'value': '3.3', 'source_text': ''}

### `pct_female_arm_a`  _(source-grounded)_
Percentage of female participants (0-100) in the intervention arm (arm A). Derive from M:F counts if only raw numbers are given. Use 'NR' if not reported.
Hints:
- Look in the baseline characteristics table under 'Sex', 'Gender', or 'Female' rows
- If only raw counts are given (e.g., '45 female out of 80'), calculate: (female_count / total_arm_a) × 100
- May be reported as a percentage directly or as a fraction/count
Rules:
- Return as a numeric percentage between 0 and 100 (e.g., 56.3)
- If only raw counts are provided, derive the percentage: (n_female / n_total) × 100, rounded to one decimal place
- Do not include the '%' symbol in the value
- Use "NR" if not reported
Examples:
- {'value': '56.3', 'source_text': ''}

### `pct_female_arm_b`  _(source-grounded)_
Percentage of female participants (0-100) in the control arm (arm B). Use 'NR' if not reported.
Hints:
- Look in the baseline characteristics table under 'Sex', 'Gender', or 'Female' rows for the control arm
- If only raw counts are given, calculate: (female_count / total_arm_b) × 100
- May be reported as a percentage directly or as a fraction/count
Rules:
- Return as a numeric percentage between 0 and 100 (e.g., 52.0)
- If only raw counts are provided, derive the percentage: (n_female / n_total) × 100, rounded to one decimal place
- Do not include the '%' symbol in the value
- Use "NR" if not reported
Examples:
- {'value': '52', 'source_text': ''}

### `diabetes_type`  _(source-grounded)_
Diabetes type at enrolment. Use the base type only; put qualifiers such as 'poorly controlled' or 'newly diagnosed' in the study notes, not here. Use 'mixed' if both T1DM and T2DM are enrolled.
Allowed values: "T1DM", "T2DM", "mixed", "NR"
Hints:
- Look in the eligibility criteria, inclusion/exclusion criteria, or participant characteristics sections
- Check the abstract or study population description for the diabetes type label
- If both T1DM and T2DM participants are enrolled together, select 'mixed'
Rules:
- Must be exactly one of: T1DM, T2DM, mixed, NR
- Use exact spelling and capitalisation as listed in options
- Do not encode qualifiers (e.g., 'poorly controlled', 'newly diagnosed') in this field — record only the base type
- Use 'mixed' only when both T1DM and T2DM participants are explicitly enrolled in the same study
- Use "NR" if diabetes type cannot be determined from the document
Examples:
- {'value': 'T2DM', 'source_text': ''}

### `baseline_hba1c_arm_a`  _(source-grounded)_
Baseline HbA1c (%) for the intervention arm (arm A). Use 'NR' if not reported.
Hints:
- Look in the baseline characteristics table, typically labelled as 'Table 1' or 'Baseline Demographics'
- The intervention arm may be labelled as arm A, treatment group, experimental group, or by the intervention name
- HbA1c values are typically expressed as a percentage (e.g., 7.1%) or in mmol/mol — extract the % value
- If only mmol/mol is reported, note that conversion may be needed; extract the value as given
Rules:
- Extract the numeric HbA1c percentage value for the intervention/treatment arm only
- Report as a decimal number (float) in percent units (e.g., 7.1, not 0.071)
- Do not extract post-treatment or follow-up HbA1c — baseline only
- If multiple intervention sub-arms exist, extract the overall or pooled baseline value if available
- Use "NR" if baseline HbA1c for the intervention arm is not reported
Examples:
- {'value': '7.1', 'source_text': ''}

### `baseline_hba1c_arm_b`  _(source-grounded)_
Baseline HbA1c (%) for the control arm (arm B). Use 'NR' if not reported.
Hints:
- Look in the baseline characteristics table alongside arm A values
- The control arm may be labelled as arm B, control group, placebo group, or comparator
- HbA1c values are typically expressed as a percentage — extract the % value
- If only mmol/mol is reported, extract the value as given
Rules:
- Extract the numeric HbA1c percentage value for the control/comparator arm only
- Report as a decimal number (float) in percent units (e.g., 8.2, not 0.082)
- Do not extract post-treatment or follow-up HbA1c — baseline only
- If multiple control sub-arms exist, extract the overall or pooled baseline value if available
- Use "NR" if baseline HbA1c for the control arm is not reported
Examples:
- {'value': '8.2', 'source_text': ''}

### `inclusion_criteria`  _(source-grounded)_
Verbatim inclusion criteria as reported by the paper.
Hints:
- Look in the Methods section under headings such as 'Eligibility Criteria', 'Inclusion Criteria', 'Study Population', or 'Participants'
- May appear as a bulleted or numbered list; concatenate into a single string if needed
Rules:
- Extract the inclusion criteria verbatim or as close to verbatim as possible
- Preserve all conditions listed (age, diagnosis, disease duration, clinical thresholds, etc.)
- Use "NR" if inclusion criteria are not reported
Examples:
- {'value': '≥35 yrs, T2DM ≥3 yrs, severe chronic periodontitis, ≥15 teeth', 'source_text': ''}

### `exclusion_criteria`  _(source-grounded)_
Verbatim exclusion criteria as reported by the paper.
Hints:
- Look in the Methods section under headings such as 'Eligibility Criteria', 'Exclusion Criteria', or 'Study Population'
- Often listed immediately after inclusion criteria
Rules:
- Extract the exclusion criteria verbatim or as close to verbatim as possible
- Preserve all conditions listed (pregnancy, smoking, BMI thresholds, prior treatments, etc.)
- Use "NR" if exclusion criteria are not reported
Examples:
- {'value': 'pregnant, smokers, BMI>35, recent periodontal/antibiotic therapy', 'source_text': ''}

### `duration_since_diabetes_dx`  _(source-grounded)_
Time since diabetes diagnosis, verbatim from the paper (OPTIONAL). Use 'NR' if not reported.
Hints:
- Often stated as part of inclusion criteria (e.g., 'T2DM for at least 3 years') or in the baseline characteristics table
- May appear as a minimum duration threshold or as a mean/median value in the sample description
Rules:
- Extract the value verbatim as it appears in the paper (e.g., '≥3 yrs', 'at least 5 years', 'mean 7.2 ± 2.1 years')
- Do not convert units or reformat the value
- Use "NR" if not reported
Examples:
- {'value': '≥3 yrs', 'source_text': ''}

### `tobacco_use`  _(source-grounded)_
Tobacco use status verbatim, or 'none (excluded)' if smokers were excluded by the criteria (OPTIONAL). Use 'NR' if not reported.
Hints:
- Check exclusion criteria for explicit exclusion of smokers or tobacco users
- Check baseline characteristics table for smoking status of enrolled participants
- May appear as 'non-smokers only', 'current smokers excluded', or a percentage of smokers in the sample
Rules:
- If smokers were explicitly excluded in the eligibility criteria, output 'none (excluded)'
- If tobacco use status is described for enrolled participants, extract verbatim
- Use "NR" if tobacco use is neither described nor addressed in the eligibility criteria
Examples:
- {'value': 'none (excluded)', 'source_text': ''}

### `alcohol_consumption`  _(source-grounded)_
Alcohol use status verbatim (OPTIONAL). Use 'NR' if not reported.
Hints:
- Check exclusion criteria for explicit exclusion of alcohol users
- Check baseline characteristics table for alcohol consumption data
- May appear as a percentage, categorical status, or as an exclusion condition
Rules:
- Extract verbatim as reported in the paper
- If alcohol users were explicitly excluded, note that
- Use "NR" if alcohol consumption is not reported or addressed
Examples:
- {'value': 'NR', 'source_text': ''}

### `n_randomised`  _(source-grounded)_
Total number of participants randomised across all arms. Use 'NR' if not stated.
Hints:
- Look in the Methods section under 'Participants', 'Randomisation', or 'Study Design'
- Check the CONSORT flow diagram or participant flow description
- The abstract or results section may also state total randomised numbers
- Sum arm-level counts if only per-arm figures are given and no total is stated
Rules:
- Extract the total count across all arms as an integer
- If only per-arm counts are given, sum them to derive the total
- Do not include screened or enrolled participants who were not randomised
- Use "NR" if the number randomised is not reported anywhere in the document
Examples:
- {'value': '24', 'source_text': ''}

### `n_evaluated`  _(source-grounded)_
Number evaluated/analysed at follow-up, which may be heterogeneous across timepoints (e.g. '24 at 6 mths'). Use 'NR' if not stated.
Hints:
- Look in the Results section for analysis populations such as 'per-protocol', 'intention-to-treat', or 'evaluable population'
- Check tables reporting outcomes at specific timepoints
- If multiple timepoints are reported, include all with their respective timepoints
- CONSORT flow diagrams often show numbers analysed at each assessment
Rules:
- Report the number analysed along with the associated timepoint when available (e.g. '24 at 6 mths')
- If multiple timepoints are reported, list all (e.g. '30 at 3 mths, 24 at 6 mths')
- Distinguish from the number randomised — only include those actually evaluated or analysed
- Use "NR" if the number evaluated is not reported anywhere in the document
Examples:
- {'value': '24 at 6 mths', 'source_text': ''}

### `metabolic_control_level`  _(source-grounded)_
Coarse classification of baseline metabolic control, derived from the reported mean baseline HbA1c (good / fair / poor). Use 'NR' if HbA1c is not reported.
Allowed values: "good", "fair", "poor", "NR"
Hints:
- Use the 'value' keys from the baseline_hba1c_arm_a and baseline_hba1c_arm_b input fields to determine the classification
- If both arms report HbA1c, average or use the overall mean to assign a single classification
- Look for baseline characteristics tables or text describing mean HbA1c at study entry
Rules:
- Must be exactly one of: good, fair, poor, NR
- Use 'good' if mean baseline HbA1c < 7.0%
- Use 'fair' if mean baseline HbA1c is 7.0% to 8.0% (inclusive)
- Use 'poor' if mean baseline HbA1c > 8.0%
- Use 'NR' if HbA1c is not reported in either arm
- Use exact lowercase spelling as listed in options
Examples:
- {'value': 'poor', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"age_arm_a": {"value": "...", "source_text": "..."}, "age_arm_a_sd": {"value": "...", "source_text": "..."}, "age_arm_b": {"value": "...", "source_text": "..."}, "age_arm_b_sd": {"value": "...", "source_text": "..."}, "pct_female_arm_a": {"value": "...", "source_text": "..."}, "pct_female_arm_b": {"value": "...", "source_text": "..."}, "diabetes_type": {"value": "...", "source_text": "..."}, "baseline_hba1c_arm_a": {"value": "...", "source_text": "..."}, "baseline_hba1c_arm_b": {"value": "...", "source_text": "..."}, "inclusion_criteria": {"value": "...", "source_text": "..."}, "exclusion_criteria": {"value": "...", "source_text": "..."}, "duration_since_diabetes_dx": {"value": "...", "source_text": "..."}, "tobacco_use": {"value": "...", "source_text": "..."}, "alcohol_consumption": {"value": "...", "source_text": "..."}, "n_randomised": {"value": "...", "source_text": "..."}, "n_evaluated": {"value": "...", "source_text": "..."}, "metabolic_control_level": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
