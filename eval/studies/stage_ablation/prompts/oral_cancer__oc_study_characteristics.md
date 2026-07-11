# Single-call extraction — Study Characteristics (oral_cancer)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `refid`  _(source-grounded)_
Unique reference identifier assigned to the study in the review database (e.g., '78', '7'). Copy exactly as listed in the extraction spreadsheet.
Hints:
- Look for a reference ID, study ID, or record number assigned in the review database or extraction spreadsheet header
- This value is typically pre-assigned and may appear in document metadata, headers, or accompanying extraction materials
Rules:
- Copy the identifier exactly as listed — do not modify, pad, or reformat
- The value is a string (may be numeric digits but treat as text)
- Use "NR" if the reference identifier is not present in the document
Examples:
- {'value': '78', 'source_text': ''}

### `authors_last_name`  _(source-grounded)_
Last name of the first author followed by the publication year. Format: '[LastName] [YYYY]' (e.g., 'Winton Kalluvelil 2022'). Use the first author only.
Hints:
- Look at the author list at the top of the article or in the citation/reference section
- Use only the first-listed author's last name
- The year should match the publication year of the article, not the data collection period
Rules:
- Format must be '[LastName] [YYYY]' with a single space between last name and year
- Use the first author's last name only — do not include given names, initials, or additional authors
- The year must be a 4-digit publication year
- Use "NR" if the author name or year cannot be determined
Examples:
- {'value': 'Winton Kalluvelil 2022', 'source_text': ''}

### `year_of_study`  _(source-grounded)_
Year the article was published as a 4-digit integer. Do NOT use the data-collection period.
Hints:
- Look for the publication date in the journal header, copyright notice, or citation metadata
- Distinguish between the publication year and the study's data-collection period — use only the publication year
Rules:
- Must be a 4-digit integer (e.g., 2022)
- Do NOT use the data-collection or study-period year
- Use "NR" if the publication year cannot be determined
Examples:
- {'value': '2022', 'source_text': ''}

### `study_period`  _(source-grounded)_
When the study data were collected, as reported in the paper (e.g., '2016 to 2018'). Use NR for studies before 2021 (Walsh or Lingen cohorts) or when not reported.
Hints:
- Look in the Methods section under 'Study Period', 'Data Collection', or 'Participants' for date ranges
- Check the abstract for a brief mention of the study timeframe
- Look for phrases like 'between [year] and [year]', 'from [year] to [year]', or 'during [year]–[year]'
Rules:
- Extract the data collection period verbatim or in a normalised 'YYYY to YYYY' format as reported
- Use "NR" for studies belonging to the Walsh or Lingen cohorts (studies conducted before 2021)
- Use "NR" if the study period is not stated anywhere in the paper
- Do not infer or estimate dates not explicitly reported
Examples:
- {'value': '2016 to 2018', 'source_text': ''}

### `country`  _(source-grounded)_
Country (or countries) where the study was conducted. If multi-site across countries, list all comma-separated. Use NR if not stated.
Hints:
- Look in the Methods section under 'Study Setting', 'Study Site', or 'Participants'
- Check author affiliations or the abstract for country mentions
- Multi-country studies often describe sites in a table or list within the Methods section
Rules:
- List the country or countries exactly as reported, using standard English country names
- For multi-country studies, list all countries comma-separated (e.g., 'India, China, Brazil')
- Use "NR" if the country of conduct is not stated anywhere in the paper
- Do not infer country from author affiliations alone unless explicitly confirmed in the text
Examples:
- {'value': 'India', 'source_text': ''}

### `number_of_centers`  _(source-grounded)_
Whether the study was conducted at a single site or multiple sites, as reported in the paper.
Allowed values: "Single center", "Multiple center", "Unclear or not reported"
Hints:
- Look in the Methods section for terms like 'single-center', 'multicenter', 'multi-site', or 'multi-institutional'
- Check the abstract or study design description for site count information
- A list of multiple hospitals or institutions in the Methods section indicates a multi-center study
Rules:
- Must be exactly one of the allowed options: 'Single center', 'Multiple center', or 'Unclear or not reported'
- Use 'Single center' if the paper explicitly states one site or institution
- Use 'Multiple center' if the paper mentions two or more sites, hospitals, or institutions
- Use 'Unclear or not reported' if the number of centers cannot be determined from the text
- Use "NR" only if the field value cannot be determined at all (maps to 'Unclear or not reported')
Examples:
- {'value': 'Single center', 'source_text': ''}

### `study_setting_verbatim`  _(source-grounded)_
Exact or paraphrased description of where the study was conducted (department name, hospital, city/country). Use NR if not stated.
Hints:
- Look in the Methods section under 'Study Setting', 'Study Site', or 'Participants' subsections
- Check the abstract for brief location mentions
- Institutional affiliations in the title page or author information may also indicate the setting
Rules:
- Extract the verbatim or closely paraphrased description of the study location including department, institution, city, and/or country as stated
- Do not infer or expand beyond what is explicitly stated in the document
- Use "NR" if the study setting is not mentioned anywhere in the document
Examples:
- {'value': 'Department of Oral Medicine and Radiology, rural Karnataka, India', 'source_text': ''}

### `study_setting_original`  _(source-grounded)_
Original setting category as reported, selected from the allowed options based on how the study describes its own setting.
Allowed values: "Primary care setting (general dental practice)", "Secondary or tertiary care setting (hospital or specialist dental service)", "Other", "Unclear or not reported"
Hints:
- Read the Methods section for explicit descriptions such as 'general dental practice', 'hospital', 'specialist clinic', or 'tertiary care centre'
- If the paper self-labels its setting, use that label to guide classification
- Community-based or population-based studies without a clinical site may fall under 'Other'
Rules:
- Must be exactly one of the allowed options
- Use exact spelling and capitalisation as listed in options
- Select 'Primary care setting (general dental practice)' for studies conducted in general dental practices or community dental clinics
- Select 'Secondary or tertiary care setting (hospital or specialist dental service)' for studies in hospitals, dental schools, or specialist referral services
- Select 'Other' if the setting is clearly described but does not fit primary or secondary/tertiary categories
- Select 'Unclear or not reported' if the setting cannot be determined from the text
- Use "NR" only if the field value itself cannot be assigned (treat as 'Unclear or not reported' in that case)
Examples:
- {'value': 'Secondary or tertiary care setting (hospital or specialist dental service)', 'source_text': ''}

### `study_setting_transformed`  _(source-grounded)_
Harmonized setting category after transformation, applied consistently across studies using the same allowed options as the original classification.
Allowed values: "Primary care setting (general dental practice)", "Secondary or tertiary care setting (hospital or specialist dental service)", "Other", "Unclear or not reported"
Hints:
- Apply the same classification logic as study_setting_original but with cross-study consistency in mind
- If the original category is ambiguous, use contextual clues (e.g., department name, referral patterns) to assign the most appropriate harmonized category
- Dental schools and university clinics are typically classified as secondary or tertiary care
Rules:
- Must be exactly one of the allowed options
- Use exact spelling and capitalisation as listed in options
- This field may differ from study_setting_original when the original report is ambiguous or inconsistent with standard definitions
- Apply harmonization rules uniformly: hospital-based or specialist-based settings → 'Secondary or tertiary care setting'; general practice or community dental → 'Primary care setting'
- Use 'Unclear or not reported' only when harmonization is genuinely impossible
- Use "NR" if the field value cannot be assigned after harmonization
Examples:
- {'value': 'Secondary or tertiary care setting (hospital or specialist dental service)', 'source_text': ''}

### `study_setting_comment`  _(source-grounded)_
Free-text notes clarifying the setting (e.g., rural vs. urban, specialty focus). Use NA if no comment needed.
Hints:
- Note any additional contextual details about the setting that are not captured by the category alone, such as rural/urban location, specialty department, or patient population served
- Check for geographic descriptors (e.g., 'rural Karnataka') or departmental names (e.g., 'Department of Oral Medicine and Radiology') that add nuance
Rules:
- Provide a brief free-text clarification if the setting has notable characteristics not captured by the category field
- Use "NA" if no additional comment is needed (i.e., the category alone is sufficient)
- Use "NR" only if the setting is entirely unreported and no comment can be made
- Do not repeat the verbatim setting description unless it adds meaningful clarification beyond what is in study_setting_verbatim
Examples:
- {'value': 'Department of Oral Medicine and Radiology, rural Karnataka, India', 'source_text': ''}

### `funding_category`  _(source-grounded)_
Classification of the funding source as reported in the paper.
Allowed values: "Industry and for-profit organizations", "Government, institutional, not-for-profit foundation", "None", "Not reported"
Hints:
- Look in the Acknowledgements, Funding, or Financial Disclosure sections near the end of the paper
- Check footnotes on the title page or abstract for funding statements
Rules:
- Must be exactly one of the allowed options
- Use exact spelling and capitalisation as listed in options
- Select 'None' if the paper explicitly states no funding was received
- Select 'Not reported' if the paper contains no mention of funding at all
- Use "NR" only if the field itself cannot be determined (e.g., document is truncated)
Examples:
- {'value': 'None', 'source_text': ''}

### `funding_comment`  _(source-grounded)_
Verbatim funding statement or relevant notes. Use NA if the funding category is self-explanatory.
Hints:
- Copy the exact funding statement from the Acknowledgements or Funding section
- If the funding category alone is sufficient (e.g., 'None' or 'Not reported'), set value to NA
Rules:
- Copy the funding statement verbatim from the document when present
- Use "NA" if the funding category is self-explanatory and no additional comment is needed
- Use "NR" if the document is unreadable or the field cannot be assessed
Examples:
- {'value': 'NA', 'source_text': ''}

### `conflicts_of_interest`  _(source-grounded)_
Conflict-of-interest declaration as stated in the paper.
Allowed values: "The authors declare conflict of interests.", "The authors declare that there are no conflicts of interest.", "Report includes no conflict of interest statements."
Hints:
- Look for a 'Conflict of Interest', 'Competing Interests', or 'Disclosures' section, typically near the end of the paper
- Check author contribution statements or footnotes for COI declarations
Rules:
- Must be exactly one of the allowed options
- Use exact spelling and capitalisation as listed in options
- Select 'The authors declare conflict of interests.' if any author discloses a conflict
- Select 'The authors declare that there are no conflicts of interest.' if the paper explicitly states no conflicts
- Select 'Report includes no conflict of interest statements.' if the paper contains no COI section or statement at all
- Use "NR" only if the document is truncated or the field cannot be assessed
Examples:
- {'value': 'The authors declare that there are no conflicts of interest.', 'source_text': ''}

### `conflicts_of_interest_comment`  _(source-grounded)_
Additional detail about the conflict-of-interest declaration if needed. Use NA if none.
Hints:
- If specific conflicts are named (e.g., speaker fees, stock ownership, advisory roles), quote them verbatim
- If the COI statement is a simple blanket declaration with no specifics, set value to NA
Rules:
- Copy relevant COI details verbatim from the document when specific conflicts are disclosed
- Use "NA" if no additional detail is needed beyond the selected conflicts_of_interest category
- Use "NR" if the document is unreadable or the field cannot be assessed
Examples:
- {'value': 'NA', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"refid": {"value": "...", "source_text": "..."}, "authors_last_name": {"value": "...", "source_text": "..."}, "year_of_study": {"value": "...", "source_text": "..."}, "study_period": {"value": "...", "source_text": "..."}, "country": {"value": "...", "source_text": "..."}, "number_of_centers": {"value": "...", "source_text": "..."}, "study_setting_verbatim": {"value": "...", "source_text": "..."}, "study_setting_original": {"value": "...", "source_text": "..."}, "study_setting_transformed": {"value": "...", "source_text": "..."}, "study_setting_comment": {"value": "...", "source_text": "..."}, "funding_category": {"value": "...", "source_text": "..."}, "funding_comment": {"value": "...", "source_text": "..."}, "conflicts_of_interest": {"value": "...", "source_text": "..."}, "conflicts_of_interest_comment": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
