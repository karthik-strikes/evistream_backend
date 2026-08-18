"""Make the `scale` anchor unambiguous on the continuous-outcomes forms.

Stage 1 flip-flopped on Polat 2005b: sometimes 8 rows with scale="VAS",
sometimes 32 with scale="VAS - chewing" / "VAS - biting" / ... — a 4x swing in
row count from one free-text anchor. The collapsed version then broke Stage 2:
with the activity unspecified the model deliberated in `reasoning` about which
activity to report and never emitted the answer field (production job
4c23c525, twice in a row, Aug 12 2026).

The column's own description never said whether the measurement CONDITION is
part of the scale, so both readings were defensible. This states it.

Methodologically the split is the correct reading: chewing pain and biting pain
at the same timepoint are different outcomes and cannot be pooled.

Runtime-composed from schema_def, so it applies on the next extraction with no
regeneration. Writes forms.schema_def, forms.fields and the schemas mirror.

Usage:
    python zscripts/fix_scale_ambiguity.py            # dry run
    python zscripts/fix_scale_ambiguity.py --apply
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402

APPLY = "--apply" in sys.argv

TARGET_COL = "scale"

ADDED_DESC = (
    " CONDITION IS PART OF THE SCALE: when the same instrument is applied under "
    "distinct conditions and reported separately — chewing vs biting, at rest vs "
    "on movement, front vs back teeth — each condition is a DIFFERENT value here, "
    "written as 'VAS (chewing)', 'VAS (biting)'. Never collapse separately "
    "reported conditions into one value: they are different outcomes and cannot "
    "be pooled."
)

ADDED_RULES = [
    "If the paper reports the same instrument separately for several activities, "
    "conditions or body sites, create one row per condition and name the condition "
    "in this column — do not report only one of them and do not merge them.",
    "Two rows that differ only by the condition measured are DIFFERENT rows, not "
    "duplicates.",
]


def main() -> None:
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    forms = sb.table("forms").select(
        "id, form_name, schema_name, schema_def, fields"
    ).not_.is_("schema_def", "null").execute().data

    planned = []
    for form in forms:
        sd = form["schema_def"] or {}
        hits = []
        for sig in sd.get("signatures", []):
            for of in sig.get("output_fields", []):
                anchors = set(of.get("anchor_columns") or [])
                if TARGET_COL not in anchors:
                    continue
                for sf in (of.get("subform_fields") or []):
                    if sf.get("field_name") != TARGET_COL:
                        continue
                    if "CONDITION IS PART OF THE SCALE" in (sf.get("field_description") or ""):
                        continue  # already applied
                    hits.append((of["name"], sf))
        if hits:
            planned.append((form, hits))

    print("=" * 76)
    print(f"SCALE AMBIGUITY FIX — {'APPLYING' if APPLY else 'DRY RUN'}")
    print("=" * 76)
    for form, hits in planned:
        print(f"\n{form['form_name']}  [{form['id']}]")
        for field_name, sf in hits:
            print(f"  field {field_name} · column {TARGET_COL}")
            print(f"    rules: {len(sf.get('rules') or [])} -> {len(sf.get('rules') or []) + len(ADDED_RULES)}")
    print(f"\nforms affected: {len(planned)}")

    if not APPLY:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    schema_names = []
    for form, _ in planned:
        sd = json.loads(json.dumps(form["schema_def"]))
        touched_fields = []
        for sig in sd.get("signatures", []):
            for of in sig.get("output_fields", []):
                if TARGET_COL not in set(of.get("anchor_columns") or []):
                    continue
                for sf in (of.get("subform_fields") or []):
                    if sf.get("field_name") != TARGET_COL:
                        continue
                    if "CONDITION IS PART OF THE SCALE" in (sf.get("field_description") or ""):
                        continue
                    sf["field_description"] = (sf.get("field_description") or "").rstrip() + ADDED_DESC
                    sf["rules"] = list(sf.get("rules") or []) + ADDED_RULES
                    touched_fields.append(of["name"])

        try:
            sd["version"] = int(sd.get("version") or 1) + 1
        except (TypeError, ValueError):
            sd["version"] = 2

        # Mirror into forms.fields so the builder UI shows the same wording.
        fields = json.loads(json.dumps(form.get("fields") or []))
        if isinstance(fields, list):
            for fld in fields:
                if not isinstance(fld, dict) or fld.get("field_name") not in touched_fields:
                    continue
                for sf in (fld.get("subform_fields") or []):
                    if sf.get("field_name") != TARGET_COL:
                        continue
                    if "CONDITION IS PART OF THE SCALE" in (sf.get("field_description") or ""):
                        continue
                    sf["field_description"] = (sf.get("field_description") or "").rstrip() + ADDED_DESC
                    sf["rules"] = list(sf.get("rules") or []) + ADDED_RULES

        sb.table("forms").update({"schema_def": sd, "fields": fields}).eq("id", form["id"]).execute()
        if form.get("schema_name"):
            sb.table("schemas").update({"schema_def": sd}).eq(
                "schema_name", form["schema_name"]).execute()
            schema_names.append(form["schema_name"])
        print(f"updated {form['form_name']} -> version {sd['version']}")

    print(f"\n{len(planned)} form(s) written.")
    print("\nDrop L2 and restart workers so the old wording leaves memory:")
    print("  redis-cli -p 6380 DEL " + " ".join(f"schema:{n}" for n in sorted(set(schema_names))))


if __name__ == "__main__":
    main()
