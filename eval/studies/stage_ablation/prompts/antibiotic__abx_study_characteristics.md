# Single-call extraction — Study Characteristics (antibiotic)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `year`  _(source-grounded)_
Year the article was published as a 4-digit integer. Do NOT use the data-collection or recruitment period.
Hints:
- Look for the publication date in the journal header, citation line, copyright notice, or article metadata at the top or bottom of the document
- Distinguish between the study recruitment/data-collection period and the actual publication year
Rules:
- Return a 4-digit integer (e.g., 2004, 2010)
- Use the publication year only — not the data-collection or recruitment period
- Must be a plausible journal publication year (e.g., 1900–2100)
Examples:
- {'value': '2004', 'source_text': ''}

### `country`  _(source-grounded)_
Country in which the study was conducted, derived from the author affiliations or setting statement. If multiple sites span different countries, list all comma-separated.
Hints:
- Check author affiliations, the Methods section under 'Study Setting' or 'Study Site', and any institutional address lines
- If the paper mentions a specific city or hospital, infer the country from that context if not explicitly stated
Rules:
- Use the country name in English (e.g., 'Jordan', 'United States', 'United Kingdom')
- If multiple countries are involved, list all comma-separated (e.g., 'Jordan, Egypt')
- Use "NR" if the country is not stated or cannot be inferred from the document
Examples:
- {'value': 'Jordan', 'source_text': ''}

### `setting_institution`  _(source-grounded)_
Verbatim institution or department name where the study was conducted, typically the hospital, dental school, or university clinic listed in the Methods section or author affiliations.
Hints:
- Look in the author affiliation block, the Methods section under 'Study Setting', 'Study Site', or 'Participants', and any ethics approval statements that name the institution
- Prefer the most specific unit name (e.g., department + faculty + university) over a generic hospital name alone
Rules:
- Extract the institution name verbatim as it appears in the document
- Include department, faculty, university, and city if all are stated (e.g., 'Oral and Maxillofacial Surgery Department, Faculty of Dentistry, University of Jordan, Amman')
- Do not paraphrase or abbreviate the institution name
- Use "NR" if no institution or setting is stated in the document
Examples:
- {'value': 'Oral and Maxillofacial Surgery Department, Faculty of Dentistry, University of Jordan, Amman', 'source_text': ''}

### `design`  _(source-grounded)_
High-level trial design. Use 'RCT' for any randomised parallel-group or split-mouth design; 'quasi-RCT' if allocation is by alternation, date of birth, or another non-random method; 'other' for non-randomised comparative designs.
Allowed values: "RCT", "quasi-RCT", "other"
Hints:
- Look in the Methods section under headings such as 'Study Design', 'Trial Design', or 'Randomisation'
- Check the abstract for design descriptors such as 'randomised', 'randomized', 'alternation', 'quasi-randomised'
- Inspect the title for design keywords if the methods section is absent
Rules:
- Must be exactly one of: RCT, quasi-RCT, other
- Use 'RCT' for parallel-group or split-mouth designs with explicit random allocation
- Use 'quasi-RCT' when allocation is by alternation, odd/even date of birth, hospital number, or any other non-random systematic method
- Use 'other' for all non-randomised comparative designs (e.g., controlled clinical trials, before-after studies)
- Use exact spelling and capitalisation as listed in options
Examples:
- {'value': 'RCT', 'source_text': ''}

### `publication_type`  _(source-grounded)_
Type of publication for this primary study record. Use 'full paper' for peer-reviewed full-text articles and 'conference abstract' for conference proceedings / unpublished abstracts.
Allowed values: "full paper", "conference abstract"
Hints:
- Check the document header, journal name, or source metadata for publication venue
- Conference abstracts typically lack full methods sections and appear in proceedings volumes or supplement issues
- Full papers have structured sections (Introduction, Methods, Results, Discussion) and a journal ISSN or DOI
Rules:
- Must be exactly one of: full paper, conference abstract
- Use 'full paper' for any peer-reviewed full-text article published in a journal
- Use 'conference abstract' for conference proceedings, poster abstracts, or unpublished abstracts
- Use exact spelling and capitalisation as listed in options
Examples:
- {'value': 'full paper', 'source_text': ''}

### `n_enrolled`  _(source-grounded)_
Total number of participants enrolled or randomised across all study arms, not the number analyzed after drop-out.
Hints:
- Look in the Methods section under 'Participants', 'Study Population', or 'Randomisation'
- Check CONSORT flow diagrams or participant flow charts for enrollment numbers
- Distinguish between enrolled/randomised (target) and analyzed (post-dropout) counts — report the enrolled/randomised figure
Rules:
- Extract the total enrolled or randomised count across ALL arms combined
- Do NOT report the number analyzed if it differs from enrolled; report the enrolled/randomised figure
- Return as an integer
- Use "NR" if the enrollment count is not stated in the document
Examples:
- {'value': '34', 'source_text': ''}

### `surgery_type`  _(source-grounded)_
Type of orthognathic surgery studied, copied or paraphrased from the paper (e.g. mixed orthognathic surgery, bimaxillary OS, bilateral sagittal split ramus osteotomy, Le Fort I osteotomy).
Hints:
- Look in the title, abstract, or Methods section for the surgical procedure name
- Check inclusion/exclusion criteria for the specific surgery type described
Rules:
- Copy or closely paraphrase the surgery type as described in the paper
- Preserve standard surgical terminology (e.g. 'bilateral sagittal split ramus osteotomy', 'Le Fort I osteotomy', 'bimaxillary osteotomy')
- If multiple surgery types are included, describe the mix (e.g. 'mixed orthognathic surgery')
- Use "NR" if the surgery type is not stated
Examples:
- {'value': 'orthognathic operations (mixed)', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"year": {"value": "...", "source_text": "..."}, "country": {"value": "...", "source_text": "..."}, "setting_institution": {"value": "...", "source_text": "..."}, "design": {"value": "...", "source_text": "..."}, "publication_type": {"value": "...", "source_text": "..."}, "n_enrolled": {"value": "...", "source_text": "..."}, "surgery_type": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
