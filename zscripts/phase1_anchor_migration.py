"""Phase 1 migration — take measured results OUT of live row keys.

The LLM anchor classifier put measured numbers in `anchor_columns` on 16 of 53
live table fields (`arm1_events`, `arm1_n`, `n_in_arm`, `n_events_number`, ...).
A measurement in the row key turns a value disagreement into an identity
disagreement: 25 and 26 become two DIFFERENT rows, so row coverage reports a
phantom missing row and R1-vs-R2 comparison reports a phantom conflict.

`is_measured_result_column` now blocks this at codegen, but only for NEW forms.
This applies the same rule to the forms already in the database.

Two kinds of change, kept separate because they carry different authority:

  REMOVE — mechanical, decided by `is_measured_result_column` (numeric type AND
           a statistical token in the name). Applied to every affected field.

  ADD    — a judgement the guard cannot make: it only ever removes. Listed
           explicitly per form below so the change is auditable, never inferred.

Writes all three places the schema lives (forms.schema_def, forms.fields for the
builder UI, and the schemas table as the L3 cache), bumps schema_def.version,
and prints the Redis keys to drop. Workers must be restarted afterwards to clear
the in-process registry and the signature-class LRU.

Usage:
    python zscripts/phase1_anchor_migration.py           # dry run
    python zscripts/phase1_anchor_migration.py --apply
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
from core.generators.signature_gen import is_measured_result_column  # noqa: E402

APPLY = "--apply" in sys.argv

# Columns to ADD to a row key, per form field. The guard only removes, so a
# missing identity dimension needs naming by hand.
#
# `follow_up_duration` on the dichotomous-outcomes forms: its own description
# reads "Time after surgery at which THIS row's outcome was assessed" — so a
# trial reporting SSI at 30 days AND 6 months currently collapses both into one
# row and one set of numbers survives. Confirmed with the reviewer before this
# ran; nothing else is added.
ADDITIONS: dict[tuple[str, str], list[str]] = {
    ("dichotomous_outcomes", "Dichotomous Outcomes v2"): ["follow_up_duration"],
    ("dichotomous_outcomes", "Dichotomous Outcomes v2 (Desc only)"): ["follow_up_duration"],
}


def corrected(anchors: list[str], cols: list[dict], form_name: str, field_name: str):
    by_name = {c.get("field_name"): c for c in cols if c.get("field_name")}
    removed = sorted(a for a in anchors if is_measured_result_column(by_name.get(a, {})))
    kept = [a for a in anchors if a not in set(removed)]

    added = []
    for cand in ADDITIONS.get((field_name, form_name), []):
        if cand in by_name and cand not in kept:
            added.append(cand)
    new_anchors = kept + added

    values_left = [c for c in by_name if c not in set(new_anchors)]
    return removed, added, new_anchors, values_left


def main() -> None:
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    forms = sb.table("forms").select(
        "id, form_name, schema_name, schema_def, fields"
    ).not_.is_("schema_def", "null").execute().data

    planned = []
    for form in forms:
        sd = form["schema_def"] or {}
        changed_fields = []
        for sig in sd.get("signatures", []):
            for of in sig.get("output_fields", []):
                anchors = list(of.get("anchor_columns") or [])
                cols = list(of.get("subform_fields") or [])
                if not anchors or not cols:
                    continue
                removed, added, new_anchors, values_left = corrected(
                    anchors, cols, form["form_name"], of["name"])
                if not removed and not added:
                    continue
                if not new_anchors or not values_left:
                    print(f"!! SKIP {form['form_name']}·{of['name']}: correction would "
                          f"leave anchors={new_anchors} values={values_left}")
                    continue
                changed_fields.append({
                    "field": of["name"], "before": anchors, "after": new_anchors,
                    "removed": removed, "added": added,
                })
        if changed_fields:
            planned.append({"form": form, "changes": changed_fields})

    print("=" * 78)
    print(f"PHASE 1 MIGRATION — {'APPLYING' if APPLY else 'DRY RUN'}")
    print("=" * 78)
    for p in planned:
        f = p["form"]
        print(f"\n{f['form_name']}   [{f['id']}]")
        for c in p["changes"]:
            print(f"  field {c['field']}")
            if c["removed"]:
                print(f"    REMOVE {c['removed']}")
            if c["added"]:
                print(f"    ADD    {c['added']}")
            print(f"    {c['before']}")
            print(f"    -> {c['after']}")
    print(f"\nforms affected: {len(planned)}   "
          f"fields: {sum(len(p['changes']) for p in planned)}")

    if not APPLY:
        print("\nDry run — nothing written. Re-run with --apply.")
        return

    # ── write ────────────────────────────────────────────────────────────────
    schema_names = []
    for p in planned:
        form = p["form"]
        sd = json.loads(json.dumps(form["schema_def"]))
        by_field = {c["field"]: c["after"] for c in p["changes"]}

        for sig in sd.get("signatures", []):
            for of in sig.get("output_fields", []):
                if of["name"] in by_field:
                    new_anchors = by_field[of["name"]]
                    of["anchor_columns"] = new_anchors
                    aset = set(new_anchors)
                    for sf in (of.get("subform_fields") or []):
                        sf["extraction_role"] = "anchor" if sf.get("field_name") in aset else "value"

        try:
            sd["version"] = int(sd.get("version") or 1) + 1
        except (TypeError, ValueError):
            sd["version"] = 2

        # The builder UI reads forms.fields, not schema_def — keep them in step.
        fields = json.loads(json.dumps(form.get("fields") or []))
        if isinstance(fields, list):
            for fld in fields:
                if isinstance(fld, dict) and fld.get("field_name") in by_field:
                    fld["anchor_columns"] = by_field[fld["field_name"]]

        sb.table("forms").update({"schema_def": sd, "fields": fields}).eq("id", form["id"]).execute()
        if form.get("schema_name"):
            sb.table("schemas").update({"schema_def": sd}).eq(
                "schema_name", form["schema_name"]).execute()
            schema_names.append(form["schema_name"])
        print(f"updated {form['form_name']} -> version {sd['version']}")

    print(f"\n{len(planned)} form(s) written (forms.schema_def, forms.fields, schemas).")
    print("\nNow drop the L2 cache entries and restart workers to clear L1 + the")
    print("signature-class LRU, or the old row keys stay live in memory:")
    print("  redis-cli -p 6380 DEL " + " ".join(f"schema:{n}" for n in sorted(set(schema_names))))
    print("  sudo systemctl restart evistream-worker-extraction evistream-worker-pdf "
          "evistream-worker-codegen evistream-fastapi")


if __name__ == "__main__":
    main()
