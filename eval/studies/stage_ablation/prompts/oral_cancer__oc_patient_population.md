# Single-call extraction — Patient Population Characteristics (oral_cancer)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `paper`  _(source-grounded)_
Study identifier. Format: 'First Author Year' (e.g., 'Aggarwal 2022'). If the same study has multiple entries (e.g., two index-test arms reported separately), use a suffix such as 'Sharma 2021a' and 'Sharma 2021b'. This field is the row key and must be filled for every row.
Hints:
- Look at the author list on the title page or header of the paper for the first (or sole) author's surname
- Look for the publication year in the citation, copyright line, or journal header
- If the document covers multiple studies or arms, check whether a suffix (a, b, c) is needed to distinguish them
Rules:
- Format must be 'Surname YYYY' with a single space between surname and year (e.g., 'Aggarwal 2022')
- Use only the first author's surname — do not include initials, given names, or 'et al.'
- If the same study contributes multiple rows (e.g., separate index-test arms), append a lowercase letter suffix: 'Sharma 2021a', 'Sharma 2021b'
- Year must be the 4-digit publication year
- Capitalise the surname as it appears in the paper
- This field must always be filled — do not use NR
Examples:
- {'value': 'Aggarwal 2022', 'source_text': ''}

### `patient_population_categories`  _(source-grounded)_
All patient population categories enrolled in the study, selected from a fixed list of four options; multiple selections are allowed when the study includes more than one arm.
Allowed values: "Patients with clinically evident, innocuous, or nonsuspicious lesions in the oral cavity or lips", "Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips", "Healthy patients without lesions", "Other"
Hints:
- Look in the Methods section under 'Study Population', 'Participants', 'Inclusion Criteria', or 'Study Design'
- Check whether the study enrolled healthy controls, patients with benign/innocuous lesions, patients with suspicious lesions, or any other group
- If multiple arms are described (e.g., a control group and a lesion group), select all applicable options
Rules:
- Select ALL options that apply — this is a multi-select field
- Each selected value must be exactly one of the four allowed options, using exact spelling
- Do NOT invent or free-text values outside the four listed options
- Return the value as a list of strings (List[str]) even if only one option applies
- Use "NR" if the study population cannot be determined from the document
Examples:
- {'value': 'Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips', 'source_text': ''}

### `patient_population_innocuous_comment`  _(source-grounded)_
Verbatim or paraphrased description of the innocuous/nonsuspicious lesion arm's inclusion criteria; use NA when no such arm was enrolled, or NR when the arm exists but criteria are not described.
Hints:
- Look for descriptions of lesions that were clinically considered benign, non-suspicious, or without malignancy concern
- This arm is often labelled 'benign lesions', 'non-suspicious lesions', 'clinically innocuous', or 'low-risk lesions'
- Do NOT include criteria from the suspicious-lesion arm here — keep arms strictly separated
Rules:
- Extract verbatim or closely paraphrased inclusion criteria for the innocuous/nonsuspicious lesion arm only
- Use exactly "NA" (not blank, not 'not applicable') when the study did NOT include an innocuous-lesion arm
- Use exactly "NR" when the study had an innocuous-lesion arm but did not describe its inclusion criteria
- Do NOT include histopathological findings, recruitment method, or anatomical site details — those belong in other fields
- Do NOT include criteria from the suspicious-lesion arm
Examples:
- {'value': 'Clinically visible lesions without suspicion of malignancy', 'source_text': ''}

### `patient_population_suspicious_comment`  _(source-grounded)_
Verbatim or paraphrased inclusion criteria for the suspicious-lesion arm, capturing the clinical features that qualified patients for enrolment such as lesion type, duration, persistence after irritant removal, age cut-off, or OPMD/OSCC clinical diagnosis.
Hints:
- Look for descriptions of lesions clinically suspected of being oral potentially malignant disorders (OPMDs) or oral squamous cell carcinoma (OSCC)
- Relevant terms include 'suspicious lesion', 'potentially malignant', 'leukoplakia', 'erythroplakia', 'persistent ulcer', 'clinically suspected malignancy'
- Focus on CLINICAL enrolment criteria (what the clinician observed), not on histopathological confirmation or biopsy results
- Check for age thresholds, lesion duration requirements, or persistence criteria after removal of irritants
Rules:
- Extract verbatim or closely paraphrased CLINICAL inclusion criteria for the suspicious-lesion arm only
- Do NOT include histopathological findings (e.g., dysplasia grade, biopsy result) — those belong in severity_target_condition
- Do NOT include recruitment or referral method — that belongs in method_of_patient_selection
- Do NOT include anatomical site details — those belong in site_target_condition
- Use exactly "NA" when the study did NOT include a suspicious-lesion arm
- Use exactly "NR" when the arm exists but inclusion criteria are not stated
Examples:
- {'value': 'Patients ≥18 years with oral lesions persistent for >3 weeks despite removal of possible causative agents (sharp teeth, ill-fitting denture, infection).', 'source_text': ''}

### `method_of_patient_selection`  _(source-grounded)_
RECRUITMENT mechanism only — describe HOW patients were selected (consecutive, random/randomized, alternate allocation, convenience, purposive, retrospective chart review). Begin with a short label (e.g., 'Consecutive sample', 'Convenience sample', 'Randomized — alternate allocation', 'NR') and then add a single short paraphrase. Do NOT put inclusion criteria here (those belong in patient_population_suspicious_comment). Do NOT combine 'NR' with a follow-up quote on a new line — pick one stance. Use NR if no recruitment mechanism is described.
Hints:
- Look in the Methods section under 'Study Design', 'Participants', 'Patient Selection', or 'Recruitment'
- Check for keywords such as consecutive, random, convenience, purposive, retrospective, or prospective enrollment
- Distinguish recruitment mechanism from inclusion/exclusion criteria — only the former belongs here
Rules:
- Begin the value with a short label identifying the recruitment type (e.g., 'Consecutive sample', 'Convenience sample', 'Randomized — alternate allocation')
- Follow the label with a single short paraphrase describing the mechanism
- Do NOT include inclusion or exclusion criteria in this field
- Do NOT combine 'NR' with a follow-up quote — if the mechanism is not described, use exactly 'NR'
- Use "NR" if no recruitment mechanism is described
Examples:
- {'value': 'Consecutive sample — patients attending the oral medicine clinic between Jan and Dec 2020 were enrolled in order of presentation.', 'source_text': ''}

### `ses_population`  _(source-grounded)_
Socioeconomic status descriptor as reported by the paper. Acceptable content: rural/urban split, income category, employment, education level, insurance status. Use NR if not reported. Do NOT infer SES from the hospital location unless the paper explicitly states it. Do NOT include unrelated demographics (those go in their own fields).
Hints:
- Look in the Methods section under 'Study Population', 'Demographics', or Table 1 for SES-related variables
- Check for rural/urban classification, income brackets, employment status, education level, or insurance coverage
- Do not infer SES from the hospital name, city, or country unless the paper explicitly labels it as such
Rules:
- Only report SES descriptors explicitly stated in the paper (rural/urban, income, employment, education, insurance)
- Do NOT infer SES from geographic location or institution name
- Do NOT include age, sex, or disease-related demographics here — those belong in their own fields
- Use "NR" if socioeconomic status is not reported
Examples:
- {'value': '77% rural, 23% urban (from study Table 1).', 'source_text': ''}

### `ethnicity_population`  _(source-grounded)_
Ethnicity or racial composition of the study population as reported in the paper. Acceptable content: counts/percentages per ethnic group, or a single-ethnicity descriptor. Use NR if not reported. Do NOT infer ethnicity from the country of the study.
Hints:
- Look in the Methods section, Table 1, or baseline characteristics table for ethnicity or race data
- Check for labels such as 'race', 'ethnicity', 'ethnic group', or specific group names (e.g., Hispanic, White, African American)
- Do not assume ethnicity based on the country or region where the study was conducted
Rules:
- Report ethnicity/race exactly as described in the paper, including counts or percentages if provided
- Do NOT infer or impute ethnicity from the study country, region, or institution
- If only a single ethnicity is described for the whole cohort, report that descriptor
- Use "NR" if ethnicity or race is not reported
Examples:
- {'value': 'African American 15 (34.1%), White Caucasian 16 (36.4%), Hispanic/Latino 10 (22.7%), Other 3 (6.8%).', 'source_text': ''}

### `risk_factors_original`  _(source-grounded)_
Risk-factor information VERBATIM (or close paraphrase) as reported by the paper. Common factors: tobacco chewing, smoking, alcohol, betel nut, gutkha, pan. Preserve counts and percentages exactly as in the source. If the paper explicitly states no risk-factor data, write 'None reported' rather than NR. Use NR only if the paper does not address risk factors at all. Use NA if risk factors are not applicable (rare — typically only for healthy-only studies).
Hints:
- Look in the patient demographics, baseline characteristics, or methods section for risk-factor tables or sentences
- Check for terms like 'habits', 'tobacco use', 'alcohol consumption', 'betel nut', 'gutkha', 'pan', 'smokeless tobacco'
- Risk factors may appear in a dedicated table row or as a parenthetical in the study population description
Rules:
- Reproduce counts and percentages exactly as written in the source — do not recalculate or round
- If the paper explicitly states no risk-factor data is available, write 'None reported' as the value (not NR)
- Use 'NR' only when the paper does not address risk factors at all (the topic is entirely absent)
- Use 'NA' only when risk factors are genuinely not applicable (e.g., healthy-volunteer-only studies)
- Do not infer or impute values not stated in the paper
Examples:
- {'value': 'Smokeless tobacco 87 (87%), alcohol 37 (37%), betel nut 56 (56%), smoking tobacco 39 (39%) [n=100; multiple habits per patient].', 'source_text': ''}

### `risk_factors_transformed`  _(source-grounded)_
Standardized risk-factor summary. Format: 'Substance N(%)' for each factor reported, joined by semicolons. Use lower-case substance names (e.g., 'smokeless tobacco', 'alcohol', 'betel nut', 'smoking tobacco', 'gutkha', 'pan'). Append a trailing note in square brackets if totals differ from n_total_patients (e.g., '[total n=100; multiple habits per patient]'). Use NR if 'risk_factors_original' is NR. Use NA only if the field does not apply.
Hints:
- Derive directly from the risk_factors_original value — do not re-read the document independently
- Map reported substance names to the canonical lower-case forms: 'smokeless tobacco', 'smoking tobacco', 'alcohol', 'betel nut', 'gutkha', 'pan'
- If the sum of individual habit counts exceeds the total patient count, append a bracketed note explaining the discrepancy
Rules:
- Format each factor as 'substance_name N(%)' with no space between N and the opening parenthesis, e.g., '87(87%)'
- Join multiple factors with '; ' (semicolon followed by a space)
- Use strictly lower-case substance names
- Append '[total n=<X>; multiple habits per patient]' (or equivalent) in square brackets when the denominator or overlap needs clarification
- Use 'NR' if risk_factors_original is NR
- Use 'NA' only if risk factors are not applicable
- Do not add substances not mentioned in risk_factors_original
Examples:
- {'value': 'smokeless tobacco 87(87%); alcohol 37(37%); betel nut 56(56%); smoking tobacco 39(39%) [total n=100; multiple habits per patient]', 'source_text': ''}

### `age_central_tendency_type`  _(source-grounded)_
Which central-tendency statistic (Mean or Median) is reported for age in the study, or 'Not reported' if neither is given.
Allowed values: "Mean", "Median", "Not reported"
Hints:
- Look in the Demographics or Patient Characteristics table, or the first paragraph of the Results section
- If both mean and median are reported, select 'Mean'
- If age is reported only per subgroup (e.g., SCC vs non-SCC) with no whole-sample value, still record the type used for those subgroups
Rules:
- Must be exactly one of: 'Mean', 'Median', 'Not reported'
- Use exact spelling and capitalisation as listed in options
- Prefer 'Mean' when both mean and median are reported
- Use 'Not reported' only when neither mean nor median appears anywhere in the paper
Examples:
- {'value': 'Mean', 'source_text': ''}

### `age_central_tendency_value`  _(source-grounded)_
Single numeric value of the mean or median age for the WHOLE study sample, in years. Decimal allowed (e.g., 49.70). MUST be a single number or NR. Do NOT enter per-subgroup values (e.g., 'OSCC: 56.85; Benign: 58.03') — those belong in 'severity_target_condition' as a note. Do NOT append text such as '(analyzed)'. Use NR if not reported for the whole sample.
Hints:
- Look in the Demographics or Patient Characteristics table for a row labelled 'Age (mean)' or 'Age (median)'
- Check the first paragraph of the Results or Methods section
- If age is reported only per subgroup, enter NR here and note subgroup values elsewhere
Rules:
- Must be a single numeric value (integer or decimal) or 'NR'
- Do NOT include units, parenthetical text, or subgroup breakdowns
- Decimal values are allowed (e.g., 49.70)
- Use 'NR' if the whole-sample value is not reported
Examples:
- {'value': '49.70', 'source_text': ''}

### `age_sd`  _(source-grounded)_
Standard deviation of age for the WHOLE study sample. Single numeric value or NR. Do NOT enter per-subgroup SDs or text such as '(analyzed)'. Use NR if not reported (including studies that report only IQR or range).
Hints:
- Look for 'SD', '±', or 'standard deviation' adjacent to the mean age value
- Check footnotes of the demographics table
- If only IQR or range is reported, enter NR
Rules:
- Must be a single numeric value (integer or decimal) or 'NR'
- Do NOT include the '±' symbol or any text
- Do NOT enter per-subgroup SDs
- Use 'NR' if SD is not reported, including when only IQR or range is given
Examples:
- {'value': '12.38', 'source_text': ''}

### `age_range`  _(source-grounded)_
Age range for the WHOLE study sample, formatted as 'Min-Max' using a plain hyphen (e.g., '25-86'). If the paper reports a categorical range (e.g., '<20 to >70'), preserve that wording. Do NOT enter multiple per-subgroup ranges here (e.g., 'SCC 39-98, non-SCC 20-98'); put subgroup ranges in 'severity_target_condition'. Use NR if not reported. Use a single dash character consistently — do NOT mix en-dash / em-dash.
Hints:
- Look for 'range', 'min', 'max', or parenthetical values adjacent to the mean/median age
- Check the demographics table footnotes or the patient characteristics section
- If only a categorical range is given (e.g., '<20 to >70'), preserve that exact wording
Rules:
- Format as 'Min-Max' using a plain hyphen (e.g., '25-86')
- Do NOT use en-dash (–) or em-dash (—); use only a plain hyphen (-)
- If a categorical range is reported, preserve the original wording verbatim
- Do NOT enter multiple per-subgroup ranges; enter NR and note subgroup ranges elsewhere
- Use 'NR' if no range is reported for the whole sample
Examples:
- {'value': '25-86', 'source_text': ''}

### `n_total_patients`  _(source-grounded)_
Total number of patients INITIALLY recruited (denominator before any attrition). MUST be a single integer or NR. Do NOT include parenthetical detail such as '105 (98 analyzed)' or '200 (100 stained, 100 not)' — the analyzed count belongs in the reference-standard form. Verify any value that looks like a year (e.g., 2018, 2026) before entering. Use NR if not reported.
Hints:
- Look for 'enrolled', 'recruited', 'included', or 'total' in the Methods or Patient Characteristics section
- Check the CONSORT flow diagram or study flowchart for the initial enrolment number
- Distinguish the recruited total from the analysed subset — use the recruited total here
Rules:
- Must be a single integer or 'NR'
- Do NOT include parenthetical text such as '(98 analyzed)'
- Do NOT enter a value that is plausibly a year (e.g., 2018, 2026) without verifying it is a patient count
- Use 'NR' if the recruited total is not reported
Examples:
- {'value': '87', 'source_text': ''}

### `n_female`  _(source-grounded)_
Number of female participants in the study. Single integer or NR. Use 0 if explicitly reported as zero. Do NOT include percentages here — those belong in 'pct_female'.
Hints:
- Look for 'female', 'women', or 'F' in the demographics table or patient characteristics section
- Check if sex is reported as a count (n) or only as a percentage; if only percentage, enter NR here
Rules:
- Must be a single integer or 'NR'
- Use 0 if the paper explicitly states zero female participants
- Do NOT include percentages in this field
- Use 'NR' if the count of female participants is not reported
Examples:
- {'value': '38', 'source_text': ''}

### `pct_female`  _(source-grounded)_
Percentage of female participants as a decimal number WITHOUT the '%' symbol (e.g., 43.70). If only n_female and n_total_patients are reported, compute pct_female = round(100 × n_female / n_total_patients, 2). Use NR if neither the percentage nor the components are reported.
Hints:
- Look for '%' or 'percent' adjacent to the female count in the demographics table
- If only counts are available, compute: round(100 × n_female / n_total_patients, 2)
Rules:
- Must be a decimal number without the '%' symbol (e.g., 43.70)
- If the percentage is not directly stated but n_female and n_total_patients are available, compute it: round(100 × n_female / n_total_patients, 2)
- Do NOT include the '%' symbol
- Use 'NR' if neither the percentage nor the components needed to compute it are reported
Examples:
- {'value': '43.70', 'source_text': ''}

### `n_male`  _(source-grounded)_
Number of male participants in the study. Single integer or NR. Use 0 if explicitly reported as zero.
Hints:
- Look for 'male', 'men', or 'M' in the demographics table or patient characteristics section
- If sex is reported only as a percentage, enter NR here
- n_male may be derivable as n_total_patients − n_female if both are reported
Rules:
- Must be a single integer or 'NR'
- Use 0 if the paper explicitly states zero male participants
- Do NOT include percentages in this field
- Use 'NR' if the count of male participants is not reported
Examples:
- {'value': '49', 'source_text': ''}

### `pct_male`  _(source-grounded)_
Percentage of male participants as a decimal number without '%' (e.g., 56.30). If only counts are reported, compute pct_male = round(100 × n_male / n_total_patients, 2). pct_female + pct_male should sum to ≈100 (allow small rounding); if not, recheck the underlying numbers. Use NR if neither percentage nor components are reported.
Hints:
- Look for '%' or 'percent' adjacent to the male count in the demographics table
- If only counts are available, compute: round(100 × n_male / n_total_patients, 2)
- Verify that pct_female + pct_male ≈ 100; if not, recheck the source numbers
Rules:
- Must be a decimal number without the '%' symbol (e.g., 56.30)
- If the percentage is not directly stated but n_male and n_total_patients are available, compute it: round(100 × n_male / n_total_patients, 2)
- pct_female + pct_male should sum to approximately 100; flag discrepancies by rechecking source values
- Do NOT include the '%' symbol
- Use 'NR' if neither the percentage nor the components needed to compute it are reported
Examples:
- {'value': '56.30', 'source_text': ''}

### `target_condition_opmd_comment`  _(source-grounded)_
How the study defines the OPMD target condition — the OPMD subtypes or dysplasia grades considered positive in this study.
Hints:
- Look in the Methods section under 'Study Population', 'Inclusion Criteria', 'Case Definition', or 'Diagnostic Criteria'
- Check the Abstract for a concise statement of which OPMD subtypes were studied
- Look for terms such as 'oral leukoplakia', 'erythroplakia', 'oral submucous fibrosis (OSF)', 'oral lichen planus (OLP)', 'potentially malignant disorder', 'PMD', 'epithelial dysplasia'
Rules:
- Extract the definition or list of OPMD subtypes/dysplasia grades the study considers as the positive target condition
- Do NOT include oral cancer or OSCC content here — that belongs in 'target_condition_oscc_comment'
- Do NOT copy inclusion/exclusion criteria verbatim; summarise which OPMD subtypes or grades are considered positive
- Use "NA" if OPMD is NOT a target condition in this study
- Use "NR" if OPMD is a target condition but the paper does not define or specify which subtypes/grades are included
Examples:
- {'value': 'Low- and high-risk PMDs; included subtypes: oral leukoplakia, OLP, oral submucous fibrosis, erythroplakia.', 'source_text': ''}

### `target_condition_oscc_comment`  _(source-grounded)_
How the study defines the oral cancer / OSCC target condition — the cancer subtypes, differentiation grades, or staging categories considered positive in this study.
Hints:
- Look in the Methods section under 'Case Definition', 'Histopathological Criteria', or 'Diagnostic Criteria'
- Check for terms such as 'squamous cell carcinoma', 'SCC', 'carcinoma in situ', 'CIS', 'verrucous carcinoma', 'well/moderately/poorly differentiated'
- The Abstract or Results may also state which cancer categories were included
Rules:
- Extract the definition or list of oral cancer/OSCC subtypes, differentiation grades, or staging categories the study considers as the positive target condition
- Do NOT include OPMD or dysplasia content here — that belongs in 'target_condition_opmd_comment'
- Do NOT copy inclusion/exclusion criteria verbatim; summarise which cancer subtypes or grades are considered positive
- Use "NA" if OSCC is NOT a target condition in this study
- Use "NR" if OSCC is a target condition but the paper does not define or specify which subtypes/grades are included
Examples:
- {'value': 'Carcinoma in situ; squamous cell carcinoma (well/moderately/poorly differentiated, invasive/microinvasive).', 'source_text': ''}

### `severity_target_condition`  _(source-grounded)_
Free-text description of the severity or grade spectrum of the target condition as reported in the study sample, including dysplasia grades, CIS, SCC differentiation tiers, TNM staging, risk strata, or per-subgroup counts and demographic breakdowns.
Hints:
- Look in the Results section for tables or text reporting the distribution of severity grades, dysplasia categories, or TNM stages among study participants
- Check for phrases such as 'mild dysplasia', 'moderate dysplasia', 'severe dysplasia', 'carcinoma in situ', 'well-differentiated', 'Stage I/II/III/IV', 'low-risk PMD', 'high-risk PMD'
- Per-subgroup age/sex breakdowns and per-subgroup age ranges should also be captured here when they cannot fit in numeric fields
- Frequency tables (e.g., 'mild 28.6%, moderate 30.2%') should be transcribed as reported
Rules:
- Report the severity/grade spectrum exactly as described in the paper, including any percentages or counts per subgroup
- Include dysplasia grades (mild/moderate/severe), CIS, SCC differentiation tiers (well/moderately/poorly), TNM staging, or risk strata as applicable
- Include per-subgroup demographic breakdowns (age, sex) when reported alongside severity categories
- Use "NR" if severity or grade information is not stated in the paper
Examples:
- {'value': 'Low-risk (no/questionable/mild dysplasia) and high-risk PMD (moderate/severe dysplasia); carcinoma.', 'source_text': ''}

### `site_target_condition`  _(source-grounded)_
Anatomical site(s) within the oral cavity or lips where the target lesion(s) occurred, as reported in the study, including any frequency distributions by site.
Hints:
- Look in the Results section or participant characteristics tables for site distribution data
- Check for terms such as 'buccal mucosa', 'tongue', 'floor of mouth', 'gingiva', 'palate', 'lip', 'alveolar ridge', 'retromolar trigone'
- Frequency tables listing site counts or percentages (e.g., 'buccal mucosa 40 (63.5%)') should be transcribed as reported
- Do not confuse anatomical site with histopathological severity — severity belongs in 'severity_target_condition'
Rules:
- Extract only anatomical site information — do NOT include histopathological severity grades (those go in 'severity_target_condition')
- Do NOT include the name of the target condition itself (e.g., 'OSCC', 'leukoplakia') as the site
- Do NOT include examination methodology or instrument descriptions
- A list of sites or a frequency table by site is acceptable
- Use "NR" if anatomical site information is not stated in the paper
Examples:
- {'value': 'Lateral tongue, buccal mucosa, floor of mouth, lower lip, soft palate, hard palate, alveolar ridge.', 'source_text': ''}

### `population_transformed`  _(source-grounded)_
Harmonized population category for meta-analysis derived by mapping the previously extracted patient_population_categories to a standardized controlled vocabulary using a defined precedence rule.
Allowed values: "Patients with clinically evident, innocuous, or nonsuspicious lesions in the oral cavity or lips", "Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips", "Healthy patients without lesions"
Hints:
- Inspect the 'value' key of the input patient_population_categories field to determine which options were selected
- If only one option was selected, carry it forward verbatim
- If multiple options were selected, apply the precedence rule: 'Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips' takes priority
- If two populations are co-primary (e.g., suspicious + healthy controls, or innocuous + suspicious without a clear dominant arm), list both separated by a semicolon
Rules:
- Output value MUST be exactly one of the three listed options, or two options joined by a semicolon separator when co-primary
- Never output 'Other' — map or omit it entirely
- If only one option was selected in patient_population_categories, enter that option verbatim
- If two or more options were selected, apply precedence: 'Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips' takes precedence when present
- If two populations are co-primary (e.g., suspicious + healthy controls, or innocuous + suspicious without a clear dominant arm), list both using a semicolon separator
- Use "NR" if patient_population_categories is NR
- Use exact spelling and capitalisation matching the listed options
Examples:
- {'value': 'Patients with clinically evident suspicious lesions or seemingly malignant lesions in the oral cavity or lips', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"paper": {"value": "...", "source_text": "..."}, "patient_population_categories": {"value": "...", "source_text": "..."}, "patient_population_innocuous_comment": {"value": "...", "source_text": "..."}, "patient_population_suspicious_comment": {"value": "...", "source_text": "..."}, "method_of_patient_selection": {"value": "...", "source_text": "..."}, "ses_population": {"value": "...", "source_text": "..."}, "ethnicity_population": {"value": "...", "source_text": "..."}, "risk_factors_original": {"value": "...", "source_text": "..."}, "risk_factors_transformed": {"value": "...", "source_text": "..."}, "age_central_tendency_type": {"value": "...", "source_text": "..."}, "age_central_tendency_value": {"value": "...", "source_text": "..."}, "age_sd": {"value": "...", "source_text": "..."}, "age_range": {"value": "...", "source_text": "..."}, "n_total_patients": {"value": "...", "source_text": "..."}, "n_female": {"value": "...", "source_text": "..."}, "pct_female": {"value": "...", "source_text": "..."}, "n_male": {"value": "...", "source_text": "..."}, "pct_male": {"value": "...", "source_text": "..."}, "target_condition_opmd_comment": {"value": "...", "source_text": "..."}, "target_condition_oscc_comment": {"value": "...", "source_text": "..."}, "severity_target_condition": {"value": "...", "source_text": "..."}, "site_target_condition": {"value": "...", "source_text": "..."}, "population_transformed": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
