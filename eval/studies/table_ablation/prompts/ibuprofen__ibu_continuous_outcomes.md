# Single-call extraction — Continuous Outcomes (ibuprofen)

You are extracting the "outcomes" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

One row per (comparison × outcome_type × reporter × timepoint). If the same outcome is reported at multiple timepoints, create one row per timepoint. If ibuprofen is compared to more than one drug, create separate rows per comparison.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `comparison`  _(anchor)_
The comparator drug that arm 2 received in this comparison (ibuprofen is always arm 1). 'naproxen_sodium' = naproxen sodium. Map 'acetaminophen' to 'paracetamol'.
Allowed values: "placebo", "paracetamol", "morphine", "ketorolac", "naproxen_sodium", "rofecoxib", "aspirin"
Hints:
- Identify the arm/group label as named in the Methods, study design, or table column headers where ibuprofen is compared against another treatment
- Check the randomisation/allocation description for the exact drug name given to the non-ibuprofen group
- If multiple comparator arms exist, treat each ibuprofen-vs-X pairing as a separate comparison value
Rules:
- Must be exactly one of the listed options
- Map synonyms/brand or generic name variants to the closest matching option (e.g. 'acetaminophen' → 'paracetamol', 'Toradol' → 'ketorolac')
- Do not invent a comparator not explicitly named or clearly identifiable as the comparator arm in the source text

### `outcome_type`  _(anchor)_
Continuous outcome measured. 'pain_intensity' = a post-operative pain-score scale (VAS, FLACC, CHEOPS, Wong-Baker, Oucher, etc.); 'opioid_consumption' = amount of rescue opioid consumed (e.g. mg morphine/fentanyl); 'time_to_rescue' = time to first rescue analgesia. Map synonyms to the closest option. For 'pain_intensity' use ONLY validated pain-score instruments — do NOT record agitation, sedation, emergence-delirium, anxiety, or patient/parent satisfaction scores as pain, even when they are the study's headline result.
Allowed values: "pain_intensity", "opioid_consumption", "time_to_rescue"
Hints:
- Identify this from the outcome/results section headers, table row labels, or the name of the scale/instrument used
- Look for explicit mentions of rescue medication doses (mg/kg) to identify opioid_consumption rows
- Look for Kaplan-Meier curves or 'time to first analgesic request' language to identify time_to_rescue rows
- If a table reports multiple outcomes stacked together, treat each distinct outcome type as its own set of rows
Rules:
- Value must be exactly one of the listed options
- Do not classify agitation, sedation, emergence-delirium, anxiety, or satisfaction scores as 'pain_intensity' even if emphasized as primary outcome
- Map synonymous outcome names (e.g. 'analgesic consumption', 'morphine use') to 'opioid_consumption'
- Map 'time to first rescue analgesic', 'time to first analgesic request' to 'time_to_rescue'

### `reporter`  _(anchor)_
Who reported/assessed the outcome. opioid_consumption ALWAYS maps to 'NA'; time_to_rescue ALWAYS maps to 'third_party' (rescue timing is recorded by staff/parents). For pain_intensity, decide from the instrument: SELF-REPORT scales (VAS, Wong-Baker FACES, Oucher, Faces Pain Scale, numeric or verbal rating scale) → 'child'; OBSERVER/behavioural scales (FLACC, CHEOPS, CHIPPS, OPS/objective pain score, or any parent-, nurse- or observer-rated score) → 'third_party'. If a self-report scale is nominally used but the children are too young for it to be reliable (roughly under 4 years) and it was administered by an observer, use 'third_party'.
Allowed values: "child", "third_party", "NA"
Hints:
- Cross-reference the 'scale' field/instrument name to infer reporter type: patient-completed VAS/FACES scales imply 'child', while behavioural/observational checklists imply 'third_party'
- Check the Methods or Outcome Measures section for who administered/scored the pain instrument and the age of the study population
- For opioid_consumption rows set reporter to 'NA' regardless of who recorded the dose (it is measured objectively, not reported by a person)
- For time_to_rescue rows set reporter to 'third_party' since staff or parents record the time of rescue analgesia request/administration
Rules:
- Must be exactly one of the listed options: 'child', 'third_party', 'NA'
- opioid_consumption outcome_type rows MUST always use 'NA'
- time_to_rescue outcome_type rows MUST always use 'third_party'
- For pain_intensity, base the decision on the instrument type and reported age of participants, not on assumption
- If a self-report scale is used but participants are roughly under 4 years old and an observer administered it, classify as 'third_party' rather than 'child'

### `timepoint`  _(anchor)_
Timepoint window after the intervention at which this outcome was measured, mapped to the nearest bin. 'under_2h' = less than 2 hours postintervention; '2h_to_24h' = 2 hours to less than 24 hours; '24h_to_7d' = from 24 hours up to 7 days; 'overall' = no specific window / whole postoperative period. If several measurements fall inside the same bin, report the EARLIEST post-operative measurement in that bin. 'time_to_rescue' is normally a single summary value → use 'overall' unless the paper ties it to a specific window.
Allowed values: "under_2h", "2h_to_24h", "24h_to_7d", "overall"
Hints:
- Look for time labels attached to the outcome measurement, such as '30 min', '2 h', '6 hr', 'Day 1', 'postoperative day 3', or figure/table axis labels showing elapsed time since surgery or drug administration
- Check table column headers and figure x-axis labels for repeated-measures data ow a distinct timepoint
- For opioid_consumption reported as a single cumulative total (e.g. 'total morphine consumption in first 24h'), treat the stated cumulative window as the timepoint bin
- If the row anchors on time_to_rescue and no specific window is tied to it, default to 'overall' rather than guessing a bin
Rules:
- Value must be exactly one of the listed options
- Map the reported time to the nearest bin using the stated cutoffs (under 2h, 2h to <24h, 24h to 7d)
- When multiple measurements exist within the same bin for the same row, extract only the earliest one and record its bin
- Do not invent a specific timepoint bin when the paper reports only an unspecified or whole-study-period outcome ", use use 'overall' in that case

### `scale`  _(value)_
Measurement scale/instrument used, verbatim (e.g. 'VAS', 'FLACC', 'CHEOPS', 'Wong-Baker FACES', 'mg morphine equivalents') (OPTIONAL). Use 'NR' if not stated.
Hints:
- Look near the outcome definition, methods, or table headers/footnotes where the instrument or unit is named alongside the mean/SD values
- For opioid_consumption, this is often a unit such as 'mg morphine', 'mg/kg fentanyl', or 'morphine equivalents (mg)' rather than a named psychometric scale
- For time_to_rescue, this is usually a time unit (e.g. 'minutes', 'hours') rather than a named instrument
- Copy the name or unit exactly as printed, including capitalisation and abbreviations (e.g. 'VAS' not 'visual analogue scale' if that is how it appears)
Rules:
- Record the scale/instrument name or unit verbatim as it appears in the source; do not translate or standardise abbreviations
- Do not infer a scale name that is not explicitly stated in the text or tables
- Use "NR" if the scale/instrument or unit is not explicitly stated

### `mean_arm1`  _(value)_
Mean value of the outcome in the IBUPROFEN arm (arm 1) at this timepoint. Read the mean, SD and N for this arm and timepoint from the SAME reported cell/row (e.g. split a 'mean ± SD' or 'mean (SD)' entry). Use 'NR' if not reported.
Hints:
- The row's comparison, outcome_type, reporter, and timepoint are already fixed — locate the results table cell or text sentence matching that specific arm 1 (ibuprofen) row and timepoint column
- Look in results tables reporting outcome means by group and timepoint, or in text passages describing outcome scores 'in the ibuprofen group'
- When a cell reports 'mean ± SD' or 'mean (SD)', take only the first number as the mean; the SD is captured separately in sd_arm1
- If the paper reports a median (IQR) instead of a mean, still record the reported median value here and note in scale/elsewhere that it is a median, since no true mean is given
- Ensure the value corresponds to the SAME timepoint bin and SAME outcome_type/reporter combination already identified for this row, not a different visit or scale
Rules:
- Report as a plain numeric value using the same units as reported in the source (e.g., mm on VAS, mg morphine, minutes/hours)
- Do not convert units unless necessary to match the scale reported for arm 2 in the same row
- Use 'NR' if the mean for this arm/outcome/timepoint combination is not reported anywhere in the paper

### `sd_arm1`  _(value)_
Standard deviation of the outcome in the ibuprofen arm (arm 1) at this timepoint. Split it from a 'mean ± SD' or 'mean (SD)' cell when reported that way. If the paper gives only SE, 95% CI, IQR, or min–max range instead of SD, derive SD (SD = SE×√n; SD = √n×(CI_high−CI_low)/3.92; SD ≈ IQR/1.35; or a range-based method). Use 'NR' only when no dispersion statistic of any kind is reported.
Hints:
- The row's comparison, outcome_type, reporter, and timepoint are already fixed — find the ibuprofen-arm dispersion value for that same specific outcome and timepoint, matched to the same cell used for mean_arm1
- Look for a value immediately following the mean in the ibuprofen column, e.g. 'mean ± SD', 'mean (SD)', or a separate SD/SE/CI column in the same results table row
- If the table reports SE, 95% CI, IQR, or a min–max range instead of SD, derive SD using the appropriate formula rather than defaulting to NR
- Check figure legends and table footnotes for the definition of the dispersion statistic (SD vs SE vs CI) before converting
Rules:
- Report as a numeric value only (no ± symbol or units)
- Must come from the SAME cell/row as mean_arm1 and n_arm1 for this comparison, outcome_type, reporter, and timepoint
- When converting from SE, CI, or IQR to SD, use the standard formulas and do not simply copy the reported dispersion statistic as-is
- Use 'NR' only when no dispersion statistic of any kind (SD, SE, CI, IQR, range) is reported for the ibuprofen arm at this timepoint

### `n_arm1`  _(value)_
Number of participants RANDOMISED/allocated to the ibuprofen arm (arm 1) — the group size assigned at randomisation (the CONSORT/baseline denominator), NOT the smaller number who completed or were analysed at this specific timepoint. Report this allocated N even if some participants later dropped out or returned no data. Use 'NR' only if the arm's group size is not reported.
Hints:
- The row's comparison, outcome_type, reporter, and timepoint are already identified — locate the ibuprofen (arm 1) group size for that same comparison
- Check the CONSORT flow diagram, the baseline/participant-allocation table, or the 'n=' notation under the ibuprofen column heading in the results table
- Prefer the N reported at randomisation/allocation (often in Table 1 or the flow diagram) over any per-timepoint 'n' shown alongside means/SDs in outcome tables, which may reflect only those analysed at that timepoint
- If the same ibuprofen arm size is stated once for the whole trial, reuse that same value across all rows for that arm regardless of timepoint or outcome
Rules:
- Report the number of participants allocated/randomised to ibuprofen, not the number analysed or completing follow-up at this timepoint
- Must be a positive integer
- Use 'NR' only if the arm's randomised group size is not reported anywhere in the paper

### `mean_arm2`  _(value)_
Mean value of the outcome in the COMPARATOR arm (arm 2, the drug named in 'comparison') at this timepoint. Read the mean, SD and N for this arm and timepoint from the SAME reported cell/row (e.g. split a 'mean ± SD' or 'mean (SD)' entry). Use 'NR' if not reported.
Hints:
- The row's comparison, outcome_type, reporter, and timepoint are already fixed — locate the results table row/cell matching that comparator arm and timepoint, and read the mean from the same cell used for sd_arm2 and n_arm2
- Look in results tables, figures with numeric labels, or text reporting outcome values by treatment group
- If the outcome is reported only in a figure, use any numeric value printed on/near the bar or point, or in the figure legend/caption
Rules:
- Report as a plain number (integer or decimal), matching the unit/scale used in the source
- Do not convert units; report the value exactly as presented for the comparator arm
- Must come from the same cell/row as sd_arm2 and n_arm2 for this comparison, outcome_type, reporter, and timepoint
- Use "NR" if the mean for the comparator arm at this timepoint is not reported

### `sd_arm2`  _(value)_
Standard deviation of the outcome in the comparator arm (arm 2) at this timepoint. Split it from a 'mean ± SD' or 'mean (SD)' cell when reported that way. If the paper gives only SE, 95% CI, IQR, or min–max range instead of SD, derive SD (SD = SE×√n; SD = √n×(CI_high−CI_low)/3.92; SD ≈ IQR/1.35; or a range-based method). Use 'NR' only when no dispersion statistic of any kind is reported.
Hints:
- The row's comparison, outcome_type, reporter, and timepoint are already fixed — locate the comparator (arm 2) data cell matching that exact combination
- Look in the same table cell/row as mean_arm2 and n_arm2, since mean, SD, and N for a given arm/timepoint are usually reported together
- Check figure legends or supplementary tables if the SD is not in the main results table but a graph shows error bars (often SD or SEM — verify which one is stated)
- If only SE, 95% CI, IQR, or range is given for arm 2, compute SD using n_arm2 and the appropriate conversion formula
Rules:
- Report as a plain numeric value using the same units as mean_arm2
- If the source reports SE, CI, IQR, or range instead of SD, convert to SD and do not just copy the alternate statistic
- State any derived value using the conversion consistent with the paper's reported dispersion type
- Use 'NR' only when no dispersion statistic (SD, SE, CI, IQR, or range) is reported for arm 2 at this timepoint

### `n_arm2`  _(value)_
Number of participants RANDOMISED/allocated to the comparator arm (arm 2, the drug named in 'comparison') — the group size assigned at randomisation (the CONSORT/baseline denominator), NOT the smaller number who completed or were analysed at this specific timepoint. Report this allocated N even if some participants later dropped out. Use 'NR' only if the arm's group size is not reported.
Hints:
- The row's comparison, outcome_type, reporter and timepoint are already fixed — locate the baseline/CONSORT allocation table or the 'n=' stated alongside the comparator arm's group label, not the per-timepoint analyzed N
- Check the CONSORT flow diagram or first results table row (e.g., 'Group B (n=30)') for the allocated denominator
- If the same allocated N applies across all timepoints for this arm, reuse it consistently rather than the completer count reported at this specific timepoint
- Look near where mean_arm2 and sd_arm2 for this same row are reported — they typically share the same table cell or line
Rules:
- Report the number of participants ALLOCATED/RANDOMISED to arm 2, not the number analyzed or completing at this timepoint
- Must be a whole number
- Use 'NR' if the comparator arm's group size is not reported anywhere in the document

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"comparison": {"value": "...", "source_text": "..."}, "outcome_type": {"value": "...", "source_text": "..."}, "reporter": {"value": "...", "source_text": "..."}, "timepoint": {"value": "...", "source_text": "..."}, "scale": {"value": "...", "source_text": "..."}, "mean_arm1": {"value": "...", "source_text": "..."}, "sd_arm1": {"value": "...", "source_text": "..."}, "n_arm1": {"value": "...", "source_text": "..."}, "mean_arm2": {"value": "...", "source_text": "..."}, "sd_arm2": {"value": "...", "source_text": "..."}, "n_arm2": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
