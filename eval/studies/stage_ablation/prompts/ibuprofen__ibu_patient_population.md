# Single-call extraction — Patient Population (ibuprofen)

You are extracting a structured data-extraction form from ONE study report in a systematic review. There is exactly ONE record per study (one row per paper). Read the entire paper text provided in the next message and fill in EVERY field below.

## Fields
Fill in EVERY field. Use the string "NR" when a value is not reported. Copy values from the paper; never invent. Fields marked _(source-grounded)_ must be returned as an object {"value": ..., "source_text": ...}; any other field is a plain value.

### `age_central_tendency_type`  _(source-grounded)_
How the paper summarises participant age. 'mean' = mean (usually ± SD); 'median' = median (usually with IQR/range); 'range' = only an age range is given, no central value. Use 'NR' if age is not reported at all.
Allowed values: "mean", "median", "range", "NR"
Hints:
- Check the Methods or Results section, typically in a demographics or baseline characteristics table, for how age is reported for the ibuprofen arm
Rules:
- Must be exactly one of the listed options
- Use "NR" if age is not reported at all
Examples:
- {'value': 'mean', 'source_text': ''}

### `age_value`  _(source-grounded)_
The central age value (mean or median, in the unit the paper reports — usually years, occasionally months) for the ibuprofen arm. Leave 'NR' when only an age range is reported. Record the number only; note the unit in age_range if it is months.
Hints:
- Look at the baseline demographics table or text describing the ibuprofen/treatment group's age
- If the unit is months rather than years, still record the numeric value here and note the unit in age_range
Rules:
- Record only the numeric central value (mean or median), no units
- Leave as "NR" when only an age range is reported with no central value
- Use "NR" if age is not reported
Examples:
- {'value': '7.4', 'source_text': ''}

### `age_sd`  _(source-grounded)_
Standard deviation of age for the ibuprofen arm, when the paper reports age as mean ± SD. Use 'NR' if not reported (e.g. median/IQR or range studies).
Hints:
- Typically appears immediately after the mean age, formatted as 'mean ± SD'
Rules:
- Record only the numeric SD value, no units
- Use "NR" if not reported (e.g. when median/IQR or range is used instead)
Examples:
- {'value': '1.9', 'source_text': ''}

### `age_range`  _(source-grounded)_
Reported age range or IQR for the ibuprofen arm, written as 'min-max unit' with a single hyphen (e.g. '3-5.2 years', '13-18 years'). Use 'NR' if not reported.
Hints:
- Look for phrasing like 'range' or 'IQR' near the age data for the ibuprofen group
Rules:
- Format as 'min-max unit' using a single hyphen between min and max
- Include the unit (years or months) in the string
- Use "NR" if not reported
Examples:
- {'value': '3-5.2 years', 'source_text': ''}

### `n_randomised`  _(source-grounded)_
Total number of participants randomised across all arms. Use 'NR' if not stated.
Hints:
- Look in the Methods or Results section, often near 'randomised', 'randomized', or 'enrolled'
- Check the CONSORT flow diagram or participant flow description if present
- Sum across all arms if only per-arm counts are given and no overall total is explicitly stated
Rules:
- Extract as a whole number representing the total across all randomised arms
- Do not confuse with number screened, enrolled, or completing the study unless explicitly described as randomised
- Use "NR" if not stated
Examples:
- {'value': '59', 'source_text': ''}

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"age_central_tendency_type": {"value": "...", "source_text": "..."}, "age_value": {"value": "...", "source_text": "..."}, "age_sd": {"value": "...", "source_text": "..."}, "age_range": {"value": "...", "source_text": "..."}, "n_randomised": {"value": "...", "source_text": "..."}}

- Include every field key above, exactly once; do not return a list.
- Each _(source-grounded)_ field is an object with two keys: "value" (the value, or "NR") and "source_text" — ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it (use "NR" when value is "NR").
