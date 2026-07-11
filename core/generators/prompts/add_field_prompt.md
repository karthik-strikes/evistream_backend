You are enriching a single new field that a user is adding to an existing DSPy signature.

Your only task: produce `description`, `hints`, `rules`, `options`, and `examples` for the ONE new field described below.
You are NOT generating a full signature — the class name, docstring, and other fields already exist.

═══════════════════════════════════════════════════════════════════════════════
EXISTING SIGNATURE CONTEXT
═══════════════════════════════════════════════════════════════════════════════

[[TARGET_SIGNATURE_JSON]]

This is the signature you are adding to. It already has the fields listed in `existing_fields`.

═══════════════════════════════════════════════════════════════════════════════
NEW FIELD TO ENRICH
═══════════════════════════════════════════════════════════════════════════════

[[NEW_FIELD_JSON]]

═══════════════════════════════════════════════════════════════════════════════
EXTRACTION ROLE (follow these instructions if present; skip section if blank)
═══════════════════════════════════════════════════════════════════════════════

[[EXTRACTION_ROLE_CONTEXT]]

═══════════════════════════════════════════════════════════════════════════════
USER-SUPPLIED INPUTS ARE AUTHORITATIVE
═══════════════════════════════════════════════════════════════════════════════

When `description` in NEW_FIELD_JSON is non-empty:
- **Never rewrite it.** Your output `description` MUST match it verbatim (whitespace-normalised at most).
- Use it to steer your `hints` and `rules` — they must be consistent with, not contradictory to, the user's stated meaning.

When `examples` in NEW_FIELD_JSON is non-empty:
- **Never delete, modify, or reorder the user's examples.** You may append additional examples (especially the NR case); do not alter the user's entries.
- Your `hints` and `rules` must accommodate these anchor cases.

If `description` is empty, generate one that best reflects the field's purpose.

═══════════════════════════════════════════════════════════════════════════════
ADD-FIELD-SPECIFIC RULES ⚠️
═══════════════════════════════════════════════════════════════════════════════

1. Your output `field_name` MUST exactly equal the `field_name` from NEW_FIELD_JSON. Do NOT rename it.
2. Do NOT duplicate or contradict the `existing_fields` shown in TARGET_SIGNATURE_JSON.
   - If an existing field already captures similar data, your hints/rules should describe what makes the new field distinct.
3. Your hints/rules must be consistent with the signature's overall purpose (described in `docstring`).

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FIELD STRUCTURE (STRUCTURED KEYS)
═══════════════════════════════════════════════════════════════════════════════

Return EXACTLY these keys:

```json
{
  "field_name": "<must equal NEW_FIELD_JSON field_name exactly>",
  "description": "<CLEAN 1-2 SENTENCE SUMMARY of what to extract. No embedded hints/rules/examples.>",
  "hints": [
    "<soft hint: where/how to locate the value in the document>"
  ],
  "rules": [
    "<hard constraint: format, normalisation, enum enforcement, must/must-not>"
    // <-- include "Use \"NR\" if not reported" ONLY if this field can be genuinely missing in the source -->
  ],
  "options": ["<only for select/enum fields — list all allowed values>"],
  "examples": [
    {"value": "<EXAMPLE_VALUE>", "source_text": "<EXACT QUOTE FROM DOCUMENT>"}
    // <-- add {"value":"NR","source_text":"NR"} ONLY if NR is a plausible value for this field -->
  ],
  "subform_fields": []
}
```

Rules for each key:
- `description`: 1-2 sentences only. No headers, no bullets, no embedded sections.
- `hints`: soft navigation — where/how to find the value. May be empty `[]`.
- `rules`: hard output constraints — format, normalisation, enum enforcement. Add an NR rule **only** when the field can be genuinely missing in the source (e.g., demographics, outcome counts, confidence intervals). Skip it for fields the document is guaranteed to contain (titles, study type, intervention name).
- `options`: allowed values for enum/select fields. Empty `[]` for free-text fields.
- `examples`: list of `{"value": ..., "source_text": "..."}` objects. Include an NR example only when NR is a plausible value for this field (same criterion as the NR rule above).
- `subform_fields`: enrichment for subform columns — see below.

⚠️ DO NOT include a "Source Grounding" block anywhere in `description` or `rules`.
   It is automatically injected by the runtime. Including it will duplicate it.

═══════════════════════════════════════════════════════════════════════════════
TYPE MAPPING
═══════════════════════════════════════════════════════════════════════════════

Use the `field_type` from NEW_FIELD_JSON to guide your examples:
- "text"    → `"value"` is a `str`
- "number"  → `"value"` is an `int` or `float`
- "select"  → `"value"` is a `str` (one of the options); set `options` to the provided list
             If `multiple: true` on the field, `"value"` is a `List[str]` (multiple selections allowed)
- "boolean" → `"value"` is `true` or `false`
- "array"   → `"value"` is a list of objects (see SUBFORM section below)

NR convention (when applicable): if a value is genuinely not reported, use `{"value": "NR", "source_text": "NR"}` — never empty strings or null. Only emit NR for fields that can be genuinely missing in the source.

═══════════════════════════════════════════════════════════════════════════════
SUBFORM FIELDS (only when field_type = "array")
═══════════════════════════════════════════════════════════════════════════════

If `field_type` is `"array"` and `subform_fields` is present in NEW_FIELD_JSON:
- The outer `"value"` key contains a List of objects; `"source_text"` quotes the source passage.
- Each cell inside each row is itself `{"value": ..., "source_text": ...}` — never a bare scalar. This mirrors how every scalar field carries its own source grounding.
- Parent `description` is 1-3 sentences only — do NOT enumerate column names or types.
- Populate `subform_fields` with one entry per column that needs enrichment:
  - `field_name` MUST exactly match an input column name (never invent columns).
  - `field_description`: copy verbatim if user provided non-empty; enrich only when blank.
  - `hints`, `rules`, `examples`: column-specific. Omit if not needed.
- STRUCTURAL RULES: Never add, rename, remove, or reorder columns.
- For "NR" case: `{"value": "NR", "source_text": "NR"}` — NOT an empty list.

═══════════════════════════════════════════════════════════════════════════════
WORKED EXAMPLE — TEXT / SELECT FIELD
═══════════════════════════════════════════════════════════════════════════════

TARGET_SIGNATURE_JSON:
```json
{
  "class_name": "ExtractDemographics",
  "docstring": "Extract patient demographic information from clinical documents.",
  "existing_fields": [
    {"name": "patient_age", "description": "Patient age in years as a whole number."}
  ]
}
```

NEW_FIELD_JSON:
```json
{
  "field_name": "patient_sex",
  "field_type": "select",
  "description": "Biological sex of the patient.",
  "examples": [],
  "options": ["Male", "Female", "Other", "NR"]
}
```

OUTPUT:
```json
{
  "field_name": "patient_sex",
  "description": "Biological sex of the patient.",
  "hints": ["Look in the demographics, patient profile, or baseline characteristics section"],
  "rules": [
    "Must be exactly one of the listed options",
    "Use exact spelling and capitalisation",
    "Use \"NR\" if sex is not reported"
  ],
  "options": ["Male", "Female", "Other", "NR"],
  "examples": [
    {"value": "Male", "source_text": "Patient: 52-year-old male with a history of hypertension."},
    {"value": "Female", "source_text": "Demographics: Female, age 34, non-smoker."},
    {"value": "NR", "source_text": "NR"}
  ],
  "subform_fields": []
}
```

Note: `patient_age` already exists in the signature — the new field's hints and rules do NOT repeat age-related guidance.

═══════════════════════════════════════════════════════════════════════════════
WORKED EXAMPLE — SUBFORM FIELD
═══════════════════════════════════════════════════════════════════════════════

NEW_FIELD_JSON:
```json
{
  "field_name": "adverse_events",
  "field_type": "array",
  "description": "",
  "examples": [],
  "options": [],
  "subform_fields": [
    {"field_name": "event_name", "field_type": "text", "field_description": ""},
    {"field_name": "severity",   "field_type": "select", "field_description": "Grade 1–5 per CTCAE"},
    {"field_name": "outcome",    "field_type": "text", "field_description": ""}
  ]
}
```

OUTPUT:
```json
{
  "field_name": "adverse_events",
  "description": "All adverse events reported in the study, including name, severity grade, and outcome.",
  "hints": ["Look in the Safety, Adverse Events, or Tolerability sections"],
  "rules": [
    "Extract EVERY adverse event mentioned, including those in tables",
    "Use {\"value\": \"NR\", \"source_text\": \"NR\"} if no adverse events are reported"
  ],
  "options": [],
  "examples": [
    {"value": [{
      "event_name": {"value": "Nausea", "source_text": "Adverse events included Grade 1 nausea which resolved without intervention."},
      "severity":   {"value": "Grade 1", "source_text": "Adverse events included Grade 1 nausea which resolved without intervention."},
      "outcome":    {"value": "Resolved", "source_text": "Grade 1 nausea which resolved without intervention."}
    }], "source_text": "Adverse events included Grade 1 nausea which resolved without intervention."},
    {"value": "NR", "source_text": "NR"}
  ],
  "subform_fields": [
    {
      "field_name": "event_name",
      "field_description": "Name of the adverse event using MedDRA preferred term when available.",
      "hints": ["Use MedDRA preferred term if listed"],
      "rules": [],
      "examples": [{"value": "Nausea", "source_text": "Grade 1 nausea was reported in 5 patients."}]
    },
    {
      "field_name": "severity",
      "field_description": "Grade 1–5 per CTCAE",
      "hints": [],
      "rules": [],
      "examples": [{"value": "Grade 3", "source_text": "Grade 3 thrombocytopenia occurred in 2 patients."}]
    },
    {
      "field_name": "outcome",
      "field_description": "Resolution status or final outcome of the adverse event.",
      "hints": [],
      "rules": [],
      "examples": [{"value": "Resolved", "source_text": "resolved spontaneously after 3 days"}]
    }
  ]
}
```

═══════════════════════════════════════════════════════════════════════════════
NOW GENERATE THE FIELD ENRICHMENT
═══════════════════════════════════════════════════════════════════════════════

Using the TARGET_SIGNATURE_JSON and NEW_FIELD_JSON provided above, output the field enrichment JSON following all rules above.
