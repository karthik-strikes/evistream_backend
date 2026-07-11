# Single-call extraction — Intervention Characteristics (ibuprofen)

You are extracting the "interventions" table from ONE study report in a systematic review. Produce one row per study arm / record. Read the entire paper text provided in the next message and extract EVERY row the paper supports — do not stop early, and do not merge two distinct arms/records into one row.

One row per study arm (including the ibuprofen arm, comparator arms, placebo, and combination arms). Each row is independent.

## Columns
For every row, return each column as an object {"value": ..., "source_text": ...} (source grounding). Use "NR" when a value is not reported. Copy values from the paper; never invent.

### `arm_label`  _(anchor)_
The arm's drug / group name as a SHORT label — just the comparator word (e.g. 'ibuprofen', 'placebo', 'paracetamol', 'ketorolac'); for crossover/combination arms use the paper's short group wording. This is a row-matching key — put the dose in 'dose', not here.
Hints:
- Look at the arm/group headings used in the methods, tables, or figures (e.g. 'Group A', 'Ibuprofen group', 'Placebo') and copy the short label the paper itself uses to name that arm
- For combination arms, use the paper's own short combined wording (e.g. 'ibuprofen/paracetamol') rather than inventing a new phrase
- If the paper only numbers or letters the groups (e.g. 'Group 1', 'Group 2'), use that label and rely on drug_name/dose fields to disambiguate identity
- When an arm is only implied by context (e.g. 'the remaining patients received placebo'), infer the short label from that context rather than leaving it blank
Rules:
- Must be a short label (1-3 words), not a full sentence or dose description
- Do not include dose, route, or frequency information in this field — those belong in their own fields
- Use the paper's own terminology/spelling for the label whenever possible, normalising only obvious synonyms (e.g. 'acetaminophen' → 'paracetamol') for consistency with drug_name
- Each row in the extraction must have a unique, non-empty arm_label distinguishing it from other arms

### `arm_category`  _(anchor)_
Treatment class of this arm. 'ibuprofen' = the ibuprofen arm; the comparator options name the drug this arm received; 'combination' = a fixed combination arm (e.g. ibuprofen + paracetamol); 'placebo' = placebo / saline / no-treatment control; 'other' = any other drug (e.g. codeine, piroxicam, flurbiprofen, chewing gum/wafer).
Allowed values: "ibuprofen", "placebo", "paracetamol", "morphine", "ketorolac", "naproxen_sodium", "rofecoxib", "aspirin", "combination", "other"
Hints:
- Match this to the same arm identified by arm_label and drug_name — classify based on what drug(s) that arm actually received, not the paper's group letter or number
- If the arm receives two or more active drugs together, classify as 'combination' even if one of the components is ibuprofen
- Check the methods/interventions section where arms are first named, and confirm against tables listing group assignments
- If the drug named doesn't match any listed comparator option, use 'other' rather than inventing a new category
Rules:
- Must be exactly one of the listed options
- Select 'combination' only for arms receiving a fixed combination of two or more active drugs, not for an active-drug-vs-comparator design
- Do not use 'NR' — every arm must be classifiable into one of the listed categories based on its drug_name

### `drug_name`  _(anchor)_
Generic drug name for this arm (INN), or 'placebo' for control arms. For combination arms join with ' + ' (e.g. 'ibuprofen + paracetamol'). Note: 'acetaminophen' = paracetamol. Use 'NR' if only a class is given.
Hints:
- Look for the drug's generic/INN name in the methods or interventions section, near the arm label or dose
- If only a brand/trade name is given, convert to the generic name when identifiable
- For combination arms, list all active drugs joined by ' + ' in the order the paper presents them
- Distinguish this from 'arm_label', which is a short row-matching key rather than the full generic drug identity
Rules:
- Use the generic/INN drug name, not brand names, when identifiable
- Use 'placebo' (lowercase) for placebo/control arms, not the drug name of a comparator
- Normalize 'acetaminophen' to 'paracetamol'
- For combination arms, join constituent drug names with ' + ' in a consistent order
- Use 'NR' only if the paper names a drug class without specifying the actual drug

### `dose`  _(value)_
Dose with units as the paper states it (e.g. '10 mg/kg', '400 mg', '10 mg/kg (max 600 mg)'). Use 'NR' if not stated.
Hints:
- Locate the dose value reported alongside the drug/arm name for this row, usually in the same sentence, table row, or Methods paragraph that names the arm
- If two rows share the same drug but differ only by dose (e.g. 'ibuprofen 400 mg' vs 'ibuprofen 800 mg'), treat these as distinct arms and use the dose to tell them apart, since arm_label alone may not distinguish them
- For combination arms, copy the dose paired with each component drug as stated, in the same order as drug_name
- Watch for dose given implicitly via a fixed regimen description (e.g. 'the standard 10 mg/kg ibuprofen suspension') rather than a clearly labelled 'Dose:' field
Rules:
- Copy the dose exactly as written in the source text, including units and any max-dose qualifiers — do not convert, recalculate, or standardise units
- For combination arms, list each component's dose in the same order as the corresponding drugs in drug_name (e.g. '10 mg/kg + 15 mg/kg')
- Treat arms with the same drug but different stated doses as separate rows, not duplicates
- Use "NR" if the dose is not stated for this arm

### `route`  _(value)_
Route of administration for this arm. 'oral' = by mouth; 'IV' = intravenous; 'rectal' = suppository. Use 'NR' if not stated.
Allowed values: "oral", "IV", "rectal", "NR"
Hints:
- Look near the dosing description or methods section where the drug administration is described, e.g. 'administered orally', 'given IV', 'rectal suppository'
- Watch for abbreviations or synonyms such as 'PO' or 'p.o.' for oral, 'i.v.' for IV, and 'PR' or 'per rectum' for rectal
- If the route is only implied by the dosage form (e.g. 'tablet', 'syrup', 'suppository', 'infusion') rather than stated explicitly, infer the matching option from that wording
- Check tables listing arms/groups as well as the running text, since route is sometimes only given once for all arms
Rules:
- Must be exactly one of the listed options
- Do not invent a route that is not stated or clearly implied by dosage form wording
- Use "NR" if the route is not stated or cannot be inferred for this arm

### `frequency`  _(value)_
Dosing interval / schedule condensed, INCLUDING timing relative to surgery when stated (e.g. 'single preoperative dose', 'every 6 h for 24 h', 'preoperative then every 6 h for 3 days'). Use 'NR' if not stated.
Hints:
- Look for the dosing schedule wording near the dose and route description in the Methods or Interventions section
- Copy the schedule phrase as the paper states it rather than inferring a numeric interval
- Watch for timing cues relative to surgery (e.g. 'preoperatively', 'postoperatively', 'on induction') and fold them into the label if present
- If the schedule is only implied by repeated administration described in text (e.g. 'given every 6 hours for the first day'), condense it into a short phrase rather than leaving it blank
- Check tables and figure legends as well as prose, since dosing schedules are often given in a treatment protocol table
Rules:
- Condense the schedule into a short phrase rather than a full sentence, but preserve timing relative to surgery when stated
- Do not invent a frequency if only a single dose is described without repetition — use 'single dose' or 'single preoperative dose' as appropriate
- Use "NR" if the dosing schedule/interval is not stated anywhere in the paper

### `n_in_arm`  _(anchor)_
Number of participants randomised to THIS arm (not the number analysed at follow-up). Use 'NR' if not stated per arm.
Hints:
- The arm for this row is already identified via arm_label/drug_name/dose — locate the sample size specifically for that arm
- Check the Methods/randomisation description, CONSORT flow diagram, or Table 1 baseline characteristics table for per-arm 'n' values
- Look for phrasing like 'n=30 per group' or a breakdown table listing group sizes side by side
- Do not use the total study enrollment or the number completing/analysed at final follow-up unless it is the only figure given for randomisation
Rules:
- Report the number randomised/allocated to this specific arm, not the overall trial total
- If only participants completing or analysed at the endpoint are reported (with no randomised number given), extract that value but do not confuse it with total sample size
- Value must be a whole number
- Use "NR" if the per-arm sample size is not stated

### `comparison_summary`  _(value)_
One-line summary of the comparison this study makes, from the paper's own description (e.g. 'ibuprofen vs ketorolac', 'ibuprofen vs paracetamol vs placebo').
Hints:
- Check the title, abstract, or objectives/aims section where the study design is typically stated as a comparison
- Look for phrasing like 'compared to', 'versus', 'vs.', or 'in comparison with' near the study description
- This is a single overall summary of the trial's design, not a per-arm value — it should reflect all arms together, unlike arm_label which names one arm at a time
Rules:
- Use 'vs' between arm names, listed in the order the paper presents them
- Use short drug/group names consistent with arm_label wording (e.g. 'ibuprofen', 'placebo') rather than full dosing details
- Do not include doses, routes, or sample sizes in this summary — keep it to drug/group names only
- Use "NR" if the paper does not explicitly state the comparison as a single phrase and it cannot be reasonably inferred from the arms studied

## Output format
Return ONLY a single JSON object — no prose, no explanation, no markdown code fences:

{"rows": [
  {"arm_label": {"value": "...", "source_text": "..."}, "arm_category": {"value": "...", "source_text": "..."}, "drug_name": {"value": "...", "source_text": "..."}, "dose": {"value": "...", "source_text": "..."}, "route": {"value": "...", "source_text": "..."}, "frequency": {"value": "...", "source_text": "..."}, "n_in_arm": {"value": "...", "source_text": "..."}, "comparison_summary": {"value": "...", "source_text": "..."}}
]}

Source grounding — EVERY cell is an object with two keys:
- "value": the extracted value, or "NR" if the paper does not report it.
- "source_text": ONE sentence (≤30 words) copied VERBATIM from the paper that supports the value; the value (or the phrase it was derived from) must appear in it. Use "NR" when value is "NR".
- One object per row; include every column key above in every row; never return a bare string for a cell.
- If the paper reports no rows for this table, return {"rows": []}.
