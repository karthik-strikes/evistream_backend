"""Convert the flat ibuprofen_forms/*.json form definitions into the
{"signatures": [{"output_fields": [...]}]} shape seed_scalar_prompts.py /
seed_prompts.py expect (the shape export_schema_def.py produces when pulling a
live form's schema_def from Supabase).

ibuprofen's forms were supplied as static {form_name, form_description, fields}
JSON rather than pulled live, and use field_name/field_description instead of
name/description, so a conversion step is needed before the existing prompt-arm
seeding tooling can run against them unmodified.

    python -m eval.studies.ablation.convert_ibuprofen_schemas
"""

from __future__ import annotations

import json
from pathlib import Path

SRC_DIR = Path("/home/ubuntu/evistream/ibuprofen_forms")
OUT_DIR = Path("/home/ubuntu/evistream/eval/studies/ablation/base_schemas")

# Each entry: source filename -> (output base_schemas filename, table field_name or None,
# anchor column field_names for that table field).
FORMS = [
    ("cd015432_—_study_characteristics_v3.json", "ibuprofen_study_characteristics.json", None, ()),
    ("cd015432_—_patient_population.json", "ibuprofen_patient_population.json", None, ()),
    ("cd015432_—_intervention_characteristics.json", "ibuprofen_interventions.json",
     "interventions", ("arm_label", "arm_category", "drug_name", "n_in_arm")),
    ("cd015432_—_continuous_outcomes_v2.json", "ibuprofen_continuous_outcomes.json",
     "outcomes", ("comparison", "outcome_type", "reporter", "timepoint")),
    ("cd015432_—_dichotomous_outcomes.json", "ibuprofen_dichotomous_outcomes.json",
     "dichotomous_outcomes", ("comparison", "outcome", "timepoint", "arm1_label", "arm2_label")),
]


def _convert_field(f: dict, table_field_name: str | None, anchors: tuple[str, ...]) -> dict:
    out = dict(f)
    out["name"] = f["field_name"]
    out["description"] = f.get("field_description", "")
    if f["field_name"] == table_field_name:
        out["subform_fields"] = [
            {**c, "extraction_role": "anchor" if c["field_name"] in anchors else "value"}
            for c in f.get("subform_fields", [])
        ]
    else:
        out["source_grounded"] = True
    return out


def convert(src_name: str, out_name: str, table_field_name: str | None, anchors: tuple[str, ...]) -> Path:
    raw = json.loads((SRC_DIR / src_name).read_text())
    class_name = "Ibu" + "".join(w.capitalize() for w in out_name[len("ibuprofen_"):-len(".json")].split("_"))
    output_fields = [_convert_field(f, table_field_name, anchors) for f in raw["fields"]]
    schema_def = {
        "form_name": raw["form_name"],
        "signatures": [{"class_name": class_name, "output_fields": output_fields}],
    }
    out_path = OUT_DIR / out_name
    out_path.write_text(json.dumps(schema_def, indent=2))
    return out_path


def main() -> int:
    for src_name, out_name, table_field_name, anchors in FORMS:
        out_path = convert(src_name, out_name, table_field_name, anchors)
        print(f"  {src_name} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
