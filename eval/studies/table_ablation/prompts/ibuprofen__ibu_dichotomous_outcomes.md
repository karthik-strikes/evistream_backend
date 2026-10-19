# Single-call extraction — Dichotomous Outcomes (ibuprofen)

You are extracting the "dichotomous_outcomes" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

One row per (comparison × outcome × timepoint). For an outcome reported at multiple timepoints, create one row per timepoint. If ibuprofen is compared to more than one drug, create separate rows per comparison.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `comparison`  _(anchor)_
The comparator drug that arm 2 received (ibuprofen is always arm 1). 'naproxen_sodium' = naproxen sodium. Map 'acetaminophen' to 'paracetamol'.
Allowed values: "placebo", "paracetamol", "morphine", "ketorolac", "naproxen_sodium", "rofecoxib", "aspirin"
Hints:
- Identify the non-ibuprofen drug named in the study arm/group description or table column header for this comparison
- Check the methods/interventions section for the exact drug names used to define each treatment arm
- If the comparator is named generically (e.g. 'control' or 'active comparator'), look for the actual drug identity elsewhere in the text or tables
Rules:
- Must be exactly one of the listed options
- Map synonyms/brand names to the closest option (e.g. 'acetaminophen' → 'paracetamol', 'Toradol' → 'ketorolac')
- Use this field to identify which arm2_label drug is being compared, one row per unique comparator drug
- Do not use this field for ibuprofen itself, since ibuprofen is always arm 1

### `outcome`  _(anchor)_
Dichotomous outcome reported. 'adverse_events' = any/overall adverse events; 'rescue_medication' = number of children who required rescue analgesia; 'nausea_or_vomiting' = nausea and/or vomiting; 'bleeding' = any bleeding (incl. GI or oral bleeding); 'renal_dysfunction' = renal dysfunction. Map synonyms to the closest option.
Allowed values: "adverse_events", "rescue_medication", "nausea_or_vomiting", "bleeding", "renal_dysfunction"
Hints:
- Look in Results/Safety/Adverse Events tables and text comparing ibuprofen to comparator drugs for named outcomes
- Outcome name may be a table row label, subheading, or embedded in a sentence (e.g. 'children requiring rescue analgesia', 'incidence of vomiting')
- Treat 'nausea', 'vomiting', or 'PONV' as nausea_or_vomiting; 'need for additional analgesia' or 'supplemental analgesic use' as rescue_medication; 'side effects' or 'complications' generally as adverse_events unless a more specific option applies
- If a table reports a composite 'adverse events' category that separately breaks out nausea, bleeding, etc., create separate rows for each specific outcome rather than only the composite
Rules:
- Value must be exactly one of the listed options
- Map synonyms/paraphrases to the closest matching option; do not invent new outcome categories
- Each row must correspond to a single outcome value — do not combine multiple outcomes into one row

### `timepoint`  _(anchor)_
Timepoint window at which this outcome was assessed, mapped to the nearest bin. Used mainly for rescue_medication (reported by window). 'under_2h' = less than 2 hours postintervention; '2h_to_24h' = 2 hours to less than 24 hours; '24h_to_7d' = from 24 hours up to 7 days; 'overall' = whole postoperative period / no specific window (typical for adverse events, nausea/vomiting, bleeding, renal dysfunction).
Allowed values: "under_2h", "2h_to_24h", "24h_to_7d", "overall"
Hints:
- Look for time windows near the outcome data, e.g. '0-2h', '2-24h', 'first 24 hours', 'postoperative day 1-7', or table column headers indicating assessment periods
- For rescue_medication, check if the paper reports separate counts for different postoperative windows rather than a single overall figure
- If the outcome is reported once for the whole study period with no explicit window, use 'overall' as the implicit default
- When multiple windows are reported for the same comparison and outcome, create separate rows using the timepoint that matches each reported window
Rules:
- Must be exactly one of the listed options
- Map any explicit time window to the nearest bin: <2h → 'under_2h'; 2h to <24h → '2h_to_24h'; 24h to 7 days → '24h_to_7d'
- Use 'overall' when no specific time window is stated or when the outcome represents the entire postoperative/study period
- Do not invent a specific timepoint if the source text does not indicate one — default to 'overall'

### `arm1_label`  _(anchor)_
Label for arm 1 — this is always the ibuprofen arm, so use 'ibuprofen'.
Hints:
- This is a fixed anchor value for every row since ibuprofen is always arm 1 in this review
- Use the exact drug name/dose label as it appears in the study's arm description if it differs slightly (e.g. 'ibuprofen 10mg/kg'), but default to plain 'ibuprofen' if no distinguishing detail is needed
- Do not confuse with arm2_label, which names the comparator drug
Rules:
- Always set to 'ibuprofen' (case-insensitive match acceptable, but output lowercase 'ibuprofen')
- Never leave blank or use NR — ibuprofen is always present as arm 1 by definition of this extraction task
- Do not substitute the comparator drug name here

### `arm1_events`  _(value)_
Number of events (e.g. children with the adverse event, or who required rescue medication) in the IBUPROFEN arm (arm 1). Use '0' if explicitly zero, 'NR' if not stated.
Hints:
- The comparison, outcome, and timepoint for this row are already identified — find the corresponding results row or table cell for the ibuprofen arm at that specific outcome and timepoint
- Look in results tables reporting adverse events, rescue medication use, nausea/vomiting, bleeding, or renal dysfunction by treatment group
- Events are typically reported as a count (n) alongside a denominator (N) in a fraction or 'n/N' format, e.g. '3/45' — extract only the numerator here
Rules:
- Extract only the numeric count of events, not the percentage or the denominator
- Use '0' if the text or table explicitly states zero events occurred
- Use 'NR' if the number of events for the ibuprofen arm at this outcome/timepoint is not stated

### `arm1_n`  _(value)_
Denominator (number of children assessed) for the ibuprofen arm (arm 1) at this outcome. Use 'NR' if not stated.
Hints:
- The comparison, outcome, and timepoint for this row are already identified — find the ibuprofen arm's sample size for that specific outcome/timepoint
- Look in results tables near the arm1_events count, often reported as 'n/N' (e.g. '3/45') where N is this denominator
- Note the denominator for a specific outcome may differ from the overall arm sample size if there was attrition or missing data for that assessment
Rules:
- Extract as a number matching the ibuprofen group's assessed denominator for this specific outcome and timepoint
- If only the overall arm size is reported and no outcome-specific denominator is given, use the overall ibuprofen arm size
- Use "NR" if the denominator is not stated anywhere in the source

### `arm2_label`  _(anchor)_
Label for arm 2 — the comparator drug named in 'comparison' (e.g. 'placebo', 'paracetamol', 'ketorolac').
Hints:
- Copy the drug/treatment name as it appears in the paper's group labels, table headers, or methods description of study arms
- Should correspond directly to the drug selected in the 'comparison' field for this row — use the same drug identity, but preserve the paper's own wording/label rather than the normalized comparison code
- If the arm is a control group without an active drug, use the label as given in the paper (e.g. 'placebo' or 'control')
- Look near the outcome data table or in the intervention/methods section where treatment groups are first defined
Rules:
- Must name the comparator drug given in 'comparison' for this row, using the paper's own label wording (may differ from the normalized comparison value, e.g. 'acetaminophen' vs 'paracetamol')
- Do not use 'ibuprofen' — that is reserved for arm1_label
- Provide a concise label (drug name, optionally with dose/route if that is how the paper distinguishes arms), not a full sentence

### `arm2_events`  _(value)_
Number of events in the COMPARATOR arm (arm 2). Use '0' if explicitly zero, 'NR' if not stated.
Hints:
- The comparison/outcome/timepoint/arm2 label for this row are already determined — find the matching numerator (event count) reported for the comparator group at that same outcome and timepoint
- Look in results tables or text near the corresponding arm1_events and arm2_n values, often presented as 'n/N' or 'n (%)' per arm
- Check for outcome-specific subsections (e.g., adverse events table, rescue medication table) since events for different comparators may be reported in separate rows or columns
Rules:
- Report as a whole number count of children/events, not a percentage
- If the source reports only a percentage, do not calculate the count unless the denominator (arm2_n) is explicitly given and the calculation is unambiguous
- Use '0' if the text explicitly states zero events occurred in the comparator arm
- Use 'NR' if the event count for this specific comparator, outcome, and timepoint combination is not stated

### `arm2_n`  _(value)_
Denominator (number of children assessed) for the comparator arm (arm 2) at this outcome. Use 'NR' if not stated.
Hints:
- The row's comparison drug, outcome, and timepoint are already identified; locate the total N assessed for that comparator arm for this specific outcome, which may differ from the overall group size if there was attrition or missing data
- Check results tables near the arm1_events/arm2_events counts, or group sample size footnotes, for the denominator specific to this outcome
Rules:
- Report as a whole number
- Use "NR" if the denominator for the comparator arm is not explicitly stated for this outcome

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"comparison": {"value": "...", "source_text": "..."}, "outcome": {"value": "...", "source_text": "..."}, "timepoint": {"value": "...", "source_text": "..."}, "arm1_label": {"value": "...", "source_text": "..."}, "arm1_events": {"value": "...", "source_text": "..."}, "arm1_n": {"value": "...", "source_text": "..."}, "arm2_label": {"value": "...", "source_text": "..."}, "arm2_events": {"value": "...", "source_text": "..."}, "arm2_n": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
