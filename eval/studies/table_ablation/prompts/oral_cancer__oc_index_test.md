# Single-call extraction — Index Test (oral_cancer)

You are extracting the "index_test_arms" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

All index-test arms evaluated in the study, with one structured row per arm capturing test identity, technique, participant counts, positivity criteria, and assessor blinding details.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `index_test_arm_label`  _(anchor)_
Full label for this specific index-test arm as it appears in the extraction spreadsheet, including the test type and concentration if applicable (e.g., 'Vital Staining (1% Toluidine Blue)', 'Vital Staining (5% Acetic Acid)'). This links the row to the correct arm in multi-test studies.
Hints:
- Look in the Methods section, tables of study arms, or figure legends where individual index tests are named or listed
- In multi-arm studies, each distinct test (e.g., different staining agents or concentrations) should yield a separate label
- If the paper uses abbreviations (e.g., 'TB' for toluidine blue), expand to the full label format used in the extraction spreadsheet
- If the arm label is not stated explicitly, construct it from the broad category and the specific reagent/concentration reported (e.g., 'Vital Staining (1% Toluidine Blue)')
- In single-arm studies, the label is typically the name of the one index test evaluated
Rules:
- Copy the label exactly as it appears in the extraction spreadsheet; if constructing from paper text, follow the format: 'Category (Reagent/Concentration)'
- Include the concentration in parentheses when reported (e.g., '1% Toluidine Blue', '5% Acetic Acid')
- Each unique index-test arm must have a distinct label — do not reuse the same label for two different arms
- Do not abbreviate the category or reagent name unless the spreadsheet convention explicitly uses abbreviations
- This field must always be populated; it is the row anchor — do not leave blank or use NR

### `index_test_original_category`  _(anchor)_
Broad category of the index test as originally classified in the paper or by the reviewers before transformation.
Allowed values: "Vital Staining", "Light-based test - Tissue reflectance", "Light-based test - Narrow band imaging", "Light-based test - Autofluorescence", "Cytology", "Combined", "Other"
Hints:
- Look in the Methods section under 'Index Test', 'Diagnostic Test', or 'Intervention' headings for how the authors categorise the test
- Check the abstract or introduction where the test type is first introduced — authors often name the category explicitly (e.g., 'vital staining', 'autofluorescence device', 'cytology brush')
- In multi-arm studies, each arm may be described with its category label in a table or list of interventions
- If the paper does not use these exact category names, map the described test to the closest matching option based on the test mechanism
Rules:
- Must be exactly one of the listed options — no free-text variants
- Use 'Combined' only when the arm explicitly combines tests from two or more distinct categories (e.g., vital staining plus autofluorescence); do not use it for two agents within the same category
- Use 'Other' if the test does not fit any listed category, and record details in comments_index_test
- This field captures the original/pre-harmonisation classification; do not apply the harmonised type from index_test_type_transformed here
- Select the category that best matches the test mechanism as described by the authors, even if the authors do not use the exact option wording

### `index_test_type_transformed`  _(anchor)_
Harmonized index test type applied consistently across the review. Vital-staining studies are sub-classified by agent. Select 'other' and describe in comments if the test does not fit any listed category.
Allowed values: "toluidine blue", "acetic acid", "lugol's iodine", "methylene blue", "rose bengal", "toluidine blue + acetic acid", "acetic acid + lugol's iodine", "methylene blue + lugol's iodine", "tissue reflectance", "narrow band imaging", "autofluorescence", "cytology", "combined", "other"
Hints:
- Look at the Methods section where the index test is described, and cross-reference with the index_test_original_category and index_test_reagent_name fields already extracted
- For vital staining arms, identify the specific agent (e.g., toluidine blue, acetic acid, lugol's iodine) to select the correct sub-category
- For combined tests, use a combination option (e.g., 'toluidine blue + acetic acid') if the exact pairing is listed; otherwise use 'combined'
- If the paper uses a synonym or abbreviation (e.g., 'TB' for toluidine blue, 'AA' for acetic acid), map it to the appropriate harmonized option
- When the test type is implicit (e.g., described only by brand name), infer the category from the reagent name or device description
Rules:
- Must be exactly one of the listed options — no free-text modifications
- Use lowercase exactly as shown in the options list (e.g., 'toluidine blue', not 'Toluidine Blue')
- If the test uses two agents listed as a combination option, select that combination option rather than 'combined'
- Use 'combined' only when the multi-test combination does not match any of the specific paired options
- Use 'other' if the test type does not fit any listed category, and document the actual test type in comments_index_test
- This field must be populated for every arm — do not leave blank or use NR

### `index_test_reagent_name`  _(anchor)_
Specific reagent, agent, or device name and concentration as used in the study (e.g., '1% toluidine blue', '5% acetic acid'). This is more specific than the broad test category and captures the exact formulation used.
Hints:
- Look in the Methods section under 'Index Test', 'Materials', or 'Procedure' for the exact reagent name and concentration
- The concentration is often stated alongside the agent name (e.g., '1% toluidine blue solution', '3% acetic acid')
- For device-based tests (e.g., tissue reflectance, narrow band imaging), record the device name and model if given rather than a chemical concentration
- If multiple concentrations were tested in the same arm, record all as stated (e.g., '1% and 2% toluidine blue')
- The reagent name may appear in the abstract or introduction when the test is first introduced, as well as in the Methods
Rules:
- Record the reagent or device name and concentration exactly as stated in the paper; do not paraphrase or standardise spelling beyond minor normalisation
- Include the concentration unit (%, mg/mL, etc.) when reported
- Do not conflate this field with index_test_type_transformed (the harmonised category) or index_test_commercial_name (the brand name) — record the generic formulation here
- Use "NR" if neither the reagent name nor concentration is reported anywhere in the paper

### `index_test_commercial_name`  _(anchor)_
Commercial or brand name of the test kit or device if reported. Use NR if not reported.
Hints:
- Look in the Materials, Methods, or Index Test sections for brand names, trade names, or manufacturer product names
- Commercial names are often presented alongside the reagent name or device description, sometimes in parentheses or as a footnote
- Check figure legends, tables, and supplementary materials where product names may be listed separately from the main text
- Distinguish from the reagent name (captured in index_test_reagent_name) — this field captures only the marketed product or brand identity
Rules:
- Record the commercial or brand name exactly as it appears in the paper, preserving capitalisation and any registered trademark indicators
- Do not conflate with the generic reagent name or concentration already captured in index_test_reagent_name
- Use "NR" if no commercial or brand name is mentioned for this arm

### `site_selection_index_test`  _(value)_
How and where the index test was applied within the oral cavity — the site-selection procedure used by the clinician. Quote verbatim or paraphrase closely. Use NR if not reported.
Hints:
- Look in the Methods section under 'Index Test', 'Procedure', or 'Examination Protocol' for descriptions of which oral sites were examined or how sites were selected for staining/testing
- Check for phrases describing whole-mouth rinse, targeted application to suspicious lesions, or systematic examination of specific anatomical sites (e.g., floor of mouth, lateral tongue, buccal mucosa)
- For the already-identified arm row, locate the site-selection description specific to that test arm — in multi-arm studies, each arm may have a distinct application site or selection procedure
Rules:
- Quote verbatim if the paper provides a clear, concise description; paraphrase closely if the original text is very long
- Do not conflate site-selection with the step-by-step technique (captured in the 'technique' field) — focus on which sites were examined and how they were chosen, not the procedural steps
- Do not duplicate information already captured in 'technique'; if site selection is embedded in technique text, extract only the site-relevant portion here
- Use "NR" if the paper does not describe how or where the index test was applied within the oral cavity

### `specimen_collection`  _(value)_
Description of how the specimen was collected for tests requiring tissue or cellular material. Use NA for in-vivo staining tests where no specimen is collected.
Hints:
- Look in the Methods section under specimen collection, sample preparation, or cytology procedures for the identified index-test arm
- For cytology arms, look for descriptions of brush biopsy, scrape, swab, or exfoliative cytology techniques
- For tissue reflectance or narrow band imaging arms, no specimen is typically collected — use NA
- Distinguish this field from 'technique', which covers the full application procedure; this field focuses specifically on how the physical specimen was obtained
Rules:
- Quote verbatim or paraphrase closely from the Methods section
- Use "NA" when the index test is an in-vivo staining or optical test that does not require specimen collection (e.g., toluidine blue, acetic acid, tissue reflectance, narrow band imaging, autofluorescence)
- Use "NR" if the test requires specimen collection but the collection method is not described
- Do not conflate specimen collection with the staining or analysis technique — capture only the physical collection step

### `technique`  _(value)_
Step-by-step technique used to apply or perform the index test, as described in the Methods section. Quote verbatim if precise; paraphrase for very long descriptions. Use NR if not stated.
Hints:
- Look in the Methods section under headings such as 'Index Test', 'Procedure', 'Staining Protocol', or 'Test Application'
- For the already-identified arm (e.g., toluidine blue, acetic acid), locate the paragraph describing how that specific agent or device was applied — rinse steps, dwell time, wash steps, interpretation procedure
- If multiple arms share a common technique, note any arm-specific deviations
- Technique details may also appear in a supplementary protocol or figure legend
Rules:
- Quote verbatim when the description is concise and precise; paraphrase or summarise when the original text is excessively long (>5 sentences)
- Capture the full procedural sequence: preparation, application, dwell/contact time, rinsing, and interpretation steps
- Do not conflate with site_selection_index_test (where the test was applied) or specimen_collection (how tissue was collected) — focus on the procedural steps of the test itself
- Do not conflate with positivity_threshold — technique describes application steps, not the criterion for a positive result
- Use "NR" if the technique is not described in the paper

### `n_patients_received`  _(value)_
Number of patients who received the index test (denominator for this arm). Use NR if not reported.
Hints:
- Look in the Methods or Results section for enrollment or flow diagrams describing how many patients underwent the index test for this specific arm
- In multi-arm studies, locate the arm-specific count rather than the overall study total
- Check participant flow tables, CONSORT-style diagrams, or the opening sentence of the Results section
- Distinguish from n_patients_analyzed: this is the number who received the test, not necessarily those whose results were analyzed
Rules:
- Output a whole integer (e.g., 45) representing the count of patients who received the index test for this arm
- Do not conflate with n_patients_analyzed (existing field): n_patients_received is the denominator before exclusions; n_patients_analyzed is the number actually included in analysis
- Do not sum across arms unless the paper explicitly states a combined figure applies to this arm
- Use "NR" if the number of patients who received the test is not reported

### `n_patients_analyzed`  _(value)_
Number of patients whose index test results were included in the analysis. Use NR if not reported.
Hints:
- Look in the Methods (participants/flow) or Results sections for the number of patients contributing to the index-test analysis for this specific arm
- Check participant flow diagrams, CONSORT-style tables, or footnotes to results tables for exclusions between enrollment and analysis
- Distinguish from n_patients_received: this is the post-exclusion count actually used in sensitivity/specificity calculations
- In multi-arm studies, confirm the count corresponds to the already-identified arm row, not the total study population
Rules:
- Output a whole integer (e.g., 120); do not include units, ranges, or text
- If the paper reports only one patient count without distinguishing received vs. analyzed, record it here and also in n_patients_received
- Use "NR" if the number of analyzed patients is not explicitly reported
- Do not conflate with n_lesions_analyzed — this field counts patients, not lesions

### `n_lesions_received`  _(value)_
Number of lesions that received the index test in studies where the unit of analysis is the lesion. Use NR if not reported or if unit of analysis is patients only.
Hints:
- Look in the Methods or Results section for enrollment or flow diagrams describing lesion-level counts
- Check participant flow tables, STARD-style diagrams, or the opening Results paragraph for the number of lesions enrolled or screened with this specific index-test arm
- Distinguish from n_lesions_analyzed (which is the post-exclusion count used in analysis); n_lesions_received is the pre-exclusion denominator
Rules:
- Record as a whole integer (e.g., 45, 120)
- Use "NR" if the number of lesions that received the test is not explicitly stated
- Use "NR" if the study's unit of analysis is patients rather than lesions
- Do not infer or calculate this value from other counts unless the paper explicitly states the derivation
- Do not confuse with n_lesions_analyzed, which captures the subset included in the final analysis

### `n_lesions_analyzed`  _(value)_
Number of lesions included in the index-test analysis. Use NR if not reported or if unit of analysis is patients only.
Hints:
- Look in the Results section, participant flow diagrams, or analysis tables for lesion-level counts specific to this index-test arm
- Distinguish from n_lesions_received (lesions that received the test) — this is the subset whose results were included in the final analysis
- In multi-arm studies, confirm the count corresponds to the specific arm already identified, not the total across all arms
- If the paper reports only patient-level counts and never mentions lesion counts, use NR
Rules:
- Output a whole integer (e.g., 45) or the string "NR"
- Use "NR" if lesion-level counts are not reported or if the unit of analysis is patients only
- Do not conflate with n_lesions_received; n_lesions_analyzed may be smaller due to exclusions or missing results
- Do not sum across arms unless the paper explicitly states a combined lesion count for this arm

### `positivity_threshold`  _(value)_
Exact or paraphrased positivity criterion used to classify a test result as positive. Quote verbatim as stated in the paper. Use NR if not pre-specified.
Hints:
- Look in the Methods section under 'Index Test', 'Test Interpretation', or 'Criteria for Positivity'
- May appear in a table describing test protocols or in the statistical analysis plan
- For vital staining tests, look for descriptions of colour retention, staining intensity, or uptake patterns that define a positive result
- This field captures the raw, verbatim or closely paraphrased criterion from the paper; the harmonized summary belongs in positivity_threshold_transformed
Rules:
- Quote verbatim from the paper wherever possible; paraphrase only when the original text is excessively long
- Do not summarize or harmonize — that is the role of positivity_threshold_transformed
- Use "NR" if no positivity criterion is pre-specified or described in the paper
- Do not leave blank; always use NR when the criterion is absent

### `positivity_threshold_transformed`  _(value)_
Harmonized short summary of the positivity threshold after review adjudication (e.g., 'positive: stain', 'positive: unstained'). Allows cross-study comparison.
Hints:
- Derive from the verbatim positivity_threshold already extracted for this arm — do not re-read the paper independently
- Apply reviewer adjudication conventions to collapse varied phrasings into a standardized short label
- Common patterns: 'positive: stain retention', 'positive: unstained area', 'positive: fluorescence loss', 'positive: abnormal reflectance'
- If the arm's positivity_threshold is NR, this field should also be NR
Rules:
- Output a short harmonized phrase, typically 5–15 words, beginning with 'positive:' followed by the key criterion
- Normalize equivalent phrasings across arms and studies to the same label (e.g., all 'blue stain retained' variants → 'positive: stain retention')
- Do not copy the verbatim positivity_threshold text — this field is the adjudicated harmonized form
- Use "NR" if the positivity threshold was not reported and cannot be inferred

### `calibration_of_assessors`  _(value)_
Any training, calibration, or standardization undertaken by the clinicians who applied or interpreted the index test before the study. Use NR if not reported.
Hints:
- Look in the Methods section under headings such as 'Training', 'Calibration', 'Standardization', 'Examiner training', or 'Quality assurance'
- May appear in a subsection describing the index test procedure or in a description of examiner qualifications
- Check for mention of pilot studies, consensus exercises, kappa exercises, or inter-rater reliability training sessions conducted prior to data collection
- For multi-arm studies, note whether calibration details are shared across arms or specific to this arm
Rules:
- Quote verbatim or paraphrase closely the calibration or training procedure described for the assessors of this specific index-test arm
- Include details such as number of training cases, consensus sessions, use of reference images, or inter-examiner agreement exercises if reported
- Do not conflate with blinding information already captured in blinding_index_test_assessors or blinding_index_test_examiner
- Use "NR" if no training, calibration, or standardization of assessors is mentioned in the paper

### `blinding_index_test_assessors`  _(value)_
Whether the people who applied or interpreted the index test were blinded to the reference standard result. Quote the relevant sentence if available. Use NR if not reported.
Hints:
- Look in the Methods section under 'blinding', 'masking', or 'study design' subsections
- Check the risk-of-bias or quality assessment discussion for blinding statements
- This field concerns blinding to the reference standard result specifically — distinct from blinding_index_test_examiner, which concerns blinding to other clinical information
- For the already-identified index-test arm, locate any sentence describing whether assessors were masked to histopathology or biopsy results when reading the index test
Rules:
- Quote the relevant sentence verbatim if the paper explicitly states blinding status; paraphrase only if the original text is very long
- Capture blinding to the reference standard result only — do not conflate with blinding to patient history or demographics (captured in blinding_index_test_examiner)
- Use "NR" if blinding status is not reported or cannot be inferred from the text
- Do not infer blinding from study design alone unless the paper explicitly states it

### `blinding_index_test_examiner`  _(value)_
Whether the examiner performing or reading the index test was blinded to other clinical information (patient history, demographics, prior diagnoses). Note any ambiguity that affects risk-of-bias judgement. Use NR if not reported.
Hints:
- Look in the Methods section under 'blinding', 'masking', or 'examiner' subsections
- Check the study design or risk-of-bias discussion for statements about what information the index-test examiner had access to
- Distinguish from blinding_index_test_assessors: this field focuses on blinding to clinical information (history, demographics, prior diagnoses) rather than blinding to the reference standard result
- If the paper uses a single examiner description that conflates both types of blinding, note the ambiguity in this field
Rules:
- Copy or closely paraphrase the relevant sentence(s) from the paper describing what clinical information the examiner was or was not blinded to
- If the paper states blinding but does not specify what information was withheld, record what is stated and flag the ambiguity
- Do not conflate with blinding_index_test_assessors, which captures blinding to the reference standard; this field is specifically about blinding to patient clinical information
- Use "NR" if the paper does not report whether the examiner was blinded to clinical information

### `comments_index_test`  _(value)_
Free-text reviewer notes about this index-test arm — methodological concerns, unusual study designs, multi-arm overlap, or clarifications needed before meta-analysis. Use NA if no notes are needed.
Hints:
- Check reviewer annotation columns, footnotes, or supplementary notes sections for remarks specific to this arm
- Look for any caveats mentioned in the Methods or Discussion that apply specifically to this index-test arm and are not captured by other structured fields
- Note any overlap or shared patients/lesions between arms in multi-arm studies that could affect independence assumptions in meta-analysis
Rules:
- Enter free text; no controlled vocabulary applies
- Use "NA" if there are no reviewer notes or concerns for this arm — do not leave blank
- Do not duplicate information already captured in structured fields (e.g., positivity_threshold, blinding, technique); only record residual concerns or clarifications not covered elsewhere
- Keep notes concise but sufficiently detailed for a meta-analyst to act on them without returning to the source paper

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"index_test_arm_label": {"value": "...", "source_text": "..."}, "index_test_original_category": {"value": "...", "source_text": "..."}, "index_test_type_transformed": {"value": "...", "source_text": "..."}, "index_test_reagent_name": {"value": "...", "source_text": "..."}, "index_test_commercial_name": {"value": "...", "source_text": "..."}, "site_selection_index_test": {"value": "...", "source_text": "..."}, "specimen_collection": {"value": "...", "source_text": "..."}, "technique": {"value": "...", "source_text": "..."}, "n_patients_received": {"value": "...", "source_text": "..."}, "n_patients_analyzed": {"value": "...", "source_text": "..."}, "n_lesions_received": {"value": "...", "source_text": "..."}, "n_lesions_analyzed": {"value": "...", "source_text": "..."}, "positivity_threshold": {"value": "...", "source_text": "..."}, "positivity_threshold_transformed": {"value": "...", "source_text": "..."}, "calibration_of_assessors": {"value": "...", "source_text": "..."}, "blinding_index_test_assessors": {"value": "...", "source_text": "..."}, "blinding_index_test_examiner": {"value": "...", "source_text": "..."}, "comments_index_test": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
