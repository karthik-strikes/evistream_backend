# Single-call extraction — Continuous Outcomes (periodontitis)

You are extracting the "outcomes" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

All continuous outcomes reported in the study, with one row per unique combination of outcome type, follow-up timepoint, and comparison subgroup. Each row captures group-level means, standard deviations, and sample sizes for both the intervention and control arms.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `outcome_type`  _(anchor)_
Continuous outcome measured. 'HbA1c' = glycated haemoglobin (%); 'CAL' = clinical attachment level; 'PPD' = probing pocket depth; 'BOP' = bleeding on probing; 'PI' = plaque index; 'GI' = gingival index. Map synonyms to the closest option.
Allowed values: "HbA1c", "CAL", "PPD", "BOP", "PI", "GI"
Hints:
- Look in the outcomes, results, or methods sections for named outcome measures
- Map synonyms and abbreviations to the closest option: e.g., 'glycated haemoglobin', 'HbA1c', 'A1c' → 'HbA1c'; 'attachment level', 'clinical attachment loss' → 'CAL'; 'pocket depth', 'probing depth', 'PD' → 'PPD'; 'bleeding on probing', 'BOP%' → 'BOP'; 'plaque index', 'plaque score' → 'PI'; 'gingival index', 'gingival score' → 'GI'
- Outcome labels are typically found in table headers, figure legends, or the primary/secondary outcomes list in the Methods section
- Each distinct outcome type should generate a separate row — scan all tables and result paragraphs to ensure no outcome is missed
Rules:
- Must be exactly one of the listed options: HbA1c, CAL, PPD, BOP, PI, GI
- Map all synonyms and variant abbreviations to the canonical option label
- Do not invent new outcome types; if an outcome cannot be mapped to any listed option, omit that row
- Use exact spelling and capitalisation as listed in options

### `timepoint`  _(anchor)_
Timepoint after baseline at which this outcome was measured, mapped to the nearest bin. '3-4_months' covers ~3-4 month follow-up; '6_months' covers ~6 months; '12_months' covers ~12 months.
Allowed values: "3-4_months", "6_months", "12_months"
Hints:
- Look in the Results, Tables, or Follow-up sections for explicit time labels such as '3 months', '4 months', '6 months', '1 year', '12 months'
- Map approximate or colloquial labels (e.g., 'short-term', '3-month visit', 'one year') to the nearest bin
- If the paper reports multiple follow-up timepoints, create a separate row for each one
- Timepoints are often column headers in results tables or stated in the Methods section under 'Follow-up schedule'
- If the paper uses weeks (e.g., 24 weeks ≈ 6 months, 52 weeks ≈ 12 months), convert to the nearest month bin
Rules:
- Must be exactly one of the listed options: '3-4_months', '6_months', '12_months'
- Map any follow-up between ~10 and ~14 weeks to '3-4_months'; ~5–7 months to '6_months'; ~10–14 months to '12_months'
- Do not use baseline measurements as a timepoint row — only post-baseline follow-up visits qualify
- Each distinct follow-up timepoint reported in the paper must generate its own row in the output
- Use exact option spelling with underscores as shown

### `subgroup`  _(anchor)_
Which comparison this row belongs to, derived from what the paper's control arm received (OPTIONAL). 'SI_vs_usual_care' = subgingival instrumentation vs no active treatment / usual care; 'SI_plus_antimicrobial' = the intervention adds a systemic or local antimicrobial; 'SI_plus_mouthrinse' = the intervention adds an antiseptic mouthrinse.
Allowed values: "SI_vs_usual_care", "SI_plus_antimicrobial", "SI_plus_mouthrinse"
Hints:
- Look at the Methods or Interventions section to identify what the control arm received and what adjunct (if any) was added to the SI arm
- If the paper has only one comparison arm, the subgroup is typically 'SI_vs_usual_care'
- If the paper describes multiple arms (e.g., SI alone vs SI plus antibiotic vs control), create a separate row for each comparison and assign the appropriate subgroup label
- Check table headers, arm labels, and footnotes for descriptions of what each group received
- If the control arm is described as 'no treatment', 'usual care', 'supragingival scaling only', or 'no periodontal treatment', map to 'SI_vs_usual_care'
- If the intervention arm includes a systemic antibiotic (e.g., doxycycline, metronidazole, amoxicillin) or local antimicrobial delivery, map to 'SI_plus_antimicrobial'
- If the intervention arm includes an antiseptic rinse (e.g., chlorhexidine mouthwash), map to 'SI_plus_mouthrinse'
Rules:
- Must be exactly one of the listed options: 'SI_vs_usual_care', 'SI_plus_antimicrobial', 'SI_plus_mouthrinse'
- Use exact spelling and underscores as shown; do not paraphrase or abbreviate
- This field classifies the comparison type, not the outcome or timepoint — do not conflate with outcome_type or timepoint
- Use 'NR' if the comparison type cannot be determined from the paper

### `mean_arm1`  _(value)_
Mean value of the outcome in the intervention (subgingival instrumentation) arm at this timepoint, from the paper's results. Use 'NR' if not reported.
Hints:
- Look in the results tables or text for the intervention arm (subgingival instrumentation / SI) column at the already-identified outcome type and timepoint
- The intervention arm is typically labelled 'SI', 'treatment', 'test', or 'periodontal therapy' group
- Values may appear as post-treatment means or change-from-baseline means; extract whichever the paper reports consistently across arms
- Check both in-text summaries and tabulated results; prefer the table value if both are present
Rules:
- Report as a numeric value (integer or decimal) matching the precision given in the source
- Do not convert units — report the value exactly as stated in the paper
- If the paper reports a change from baseline rather than an absolute mean, record that change value and note it is consistent with how mean_arm2 is recorded
- Use "NR" if the mean for the intervention arm is not reported for this outcome × timepoint combination

### `sd_arm1`  _(value)_
Standard deviation of the outcome in the intervention arm at this timepoint. Use 'NR' if not reported.
Hints:
- Look in the results tables where the intervention arm (subgingival instrumentation) statistics are reported for the already-identified outcome type and timepoint
- SD is often presented alongside the mean in the format 'mean ± SD' or in a separate SD column in the results table
- Check table footnotes or in-text results if the SD is not in the main table cell
- This is the SD for arm 1 (intervention); the corresponding SD for arm 2 (control) is captured in sd_arm2
Rules:
- Report as a numeric value (integer or decimal) matching the precision given in the source
- Do not convert or round beyond the precision reported in the paper
- Use 'NR' if the standard deviation for the intervention arm is not reported at this timepoint
- Do not substitute SEM, IQR, or confidence interval widths for SD unless the paper explicitly states the value is an SD

### `n_arm1`  _(value)_
Number of participants analysed in the intervention arm at this timepoint. Use 'NR' if not reported.
Hints:
- Look in the results tables for the intervention (subgingival instrumentation) arm sample size at the specific timepoint already identified
- Check footnotes or table headers for per-timepoint n values, as sample sizes may differ from baseline due to dropouts
- The value may appear as 'n=', 'N=', or in a dedicated column labelled 'n' or 'participants' adjacent to the mean and SD for the intervention arm
Rules:
- Report as a positive integer (whole number of participants)
- Use 'NR' if the per-timepoint sample size for the intervention arm is not explicitly reported
- Do not substitute the baseline enrolment count if the analysed n at this timepoint is not given — use 'NR' instead
- This field captures the intervention arm count only; the control arm count is captured in n_arm2

### `mean_arm2`  _(value)_
Mean value of the outcome in the control / usual-care arm at this timepoint, from the paper's results. Use 'NR' if not reported.
Hints:
- Look in the results tables or text for the control arm column corresponding to the already-identified outcome type and timepoint
- The control arm may be labelled 'usual care', 'no treatment', 'control', or named after a specific comparator (e.g., 'scaling and root planing alone', 'sham treatment')
- In two-arm tables, mean_arm2 is typically the right-hand or second column of paired mean ± SD values
- Cross-reference with n_arm2 and sd_arm2 in the same row to confirm you are reading the correct arm
Rules:
- Report as a numeric value (integer or decimal) matching the precision given in the source
- Do not convert units — report in the same units as the source (e.g., % for HbA1c, mm for CAL/PPD)
- Do not confuse with mean_arm1, which captures the intervention (subgingival instrumentation) arm
- Use "NR" if the control arm mean is not reported for this outcome × timepoint combination

### `sd_arm2`  _(value)_
Standard deviation of the outcome in the control arm at this timepoint. Use 'NR' if not reported.
Hints:
- Once the row's outcome type, timepoint, and subgroup are identified, locate the corresponding control arm column in the results table
- Look for columns labelled 'SD', 'standard deviation', '±', or parenthetical values adjacent to the control arm mean (mean_arm2)
- Check table footnotes or in-text results if the SD is not in the main table cell
- Distinguish from sd_arm1 (intervention arm) — this value belongs to the control/usual-care arm only
Rules:
- Report as a numeric value (integer or decimal) matching the precision given in the source
- Do not convert or round beyond the precision reported in the paper
- Use "NR" if the standard deviation for the control arm is not reported at this timepoint
- Do not confuse with the intervention arm SD (sd_arm1) or with standard error (SE) — only extract SD; if only SE is given, note NR unless SE is explicitly labelled as SD

### `n_arm2`  _(value)_
Number of participants analysed in the control arm at this timepoint. Use 'NR' if not reported.
Hints:
- Once the row's outcome type, timepoint, and subgroup are identified, locate the corresponding sample size for the control/usual-care arm in the results table or text for that specific timepoint
- Look for columns labelled 'n', 'N', 'control n', or similar in results tables; footnotes may report attrition-adjusted counts
- Prefer the analysed (per-protocol or intention-to-treat) count at the specific follow-up timepoint rather than the enrolled count at baseline
- This is the control-arm counterpart to n_arm1, which captures the intervention-arm count
Rules:
- Report as a positive integer (whole number of participants)
- Do not report the baseline enrolment count if a timepoint-specific analysed count is available
- Use "NR" if the control-arm sample size at this timepoint is not reported in the paper

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"outcome_type": {"value": "...", "source_text": "..."}, "timepoint": {"value": "...", "source_text": "..."}, "subgroup": {"value": "...", "source_text": "..."}, "mean_arm1": {"value": "...", "source_text": "..."}, "sd_arm1": {"value": "...", "source_text": "..."}, "n_arm1": {"value": "...", "source_text": "..."}, "mean_arm2": {"value": "...", "source_text": "..."}, "sd_arm2": {"value": "...", "source_text": "..."}, "n_arm2": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
