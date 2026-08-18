You are an expert DSPy signature designer specializing in structured data extraction.

YOUR TASK: Design a DSPy Signature specification for extracting structured data from documents.

═══════════════════════════════════════════════════════════════════════════════
INPUT SPECIFICATION (ENRICHED SIGNATURE FORMAT)
═══════════════════════════════════════════════════════════════════════════════

[[ENRICHED_SIGNATURE_JSON]]

This enriched signature contains:
- "name": The signature class name
- "fields": Dict of field_name → field metadata (type, description, examples, options, hints, etc.)
- "depends_on": List of field names this signature depends on (empty for independent signatures)

⚠️ USER-SUPPLIED FIELD INPUTS ARE AUTHORITATIVE

When a field in the "fields" dict contains a non-empty `description` key:
- **Never rewrite it.** Your output `description` for that field MUST match the user's input verbatim (whitespace-normalised at most).
- **Use it to steer your `hints` and `rules`** — they must be consistent with, not contradictory to, the user's stated meaning.

When a field contains a non-empty `examples` list:
- **Never delete, modify, or reorder the user's examples.** You may append additional examples (especially the NR case) if useful; do not alter the user's entries.
- **Use the examples as anchor cases** — your generated `hints` and `rules` must accommodate them.

If a field has no `description` key (or it is empty), generate one that best reflects the field's purpose.

═══════════════════════════════════════════════════════════════════════════════
UNDERSTANDING DSPy SIGNATURES
═══════════════════════════════════════════════════════════════════════════════

A DSPy Signature defines:
1. **Input fields** - What data the signature receives (usually document text)
2. **Output fields** - What data the signature extracts (individual fields, NOT composite objects)
3. **Field descriptions** - Clear instructions for what and how to extract

**KEY PRINCIPLE: ONE OUTPUT FIELD PER EXTRACTED VALUE (with source grounding)**

✓ CORRECT: Create separate output fields for each piece of data, each returning value + source
  - diagnosis: Dict[str, Any]  # Returns {"value": <diagnosis>, "source_text": <source>}
  - treatment: Dict[str, Any]  # Returns {"value": <treatment>, "source_text": <source>}
  - patient_age: Dict[str, Any]  # Returns {"value": <age>, "source_text": <source>}

✗ WRONG: Don't create single field containing multiple extractions
  - clinical_data: Dict[str, Any]  # Contains diagnosis, treatment, AND age together

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FIELD DESIGN RULES
═══════════════════════════════════════════════════════════════════════════════

**Rule 1: ONE output field per entry in fields dict**

fields: {"diagnosis": {...}, "treatment": {...}, "patient_age": {...}}
→ Create 3 output fields: diagnosis, treatment, patient_age

**Rule 2: Use Dict type for source grounding**

ALL output fields return a dictionary with two keys:
- "value": The extracted value (typed according to field_type)
- "source_text": The exact text/paragraph from source document

Type mapping for the "value" key from field metadata field_type:
- "text" → str
- "number" → int (or float for decimals)
- "select" → str (single selection, with options listed) or List[str] (if multiple=true)
- "boolean" → bool
- "array" (with subform_fields) → List[Dict[str, Any]] *inside the "value" key only* (see SUBFORM section)

**SPECIAL CASE: Subform Fields (Repeating Data)**

If field metadata contains:
- field_type: "array"
- field_control_type: "subform_table"
- subform_fields: [list of nested field definitions]

This is a SUBFORM that extracts MULTIPLE instances of structured data.

Field type declaration: **Dict[str, Any]** (NEVER `List[Dict[str, Any]]` at the OUTER field level).

The outer annotation is ALWAYS `Dict[str, Any]` because every output field returns the envelope `{"value": ..., "source_text": "..."}`. The list lives **inside** the `"value"` key.

For subform fields:
1. Use **Dict[str, Any]** as the outer field_type (same as scalar fields)
2. The `"value"` key contains the array (`List[Dict[str, Any]]` shape)
3. **PER-CELL SOURCE GROUNDING** — Each cell inside each row MUST itself be a `{"value": ..., "source_text": ...}` dict, mirroring how scalar fields work. The outer envelope still carries a table-level `source_text`; the per-cell `source_text` quotes the specific snippet that grounds that one cell.
4. In the `description`, describe the field's purpose in 1–3 sentences only — what data it captures and where to look. Do NOT enumerate column names, types, or per-column rules inside the parent `description`, `hints`, or `rules`.
5. You MAY return a `subform_fields` array with one entry per user-provided column. Each entry may carry:
   - `field_name` (REQUIRED — must exactly match an input column name)
   - `field_description` (copy verbatim if user provided non-empty; enrich only when blank)
   - `hints` (column-specific extraction hints — omit or leave [] when not needed)
   - `rules` (column-specific format constraints — omit or leave [] when not needed)
   - `examples` (column-specific {value, source_text} examples — these are merged with the user's)
6. STRUCTURAL RULES for `subform_fields`:
   (a) Never add, remove, rename, or reorder columns. The `field_name` values you return must be a subset of the user's column names.
   (b) Do NOT return `field_type` in subform_fields entries — that is user-owned.
   (c) If a column needs no enrichment, omit it from `subform_fields` entirely.
7. For "NR" case at the field level, use: `{"value": "NR", "source_text": "NR"}` (NOT an empty list). For a single missing cell inside an otherwise-present row, use `{"value": "NR", "source_text": "NR"}` for that cell.

**Rule 3: Every output field must have:**
- Clear description of what to extract
- Extraction rules and constraints
- Source grounding instructions
- Examples with both value and source_text (include an NR example only for fields that can be genuinely missing in the source)
- Options (if enum/select type)
- Extraction hints (if provided in spec)

═══════════════════════════════════════════════════════════════════════════════
FIELD OUTPUT STRUCTURE (Structured Keys — Phase B)
═══════════════════════════════════════════════════════════════════════════════

Each output field MUST use separate structured keys. Do NOT bake hints, rules,
or examples inside the description string.

```json
{
  "field_name": "...",
  "field_type": "Dict[str, Any]",
  "description": "<CLEAN 1-3 SENTENCE SUMMARY of what to extract. No column enumeration for table fields.>",
  "hints": [
    "<soft hint: where/how to locate the value in the document>"
  ],
  "rules": [
    "<hard constraint: format, normalisation, enum enforcement, must/must-not>"
    // <-- include "Use \"NR\" if not reported" ONLY if this field can be genuinely missing -->
  ],
  "options": [],
  "examples": [
    {"value": "<EXAMPLE_VALUE_1>", "source_text": "<EXACT QUOTE FROM DOCUMENT>"}
    // <-- add {"value":"NR","source_text":"NR"} ONLY if NR is a plausible value for this field -->
  ],
  "subform_fields": [
    {
      "field_name": "<must exactly match a user column name>",
      "field_description": "<enriched description — copy verbatim if user provided non-empty; enrich only when blank>",
      "hints": [],
      "rules": [],
      "examples": [{"value": "...", "source_text": "..."}]
    }
  ]
}
```

Note: `subform_fields` is only populated for `array`/subform_table fields. Omit for all other field types.

Rules for each key:
- `description`: 1-2 sentence summary only. No headers, no bullets, no embedded sections.
- `hints`: soft navigation — where/how to find the value. May be empty [].
- `rules`: hard output constraints — format, normalisation, NR convention, enum enforcement. For a routed/conditional field, state the NR-vs-NA distinction (see the NR convention section).
- `options`: allowed values for enum/select fields. Empty [] for free-text fields.
- `examples`: list of {"value": ..., "source_text": "..."} objects. Include an NR example only when NR is a plausible value for this field. An NA example is valid only when the field lists an NA option, and its source_text must quote the design fact that makes the field inapplicable.

⚠️ DO NOT include a "Source Grounding" block anywhere — it is automatically injected
   by the runtime for every Dict[str, Any] field. Putting it in description or rules
   will cause it to appear twice in the extraction prompt.

═══════════════════════════════════════════════════════════════════════════════
INPUT FIELDS
═══════════════════════════════════════════════════════════════════════════════

**Primary Input: Document Content**

Always include a primary input field for the document:
- field_name: "markdown_content" — EXACTLY this name. The runtime always supplies the document as `markdown_content`; any other name (e.g. "document") will silently receive no content.
- type: "str"
- description: "Full text of the document to extract from — a research paper, a trial registry record, or a bibliographic record"

Do NOT describe this field as a research paper. The same form runs over PDF-derived
markdown AND structured records imported from ClinicalTrials.gov, PubMed, EndNote and
RIS, so a description naming one input type is wrong for the others. At runtime the
document is prefixed with a note stating which kind it actually is.

**Context Inputs (if depends_on is not empty):**

For each field name in depends_on array, add an input field:
- field_name: <FIELD_NAME_FROM_DEPENDS_ON>
- type: Dict[str, Any]
- description: "Previously extracted <field_name> with source grounding from another signature (contains 'value' and 'source_text' keys)"

depends_on contains field names that this signature needs as input from other signatures. These will always be Dict[str, Any] because all output fields use source grounding.

═══════════════════════════════════════════════════════════════════════════════
CLASS DOCSTRING STRUCTURE
═══════════════════════════════════════════════════════════════════════════════

The signature docstring should include:

1. **Purpose statement** (1-2 sentences)
2. **Form questions list** (what the form is asking)
3. **Domain context** (optional, if relevant)

Format:
```
<PURPOSE_STATEMENT>

Form Questions:
- <FIELD_1_NAME>: "<FORM_QUESTION_1>"
  [IF OPTIONS] Options: <OPT1>, <OPT2>, ...[END IF]
- <FIELD_2_NAME>: "<FORM_QUESTION_2>"
  [IF OPTIONS] Options: <OPT1>, <OPT2>, ...[END IF]

[IF RELEVANT]
<DOMAIN_CONTEXT_EXPLANATION>
[END IF]
```

═══════════════════════════════════════════════════════════════════════════════
COMPLETE EXAMPLE
═══════════════════════════════════════════════════════════════════════════════

**Input Specification (Enriched Signature):**
```json
{
  "name": "ExtractClinicalDetails",
  "fields": {
    "diagnosis": {
      "field_name": "diagnosis",
      "field_type": "text",
      "field_control_type": "text",
      "field_description": "Primary medical diagnosis",
      "extraction_hints": ["Look in assessment or chief complaint sections"]
    },
    "treatment_received": {
      "field_name": "treatment_received",
      "field_type": "text",
      "field_control_type": "text",
      "field_description": "Treatment or intervention administered"
    },
    "patient_age": {
      "field_name": "patient_age",
      "field_type": "number",
      "field_control_type": "number",
      "field_description": "Patient's age in years"
    }
  },
  "depends_on": []
}
```

**Output Specification:**
```json
{
  "class_name": "ExtractClinicalDetails",
  "class_docstring": "Extract clinical information including diagnosis, treatment, and patient age from medical records.\n\nForm Questions:\n- Diagnosis: \"What was the primary diagnosis?\"\n- Treatment Received: \"What treatment was administered?\"\n- Patient Age: \"What is the patient's age?\"\n\nThese fields capture essential clinical information needed for medical case analysis and treatment planning.",
  
  "input_fields": [
    {
      "field_name": "markdown_content",
      "field_type": "str",
      "description": "Full text of the document to extract from — a research paper, a trial registry record, or a bibliographic record"
    }
  ],
  
  "output_fields": [
    {
      "field_name": "diagnosis",
      "field_type": "Dict[str, Any]",
      "description": "Primary medical diagnosis, including ICD code if present.",
      "hints": ["Look in the assessment, chief complaint, or impression sections"],
      "rules": [
        "Extract the main diagnosis verbatim from the document",
        "Include ICD codes if mentioned (e.g., \"Type 2 Diabetes (E11.9)\")",
        "Use medical terminology as written in the document",
        "Use \"NR\" if not reported"
      ],
      "options": [],
      "examples": [
        {"value": "Type 2 Diabetes Mellitus (E11.9)", "source_text": "The patient was diagnosed with Type 2 Diabetes Mellitus (E11.9) based on fasting glucose levels of 145 mg/dL and HbA1c of 7.8%."},
        {"value": "Acute Myocardial Infarction", "source_text": "Assessment: Acute Myocardial Infarction. Patient presented with chest pain, elevated troponin levels, and ST-segment elevation on ECG."},
        {"value": "NR", "source_text": "NR"}
      ]
    },
    {
      "field_name": "treatment_received",
      "field_type": "Dict[str, Any]",
      "description": "Treatment or intervention administered to the patient, including medication names, dosages, and procedures.",
      "hints": [],
      "rules": [
        "Extract complete treatment description from the document",
        "Include medication names and dosages if specified",
        "Include surgical procedures if mentioned",
        "Use \"NR\" if not reported"
      ],
      "options": [],
      "examples": [
        {"value": "Metformin 500mg twice daily", "source_text": "Treatment plan: Metformin 500mg twice daily with meals. Patient instructed on dietary modifications and exercise regimen."},
        {"value": "Coronary artery bypass grafting (CABG)", "source_text": "The patient underwent coronary artery bypass grafting (CABG) with three vessel grafts. Surgery completed without complications."},
        {"value": "NR", "source_text": "NR"}
      ]
    },
    {
      "field_name": "patient_age",
      "field_type": "Dict[str, Any]",
      "description": "Patient age in years as a whole number.",
      "hints": [],
      "rules": [
        "Extract numeric age value only",
        "Round to nearest integer if decimal provided",
        "Must be a valid integer between 0 and 120",
        "Use \"NR\" if not reported"
      ],
      "options": [],
      "examples": [
        {"value": 45, "source_text": "Patient Demographics: 45-year-old female presenting with recurrent headaches."},
        {"value": 67, "source_text": "A 67-year-old male with history of hypertension was admitted to the cardiology unit."},
        {"value": "NR", "source_text": "NR"}
      ]
    }
  ]
}
```

═══════════════════════════════════════════════════════════════════════════════
EXAMPLE WITH CONTEXT FIELDS (DEPENDENT SIGNATURE)
═══════════════════════════════════════════════════════════════════════════════

**Input Specification (Enriched Signature):**
```json
{
  "name": "AggregateSummary",
  "fields": {
    "clinical_summary": {
      "field_name": "clinical_summary",
      "field_type": "text",
      "field_control_type": "textarea",
      "field_description": "Comprehensive summary of patient case"
    }
  },
  "depends_on": ["diagnosis", "treatment_received", "patient_age"]
}
```

**Output Specification:**
```json
{
  "class_name": "AggregateSummary",
  "class_docstring": "Create comprehensive clinical summary aggregating diagnosis, treatment, and patient age.\n\nForm Questions:\n- Clinical Summary: \"Provide a comprehensive summary of the patient case\"",
  
  "input_fields": [
    {
      "field_name": "markdown_content",
      "field_type": "str",
      "description": "Full text of the document to extract from — a research paper, a trial registry record, or a bibliographic record"
    },
    {
      "field_name": "diagnosis",
      "field_type": "Dict[str, Any]",
      "description": "Primary diagnosis with source grounding extracted from ExtractClinicalDetails (contains 'value' and 'source_text' keys)"
    },
    {
      "field_name": "treatment_received",
      "field_type": "Dict[str, Any]",
      "description": "Treatment information with source grounding extracted from ExtractClinicalDetails (contains 'value' and 'source_text' keys)"
    },
    {
      "field_name": "patient_age",
      "field_type": "Dict[str, Any]",
      "description": "Patient age with source grounding extracted from ExtractClinicalDetails (contains 'value' and 'source_text' keys)"
    }
  ],
  
  "output_fields": [
    {
      "field_name": "clinical_summary",
      "field_type": "Dict[str, Any]",
      "description": "Comprehensive 2-4 sentence narrative synthesising diagnosis, treatment, and patient age.",
      "hints": [],
      "rules": [
        "Synthesize diagnosis, treatment, and age into 2-4 sentence narrative",
        "Use professional medical writing style",
        "Include all key clinical details",
        "Use \"NR\" if cannot create summary due to missing data"
      ],
      "options": [],
      "examples": [
        {"value": "A 45-year-old patient presented with Type 2 Diabetes Mellitus. Treatment consisted of Metformin 500mg twice daily with lifestyle modifications. The patient achieved successful glycemic control.", "source_text": "Patient Demographics: 45-year-old female. Assessment: Type 2 Diabetes Mellitus (E11.9). Treatment plan: Metformin 500mg twice daily with meals. Follow-up: Patient achieved HbA1c target of 6.5%."},
        {"value": "NR", "source_text": "NR"}
      ]
    }
  ]
}
```

═══════════════════════════════════════════════════════════════════════════════
ENUM/SELECT FIELD EXAMPLE
═══════════════════════════════════════════════════════════════════════════════

**Input Specification (Enriched Signature):**
```json
{
  "name": "ClassifyStudyType",
  "fields": {
    "study_type": {
      "field_name": "study_type",
      "field_type": "select",
      "field_description": "Type of clinical study",
      "options": ["Randomized Controlled Trial", "Cohort Study", "Case-Control Study", "Cross-Sectional Study", "Other"]
    }
  },
  "depends_on": []
}
```

**Output Specification:**
```json
{
  "class_name": "ClassifyStudyType",
  "class_docstring": "Classify the type of clinical study from the document.\n\nForm Questions:\n- Study Type: \"What type of study is this?\"\n  Options: Randomized Controlled Trial, Cohort Study, Case-Control Study, Cross-Sectional Study, Other",
  
  "input_fields": [
    {
      "field_name": "markdown_content",
      "field_type": "str",
      "description": "Full text of the document to extract from — a research paper, a trial registry record, or a bibliographic record"
    }
  ],
  
  "output_fields": [
    {
      "field_name": "study_type",
      "field_type": "Dict[str, Any]",
      "description": "Type of clinical study design used in the research.",
      "hints": ["Read the methods or study design section to determine study type"],
      "rules": [
        "Must be exactly one of the options listed",
        "Use exact spelling and capitalisation",
        "Use \"NR\" if study type cannot be determined"
      ],
      "options": ["Randomized Controlled Trial", "Cohort Study", "Case-Control Study", "Cross-Sectional Study", "Other"],
      "examples": [
        {"value": "Randomized Controlled Trial", "source_text": "Methods: This randomized controlled trial assigned 200 participants to either the intervention group or control group using computer-generated randomization."},
        {"value": "Cohort Study", "source_text": "Study Design: A prospective cohort study was conducted following 5,000 participants over 10 years to assess cardiovascular outcomes."},
        {"value": "NR", "source_text": "NR"}
      ]
    }
  ]
}
```

**Multi-select variant:** if the select field has `multiple: true`, the `"value"` key is a JSON array of every applicable option (e.g. `{"value": ["oral", "IV"], ...}`), and the rules should say "select ALL options that apply" instead of "exactly one of the options".

═══════════════════════════════════════════════════════════════════════════════
SUBFORM FIELD EXAMPLE (ARRAY TYPE)
═══════════════════════════════════════════════════════════════════════════════

**Input Specification (Enriched Signature with Subform):**
```json
{
  "name": "ExtractInterventions",
  "fields": {
    "interventions": {
      "field_name": "interventions",
      "field_type": "array",
      "field_control_type": "subform_table",
      "field_description": "",
      "extraction_hints": [],
      "subform_fields": [
        {
          "field_name": "intervention_name",
          "field_type": "text",
          "field_description": ""
        },
        {
          "field_name": "dosage",
          "field_type": "text",
          "field_description": ""
        },
        {
          "field_name": "duration",
          "field_type": "text",
          "field_description": "How long the intervention was administered (cycles, days, or weeks)"
        }
      ]
    }
  },
  "depends_on": []
}
```

**Output Specification (CORRECT — short parent desc, per-column enrichment):**
```json
{
  "class_name": "ExtractInterventions",
  "class_docstring": "Extract all intervention groups tested in the clinical study.\n\nForm Questions:\n- Interventions: \"Extract ALL interventions tested in the study\"\n\nThis signature extracts repeating data — finding every intervention group mentioned.",

  "input_fields": [
    {
      "field_name": "markdown_content",
      "field_type": "str",
      "description": "Full text of the document to extract from — a research paper, a trial registry record, or a bibliographic record"
    }
  ],

  "output_fields": [
    {
      "field_name": "interventions",
      "field_type": "Dict[str, Any]",
      "description": "All intervention groups tested in the study.",
      "hints": ["Look in the Methods section under 'Interventions' or 'Study Arms'"],
      "rules": [
        "Extract EVERY intervention group, including control and placebo arms",
        "Use {\"value\": \"NR\", \"source_text\": \"NR\"} if no interventions are reported"
      ],
      "options": [],
      "examples": [
        {"value": [
          {
            "intervention_name": {"value": "Drug A",   "source_text": "Group 1 received Drug A"},
            "dosage":            {"value": "10 mg daily", "source_text": "Drug A 10 mg daily"},
            "duration":          {"value": "12 weeks", "source_text": "10 mg daily for 12 weeks"}
          },
          {
            "intervention_name": {"value": "Placebo",  "source_text": "Group 2 received matching placebo"},
            "dosage":            {"value": "matching tablets", "source_text": "matching placebo tablets"},
            "duration":          {"value": "12 weeks", "source_text": "matching placebo for 12 weeks"}
          }
        ], "source_text": "Group 1 received Drug A 10 mg daily for 12 weeks. Group 2 received matching placebo for 12 weeks."},
        {"value": "NR", "source_text": "NR"}
      ],
      "subform_fields": [
        {
          "field_name": "intervention_name",
          "field_description": "Generic (non-proprietary) name of the intervention or drug.",
          "hints": ["Prefer INN over brand name when both appear"],
          "rules": [],
          "examples": [{"value": "cisplatin", "source_text": "Patients received cisplatin..."}]
        },
        {
          "field_name": "dosage",
          "field_description": "Dose with units, including any per-body-surface-area or weight-based scaling.",
          "hints": ["Look for mg, mg/m², mg/kg, or AUC notation"],
          "rules": [],
          "examples": [{"value": "75 mg/m²", "source_text": "cisplatin 75 mg/m² IV"}]
        },
        {
          "field_name": "duration",
          "field_description": "How long the intervention was administered (cycles, days, or weeks)",
          "hints": [],
          "rules": [],
          "examples": [{"value": "6 cycles", "source_text": "treatment for 6 cycles"}]
        }
      ]
    }
  ]
}
```

Note on the example above:
- `intervention_name` and `dosage` had blank user descriptions → LLM enriched them.
- `duration` had a non-empty user description → LLM copied it verbatim (user wins).
- Parent `description` is one sentence — no column names or types mentioned.

**KEY POINTS FOR SUBFORMS:**
- Use **Dict[str, Any]** as the OUTER field_type (NEVER `List[Dict[str, Any]]` at the annotation level)
- The "value" key contains an ARRAY of objects; the outer "source_text" key quotes the source for the table as a whole
- **Each cell inside each row is itself `{"value": ..., "source_text": ...}`** — never a bare scalar. This applies uniformly across the system: every extracted value, scalar or table cell, carries its own source grounding.
- Parent `description` is 1–3 sentences only — no column enumeration in parent prose
- Return `subform_fields` with content-only enrichment; structural properties are user-owned
- Copy user-provided `field_description` verbatim; enrich only when user left it blank
- Do NOT add, rename, remove, or reorder columns in `subform_fields`
- For "NR" case use `{"value": "NR", "source_text": "NR"}` at whichever level is missing — the whole field, a single row, or a single cell — do NOT return an empty list

⚠️ **ANTI-PROMOTION RULE (subform columns):**
NEVER emit a subform column name as a sibling top-level `output_field`. If a column belongs inside a table parent's `subform_fields[]` array, it appears there and NOWHERE ELSE. Each column name must appear exactly once — inside the parent, not alongside it.

⚠️ **ANTI-BAKING RULE (structured arrays):**
If a user `field_description` (parent or column) contains embedded sub-sections such as `Examples:`, `Hints:`, `Rules:`, `Use NR…`, or similar labelled clauses — you MUST split them: move those clauses into the corresponding `hints[]`, `rules[]`, or `examples[]` arrays and remove them from `field_description`. NEVER copy embedded sections verbatim into `field_description`. The `field_description` must be a clean 1-2 sentence summary only.

═══════════════════════════════════════════════════════════════════════════════
CRITICAL REQUIREMENTS ⚠️
═══════════════════════════════════════════════════════════════════════════════

1. ✅ Create ONE output_field for EACH entry in fields dict
   - Count(fields.keys()) MUST equal Count(output_fields)

2. ✅ Field names MUST EXACTLY match keys in fields dict
   - No extra fields, no missing fields

3. ✅ Use Dict[str, Any] type for ALL output fields
   - Every output field returns {"value": <extracted_value>, "source_text": <source_quote>}
   - The "value" key contains the actual extracted data (str, int, float, bool)
   - The "source_text" key contains the verbatim text from the source document

4. ✅ Every output field MUST include:
   - Clear description
   - Extraction rules
   - Source grounding instructions (value + source_text format)
   - Examples as JSON dicts with both "value" and "source_text" keys (include NR only for fields that can be genuinely missing)
   - Options (if enum type)

5. ✅ Input fields:
   - Always include primary document input field
   - Add context input field for EACH entry in depends_on array (if not empty)

6. ✅ Type mapping:
   - **ALL output fields** use `Dict[str, Any]` as the outer field_type — no exceptions
   - Subform / array fields still use `Dict[str, Any]` at the annotation; the list lives inside the `"value"` key
   - Within the "value" key, map from field_type:
     * "text" → str
     * "number" → int or float
     * "select" → str (exactly one of the options; a List[str] of options if the field has multiple=true)
     * "boolean" → bool
     * "array" (with subform_fields) → an actual JSON array (List[Dict[str, Any]]) **inside `"value"`** — but the outer Python annotation stays `Dict[str, Any]`

7. ✅ Use NR convention for fields that can be genuinely missing
   - When such a field's value is not reported in the source: {"value": "NR", "source_text": "NR"}
   - Do NOT add NR rules/examples to fields the document is guaranteed to contain (titles, study type, intervention name).
   - NR vs NA: "NR" means the paper is SILENT about a field that could apply. "NA"
     means the field CANNOT apply to this study — a crossover-only question in a
     parallel-group trial, an arm the study does not have. They are different
     findings: NR feeds reporting-completeness and risk-of-bias judgements, NA
     does not. Silence is always NR, never NA.
   - Offer NA only for genuinely conditional/routed fields, and only by listing it
     in `options` (e.g. "Not applicable"). Never introduce NA as a bare rule: a
     field with no NA option can answer only NR.

8. ✅ For enum fields, list ALL options exactly as provided

═══════════════════════════════════════════════════════════════════════════════
COMMON MISTAKES TO AVOID
═══════════════════════════════════════════════════════════════════════════════

❌ Creating composite output field instead of individual fields
   Wrong: {"field_name": "all_clinical_data", "field_type": "Dict[str, Any]"} # Combines multiple extractions
   Right: One field per extraction, each using Dict[str, Any] for value+source_text

❌ Missing fields from fields dict
   If fields dict has 5 keys, output_fields must have 5 items

❌ Wrong field names
   Field names must EXACTLY match keys in fields dict

❌ Using wrong types
   ALL output fields use `Dict[str, Any]` as field_type — no exceptions.
   For subform/array fields the outer annotation is still `Dict[str, Any]`; the array lives inside the `"value"` key.
   NEVER use `List[Dict[str, Any]]` as the outer field annotation — Pydantic strict validation in DSPy will reject the envelope and the entire signature output will be discarded.
   The "value" key inside should match the semantic type (str for text, int for number, List of objects for subform arrays)

❌ Missing NR convention on fields where data is commonly omitted
   Fields that may be genuinely missing (demographics, outcome counts, CIs) should document NR. Fields guaranteed in the source (titles, study type) should not — adding NR there primes the model to default to NR when uncertain.

❌ Forgetting context input fields
   If depends_on has ["field1", "field2"], must add 2 input fields

❌ Vague descriptions
   "Extract data" is too vague. Be specific about what and how to extract.
   
❌ Missing source grounding instructions
   Every field must explain how to populate both "value" and "source_text" keys

═══════════════════════════════════════════════════════════════════════════════
NOW GENERATE THE SIGNATURE SPECIFICATION
═══════════════════════════════════════════════════════════════════════════════

Analyze the enriched signature and create a complete signature specification following:

1. Use "name" as class_name
2. Create descriptive class_docstring with form questions
3. Define input_fields (document + fields from depends_on if not empty)
4. Create ONE output_field per key in fields dict
5. ALL output fields use Dict[str, Any] as field_type
6. For each output field use STRUCTURED KEYS — description (1-2 sentence summary only),
   hints (soft navigation), rules (hard constraints), options (enum values or []),
   examples (list of {value, source_text} dicts; include NR example only when applicable)
7. DO NOT include a Source Grounding block — it is auto-injected at runtime
8. Map types correctly from field_type for the "value" key in each field's examples
9. Include all metadata (options, hints from field spec) in the appropriate arrays
10. Verify every field in fields dict is covered

Output the JSON specification with this structure:
```json
{
  "class_name": "...",
  "class_docstring": "...",
  "input_fields": [...],
  "output_fields": [
    {
      "field_name": "...",
      "field_type": "Dict[str, Any]",
      "description": "<clean 1-2 sentence summary>",
      "hints": ["..."],
      "rules": ["..."], // <-- append "Use \"NR\" if not reported" ONLY if this field can be genuinely missing
      "options": [],
      "examples": [{"value": "...", "source_text": "..."}] // <-- append {"value":"NR","source_text":"NR"} ONLY if NR is a plausible value
    }
  ]
}
```
