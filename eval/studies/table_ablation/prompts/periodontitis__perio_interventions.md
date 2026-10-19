# Single-call extraction — Intervention Characteristics (periodontitis)

You are extracting the "interventions" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

One row per study arm (including control and usual-care arms), capturing each arm's label, comparison context, treatment description, participant count, follow-up duration, and Cochrane subgroup category. Each row is independent.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `arm_label`  _(anchor)_
Arm/group label as the paper names it, unique within the study (e.g. 'Gp A (n=12)', 'Group 1', 'SRP group', 'control'). Copy the paper's own wording.
Hints:
- Look in the Methods section under 'Study arms', 'Randomisation', 'Treatment groups', or 'Participants' for how each arm is formally named
- Check tables (e.g. baseline characteristics, CONSORT flow diagram) where arms are listed as column headers or row labels
- If the paper uses abbreviations (e.g. 'Gp A', 'T1', 'C') copy them exactly as written, including any parenthetical counts
- For control or usual-care arms, the label may be implicit (e.g. 'control group', 'placebo arm') — copy the closest explicit label used in the text
- Each arm must have a distinct label; if the paper uses the same term for two arms, append a disambiguating detail from the paper (e.g. dose level)
Rules:
- Copy the label verbatim from the paper, preserving capitalisation, abbreviations, and parenthetical notes exactly as written
- Each row must have a unique arm_label within the extraction; do not merge two arms under one label
- Do not paraphrase, translate, or normalise the label — use the paper's own wording
- If the paper assigns no explicit label to an arm (e.g. only describes it narratively), construct the shortest unambiguous label from the paper's own terms and note it is inferred

### `comparison_summary`  _(value)_
One-line summary of the comparison the paper makes, from the paper's own description (e.g. 'SRP vs supragingival scaling', 'SRP+doxycycline vs usual care').
Hints:
- Look in the abstract, introduction, or methods section where the study design or primary comparison is stated
- Often found in sentences like 'patients were randomised to X or Y' or in the trial registration description
- The comparison summary is the same for all arms within a single study — derive it from the overall study design, not arm-specific text
- Use 'vs' to separate the two (or more) arms being compared, mirroring the paper's own terminology
Rules:
- Copy the paper's own wording as closely as possible in a single concise line
- Use 'vs' (not 'versus' or '/') to separate arms unless the paper uses different notation
- Do not duplicate the full intervention_description — this is a high-level label only
- This field describes the overall study comparison and will be the same value repeated across all arms of the same study
- Use "NR" if the paper does not explicitly state a comparison and one cannot be reasonably inferred

### `intervention_description`  _(anchor)_
Verbatim description of this arm's treatment from the paper (procedure, anaesthesia, instruments, frequency, adjuncts).
Hints:
- Look in the Methods, Interventions, or Treatment Protocol sections for the detailed description of each arm's treatment
- Copy the paper's own wording as closely as possible, including procedural details such as instruments used, anaesthesia type, number of sessions, and any adjunctive agents
- If the paper uses a table to describe interventions, extract the relevant cell text for this arm
- Check figure legends and supplementary materials if the main text is sparse on procedural detail
- Distinguish this arm's description from those of other arms — each row should capture only the treatment specific to that arm
Rules:
- Copy the paper's own wording verbatim or near-verbatim; do not paraphrase or summarise beyond light normalisation of whitespace
- Include all procedural details mentioned: instruments, anaesthesia, number/frequency of sessions, adjunctive drugs or rinses, and any other treatment components
- Do not conflate details from different arms — each row must describe only its own arm's treatment
- Use "NR" if the paper provides no description of the treatment for this arm beyond its label

### `n_in_arm`  _(anchor)_
Number of participants randomised to this arm. Use 'NR' if not stated.
Hints:
- Look in the CONSORT flow diagram, randomisation section, or baseline characteristics table for per-arm allocation counts
- The value is often embedded in the arm label itself (e.g., 'Group A (n=24)') or in a participants table
- Check the methods section under 'randomisation' or 'allocation' for the number assigned to each group
- If the paper reports only a total N and equal allocation, calculate the per-arm count only if explicitly stated; otherwise use NR
Rules:
- Output a whole number (integer) representing the count of participants randomised to this arm
- Do not use the number of completers or analysed participants — randomised count only
- Use "NR" if the per-arm randomised count is not explicitly stated
- Do not infer or calculate from total N unless the paper explicitly states equal allocation and the per-arm figure

### `duration_of_followup`  _(value)_
Total follow-up duration for this arm (e.g. '6 mths', '3 months'). Use 'NR' if not stated.
Hints:
- Look in the Methods section under 'Follow-up', 'Study duration', or 'Assessment schedule' for the total observation period
- Check the Results section or summary tables for the final time-point reported for this arm
- Follow-up duration may be stated once for all arms; if so, apply the same value to each arm row
- Distinguish follow-up duration from treatment duration — capture the total post-baseline observation period, not just the active treatment window
Rules:
- Record the total follow-up period as stated in the paper, preserving the paper's own units and abbreviations (e.g. '6 months', '6 mths', '1 year', '12 wks')
- Do not convert units — if the paper says '6 mths', output '6 mths', not '0.5 years'
- If follow-up duration is not reported for this arm, output 'NR'
- Do not conflate treatment duration with follow-up duration; use the longer post-baseline observation period when both are mentioned

### `cochrane_subgroup_category`  _(anchor)_
Treatment-type class of this arm, derived from the paper's description (OPTIONAL). 'SI_alone' = subgingival instrumentation (scaling/root planing) alone; 'SI_plus_systemic_local_antimicrobial' = SI plus a systemic or locally delivered antimicrobial; 'SI_plus_mouthrinse' = SI plus an antiseptic mouthrinse; 'usual_care' = control / no active periodontal treatment; 'supragingival_only' = supragingival scaling only.
Allowed values: "SI_alone", "SI_plus_systemic_local_antimicrobial", "SI_plus_mouthrinse", "usual_care", "supragingival_only"
Hints:
- Derive the category from the intervention_description already captured for this arm — do not rely on the paper explicitly naming a Cochrane subgroup
- If the arm receives subgingival scaling/root planing with no adjuncts, classify as 'SI_alone'
- If the arm receives subgingival scaling/root planing plus any antibiotic (systemic or local) or antimicrobial agent (e.g. chlorhexidine chips, doxycycline gel, metronidazole), classify as 'SI_plus_systemic_local_antimicrobial'
- If the arm receives subgingival scaling/root planing plus an antiseptic mouthrinse (e.g. chlorhexidine rinse), classify as 'SI_plus_mouthrinse'
- If the arm is described as a control, placebo, no-treatment, or usual-care arm with no active periodontal instrumentation, classify as 'usual_care'
- If the arm receives only supragingival scaling or prophylaxis without subgingival instrumentation, classify as 'supragingival_only'
- When an arm combines mouthrinse and a systemic/local antimicrobial alongside SI, prefer 'SI_plus_systemic_local_antimicrobial'
Rules:
- Must be exactly one of the listed options: 'SI_alone', 'SI_plus_systemic_local_antimicrobial', 'SI_plus_mouthrinse', 'usual_care', 'supragingival_only'
- Use exact spelling, underscores, and lowercase as shown in the options list
- This field is OPTIONAL; use 'NR' only if the intervention description is too ambiguous to assign any category
- Do not invent new category values outside the allowed list

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"arm_label": {"value": "...", "source_text": "..."}, "comparison_summary": {"value": "...", "source_text": "..."}, "intervention_description": {"value": "...", "source_text": "..."}, "n_in_arm": {"value": "...", "source_text": "..."}, "duration_of_followup": {"value": "...", "source_text": "..."}, "cochrane_subgroup_category": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
