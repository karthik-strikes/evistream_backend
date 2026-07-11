# Single-call extraction — Study Characteristics (periodontitis)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `country`  _(source-grounded)_
Country in which the study was conducted (from the affiliations or setting statement). If multiple sites across countries, list all comma-separated. Use 'NR' if not stated.
Hints:
- Check author affiliations, the Methods section under 'Study Setting' or 'Study Site', and any ethics approval statements for country names
- Country names may appear in institutional addresses or in descriptions of the study population
Rules:
- Extract the country name(s) exactly as they appear or in standard English form (e.g. 'Brazil', 'United States', 'United Kingdom')
- If the study was conducted across multiple countries, list all country names comma-separated (e.g. 'Brazil, Argentina')
- Do not infer country from language of publication alone
- Use "NR" if the country is not stated anywhere in the document
Examples:
- {'value': 'Brazil', 'source_text': ''}

### `setting`  _(source-grounded)_
Type of setting where the study was run (e.g. hospital, primary care, university clinic, community). Use 'NR' if not stated.
Hints:
- Look in the Methods section under 'Study Setting', 'Study Site', or 'Participants'
- Setting descriptions often appear in the opening sentences of the Methods section or in the abstract
Rules:
- Extract the setting type using concise descriptive terms (e.g. 'hospital', 'primary care clinic', 'university teaching hospital', 'community health centre')
- If multiple setting types are described, list them all (e.g. 'hospital, primary care')
- Use the terminology from the document where possible
- Use "NR" if the setting is not described anywhere in the document
Examples:
- {'value': 'hospital', 'source_text': ''}

### `number_of_centres`  _(source-grounded)_
Number of recruiting centres, sometimes with location (e.g. '1', '2 centres'). Use 'NR' if not stated.
Hints:
- Look in the Methods section for terms like 'single-centre', 'multicentre', 'multi-site', or explicit counts such as '3 hospitals'
- The abstract or study design statement often mentions whether the study was single- or multi-centre
Rules:
- Extract the number of centres as stated in the document, including any qualifying description (e.g. '1', '3 centres', '2 hospitals in São Paulo')
- If the document states 'single-centre' or 'monocentre', record as '1'
- If the document states 'multicentre' without specifying a number, record as 'multicentre (number NR)'
- Use "NR" if the number of centres is not stated anywhere in the document
Examples:
- {'value': '1', 'source_text': ''}

### `trial_design`  _(source-grounded)_
Verbatim trial design description (number of arms, parallel/crossover, blinding) as the paper states it.
Hints:
- Look in the Methods section under 'Study Design', 'Trial Design', or 'Design' subsections
- Check the abstract for a concise design statement
- Look for terms such as 'parallel', 'crossover', 'double-blind', 'open-label', 'single-arm', 'two-arm'
Rules:
- Extract the design description verbatim or as close to verbatim as the paper states it
- Include number of arms, allocation type (parallel/crossover), and blinding level when stated
- Do not paraphrase or abbreviate beyond what the paper itself uses
Examples:
- {'value': '2-arm RCT', 'source_text': ''}

### `recruitment_period`  _(source-grounded)_
Recruitment dates if reported (OPTIONAL). Use 'NR' if not stated.
Hints:
- Look in the Methods section under 'Participants', 'Recruitment', 'Study Population', or 'Setting'
- Check the abstract or results section for enrollment date ranges
- Dates may appear as month-year ranges, e.g., 'February 2011 to December 2013'
Rules:
- Report the recruitment period exactly as stated in the paper, including month and year when available
- If only years are given, report years only
- Use "NR" if recruitment dates are not reported
Examples:
- {'value': 'February 2011 to December 2013', 'source_text': ''}

### `funding_source`  _(source-grounded)_
Funder(s) verbatim from the funding / acknowledgements statement, or 'none declared' (OPTIONAL). Use 'NR' if not stated.
Hints:
- Look for a 'Funding', 'Acknowledgements', 'Financial Support', or 'Conflict of Interest' section, typically near the end of the paper
- Grant numbers and funder names are often listed together; capture both if present
- If the paper explicitly states no funding was received, record 'none declared'
Rules:
- Copy the funder name(s) verbatim as they appear in the document
- Include grant or award numbers if stated alongside the funder name
- If the document explicitly states no funding, use 'none declared'
- Use "NR" if no funding statement is present anywhere in the document
Examples:
- {'value': 'Fundação de Amparo à Pesquisa do Estado de São Paulo (FAPESP)', 'source_text': ''}

### `notes`  _(source-grounded)_
Free-text study-level notes (sample-size calculation, data limitations, conflicts of interest) (OPTIONAL). Use 'None' if no extra detail.
Hints:
- Check the Methods section for sample-size or power calculation details
- Check the Limitations, Discussion, or Conflict of Interest sections for relevant caveats
- Look for statements about data availability or data extraction difficulties (e.g., data presented only in graphs)
Rules:
- Record any study-level notes that are relevant to interpreting the data, including sample-size calculations, data limitations, or conflicts of interest
- If no noteworthy additional detail exists, use 'None'
- Do not duplicate information already captured in other fields; focus on supplementary context
- Use "NR" if the document provides no relevant notes and it is unclear whether any exist
Examples:
- {'value': 'Data for HbA1c presented in a graph and could not be extracted', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"country": {"value": "...", "source_text": "..."}, "setting": {"value": "...", "source_text": "..."}, "number_of_centres": {"value": "...", "source_text": "..."}, "trial_design": {"value": "...", "source_text": "..."}, "recruitment_period": {"value": "...", "source_text": "..."}, "funding_source": {"value": "...", "source_text": "..."}, "notes": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
