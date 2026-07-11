# Single-call extraction — Dichotomous Outcomes (antibiotic)

You are extracting the "dichotomous_outcomes" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

One row per (comparison × outcome × timepoint) reported in the paper, capturing event counts and denominators for both arms along with supporting metadata.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `comparison_label`  _(anchor)_
Identify the two treatment arms this row contrasts from the paper, then pick the closest of the four review comparisons. Use the arms' duration/drug cues to decide — e.g. a 5-day vs 1-day contrast → 'short-term vs long-term'; a single pre-op dose vs a 24 h course → 'preoperative vs short-term'; a comparison of two different drugs → the matching drug comparison.
Allowed values: "short-term vs long-term", "preoperative vs short-term", "amoxicillin vs ampicillin", "amox-clav vs cefuroxime"
Hints:
- Look in the Methods (Interventions) section and the results tables for the names or descriptions of the two arms being compared
- Duration cues: if one arm receives antibiotics for several days and the other for one day or less, map to 'short-term vs long-term'
- Timing cues: if one arm receives a single pre-operative dose and the other receives a short post-operative course, map to 'preoperative vs short-term'
- Drug-name cues: amoxicillin vs ampicillin → 'amoxicillin vs ampicillin'; co-amoxiclav / amoxicillin-clavulanate vs cefuroxime → 'amox-clav vs cefuroxime'
- The comparison label may be implicit (e.g. described in the trial design paragraph) rather than stated as a heading — read the intervention descriptions carefully
- If the paper reports only one comparison, every row in this paper will share the same comparison_label
Rules:
- Must be exactly one of the four allowed options; do not invent new labels
- Use the exact spelling and hyphenation shown in the options list
- Do not leave blank — if the comparison cannot be determined from the paper, this paper likely falls outside the review scope; flag in the 'notes' field

### `outcome`  _(anchor)_
Outcome reported. 'SSI' = surgical site infection (primary outcome); 'systemic_infection' = bacteraemia / sepsis; 'adverse_events' = drug-related AEs; 'hospital_stay' = length-of-stay events; 'HRQoL' = health-related quality of life event-style outcomes. Map synonyms to the closest option (e.g. 'wound infection' → SSI).
Allowed values: "SSI", "systemic_infection", "adverse_events", "hospital_stay", "HRQoL"
Hints:
- Look in the paper's Results section, outcome tables, and Methods (Outcome measures) to identify all outcomes reported
- Common synonyms: 'wound infection' or 'surgical wound infection' → SSI; 'bacteraemia', 'sepsis', 'septicaemia' → systemic_infection; 'drug reaction', 'allergy', 'side effects' → adverse_events; 'length of stay', 'LOS', 'days in hospital' → hospital_stay; 'quality of life', 'QoL', 'SF-36', 'EQ-5D' → HRQoL
- If an outcome is mentioned only in passing (e.g. 'no adverse events were observed') still create a row for it
- Each distinct outcome type found in the paper should generate at least one row; if the same outcome is reported at multiple timepoints, create one row per timepoint (the follow_up_duration field captures the timepoint)
- The outcome label may be implicit — e.g. a table titled 'Wound complications' implies SSI
Rules:
- Must be exactly one of the five allowed options: SSI, systemic_infection, adverse_events, hospital_stay, HRQoL
- Map all synonyms and near-synonyms to the closest listed option before outputting
- Do not invent new outcome categories; if an outcome cannot be mapped to any of the five options, use the closest match and document the mapping in the notes field
- Use exact spelling and capitalisation as listed in options

### `arm1_label`  _(anchor)_
The arm-1 side of the comparison you selected, named with the comparison's own term. arm1 = the longer-duration / earlier-administered / amoxicillin / amox-clav side. E.g. comparison 'short-term vs long-term' → 'long-term'; 'preoperative vs short-term' → 'preoperative'; 'amoxicillin vs ampicillin' → 'amoxicillin'; 'amox-clav vs cefuroxime' → 'amox-clav'.
Hints:
- Determine the comparison_label first, then derive arm1_label as the first named side of that comparison (longer-duration, earlier-administered, or the first-listed drug).
- Look in the paper's Methods (Interventions) or results tables for the arm names; map them to the review's standard terminology.
- If the paper uses synonyms (e.g. 'extended prophylaxis', 'prolonged course'), map to the review term ('long-term').
- The label should mirror the exact term used in the comparison_label field — e.g. if comparison_label is 'short-term vs long-term', arm1_label is 'long-term'.
Rules:
- Must be one of the four review arm-1 terms: 'long-term', 'preoperative', 'amoxicillin', or 'amox-clav', matching the selected comparison_label.
- Use the review's standard term, not the paper's verbatim wording, unless they coincide.
- Do NOT copy a numeric duration or drug dose as the label — map it to the review term.
- arm1_label must be consistent with comparison_label: the two together must form a valid review comparison pair.

### `arm1_events`  _(anchor)_
Number of events (e.g. SSI cases) the paper reports for arm 1 (the numerator). Read from the paper's results table or text. Use '0' if explicitly zero, 'NR' if not stated.
Hints:
- Look in the results tables or text for event counts corresponding to arm 1 (the longer-duration / earlier-administered / amoxicillin / amox-clav side)
- Event counts are typically presented as fractions (e.g. '3/45') or in dedicated columns labelled 'n' or 'events' alongside the denominator
- Cross-reference with arm1_label to confirm you are reading the correct arm's numerator
Rules:
- Report the raw integer count of events as stated in the paper — do not calculate or infer from percentages unless the raw count is unambiguously derivable
- Use '0' if the paper explicitly states zero events for arm 1
- Use 'NR' if the event count for arm 1 is not reported
- Do not confuse this numerator with arm1_n (the denominator) or with arm2_events

### `arm1_n`  _(anchor)_
Denominator (N) for arm 1 at this outcome's measurement timepoint, as reported in the paper's results. Use 'NR' if not stated.
Hints:
- Look in the paper's results tables or text for the number of patients analysed in arm 1 at the relevant follow-up timepoint
- The denominator may differ from the randomised N if there were dropouts or per-protocol analyses — use the number actually analysed for this outcome
- Arm 1 corresponds to the longer-duration / earlier-administered / amoxicillin / amox-clav side of the comparison (matching arm1_label)
- The denominator is often presented as the bottom number of a fraction (e.g. '3/45' → N=45) or in a column headed 'n' or 'N' in a results table
Rules:
- Record as a plain integer (e.g. 45), not a fraction or percentage
- Use 'NR' if the denominator for arm 1 is not stated in the paper
- Do not use the randomised N as a substitute if the analysed N is explicitly different and reported
- This field captures arm 1 only; arm 2 denominator is captured separately in arm2_n

### `arm2_label`  _(anchor)_
The arm-2 side of the comparison you selected, named with the comparison's own term. arm2 = the shorter-duration / later-administered / ampicillin / cefuroxime side. E.g. comparison 'short-term vs long-term' → 'short-term'; 'amoxicillin vs ampicillin' → 'ampicillin'; 'amox-clav vs cefuroxime' → 'cefuroxime'.
Hints:
- Look in the paper's trial arms, randomisation, or interventions section to identify the two treatment groups being compared
- Use the comparison_label already assigned to this row to determine which arm is arm2 — it is always the shorter-duration, later-administered, ampicillin, or cefuroxime side
- If the paper uses a synonym or abbreviation (e.g. 'Augmentin' for amox-clav, 'co-amoxiclav'), map it to the review's standard term for arm2
- When the arm label is implicit (e.g. the paper only names the intervention and the comparator is standard care), infer the arm2 label from context and the comparison_label
Rules:
- Must be the arm-2 term corresponding to the comparison_label for this row: 'short-term' for 'short-term vs long-term'; 'short-term' for 'preoperative vs short-term'; 'ampicillin' for 'amoxicillin vs ampicillin'; 'cefuroxime' for 'amox-clav vs cefuroxime'
- Use the review's standard term, not the paper's local abbreviation or brand name
- Do not copy the arm1_label value into this field — arm1 and arm2 must always differ
- This field is guaranteed to be determinable from the comparison_label; do not use 'NR'

### `arm2_events`  _(anchor)_
Number of events the paper reports for arm 2 (the numerator). Read from the paper's results table or text. Use '0' if explicitly zero, 'NR' if not stated.
Hints:
- Look in the results tables or text for the event count corresponding to arm 2 (the shorter-duration / later-administered / ampicillin / cefuroxime side)
- Arm 2 is the comparator side: 'short-term' in 'short-term vs long-term', 'short-term' in 'preoperative vs short-term', 'ampicillin' in 'amoxicillin vs ampicillin', 'cefuroxime' in 'amox-clav vs cefuroxime'
- Event counts are typically presented alongside denominators (arm2_n) in the same cell or column of a results table
- Cross-check with arm1_events to ensure you are reading from the correct arm column
Rules:
- Output a non-negative integer (e.g. 0, 3, 12) when the count is explicitly reported
- Use '0' only when the paper explicitly states zero events for arm 2 — do not infer zero from absence of mention
- Use 'NR' if the event count for arm 2 is not stated anywhere in the paper
- Do not confuse arm 2 events with arm 1 events — arm 2 is always the shorter-duration / later-administered / ampicillin / cefuroxime side

### `arm2_n`  _(anchor)_
Denominator (N) for arm 2 at this outcome's measurement timepoint, as reported in the paper's results. Use 'NR' if not stated.
Hints:
- Look in the paper's results tables or text for the number of patients in arm 2 (the shorter-duration / later-administered / ampicillin / cefuroxime side) at the relevant follow-up timepoint
- The denominator may differ from the randomised N if patients were lost to follow-up or excluded from the per-protocol analysis — use the number actually analysed for this outcome
- Check footnotes or table headers for arm-specific sample sizes when not stated inline
Rules:
- Record the integer count of patients in arm 2 who were included in the analysis for this specific outcome and timepoint
- Do not substitute the randomised N if the paper reports a smaller analysed N for this outcome
- Use 'NR' if the denominator for arm 2 is not stated anywhere in the paper
- Do not confuse with arm1_n, which captures the denominator for the other arm

### `ssi_definition_source`  _(value)_
Source of the SSI / infection definition the study authors used. 'CDC' = US Centers for Disease Control surgical-site-infection definition; 'authors_own' = a definition the authors devised; 'composite_VAS' = a composite score (e.g. multi-variable VAS scoring of swelling/pain/exudate); 'NR' = no definition stated. Look in the paper's Methods (Outcome measures / Definitions). Use 'NA' as plain text if the outcome row is not SSI.
Allowed values: "CDC", "authors_own", "composite_VAS", "NR"
Hints:
- Look in the Methods section under headings such as 'Outcome measures', 'Definitions', or 'Endpoints' for how SSI or wound infection was defined
- If the paper cites CDC criteria by name or reference, use 'CDC'
- If the paper describes a scoring system combining multiple wound signs (e.g. swelling, redness, exudate, pain scored on a scale), use 'composite_VAS'
- If the paper states a definition in the authors' own words without citing an external standard, use 'authors_own'
- This field applies only to rows where the outcome column is 'SSI'; for all other outcome rows set to 'NA'
Rules:
- Must be exactly one of the listed options or the plain-text string 'NA'
- Use 'NA' (not 'NR') when the outcome row is not SSI
- Use 'NR' only when the outcome is SSI but no definition is stated in the paper
- Do not use null or empty string; always supply one of: CDC, authors_own, composite_VAS, NR, NA
- This field is distinct from the outcome field — it describes how SSI was operationally defined, not what the outcome is

### `follow_up_duration`  _(value)_
Time after surgery at which THIS row's outcome was assessed, in the paper's own wording. Examples: '30 days', '6 weeks', '12 weeks', '6 months', 'post-operative (duration NR)'. Look in the paper's Methods (follow-up schedule) or results tables.
Hints:
- Once the row's comparison, outcome, and arm labels are identified, scan the results table header or the corresponding row for the timepoint label
- Check the Methods section under 'Follow-up schedule', 'Outcome assessment', or 'Study visits' for the planned assessment windows
- If multiple timepoints are reported for the same outcome, each has its own row — confirm you are reading the timepoint that matches this specific row
- Timepoints may appear as column headers in results tables (e.g. '30-day SSI', 'Week 6') or as sub-rows within a table
Rules:
- Record the timepoint exactly as worded in the paper (e.g. '30 days', '6 weeks', '3 months') — do not convert units or standardise
- If the paper reports the outcome was assessed post-operatively but gives no numeric duration, use 'post-operative (duration NR)'
- Use 'NR' only if no follow-up timing information whatsoever can be inferred for this row
- Do not conflate the overall study follow-up period with the specific timepoint at which this outcome was measured — use the outcome-specific timepoint

### `notes`  _(value)_
Reviewer notes specific to this outcome row. Examples: 'trial stopped early due to harm — may overestimate effect', 'zero events in both arms — not estimable', 'outcome read from Table 2'. Use 'None' if no extra detail.
Hints:
- Check the paper's footnotes, results tables, and Methods for caveats specific to this outcome row
- Note any data quality issues such as early trial stopping, imputed data, or discrepancies between text and tables
- Record the source location (e.g. 'Table 2', 'Figure 3') if the data were read from a specific place rather than the main text
- Flag statistical anomalies such as zero events in both arms or implausibly large effect sizes
Rules:
- Free-text field — write in plain English, keeping notes concise and specific to this row
- Use 'None' if there are no noteworthy caveats or additional context for this row
- Do not duplicate information already captured in other fields (e.g. follow_up_duration, ssi_definition_source)
- Separate multiple distinct notes with a semicolon
- Do not use 'NR' — if there is nothing to note, use 'None'

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"comparison_label": {"value": "...", "source_text": "..."}, "outcome": {"value": "...", "source_text": "..."}, "arm1_label": {"value": "...", "source_text": "..."}, "arm1_events": {"value": "...", "source_text": "..."}, "arm1_n": {"value": "...", "source_text": "..."}, "arm2_label": {"value": "...", "source_text": "..."}, "arm2_events": {"value": "...", "source_text": "..."}, "arm2_n": {"value": "...", "source_text": "..."}, "ssi_definition_source": {"value": "...", "source_text": "..."}, "follow_up_duration": {"value": "...", "source_text": "..."}, "notes": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
