"""
Form specifications for the Claude Code agentic-extraction baseline (periodontitis CD004714).

Each FormSpec ties together:
  - the zforms prompt JSON (the field-spec the agent is given as its task)
  - the output CSV filename + EXACT column order the eval harness expects
    (`eval/forms/periodontitis.py` reads these by the `ai_filename` base names;
     the study key column is always `Paper`)
  - whether the form is wide (one row per study) or level-2 (one row per arm /
    per outcome×timepoint×subgroup), and for level-2 the JSON array key.

The driver (run_agent_extraction.py) is pure orchestration; this module owns the
form metadata and turns a zforms JSON into the agent task prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_REPO = Path("/home/ubuntu/evistream")
ZFORMS_DIR = _REPO / "zforms" / "periodontitis"


@dataclass(frozen=True)
class FormSpec:
    key: str                 # short id used on the CLI (--forms)
    json_name: str           # prompt JSON filename in zforms/periodontitis/
    out_filename: str        # output CSV name (matches harness ai_filename base name)
    columns: tuple[str, ...] # exact column order, including leading "Paper"
    level2: bool             # True → one row per sub-record
    array_key: str | None    # for level2: the JSON array field name the agent returns


# The 4 forms that already exist in eval/sheets/ai sheets/full_studies/periodontitis/claude/ (risk_of_bias skipped).
FORMS: dict[str, FormSpec] = {
    "study_char": FormSpec(
        key="study_char",
        json_name="cd004714_study_characteristics.json",
        out_filename="form_Study_Characteristics_long.csv",
        columns=("Paper", "country", "setting", "number_of_centres", "trial_design",
                 "recruitment_period", "funding_source", "notes"),
        level2=False,
        array_key=None,
    ),
    "patient_pop": FormSpec(
        key="patient_pop",
        json_name="cd004714_patient_population.json",
        out_filename="form_Patient_Population_long.csv",
        columns=("Paper", "age_arm_a", "age_arm_a_sd", "age_arm_b", "age_arm_b_sd",
                 "pct_female_arm_a", "pct_female_arm_b", "diabetes_type",
                 "baseline_hba1c_arm_a", "baseline_hba1c_arm_b", "metabolic_control_level",
                 "duration_since_diabetes_dx", "tobacco_use", "alcohol_consumption",
                 "inclusion_criteria", "exclusion_criteria", "n_randomised", "n_evaluated"),
        level2=False,
        array_key=None,
    ),
    "interventions": FormSpec(
        key="interventions",
        json_name="cd004714_interventions.json",
        out_filename="form_Intervention_Characteristics_long.csv",
        columns=("Paper", "arm_label", "comparison_summary", "intervention_description",
                 "n_in_arm", "duration_of_followup", "cochrane_subgroup_category"),
        level2=True,
        array_key="interventions",
    ),
    "outcomes": FormSpec(
        key="outcomes",
        json_name="cd004714_outcomes_continuous.json",
        out_filename="form_Continuous_Outcomes_long.csv",
        columns=("Paper", "outcome_type", "timepoint", "subgroup",
                 "mean_arm1", "sd_arm1", "n_arm1", "mean_arm2", "sd_arm2", "n_arm2"),
        level2=True,
        array_key="outcomes",
    ),
}


def load_spec_json(spec: FormSpec) -> dict:
    return json.loads((ZFORMS_DIR / spec.json_name).read_text())


def _render_fields(fields: list[dict], indent: str = "") -> list[str]:
    """Render a flat list of field defs into prompt bullet lines."""
    lines = []
    for f in fields:
        bits = [f"{indent}- `{f['field_name']}` ({f.get('field_type', 'text')}): {f['field_description']}"]
        if f.get("options"):
            bits.append(f"{indent}  allowed values: {', '.join(f['options'])}")
        if f.get("example") is not None:
            bits.append(f"{indent}  example: {f['example']}")
        lines.extend(bits)
    return lines


def build_prompt(spec: FormSpec) -> str:
    """Serialize the zforms JSON into the agentic extraction task.

    The agent is told to READ paper.md itself (so it works agentically — reading,
    grepping, re-checking over as many turns as it needs), then WRITE the structured
    result to extraction.json. The driver sets the `Paper` column itself, so the
    agent is NOT asked for it.
    """
    sj = load_spec_json(spec)
    form_name = sj.get("form_name", spec.key)
    form_desc = sj.get("form_description", "")

    out: list[str] = []
    out.append(f"# Data extraction task: {form_name}")
    out.append("")
    out.append("You are a meticulous systematic-review data extractor. A single clinical-trial "
               "paper has been placed in your working directory as `paper.md`. Read it carefully "
               "(read the whole file; grep/re-read specific sections — Methods, baseline tables, "
               "Results tables — as needed) and extract the fields described below.")
    out.append("")
    out.append("## What to extract")
    out.append(form_desc)
    out.append("")

    if not spec.level2:
        out.append("## Fields")
        out.extend(_render_fields(sj["fields"]))
        out.append("")
        out.append("## Output")
        out.append("When done, WRITE a file named `extraction.json` in your working directory "
                   "containing a SINGLE JSON object whose keys are exactly the field names above "
                   "and whose values are the extracted strings. Also print that same JSON as your "
                   "final message.")
        out.append("Do NOT include a `Paper`/study-id key — that is added separately.")
    else:
        array_field = next(f for f in sj["fields"] if f.get("field_type") == "array")
        out.append(f"## Rows ({array_field['field_name']})")
        out.append(array_field["field_description"])
        out.append("")
        out.append("Each row has these fields:")
        out.extend(_render_fields(array_field["subform_fields"], indent="  "))
        out.append("")
        out.append("## Output")
        out.append(f"When done, WRITE a file named `extraction.json` in your working directory "
                   f"containing a JSON object of the form "
                   f'`{{"{spec.array_key}": [ {{...row...}}, {{...row...}} ]}}` — one object per row, '
                   f"each with exactly the row field names above. Also print that same JSON as your "
                   f"final message.")
        out.append("Emit every row the paper supports (including control / usual-care arms). "
                   "Do NOT include a `Paper`/study-id key in the rows — that is added separately.")

    out.append("")
    out.append("## Rules")
    out.append("- Use the string `\"NR\"` for any value the paper does not report "
               "(and `\"None\"` for a notes field with nothing to add).")
    out.append("- Extract only from `paper.md`. Do not use outside knowledge or invent values.")
    out.append("- For `select` fields, choose from the allowed values only.")
    out.append("- Output must be valid JSON and nothing else in `extraction.json`.")
    return "\n".join(out)
