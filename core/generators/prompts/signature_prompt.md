You are an expert DSPy signature designer specializing in structured data extraction.

YOUR TASK: Design a DSPy Signature specification for extracting structured data from documents.

═══════════════════════════════════════════════════════════════════════════════
INPUT SPECIFICATION (ENRICHED SIGNATURE FORMAT)
═══════════════════════════════════════════════════════════════════════════════

[[ENRICHED_SIGNATURE_JSON]]

This enriched signature contains:
- "name": The signature class name
- "fields": Dict of field_name → field metadata (type, description, options, hints, etc.)
- "depends_on": List of field names this signature depends on (empty for independent signatures)

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
- "enum" → str (with options listed)
- "boolean" → bool
- "list" → str (comma-separated or JSON string)
- "array" (with subform_fields) → List[Dict[str, Any]] (special case - see below)

**SPECIAL CASE: Subform Fields (Repeating Data)**

If field metadata contains:
- field_type: "array"
- field_control_type: "subform_table"  
- subform_fields: [list of nested field definitions]

This is a SUBFORM that extracts MULTIPLE instances of structured data.

Field type declaration: List[Dict[str, Any]]

The field extracts an array of objects, where each object has keys matching the subform_fields.

For subform fields:
1. Use List[Dict[str, Any]] as the field_type (NOT Dict[str, Any])
2. In the description, explain this extracts ALL instances found
3. List the structure of each item (what keys it contains from subform_fields)
4. Provide examples showing an array with multiple objects
5. For "NR" case, still use Dict format: {"value": "NR", "source_text": "NR"}

Example subform output:
{
  "value": [
    {"intervention_name": "Drug A", "dosage": "10mg", "duration": "12 weeks"},
    {"intervention_name": "Placebo", "dosage": "matching", "duration": "12 weeks"}
  ],
  "source_text": "Table 1: Interventions. Drug A group received 10mg daily for 12 weeks. Placebo group received matching tablets for 12 weeks."
}

Standard Field type declaration: Dict[str, Any]

**Rule 3: Every output field must have:**
- Clear description of what to extract
- Extraction rules and constraints
- Source grounding instructions
- Examples with both value and source_text (including "NR" for not reported)
- Options (if enum/select type)
- Extraction hints (if provided in spec)

═══════════════════════════════════════════════════════════════════════════════
FIELD DESCRIPTION STRUCTURE
═══════════════════════════════════════════════════════════════════════════════

Each output field description should follow this structure:

```
<ONE_LINE_SUMMARY>

[IF PROVIDED] Description: <METADATA_DESCRIPTION>

[IF HAS OPTIONS]
Options:
- "Option 1"
- "Option 2"
- "Option N"
[END IF]

[IF HAS HINTS]
Extraction Hints:
- <HINT_1>
- <HINT_2>
[END IF]

Rules:
- <EXTRACTION_RULE_1>
- <EXTRACTION_RULE_2>
[IF ENUM]
- Must be exactly one of the options listed above
- Use exact spelling and capitalization
[END IF]
- Use "NR" if not reported or value is missing

Source Grounding:
- Return a dictionary with two keys: "value" and "source_text"
- "value": The extracted value as specified by the field type
- "source_text": Copy the exact sentence(s) or paragraph from the document where this value was found
- Include enough context in source_text to verify the extraction (typically 1-3 sentences)
- If value is "NR", set source_text to "NR" as well

Examples:
{"value": <EXAMPLE_VALUE_1>, "source_text": "<EXAMPLE_SOURCE_TEXT_1>"}
{"value": <EXAMPLE_VALUE_2>, "source_text": "<EXAMPLE_SOURCE_TEXT_2>"}
{"value": "NR", "source_text": "NR"}

Note: Examples should be valid JSON dictionaries with "value" and "source_text" keys.
```

═══════════════════════════════════════════════════════════════════════════════
INPUT FIELDS
═══════════════════════════════════════════════════════════════════════════════

**Primary Input: Document Content**

Always include a primary input field for the document:
- field_name: "markdown_content" or "document"
- type: "str"
- description: "Full markdown content of the document to extract from"

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
      "description": "Full markdown content of the medical research paper"
    }
  ],
  
  "output_fields": [
    {
      "field_name": "diagnosis",
      "field_type": "Dict[str, Any]",
      "description": "Primary medical diagnosis.\n\nDescription: Primary medical diagnosis\n\nExtraction Hints:\n- Look in assessment or chief complaint sections\n\nRules:\n- Extract the main diagnosis verbatim from the document\n- Include ICD codes if mentioned (e.g., \"Type 2 Diabetes (E11.9)\")\n- Use medical terminology as written in the document\n- Use \"NR\" if not reported\n\nSource Grounding:\n- Return a dictionary with two keys: \"value\" and \"source_text\"\n- \"value\": The extracted diagnosis as a string\n- \"source_text\": Copy the exact sentence(s) or paragraph from the document where this diagnosis was found\n- Include enough context in source_text to verify the extraction (typically 1-3 sentences)\n- If value is \"NR\", set source_text to \"NR\" as well\n\nExamples:\n{\"value\": \"Type 2 Diabetes Mellitus (E11.9)\", \"source_text\": \"The patient was diagnosed with Type 2 Diabetes Mellitus (E11.9) based on fasting glucose levels of 145 mg/dL and HbA1c of 7.8%.\"}\n{\"value\": \"Acute Myocardial Infarction\", \"source_text\": \"Assessment: Acute Myocardial Infarction. Patient presented with chest pain, elevated troponin levels, and ST-segment elevation on ECG.\"}\n{\"value\": \"NR\", \"source_text\": \"NR\"}"
    },
    {
      "field_name": "treatment_received",
      "field_type": "Dict[str, Any]",
      "description": "Treatment or intervention administered to the patient.\n\nDescription: Treatment or intervention administered\n\nRules:\n- Extract complete treatment description from the document\n- Include medication names and dosages if specified\n- Include surgical procedures if mentioned\n- Use \"NR\" if not reported\n\nSource Grounding:\n- Return a dictionary with two keys: \"value\" and \"source_text\"\n- \"value\": The extracted treatment as a string\n- \"source_text\": Copy the exact sentence(s) or paragraph from the document where this treatment was found\n- Include enough context in source_text to verify the extraction (typically 1-3 sentences)\n- If value is \"NR\", set source_text to \"NR\" as well\n\nExamples:\n{\"value\": \"Metformin 500mg twice daily\", \"source_text\": \"Treatment plan: Metformin 500mg twice daily with meals. Patient instructed on dietary modifications and exercise regimen.\"}\n{\"value\": \"Coronary artery bypass grafting (CABG)\", \"source_text\": \"The patient underwent coronary artery bypass grafting (CABG) with three vessel grafts. Surgery completed without complications.\"}\n{\"value\": \"NR\", \"source_text\": \"NR\"}"
    },
    {
      "field_name": "patient_age",
      "field_type": "Dict[str, Any]",
      "description": "Patient's age in years.\n\nDescription: Patient's age in years\n\nRules:\n- Extract numeric age value only\n- Round to nearest integer if decimal provided\n- Must be a valid integer between 0 and 120\n- Use \"NR\" if not reported\n\nSource Grounding:\n- Return a dictionary with two keys: \"value\" and \"source_text\"\n- \"value\": The extracted age as an integer\n- \"source_text\": Copy the exact sentence(s) or paragraph from the document where this age was found\n- Include enough context in source_text to verify the extraction (typically 1-3 sentences)\n- If value is \"NR\", set source_text to \"NR\" as well\n\nExamples:\n{\"value\": 45, \"source_text\": \"Patient Demographics: 45-year-old female presenting with recurrent headaches.\"}\n{\"value\": 67, \"source_text\": \"A 67-year-old male with history of hypertension was admitted to the cardiology unit.\"}\n{\"value\": \"NR\", \"source_text\": \"NR\"}"
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
      "description": "Full markdown content of the medical research paper"
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
      "description": "Comprehensive summary of patient case.\n\nRules:\n- Synthesize diagnosis, treatment, and age into 2-4 sentence narrative\n- Use professional medical writing style\n- Include all key clinical details\n- Use \"NR\" if cannot create summary due to missing data\n\nSource Grounding:\n- Return a dictionary with two keys: \"value\" and \"source_text\"\n- \"value\": The synthesized summary as a string\n- \"source_text\": Copy the relevant sections from the document used to create the summary\n- Include enough context in source_text to verify the synthesis (typically 2-5 sentences)\n- If value is \"NR\", set source_text to \"NR\" as well\n\nExamples:\n{\"value\": \"A 45-year-old patient presented with Type 2 Diabetes Mellitus. Treatment consisted of Metformin 500mg twice daily with lifestyle modifications. The patient achieved successful glycemic control.\", \"source_text\": \"Patient Demographics: 45-year-old female. Assessment: Type 2 Diabetes Mellitus (E11.9). Treatment plan: Metformin 500mg twice daily with meals. Follow-up: Patient achieved HbA1c target of 6.5%.\"}\n{\"value\": \"67-year-old patient diagnosed with Acute Myocardial Infarction underwent CABG with good recovery and ongoing cardiac rehabilitation.\", \"source_text\": \"A 67-year-old male admitted with Acute Myocardial Infarction. The patient underwent coronary artery bypass grafting (CABG) with three vessel grafts. Post-operative recovery was uncomplicated. Patient enrolled in cardiac rehabilitation program.\"}\n{\"value\": \"NR\", \"source_text\": \"NR\"}"
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
      "field_type": "enum",
      "field_control_type": "select",
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
  "class_docstring": "Classify the type of clinical study from the research paper.\n\nForm Questions:\n- Study Type: \"What type of study is this?\"\n  Options: Randomized Controlled Trial, Cohort Study, Case-Control Study, Cross-Sectional Study, Other",
  
  "input_fields": [
    {
      "field_name": "markdown_content",
      "field_type": "str",
      "description": "Full markdown content of the medical research paper"
    }
  ],
  
  "output_fields": [
    {
      "field_name": "study_type",
      "field_type": "Dict[str, Any]",
      "description": "Type of clinical study.\n\nDescription: Type of clinical study\n\nOptions:\n- \"Randomized Controlled Trial\"\n- \"Cohort Study\"\n- \"Case-Control Study\"\n- \"Cross-Sectional Study\"\n- \"Other\"\n\nRules:\n- Must be exactly one of the options listed above\n- Use exact spelling and capitalization\n- Read study methodology section to determine type\n- Use \"NR\" if study type cannot be determined\n\nSource Grounding:\n- Return a dictionary with two keys: \"value\" and \"source_text\"\n- \"value\": The study type as a string (must match one of the options)\n- \"source_text\": Copy the exact sentence(s) or paragraph from the document where the study design is described\n- Include enough context in source_text to verify the classification (typically 1-3 sentences)\n- If value is \"NR\", set source_text to \"NR\" as well\n\nExamples:\n{\"value\": \"Randomized Controlled Trial\", \"source_text\": \"Methods: This randomized controlled trial assigned 200 participants to either the intervention group or control group using computer-generated randomization.\"}\n{\"value\": \"Cohort Study\", \"source_text\": \"Study Design: A prospective cohort study was conducted following 5,000 participants over 10 years to assess cardiovascular outcomes.\"}\n{\"value\": \"NR\", \"source_text\": \"NR\"}"
    }
  ]
}
```

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
      "field_description": "Extract ALL interventions tested in the study",
      "extraction_hints": ["Look in methods section for intervention groups"],
      "subform_fields": [
        {
          "field_name": "intervention_name",
          "field_type": "text",
          "field_description": "Name of the intervention"
        },
        {
          "field_name": "dosage",
          "field_type": "text",
          "field_description": "Dosage information"
        },
        {
          "field_name": "duration",
          "field_type": "text",
          "field_description": "Duration of treatment"
        }
      ]
    }
  },
  "depends_on": []
}
```

**Output Specification:**
```json
{
  "class_name": "ExtractInterventions",
  "class_docstring": "Extract all intervention groups tested in the clinical study.\n\nForm Questions:\n- Interventions: \"Extract ALL interventions tested, including name, dosage, and duration for each\"\n\nThis signature extracts repeating data - finding every intervention group mentioned in the study.",
  
  "input_fields": [
    {
      "field_name": "markdown_content",
      "field_type": "str",
      "description": "Full markdown content of the clinical research paper"
    }
  ],
  
  "output_fields": [
    {
      "field_name": "interventions",
      "field_type": "List[Dict[str, Any]]",
      "description": "Array of all interventions tested in the study.\n\nDescription: Extract ALL interventions tested in the study\n\nExtraction Hints:\n- Look in methods section for intervention groups\n\nStructure:\nEach intervention in the array contains:\n- intervention_name (str): Name of the intervention\n- dosage (str): Dosage information\n- duration (str): Duration of treatment\n\nRules:\n- Extract EVERY intervention group mentioned in the study\n- Each intervention should be a complete object with all fields\n- Create a separate array item for each intervention found\n- Include control groups and placebo if present\n- If multiple interventions exist, the array will have multiple items\n- Use \"NR\" if no interventions are found\n\nSource Grounding:\n- Return a dictionary with two keys: \"value\" and \"source_text\"\n- \"value\": An array of intervention objects, each with intervention_name, dosage, and duration keys\n- \"source_text\": Copy the exact section(s) from the document describing all interventions\n- Include enough context in source_text to verify all extractions (typically the entire interventions section)\n- If value is \"NR\", set source_text to \"NR\" as well\n\nExamples:\n{\"value\": [{\"intervention_name\": \"Drug A 10mg\", \"dosage\": \"10mg daily\", \"duration\": \"12 weeks\"}, {\"intervention_name\": \"Drug B 20mg\", \"dosage\": \"20mg twice daily\", \"duration\": \"12 weeks\"}, {\"intervention_name\": \"Placebo\", \"dosage\": \"matching tablets\", \"duration\": \"12 weeks\"}], \"source_text\": \"Interventions: Participants were randomized to three groups. Group 1 received Drug A 10mg daily for 12 weeks. Group 2 received Drug B 20mg twice daily for 12 weeks. Group 3 received matching placebo tablets for 12 weeks.\"}\n{\"value\": [{\"intervention_name\": \"Exercise program\", \"dosage\": \"3 sessions per week\", \"duration\": \"6 months\"}, {\"intervention_name\": \"Standard care\", \"dosage\": \"N/A\", \"duration\": \"6 months\"}], \"source_text\": \"Methods: The intervention group participated in a structured exercise program with 3 sessions per week for 6 months. The control group received standard care for 6 months with no exercise intervention.\"}\n{\"value\": \"NR\", \"source_text\": \"NR\"}"
    }
  ]
}
```

**KEY POINTS FOR SUBFORMS:**
- Use List[Dict[str, Any]] as field_type (not Dict[str, Any])
- The "value" key contains an ARRAY of objects
- Each object in the array has keys from subform_fields
- Include structure description listing all subfield names and types
- Examples show arrays with multiple items
- Emphasize extracting ALL instances found

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
   - Examples as JSON dicts with both "value" and "source_text" keys (including "NR")
   - Options (if enum type)

5. ✅ Input fields:
   - Always include primary document input field
   - Add context input field for EACH entry in depends_on array (if not empty)

6. ✅ Type mapping:
   - MOST output fields use Dict[str, Any] as field_type
   - EXCEPTION: Subform fields (array with subform_fields) use List[Dict[str, Any]]
   - Within the "value" key, map from field_type:
     * "text" → str
     * "number" → int or float
     * "enum" → str
     * "boolean" → bool
     * "array" (with subform_fields) → List[Dict[str, Any]]

7. ✅ Include "NR" (Not Reported) convention in ALL field descriptions
   - When value is not found: {"value": "NR", "source_text": "NR"}

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
   MOST fields use Dict[str, Any] as field_type
   EXCEPTION: Subform fields (array with subform_fields) must use List[Dict[str, Any]]
   The "value" key inside should match the semantic type (str for text, int for number, List for arrays)

❌ Missing "NR" convention
   Every field must document "NR" for missing data

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
6. Include source grounding instructions in every output field description
7. Map types correctly from field_type for the "value" key in each field's metadata
8. Include all metadata (options, hints, descriptions) from fields
9. Write clear extraction rules and examples with both "value" and "source_text"
10. Verify every field in fields dict is covered

Output the JSON specification with this structure:
```json
{
  "class_name": "...",
  "class_docstring": "...",
  "input_fields": [...],
  "output_fields": [...]
}
```
