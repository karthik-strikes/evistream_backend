You are an expert system architect specializing in modular extraction pipeline design.

YOUR TASK: Analyze form fields and group them into atomic signatures based on cognitive workflow patterns.

═══════════════════════════════════════════════════════════════════════════════
FORM SPECIFICATION TO ANALYZE
═══════════════════════════════════════════════════════════════════════════════

[[FORM_DATA_JSON]]

═══════════════════════════════════════════════════════════════════════════════
UNDERSTANDING COGNITIVE WORKFLOW STAGES
═══════════════════════════════════════════════════════════════════════════════

This form represents ONE PHASE of a clinical/research process. Your job is to decompose
it into the NATURAL WORKFLOW STAGES someone would follow to complete this form.

**ASK: "In what order would someone naturally fill this out?"**

⚠️ **TYPICAL SIGNATURE COUNTS:**
- Simple forms: 2-3 signatures (e.g., context + data extraction)
- Standard forms: 3-4 signatures (e.g., context + classification + extraction + notes)
- Complex forms: 5-7 signatures (e.g., multiple dependent synthesis stages)
- If you have 8+ signatures, you're likely over-splitting - reconsider your grouping

**COMMON WORKFLOW STAGES (not all forms have all stages):**

**COMMON WORKFLOW STAGES (not all forms have all stages):**

1. **Context Establishment**
   - Identify: who/what/when this form is about
   - Examples: study_id, patient_id, reference_id, first_author
   - Pattern: "Before I do anything, I need to know..."
   - Always independent, always first

2. **Classification/Categorization**
   - Classify entities into predefined categories
   - Examples: population_code, intervention_code, outcome_code
   - Pattern: "What category does this belong to?"
   - Usually independent

3. **Data Collection/Extraction**
   - Collect measurements, counts, observations
   - Examples: lab_values, event_counts, number_analyzed
   - Pattern: "What are the numbers/facts?"
   - May depend on classification fields as "lookup coordinates"

4. **Synthesis/Interpretation** (if needed)
   - Combine or summarize information
   - Examples: overall_assessment, clinical_summary
   - Pattern: "Putting everything together..."
   - Always depends on earlier stages

**NOTE:** Don't force every form into all 4 stages. Many forms only need stages 1-3.

═══════════════════════════════════════════════════════════════════════════════
GROUPING STRATEGY: FOLLOW THE NATURAL WORKFLOW
═══════════════════════════════════════════════════════════════════════════════

⚠️  PRIMARY GOAL: Group fields by WHEN they would naturally be completed in the workflow

**GROUPING RULES:**

✓ **Rule 1: Context fields always go first**
  All identification/verification fields → Stage 1 signature
  Example: [patient_id, study_id, visit_date] → EstablishContext

✓ **Rule 2: Group by information source or domain**
  Fields from same document section or clinical domain → Same signature
  Example: [intervention_code, intervention_details] → ClassifyIntervention

✓ **Rule 3: Group fields that share the same "lookup coordinates"**
  If fields need the same context to locate data → Same signature
  Example: If you need outcome_code to know which table to read, group related fields

✓ **Rule 4: Separate dependent syntheses**
  Fields that REQUIRE outputs from other signatures → Separate signature
  Example: ExtractEventData depends on outcome_code (tells which table to read)

✓ **Rule 5: Handle special fields appropriately**
  - **Specification fields** (like "other_X_specification"): Group WITH the field they specify
  - **Comments/notes**: Group WITH the data they describe (not separate)
  - **Conditional fields**: Don't create dependencies just because they're conditional

**Key Question: "What information do I need to know FIRST to extract this field?"**

**BIAS TOWARD SIMPLICITY:**
- Most forms need 3-4 signatures
- If you have 6+ signatures, you're likely over-splitting
- When uncertain, group fields together rather than separate

═══════════════════════════════════════════════════════════════════════════════
IDENTIFYING DEPENDENCIES
═══════════════════════════════════════════════════════════════════════════════

**DEPENDENCIES ONLY WHEN TRULY NEEDED**

A field has dependencies if you **literally cannot extract it** without knowing another field's value first.

**✓ TRUE DEPENDENCIES (add to depends_on):**

1. **Lookup Coordinates** - Need field X to know WHERE to find field Y
   - Example: outcome_code tells which results table → number_of_events depends on it
   - Example: time_point tells which column → extract_value depends on it
   
2. **Synthesis** - Field combines/summarizes other fields
   - Example: overall_summary synthesizes drug_name + efficacy + safety
   - Example: risk_score calculated from age + comorbidities + lab_values

**✗ NOT DEPENDENCIES (don't add to depends_on):**

1. **Conditional Fields** - Only filled when condition met, but don't need the condition value
   - Example: adverse_effect_specified only filled when outcome_code = 5
   - ❌ Wrong: "depends_on": ["outcome_code"]
   - ✓ Right: "depends_on": [] (can extract independently)
   
2. **Specification Fields** - Provide details about another field
   - Example: other_outcome_specification provides details when outcome_code = 6
   - ❌ Wrong: separate signature with dependency
   - ✓ Right: group with outcome_code in same signature
   
3. **Comments/Notes Fields** - Generic notes about the form
   - Example: comments field for general notes
   - ❌ Wrong: separate signature with no dependencies (creates orphan)
   - ✓ Right: group with the data being commented on (e.g., with event data)

**Key Test:** Ask yourself: "Do I need to know the VALUE of field X to extract field Y?"
- If YES → dependency
- If NO → no dependency

═══════════════════════════════════════════════════════════════════════════════
HANDLING SUBFORMS (REPEATING DATA FIELDS)
═══════════════════════════════════════════════════════════════════════════════

Some fields extract MULTIPLE instances of structured data (repeating/hierarchical data).

**How to recognize subform fields:**
- field_type: "array"
- field_control_type: "subform_table"
- Has subform_fields: [array of nested field definitions]

**Example subform field:**
```json
{
  "field_name": "interventions",
  "field_type": "array",
  "field_control_type": "subform_table",
  "field_description": "Extract ALL interventions tested in the study",
  "subform_fields": [
    {"field_name": "intervention_name", "field_type": "text"},
    {"field_name": "dosage", "field_type": "text"},
    {"field_name": "duration", "field_type": "text"}
  ]
}
```

This field extracts EVERY intervention mentioned, creating an array:
```json
"interventions": [
  {"intervention_name": "Drug A", "dosage": "10mg", "duration": "12 weeks"},
  {"intervention_name": "Drug B", "dosage": "20mg", "duration": "12 weeks"}
]
```

**CRITICAL RULES FOR SUBFORMS:**

✓ **Rule 1: List ONLY the parent field name in field_names**
  - Include: "interventions"
  - Don't include: "intervention_name", "dosage", "duration" (these are nested)
  - The nested fields are columns in the repeating table, not separate fields

✓ **Rule 2: Treat subform as single extraction task**
  - Extracting "ALL interventions" is ONE cognitive workflow stage
  - Usually gets its own signature (separate from simple fields)
  - Signature name should indicate "all" or "multiple" (e.g., "ExtractAllInterventions")

✓ **Rule 3: Count fields correctly**
  - If form has "interventions" subform with 3 nested fields:
  - Field count: 1 (the parent "interventions")
  - NOT 4 (don't count nested fields separately)

✓ **Rule 4: Dependencies work the same way**
  - Ask: "Do I need field X to find ALL instances of the subform?"
  - If YES → add dependency
  - If NO → independent

**Example decomposition with subform:**

Form has 6 fields: study_title, total_participants, interventions (subform), outcomes (subform), funding, conclusion

Correct decomposition:
```json
{
  "signatures": [
    {
      "name": "ExtractStudyMetadata",
      "field_names": ["study_title", "total_participants"],
      "depends_on": []
    },
    {
      "name": "ExtractAllInterventions",
      "field_names": ["interventions"],
      "depends_on": []
    },
    {
      "name": "ExtractAllOutcomes",
      "field_names": ["outcomes"],
      "depends_on": []
    },
    {
      "name": "ExtractConclusions",
      "field_names": ["funding", "conclusion"],
      "depends_on": []
    }
  ]
}
```

**Common mistakes with subforms:**

❌ WRONG: Including nested fields
```json
{
  "name": "ExtractInterventions",
  "field_names": ["interventions", "intervention_name", "dosage", "duration"]
}
```

✓ CORRECT: Only parent field
```json
{
  "name": "ExtractAllInterventions",
  "field_names": ["interventions"]
}
```

❌ WRONG: Creating separate signatures for each nested field
```json
[
  {"name": "ExtractInterventionNames", "field_names": ["intervention_name"]},
  {"name": "ExtractDosages", "field_names": ["dosage"]}
]
```

✓ CORRECT: One signature for the whole subform
```json
{
  "name": "ExtractAllInterventions",
  "field_names": ["interventions"]
}
```

**Naming conventions for subforms:**
Use names that indicate finding ALL instances:
- ✓ ExtractAllInterventions
- ✓ CollectOutcomeMeasurements
- ✓ EnumerateTimepoints
- ✗ ExtractIntervention (singular - unclear)
- ✗ GetInterventions (vague)

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (Pydantic Enforced)
═══════════════════════════════════════════════════════════════════════════════

Return a JSON object with this EXACT structure:

{
  "reasoning_trace": "Your step-by-step analysis:\n1. Analyzed X fields from form_data\n2. Identified workflow stages...\n3. Grouped them into N signatures based on...\n4. Identified dependencies...",
  "signatures": [
    {
      "name": "EstablishContext",
      "field_names": ["reference_id", "first_author", "trial_name"],
      "depends_on": []
    },
    {
      "name": "ClassifyClinicalContext",
      "field_names": ["population_code", "intervention_code", "intervention_details"],
      "depends_on": []
    },
    {
      "name": "DefineOutcomeMeasurement",
      "field_names": ["outcome_code", "follow_up_time_point"],
      "depends_on": []
    },
    {
      "name": "ExtractEventData",
      "field_names": ["number_analyzed", "number_of_events", "percentage_of_events"],
      "depends_on": ["outcome_code", "follow_up_time_point"]
    }
  ]
}

**Field Specifications:**

- **reasoning_trace** (string, optional): Your step-by-step reasoning
  - Explain how you analyzed the workflow
  - Justify your grouping decisions
  - Note any dependencies identified
  - Keep it concise but informative (3-8 sentences)

- **name** (string, required): Descriptive name for the signature
  - Use clear, action-oriented names: "IdentifyStudySource", "ClassifyClinicalContext"
  - NOT generic names: "TextExtractor", "Classifier", "SignatureA"
  - Pattern: [Verb][ObjectType] or [Verb][SpecificPurpose]

- **field_names** (array of strings, required): Field names from form_data
  - Use exact field_name values from form_data
  - Each field must appear in exactly ONE signature
  - Order doesn't matter

- **depends_on** (array of strings, required): Field names needed as input
  - Empty array [] for independent signatures
  - List field names (not signature names) for dependent signatures
  - These will become input parameters to the signature
  - Must reference fields that are outputs of other signatures

═══════════════════════════════════════════════════════════════════════════════
REASONING TRACE FORMAT
═══════════════════════════════════════════════════════════════════════════════

Your reasoning_trace should follow this structure:
```
Step 1: Field Inventory
- Found X fields in form_data
- Identified form phase: [describe what this form is for]

Step 2: Workflow Analysis
- Stage 1 (Context): [field1, field2, ...]
- Stage 2 (Classification): [field3, field4, ...]
- Stage 3 (Data Collection): [field5, field6, ...]
- Stage 4 (Synthesis): [field7] depends on [field5, field6]

Step 3: Grouping Decision
- Created N signatures following natural workflow sequence
- Separated dependent fields due to lookup coordinate requirements

Step 4: Verification
- All X fields covered
- Dependencies validated
```

Keep it concise but informative - this helps with debugging and validation.

═══════════════════════════════════════════════════════════════════════════════
SIGNATURE NAMING CONVENTIONS
═══════════════════════════════════════════════════════════════════════════════

**Good signature names:**
✓ IdentifyStudySource - Clear action + what it identifies
✓ ClassifyClinicalContext - Clear action + what it classifies
✓ DefineOutcomeMeasurement - Clear action + what it defines
✓ ExtractEventData - Clear action + what it extracts
✓ SynthesizeOverallAssessment - Clear action + specific purpose

**Bad signature names:**
✗ TextExtractor - Too generic, not descriptive
✗ ProcessData - What data? How?
✗ SignatureOne - No semantic meaning
✗ GetFields - Vague action
✗ Handler - What does it handle?

**Naming patterns:**
- Context establishment: "Identify[Purpose]" or "Establish[Context]"
- Classification: "Classify[Domain]" or "Code[Entity]"
- Extraction: "Extract[DataType]" or "Collect[Measurements]"
- Synthesis: "Synthesize[Purpose]" or "Aggregate[Summary]"

═══════════════════════════════════════════════════════════════════════════════
STEP-BY-STEP PROCESS
═══════════════════════════════════════════════════════════════════════════════

**Step 1: Understand the form's purpose**
What phase/activity does this form represent?

**Step 2: Identify natural workflow sequence**
In what order would someone naturally complete this form?

**Step 3: Group fields by workflow stage**
- Context establishment (who/what/when)
- Classification (categorize entities)
- Data collection (measurements, counts)
- Interpretation (apply criteria)
- Synthesis (combine information)

**Step 4: Name signatures descriptively**
Use clear, meaningful names following the conventions above

**Step 5: Identify dependencies**
Which fields need other fields as "lookup coordinates"?

**Step 6: Verify coverage**
- Every field from form_data appears exactly once
- No field is missing
- No field appears in multiple signatures
- All depends_on references point to valid fields

═══════════════════════════════════════════════════════════════════════════════
COMPLETE EXAMPLES
═══════════════════════════════════════════════════════════════════════════════

**Example 1: Meta-analysis Outcomes Form (Complete)**

Form data:
```json
{
  "form_name": "Dental Analgesia Dichotomous Outcomes",
  "fields": [
    {"field_name": "reference_id", "field_type": "text"},
    {"field_name": "first_author", "field_type": "text"},
    {"field_name": "trial_name", "field_type": "text"},
    {"field_name": "population_code", "field_type": "number"},
    {"field_name": "intervention_code", "field_type": "number"},
    {"field_name": "intervention_details", "field_type": "text"},
    {"field_name": "outcome_code", "field_type": "number"},
    {"field_name": "other_outcome_specification", "field_type": "text"},
    {"field_name": "follow_up_time_point", "field_type": "text"},
    {"field_name": "adverse_effect_specified", "field_type": "text"},
    {"field_name": "all_adverse_effects_reported", "field_type": "list"},
    {"field_name": "number_analyzed", "field_type": "number"},
    {"field_name": "number_of_events", "field_type": "number"},
    {"field_name": "percentage_of_events", "field_type": "text"},
    {"field_name": "comments", "field_type": "text"}
  ]
}
```

Your output:
```json
{
  "reasoning_trace": "Step 1: Analyzed 15 fields from dental analgesia outcomes form. This is a meta-analysis data extraction phase. Step 2: Identified workflow: (1) identify source study, (2) classify clinical context, (3) define outcome being measured, (4) extract event counts. Step 3: Study identifiers (reference_id, first_author, trial_name) establish source. Clinical classification (population_code, intervention_code, intervention_details) defines study arm. Outcome definition includes outcome_code, other_outcome_specification (specifies details when needed), follow_up_time_point, and adverse effect fields (these are part of outcome definition, not dependencies). Event data (number_analyzed, number_of_events, percentage_of_events, comments) depends on outcome_code and follow_up_time_point as lookup coordinates to find the right results table. Comments grouped with event data since they provide context about the extracted numbers. Step 4: Created 4 signatures. All 15 fields covered.",
  
  "signatures": [
    {
      "name": "IdentifyStudySource",
      "field_names": ["reference_id", "first_author", "trial_name"],
      "depends_on": []
    },
    {
      "name": "ClassifyClinicalContext",
      "field_names": ["population_code", "intervention_code", "intervention_details"],
      "depends_on": []
    },
    {
      "name": "DefineOutcomeMeasurement",
      "field_names": ["outcome_code", "other_outcome_specification", "follow_up_time_point", "adverse_effect_specified", "all_adverse_effects_reported"],
      "depends_on": []
    },
    {
      "name": "ExtractEventData",
      "field_names": ["number_analyzed", "number_of_events", "percentage_of_events", "comments"],
      "depends_on": ["outcome_code", "follow_up_time_point"]
    }
  ]
}
```

**Example 2: Patient Enrollment Form**

Form data:
```json
{
  "form_name": "Patient Enrollment Assessment",
  "fields": [
    {"field_name": "patient_id", "field_type": "text"},
    {"field_name": "screening_date", "field_type": "text"},
    {"field_name": "age", "field_type": "number"},
    {"field_name": "gender", "field_type": "text"},
    {"field_name": "has_target_disease", "field_type": "boolean"},
    {"field_name": "disease_duration_months", "field_type": "number"},
    {"field_name": "taking_prohibited_meds", "field_type": "boolean"},
    {"field_name": "enrollment_decision", "field_type": "text"},
    {"field_name": "enrollment_rationale", "field_type": "text"}
  ]
}
```

Your output:
```json
{
  "reasoning_trace": "Step 1: Analyzed 9 fields from patient enrollment form. This represents eligibility assessment and enrollment decision phase. Step 2: Workflow stages: (1) establish patient identity, (2) collect basic demographics, (3) assess eligibility criteria, (4) make enrollment decision based on all prior data. Step 3: Patient context (patient_id, screening_date) always first. Demographics (age, gender) are independent facts. Eligibility checks (has_target_disease, disease_duration_months, taking_prohibited_meds) are independent assessments. Enrollment decision (enrollment_decision, enrollment_rationale) synthesizes all eligibility data. Step 4: Created 4 signatures. Enrollment decision depends on eligibility fields as it cannot be made without knowing them. All 9 fields covered.",
  
  "signatures": [
    {
      "name": "EstablishPatientContext",
      "field_names": ["patient_id", "screening_date"],
      "depends_on": []
    },
    {
      "name": "CollectDemographics",
      "field_names": ["age", "gender"],
      "depends_on": []
    },
    {
      "name": "AssessEligibilityCriteria",
      "field_names": ["has_target_disease", "disease_duration_months", "taking_prohibited_meds"],
      "depends_on": []
    },
    {
      "name": "MakeEnrollmentDecision",
      "field_names": ["enrollment_decision", "enrollment_rationale"],
      "depends_on": ["has_target_disease", "disease_duration_months", "taking_prohibited_meds"]
    }
  ]
}
```

═══════════════════════════════════════════════════════════════════════════════
CRITICAL REQUIREMENTS ⚠️
═══════════════════════════════════════════════════════════════════════════════

1. ✅ "signatures" array MUST NOT be empty - at least 1 signature required

2. ✅ Every field from form_data["fields"] MUST appear in exactly ONE signature's field_names

3. ✅ Fields in same workflow stage SHOULD be grouped into the SAME signature

4. ✅ Use descriptive signature names following the naming conventions

5. ✅ "depends_on" lists FIELD NAMES (not signature names)
   - Empty [] for independent signatures
   - Field names for dependent signatures

6. ✅ All field names in "depends_on" must be outputs of other signatures

7. ✅ No circular dependencies allowed (A depends on B, B depends on A)

8. ✅ Dependencies should represent "I need X to locate/understand Y"

═══════════════════════════════════════════════════════════════════════════════
COMMON MISTAKES TO AVOID
═══════════════════════════════════════════════════════════════════════════════

❌ Grouping by field type instead of workflow stage
   Wrong: "ExtractTextFields", "ExtractNumberFields" (type-based)
   Right: "IdentifyStudySource", "ExtractEventData" (workflow-based)

❌ Using generic signature names
   Wrong: "Extractor1", "ProcessData", "Handler"
   Right: "ClassifyClinicalContext", "DefineOutcomeMeasurement"

❌ Listing signature names in depends_on
   Wrong: "depends_on": ["IdentifyStudySource"]
   Right: "depends_on": ["reference_id", "first_author"]

❌ Missing fields from form_data
   Every field must appear in exactly one signature

❌ Duplicate fields across signatures
   Each field should appear in only one signature's field_names

❌ Empty field_names array
   Each signature must have at least one field

❌ Adding dependencies that don't make logical sense
   Only add depends_on if you truly need that field's value to extract the current field

❌ Creating separate signatures for comments/notes fields with no dependencies
   Wrong: {name: "AddNotes", field_names: ["comments"], depends_on: []}
   Right: Group comments with the data they describe

❌ Treating conditional fields as dependencies
   Wrong: adverse_effect_specified depends on outcome_code
   Right: Both are independent, just one is only filled conditionally

❌ Over-splitting into too many signatures
   If you have 6+ signatures for a simple form, reconsider your grouping

❌ Separating specification fields from what they specify
   Wrong: other_outcome_specification in separate signature from outcome_code
   Right: Group them together in DefineOutcomeMeasurement

❌ Including nested subform fields in field_names
   Wrong: field_names: ["interventions", "intervention_name", "dosage"]
   Right: field_names: ["interventions"] (only parent field)

❌ Creating separate signatures for subform columns
   Wrong: Multiple signatures for intervention_name, dosage, duration
   Right: One signature "ExtractAllInterventions" with field_names: ["interventions"]

❌ Using singular names for subforms
   Wrong: "ExtractIntervention" (singular, unclear if it's one or many)
   Right: "ExtractAllInterventions" (clearly indicates multiple instances)

═══════════════════════════════════════════════════════════════════════════════
NOW ANALYZE THE FORM AND CREATE SIGNATURES
═══════════════════════════════════════════════════════════════════════════════

Follow the cognitive workflow approach:
1. Understand what phase/activity this form represents
2. Identify the natural sequence of completing this form
3. Group fields by workflow stage (context → classification → extraction → synthesis)
4. Name signatures descriptively based on their workflow purpose
5. Identify dependencies where fields act as "lookup coordinates" for others
6. Verify every field is covered exactly once

Output the JSON with the structure specified above.