# Single-call extraction — Patient Population (antibiotic)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `age_central_tendency_type`  _(source-grounded)_
Which central-tendency statistic the paper uses to summarise age. Use 'NR' if no age summary is reported.
Allowed values: "mean", "median", "NR"
Hints:
- Look in the participant characteristics, demographics, or baseline table
- Common labels include 'Mean age', 'Median age', or 'Age (mean ± SD)'
Rules:
- Must be exactly one of: mean, median, NR
- Use 'mean' if the paper reports a mean age (with or without SD)
- Use 'median' if the paper reports a median age (with or without IQR)
- Use 'NR' if no central-tendency age statistic is reported anywhere in the paper
- Use exact lowercase spelling as listed in options
Examples:
- {'value': 'mean', 'source_text': ''}

### `age_value`  _(source-grounded)_
Numeric value of the central-tendency statistic above (in years). Use 'NR' if not reported. If reported per arm only, give the overall mean if computable, otherwise NR.
Hints:
- Look in the baseline characteristics table or demographics section
- If per-arm means and sample sizes are given, compute the pooled/overall mean
- Age is typically expressed in years; convert months to years if necessary
Rules:
- Extract the numeric value only, as a number (integer or decimal)
- Value must correspond to the statistic type identified in age_central_tendency_type
- If age is reported per arm only and an overall value cannot be computed, use 'NR'
- Use 'NR' if no central-tendency age value is reported
Examples:
- {'value': '27', 'source_text': ''}

### `age_sd`  _(source-grounded)_
Standard deviation of age (only meaningful when age_central_tendency_type is 'mean'). Use 'NR' if not reported.
Hints:
- Look for '± SD' notation adjacent to the mean age value
- May appear as 'SD', 'std dev', or '±' in baseline tables
- Only extract if age_central_tendency_type is 'mean'; otherwise use 'NR'
Rules:
- Extract the numeric SD value only, as a number (integer or decimal)
- Only populate when age_central_tendency_type is 'mean'; use 'NR' for median or NR cases
- Use 'NR' if the SD is not reported even when mean age is given
Examples:
- {'value': 'NR', 'source_text': ''}

### `age_range`  _(source-grounded)_
Reported age range as 'min-max' (e.g. '18-48'). Use 'NR' if no range is given.
Hints:
- Look for parenthetical ranges such as '(18–65)' or 'range: 18–65' near the mean or median age
- May appear in the baseline table, eligibility criteria, or participant description
- Ranges may use en-dash (–), hyphen (-), or 'to' notation
Rules:
- Format as 'min-max' using a hyphen, e.g. '18-48'
- Both min and max must be numeric values in years
- Do not include units in the output string
- Use 'NR' if no age range is reported in the paper
Examples:
- {'value': '18-48', 'source_text': ''}

### `pct_male`  _(source-grounded)_
Percentage of male participants (0-100). Derive from the M:F counts if the paper reports raw numbers. Use 'NR' if sex is not reported.
Hints:
- Look in the participant demographics or baseline characteristics table
- Check the abstract or methods section for sex breakdown
- If raw counts are given (e.g., '55 males out of 170'), compute: (55/170)*100 = 32.35
Rules:
- Return a numeric value between 0 and 100 (float allowed)
- If raw male and total counts are provided but no percentage, compute pct_male = (male_count / total_n) * 100, rounded to two decimal places
- Do not round aggressively — preserve at least two decimal places when derived
- Use "NR" if sex distribution is not reported anywhere in the document
Examples:
- {'value': '32.35', 'source_text': ''}

### `pct_female`  _(source-grounded)_
Percentage of female participants (0-100). Should be 100 - pct_male when both are computable. Use 'NR' if not reported.
Hints:
- Look in the participant demographics or baseline characteristics table
- Check the abstract or methods section for sex breakdown
- If pct_male is already computed, derive pct_female as 100 - pct_male
- If raw female and total counts are given (e.g., '115 females out of 170'), compute: (115/170)*100 = 67.65
Rules:
- Return a numeric value between 0 and 100 (float allowed)
- When pct_male is known, pct_female must equal 100 - pct_male (rounded to two decimal places)
- If raw female and total counts are provided but no percentage, compute pct_female = (female_count / total_n) * 100, rounded to two decimal places
- Use "NR" if sex distribution is not reported anywhere in the document
Examples:
- {'value': '67.65', 'source_text': ''}

### `inclusion_criteria`  _(source-grounded)_
Verbatim inclusion criteria as reported by the paper. Use 'NR' or 'not reported' if the paper omits inclusion criteria.
Hints:
- Look in the Methods section under headings such as 'Inclusion Criteria', 'Eligibility Criteria', or 'Patient Selection'
- Criteria may be listed as bullet points, numbered lists, or embedded in prose
- May appear in a 'Participants' or 'Study Population' subsection
Rules:
- Extract the inclusion criteria verbatim or as close to verbatim as possible from the document
- Preserve the original wording, including any medical terminology or diagnostic thresholds
- If multiple criteria are listed, include all of them in the extracted text
- Use 'NR' if the paper does not report inclusion criteria
Examples:
- {'value': 'Patients listed to undergo orthognathic operations', 'source_text': ''}

### `exclusion_criteria`  _(source-grounded)_
Verbatim exclusion criteria as reported by the paper. Use 'NR' or 'not reported' if the paper omits exclusion criteria.
Hints:
- Look in the Methods section under headings such as 'Exclusion Criteria', 'Eligibility Criteria', or 'Patient Selection'
- Criteria may be listed as bullet points, numbered lists, or embedded in prose
- May appear alongside inclusion criteria in the same subsection
Rules:
- Extract the exclusion criteria verbatim or as close to verbatim as possible from the document
- Preserve the original wording, including any medical terminology, drug names, or diagnostic conditions
- If multiple criteria are listed, include all of them in the extracted text
- Use 'NR' if the paper does not report exclusion criteria
Examples:
- {'value': 'use of antibiotics in the month before the operation; lactose intolerance (because the placebo was lactose-based); previous orthognathic operations', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"age_central_tendency_type": {"value": "...", "source_text": "..."}, "age_value": {"value": "...", "source_text": "..."}, "age_sd": {"value": "...", "source_text": "..."}, "age_range": {"value": "...", "source_text": "..."}, "pct_male": {"value": "...", "source_text": "..."}, "pct_female": {"value": "...", "source_text": "..."}, "inclusion_criteria": {"value": "...", "source_text": "..."}, "exclusion_criteria": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
