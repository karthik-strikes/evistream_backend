# Single-call extraction — Reference Standard (oral_cancer)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `paper`  _(source-grounded)_
Study identifier in 'First Author Year' format, serving as the unique row key for this entry in the evidence table.
Hints:
- Look for the first-listed or corresponding author's surname in the paper header, title page, or citation line
- The publication year is typically found on the title page, journal header, or copyright line
- If multiple entries from the same study are being extracted in separate rows, append a lowercase letter suffix (a, b, c…) to distinguish them
Rules:
- Format must be 'Surname YYYY' with a single space between surname and year (e.g., 'Aggarwal 2022')
- Use only the first author's surname — do not include initials, given names, or co-authors
- Year must be the 4-digit publication year of the paper
- If the same study has multiple entries reported separately (e.g., two index-test arms), append a lowercase letter suffix: 'Sharma 2021a', 'Sharma 2021b'
- Do not abbreviate or alter the surname spelling
Examples:
- {'value': 'Aggarwal 2022', 'source_text': ''}

### `reference_standard_type`  _(source-grounded)_
Type of reference standard used to establish the final diagnosis. For this review, the expected value is 'Biopsy and histopathological assessment'. Choose 'Clinical follow-up' only if the study uses follow-up (not biopsy) as the truth standard. Choose 'Other' for any other arrangement and describe it in 'reference_standard_comment'. Do not place biopsy technique here.
Allowed values: "Biopsy and histopathological assessment", "Clinical follow-up", "Other"
Hints:
- Look in the Methods section under headings such as 'Reference Standard', 'Gold Standard', 'Histopathological Assessment', or 'Diagnosis Confirmation'
- Check whether the study confirms disease via tissue biopsy and pathology report, or via longitudinal clinical follow-up without biopsy
- If the paper describes a combination approach, determine which method is the primary truth standard
Rules:
- Must be exactly one of the three allowed options: 'Biopsy and histopathological assessment', 'Clinical follow-up', or 'Other'
- Use 'Biopsy and histopathological assessment' when tissue biopsy followed by histopathological analysis is the primary method of diagnosis confirmation
- Use 'Clinical follow-up' only when the study uses clinical follow-up (without biopsy) as the truth standard
- Use 'Other' for any arrangement not covered by the above two options
- Do NOT record the biopsy technique (e.g., punch biopsy, excisional biopsy) in this field — that belongs elsewhere
- Use exact spelling and capitalisation as listed in the options
Examples:
- {'value': 'Biopsy and histopathological assessment', 'source_text': ''}

### `positivity_threshold_transformed`  _(source-grounded)_
Harmonized positivity threshold mapped to one of five fixed options for meta-analysis. Mapping rules: (a) 'dysplasia and carcinoma' — any degree of dysplasia OR any carcinoma is disease-positive; (b) 'severe dysplasia and carcinoma' — only severe dysplasia and/or carcinoma is positive (mild and moderate dysplasia are negative); (c) 'dysplasia' — only dysplasia (any grade) is positive, carcinoma is not targeted or excluded; (d) 'carcinoma only' — only invasive carcinoma and/or CIS is positive, dysplasia is treated as negative; (e) 'other' — any other rule (e.g., site-specific, staging-based, OPMD subtype labels). MUST be one of these five exact strings. Do not enter free text — map to the closest option and capture verbatim text in the comment fields.
Allowed values: "dysplasia and carcinoma", "severe dysplasia and carcinoma", "dysplasia", "carcinoma only", "other"
Hints:
- Look in the Methods section under 'Histopathological Criteria', 'Outcome Definition', 'Disease Definition', or 'Positivity Threshold'
- Check how the study defines a 'positive' biopsy result — which histological grades are counted as disease
- Note whether mild/moderate dysplasia are included or excluded from the positive category
- If the paper uses WHO or other grading systems, map the described threshold to the closest fixed option
Rules:
- Must be exactly one of the five allowed options: 'dysplasia and carcinoma', 'severe dysplasia and carcinoma', 'dysplasia', 'carcinoma only', or 'other'
- Use 'dysplasia and carcinoma' when any grade of dysplasia OR any carcinoma qualifies as disease-positive
- Use 'severe dysplasia and carcinoma' when only severe/high-grade dysplasia and carcinoma are positive; mild and moderate dysplasia are negative
- Use 'dysplasia' when only dysplasia (any grade) is the target outcome and carcinoma is not included or is excluded
- Use 'carcinoma only' when only invasive carcinoma and/or carcinoma in situ (CIS) is positive and dysplasia is treated as negative
- Use 'other' for any threshold rule not captured by the above four options (e.g., site-specific criteria, staging-based, OPMD subtype labels)
- Do NOT enter free text — always map to the closest fixed option
- Use exact lowercase spelling as listed in the options
Examples:
- {'value': 'dysplasia and carcinoma', 'source_text': ''}

### `n_patients_rs`  _(source-grounded)_
Total number of patients enrolled in the reference-standard (biopsy) procedure. Parenthetical context may follow the number to explain exclusions or subgroups.
Hints:
- Look in the Methods section under 'Participants', 'Study Population', or 'Patients enrolled'
- Check the flow diagram or CONSORT diagram for enrollment numbers
- Parenthetical clarifications (e.g., exclusions, subgroup breakdowns) often appear immediately after the count in the text
Rules:
- Enter as a single integer when available
- Parenthetical context may follow the number to explain exclusions or subgroups (e.g., '110 (38 excluded as a result of inadequate cellularity)', '420 (502 minus 32 stomatitis minus 50 normal mucosa)')
- If the paper reports only a year-like number or a value that looks implausible as a patient count (e.g., 2018, 2026), double-check before entering
- Use 'NR' if not reported; 'NR because [reason]' is acceptable when the paper explains why a count is not available
- Do not confuse enrollment count with analyzed count — this field captures the enrolled total
Examples:
- {'value': '87', 'source_text': ''}

### `n_patients_rs_analyzed`  _(source-grounded)_
Number of patients whose biopsy results were included in the final analysis, which may be less than the enrolled total due to inadequate specimens, withdrawal, or other exclusions.
Hints:
- Look for phrases such as 'included in the analysis', 'evaluable patients', 'adequate specimens', or 'final analysis'
- Check the Results section or a participant flow diagram for the analyzed subset
- If the paper embeds the count within a descriptive sentence, quote that sentence as source_text
Rules:
- Enter as a single integer or NR/NA
- Parenthetical context or a brief quoted sentence may be appended when the paper embeds the analyzed count within a descriptive sentence
- Use NR if not reported
- If equal to n_patients_rs, still enter the number explicitly
- Do not substitute the enrolled count for the analyzed count without explicit confirmation they are the same
Examples:
- {'value': '87', 'source_text': ''}

### `n_lesions_rs`  _(source-grounded)_
Total number of lesions biopsied when the study reports lesion-level counts, as distinct from patient-level counts.
Hints:
- Look for phrases such as 'lesions biopsied', 'biopsy sites', 'lesion-level analysis', or 'specimens obtained'
- Check the Methods and Results sections for lesion counts separate from patient counts
- If the study clearly uses only one biopsy per patient with no separate lesion count reported, use NA
Rules:
- Enter as a single integer or NR/NA
- Use NA when the study reports only patient-level counts (one biopsy per patient, no separate lesion count)
- Use NR if the study uses lesion-level analysis but does not state the total
- Parenthetical context may follow when needed
- 'NR because [reason]' is acceptable when the paper explains why a lesion count is unavailable
- Do not conflate patient count with lesion count — a single patient may contribute multiple lesions
Examples:
- {'value': '122', 'source_text': ''}

### `site_of_biopsy`  _(source-grounded)_
Where the biopsy was taken from — anatomical site(s) within the oral cavity, the selection rule used (e.g., 'biopsy taken from the blue-stained area'), or a combination of both. If the paper reports anatomical sites, list them (e.g., 'buccal mucosa, lateral tongue, floor of mouth'). If the site was determined by the index-test result, state that rule verbatim or paraphrased. If no site information is given, use NR; a brief contextual quote may follow NR on the same entry (e.g., 'NR. "a biopsy sample was taken from suspected lesions"'). Do NOT put biopsy technique here (that belongs in reference_standard_comment).
Hints:
- Look in the Methods section under headings such as 'Biopsy', 'Reference Standard', 'Histopathology', or 'Specimen Collection'
- Check whether the paper states that biopsy sites were guided by the index test (e.g., Lugol's iodine staining, VELscope, toluidine blue) — if so, record that selection rule
- Anatomical sites are often listed in participant inclusion criteria or in the description of the clinical examination workflow
Rules:
- List all anatomical sites mentioned (e.g., 'buccal mucosa, lateral tongue, floor of mouth')
- If the site was selected based on an index-test result, state that rule verbatim or paraphrased (e.g., 'biopsy taken from the Lugol-unstained area')
- Do NOT include biopsy technique (incisional, punch, etc.) in this field — that belongs in reference_standard_comment
- Use "NR" if no site information is provided; a brief contextual quote from the paper may follow NR on the same entry (e.g., 'NR. "a biopsy sample was taken from suspected lesions"')
Examples:
- {'value': 'Lateral tongue, floor of mouth, buccal mucosa', 'source_text': ''}

### `reference_standard_comment`  _(source-grounded)_
Biopsy technique and any brief context about how or why biopsies were performed. Lead with the technique name (e.g., 'Incisional biopsy', 'Punch biopsy', 'Wedge biopsy', 'Surgical excision', 'Scalpel biopsy'). Multiple techniques may be combined (e.g., 'Incisional and/or excisional biopsy'). A short contextual note may follow the technique name when the paper's description is tied to the workflow (e.g., 'lesions examined under DrOroscope were referred for a confirmatory biopsy', 'To be included, patients had to have a confirmed diagnosis by biopsy'). Use NR if neither the technique nor the context is specified; 'unclear: [quoted text]' is acceptable when the paper describes specimen processing rather than the biopsy procedure itself. Spell 'biopsy' correctly.
Hints:
- Look in the Methods section under 'Reference Standard', 'Biopsy Procedure', 'Histopathological Examination', or 'Specimen Collection'
- The technique name is often embedded in a sentence describing the clinical workflow (e.g., 'All suspicious lesions underwent incisional biopsy')
- If the paper only describes tissue processing (fixation, staining) without naming the collection technique, use 'unclear: [quoted text]'
Rules:
- Lead with the technique name (e.g., 'Incisional biopsy', 'Punch biopsy', 'Wedge biopsy', 'Surgical excision', 'Scalpel biopsy')
- Multiple techniques may be combined (e.g., 'Incisional and/or excisional biopsy')
- A short contextual note may follow the technique name when tied to the study workflow
- Use 'unclear: [quoted text]' when the paper describes specimen processing rather than the biopsy collection procedure itself
- Use "NR" if neither the technique nor any relevant context is specified
- Spell 'biopsy' correctly — never 'biopsey' or 'biposy'
Examples:
- {'value': 'Incisional biopsy', 'source_text': ''}

### `calibration_rs_examiners`  _(source-grounded)_
Who assessed the biopsy slides, their qualifications, and any calibration or inter-rater reliability process. Acceptable content: the number and type of pathologists (e.g., 'two oral pathologists'), their credentials or experience level, the grading guidelines used (e.g., WHO 2017 criteria), joint reading or consensus arrangements, and kappa or agreement statistics. If the paper only states credentials without describing the reading process, record those credentials. If the paper says no calibration was done, write 'No calibration reported' followed by any available detail. Use NR when the paper mentions pathology reading but provides no information about who did it or how.
Hints:
- Look in the Methods section under 'Histopathological Assessment', 'Pathology Review', 'Reference Standard', or 'Grading'
- Kappa statistics or inter-rater agreement values are strong indicators that calibration was described
- Check whether the paper names the grading system used (e.g., WHO 2017, Ljubljana classification, Brothwell criteria)
- If only one pathologist is mentioned with no calibration process, record their credentials and note 'No calibration reported'
Rules:
- Record the number and type of pathologists (e.g., 'two oral pathologists', 'one experienced histopathologist')
- Include credentials, experience level, or institutional affiliation if stated
- Include grading guidelines or classification systems used (e.g., WHO 2017 criteria)
- Include consensus or joint-reading arrangements and any kappa/agreement statistics if reported
- If no calibration is described, write 'No calibration reported' followed by any available credential detail
- Use "NR" when the paper mentions pathology reading but provides no information about who performed it or how
Examples:
- {'value': 'Two oral pathologists graded independently using WHO 2017 criteria; disagreements resolved by consensus.', 'source_text': ''}

### `blinding_rs_examiners`  _(source-grounded)_
Whether the pathologist(s) reading the biopsy were blinded to the index-test result and other clinical information. Preferred answers: 'Blinded' (with a brief quoted phrase or explanation), 'Not blinded' (with a brief reason), 'Probably blinded' (with the reasoning, e.g., biopsy was taken prior to the index test), or 'NR'. A brief contextual note may follow NR when the paper provides relevant but incomplete information (e.g., 'NR — two independent pathologists assessed specimens but blinding not mentioned'). Quote the relevant sentence from the paper when available.
Hints:
- Look for explicit blinding statements in the Methods section under 'Blinding', 'Masking', 'Reference Standard', or 'Histopathological Assessment'
- If the biopsy was taken before the index test was performed, 'Probably blinded' is a reasonable inference — state the reasoning
- If the paper describes independent pathologist review without mentioning blinding, note this after NR
Rules:
- Use one of the four preferred answers: 'Blinded', 'Not blinded', 'Probably blinded', or 'NR'
- Follow the answer with a brief quoted phrase or explanation from the paper when available
- Use 'Probably blinded' with explicit reasoning when blinding can be inferred from the study workflow (e.g., biopsy preceded index test)
- Use 'Not blinded' with a brief reason when the paper explicitly states or implies pathologists had access to index-test results
- Use "NR" when blinding status is not mentioned; a brief contextual note may follow (e.g., 'NR — two independent pathologists assessed specimens but blinding not mentioned')
- Quote the relevant sentence from the paper verbatim when it directly addresses blinding
Examples:
- {'value': 'Blinded — "The pathologists examining the biopsy specimens were not informed regarding the staining information of the samples."', 'source_text': ''}

### `positive_threshold_oral_cancer_comment`  _(source-grounded)_
Histopathological criteria that the study uses to call a biopsy POSITIVE for oral cavity cancer (invasive carcinoma or carcinoma in situ).
Hints:
- Look in the Methods section under 'Reference standard', 'Histopathological diagnosis', or 'Outcome definition'
- Check the Results section for descriptions of confirmed cancer cases
- Look for terms such as 'squamous cell carcinoma', 'carcinoma in situ', 'verrucous carcinoma', or 'oral cancer recurrence'
- If the study defines a binary positive/negative outcome, find the cancer-side definition
Rules:
- Extract the exact histological label(s) used by the study to define oral cancer positivity
- Do NOT include dysplasia-only grades (e.g., mild, moderate, severe dysplasia) unless the study explicitly groups them with carcinoma in the same positivity class
- Use 'NA' when oral cancer is NOT a target condition in the study
- Use 'NR' when the study targets oral cancer but does not specify the histological cut-off
- Separate multiple histological categories with ' + ' or list them as written in the paper
Examples:
- {'value': 'Carcinoma in situ and squamous cell carcinoma', 'source_text': ''}

### `positive_threshold_opmd_comment`  _(source-grounded)_
Histopathological criteria that the study uses to call a biopsy POSITIVE for an oral potentially malignant disorder (OPMD).
Hints:
- Look in the Methods section under 'Reference standard', 'Histopathological diagnosis', or 'Outcome definition'
- Check for dysplasia grading thresholds such as 'mild, moderate and severe dysplasia' or 'any dysplasia'
- Look for OPMD subtype labels such as 'leukoplakia with epithelial dysplasia' or 'oral submucous fibrosis'
- Carcinoma in situ may appear here if the study groups it with dysplasia rather than with invasive carcinoma
- Patient counts per dysplasia grade may be embedded in the threshold description — include them if present
Rules:
- Extract the exact histological label(s) or grade threshold(s) used by the study to define OPMD positivity
- Include patient counts per grade if the paper embeds them in the threshold description
- Use 'NA' when OPMD is not a target condition in the study
- Use 'NR' when OPMD is targeted but the threshold is not stated
- Do not conflate with the oral cancer threshold unless the study explicitly merges them
Examples:
- {'value': 'Mild, moderate, and severe epithelial dysplasia', 'source_text': ''}

### `final_diagnosis_patients`  _(source-grounded)_
The histopathological diagnoses observed in the study sample, preferably as a frequency table or count list by diagnostic category.
Hints:
- Look in the Results section, often in a 'Study population', 'Patient characteristics', or 'Histopathological findings' table or paragraph
- Check for a breakdown such as 'X normal, Y mild dysplasia, Z SCC'
- If no per-category breakdown is given, look for a summary label describing the positive and negative case mix
- Percentage breakdowns are acceptable when absolute counts are not reported
Rules:
- Preferred format: a count or frequency list by diagnostic category (e.g., '40 normal, 22 mild dysplasia, 10 moderate dysplasia, 5 SCC')
- If no per-category breakdown is available, enter the positivity category label as a summary (e.g., 'dysplasia and carcinoma')
- Use 'NR' if the paper does not report diagnosis distribution at all
- Do not invent or infer counts not explicitly stated in the paper
Examples:
- {'value': '40 normal, 22 mild dysplasia, 10 moderate dysplasia, 5 SCC', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"paper": {"value": "...", "source_text": "..."}, "reference_standard_type": {"value": "...", "source_text": "..."}, "positivity_threshold_transformed": {"value": "...", "source_text": "..."}, "n_patients_rs": {"value": "...", "source_text": "..."}, "n_patients_rs_analyzed": {"value": "...", "source_text": "..."}, "n_lesions_rs": {"value": "...", "source_text": "..."}, "site_of_biopsy": {"value": "...", "source_text": "..."}, "reference_standard_comment": {"value": "...", "source_text": "..."}, "calibration_rs_examiners": {"value": "...", "source_text": "..."}, "blinding_rs_examiners": {"value": "...", "source_text": "..."}, "positive_threshold_oral_cancer_comment": {"value": "...", "source_text": "..."}, "positive_threshold_opmd_comment": {"value": "...", "source_text": "..."}, "final_diagnosis_patients": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
