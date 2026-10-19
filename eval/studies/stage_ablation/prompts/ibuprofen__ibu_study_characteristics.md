# Single-call extraction — Study Characteristics (ibuprofen)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `country`  _(source-grounded)_
Country in which the study was conducted (from the affiliations or setting statement). If multiple sites across countries, list all comma-separated. Use 'NR' if not stated.
Hints:
- Check author affiliations, the methods/setting section, or acknowledgements for the country of study conduct
Rules:
- If multiple sites across countries are reported, list all countries comma-separated
- Use 'NR' if not stated
Examples:
- {'value': 'Egypt', 'source_text': ''}

### `surgery_type`  _(source-grounded)_
Type of surgery or procedure the children underwent, verbatim from the paper (e.g. 'tonsillectomy with/without adenoidectomy', 'dental extraction', 'orthodontic separator placement', 'inguinal herniorrhaphy'). Use 'NR' if not stated.
Hints:
- Look in the methods, participants, or procedure section describing the surgical intervention
Rules:
- Extract the surgery/procedure name verbatim from the document, preserving original wording
- Do not paraphrase or normalize terminology beyond what is written
- Use "NR" if not stated
Examples:
- {'value': 'tonsillectomy with or without adenoidectomy', 'source_text': ''}

### `route_of_administration`  _(source-grounded)_
Route by which ibuprofen was administered in this study. 'oral' = by mouth (tablet/suspension); 'IV' = intravenous; 'rectal' = suppository. Use 'NR' if not stated.
Allowed values: "oral", "IV", "rectal", "NR"
Examples:
- {'value': 'oral', 'source_text': ''}

### `outcomes_measured`  _(source-grounded)_
The full list of outcomes this study measured/reported, taken verbatim from the paper's Outcomes/Methods (this mirrors the 'Outcomes' column of the review's Table 1). Record every outcome as a '; '-separated list, in the paper's own wording (the list may be long). Use 'NR' if not stated.
Hints:
- Look in the Methods, Outcome Measures, or Endpoints sections of the paper
- Check subsections labeled 'Primary Outcome(s)' and 'Secondary Outcome(s)'
- Outcomes may also be listed in table footnotes or the study protocol summary
Rules:
- List every outcome the paper states it measured, not just the ones with reported results
- Preserve the paper's own wording for each outcome rather than paraphrasing
- Separate multiple outcomes with '; ' in a single string
- Include both primary and secondary outcomes if distinguished in the source
- Use "NR" if not reported
Examples:
- {'value': 'Postoperative pain score assessed by a third party (mCHEOPS scale); Time to first rescue analgesia; Total morphine consumption; Incidence of adverse effects (vomiting, epigastric pain, bleeding)', 'source_text': ''}

### `trial_design`  _(source-grounded)_
Verbatim trial design description (blinding, number of arms, parallel/crossover, superiority/non-inferiority) as the paper states it. Example: 'Double-blind, non-inferiority RCT with 2 parallel arms'.
Hints:
- Look in the abstract or methods section, often near the study design or trial registration statement
Rules:
- Extract the trial design description as close to verbatim as possible from the source text
- Use "NR" if not reported
Examples:
- {'value': 'Double-blind RCT', 'source_text': ''}

### `number_of_centres`  _(source-grounded)_
Recruiting-centre count as a coded value: output '1' if the trial is single-centre / one centre; 'multicentre' if it is described as multicentre without a specific count; otherwise 'NR'. Do NOT write phrases like 'single-centre' or '3 centres' — map single-centre to '1'.
Hints:
- Check the methods section for phrases like 'single-centre', 'single-center', 'multicentre', 'multicenter', or a specific number of sites
Rules:
- Output must be coded as '1' for single-centre/one centre studies
- Output 'multicentre' if described as multicentre without a specific numeric count
- Do NOT output phrases like 'single-centre' or '3 centres' verbatim
- Use 'NR' if the number of centres cannot be determined
Examples:
- {'value': '1', 'source_text': ''}

### `funding_source`  _(source-grounded)_
Funder(s) verbatim from the funding / acknowledgements statement, or 'none declared' (OPTIONAL). Use 'NR' if not stated.
Hints:
- Look in the funding, financial disclosure, or acknowledgements section, often near the end of the document
Rules:
- Extract the funder name(s) verbatim as written in the funding or acknowledgements statement
- If the document explicitly states no funding was received, use 'none declared'
- Use "NR" if not stated
Examples:
- {'value': 'none declared', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"country": {"value": "...", "source_text": "..."}, "surgery_type": {"value": "...", "source_text": "..."}, "route_of_administration": {"value": "...", "source_text": "..."}, "outcomes_measured": {"value": "...", "source_text": "..."}, "trial_design": {"value": "...", "source_text": "..."}, "number_of_centres": {"value": "...", "source_text": "..."}, "funding_source": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
