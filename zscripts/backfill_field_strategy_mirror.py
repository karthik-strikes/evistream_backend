"""Mirror each table field's extraction mode and composite key from
`forms.schema_def` back onto `forms.fields`.

What this repairs
-----------------
One setting lives in two columns and nothing enforced that they agree:

  · forms.schema_def  — what build_schema_classes compiles from. AUTHORITATIVE.
  · forms.fields      — the editor's copy, and the input to a regenerate.

42 live table fields carried `row_then_columns` in schema_def and nothing in
fields. Two consequences, both silent:

  1. The field editor read the mode from fields, found nothing, and fell back to
     rendering "Fast · 1 model call" (forms/page.tsx `selectedFieldMode`) for a
     field that was actually running the 3-call keyed pipeline.
  2. A regenerate reads fields, not schema_def. With no mode there,
     signature_gen.py falls back to `single_call` and writes it — silently
     downgrading Rigorous to Fast with no error and no log line.

Direction is one-way on purpose
-------------------------------
schema_def → fields, never the reverse. schema_def is what actually runs, so it
is the only defensible source of truth. Where the two already disagree the
script reports the conflict and takes schema_def; it never invents a value.

Why this is a low-risk write
----------------------------
It touches `forms.fields` only. The extractor never reads that column, the
`schemas` L3 mirror holds schema_def (untouched), and the Redis L2 copy is of
schema_def too — so no cache needs dropping and no in-flight extraction changes
behaviour. The blast radius is the form editor and the next regenerate.

Safety
------
· Dry run by default. `--apply` is required to write.
· Per-form try/except: one malformed form cannot abort the run, and failures are
  counted AND printed, never swallowed.
· Idempotent — a field already matching schema_def is skipped, so re-running is
  a no-op.
· Key columns go through set_field_key_columns, so both `key_columns` and
  `anchor_columns` are written and a reader missed by the rename stays correct.

Usage
-----
    python zscripts/backfill_field_strategy_mirror.py             # dry run
    python zscripts/backfill_field_strategy_mirror.py --apply     # write
    python zscripts/backfill_field_strategy_mirror.py --form-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from utils.table_schema import (  # noqa: E402
    field_key_columns,
    resolve_strategy,
    set_field_key_columns,
)


def _schema_def_table_fields(schema_def: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """name -> output_field, for every TABLE field in schema_def.

    Keyed by name because that is how the editor and the field-edits API address
    a field; signatures are an implementation detail of the decomposition.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for sig in (schema_def.get("signatures") or []):
        for of in (sig.get("output_fields") or []):
            if isinstance(of, dict) and of.get("subform_fields") and of.get("name"):
                out[of["name"]] = of
    return out


def _mirror_form(
    fields: Any, schema_def: Dict[str, Any]
) -> Tuple[int, List[str], List[str]]:
    """Copy mode + composite key from schema_def onto forms.fields, in place.

    Returns (n_changed, notes, conflicts).
    """
    if not isinstance(fields, list):
        return 0, [], []

    sd_fields = _schema_def_table_fields(schema_def)
    changed = 0
    notes: List[str] = []
    conflicts: List[str] = []

    for field in fields:
        if not isinstance(field, dict):
            continue
        fname = field.get("field_name")
        source = sd_fields.get(fname)
        if source is None:
            continue  # not a table field, or absent from the compiled schema

        touched = []

        # ── Mode ──────────────────────────────────────────────────────────
        sd_strategy = source.get("extraction_strategy")
        if sd_strategy:
            cur = field.get("extraction_strategy")
            if cur != sd_strategy:
                # Compare canonically: 'single_call' and 'single_pass' are the
                # same pipeline, and rewriting one spelling to the other would
                # be churn dressed up as a repair.
                if cur and resolve_strategy(cur) == resolve_strategy(sd_strategy):
                    pass
                else:
                    if cur:
                        conflicts.append(
                            f"{fname}: fields={cur!r} vs schema_def={sd_strategy!r} "
                            f"— taking schema_def (it is what runs)"
                        )
                    field["extraction_strategy"] = sd_strategy
                    touched.append(f"mode→{sd_strategy}")

        # ── Composite key ─────────────────────────────────────────────────
        sd_key = field_key_columns(source)
        if sd_key and field_key_columns(field) != sd_key:
            set_field_key_columns(field, sd_key)
            touched.append(f"key→{'×'.join(sd_key)}")

        if touched:
            changed += 1
            notes.append(f"{fname}: {', '.join(touched)}")

    return changed, notes, conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is dry run)")
    ap.add_argument("--form-id", help="restrict to one form")
    args = ap.parse_args()

    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    q = sb.table("forms").select("id, form_name, status, schema_def, fields")
    if args.form_id:
        q = q.eq("id", args.form_id)
    forms = q.not_.is_("schema_def", "null").execute().data

    stats: Counter = Counter()
    touched: List[str] = []
    all_conflicts: List[str] = []
    failures: List[str] = []

    for form in forms:
        fid, fname = form["id"], form.get("form_name") or "(unnamed)"
        try:
            schema_def = form.get("schema_def") or {}
            fields = form.get("fields")

            changed, notes, conflicts = _mirror_form(fields, schema_def)
            all_conflicts.extend(f"{fname}  {c}" for c in conflicts)

            if not changed:
                stats["unchanged"] += 1
                continue

            stats["forms_to_change"] += 1
            stats["fields_changed"] += changed
            touched.append(f"{fname}  [{form.get('status')}]")
            touched.extend(f"      {n}" for n in notes)

            if not args.apply:
                continue

            # forms.fields ONLY. schema_def is the source here and stays as-is,
            # so the schemas L3 mirror and the Redis copy remain correct and no
            # cache invalidation is needed.
            sb.table("forms").update({"fields": fields}).eq("id", fid).execute()
            stats["written"] += 1

        except Exception as exc:  # noqa: BLE001
            stats["FAILED"] += 1
            failures.append(f"{fname} ({fid}): {type(exc).__name__}: {exc}")

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 74)
    print(f"STRATEGY / KEY MIRROR  schema_def → forms.fields — {mode}")
    print("=" * 74)
    print(f"  forms with a compiled schema_def : {len(forms)}")
    for k in ("forms_to_change", "fields_changed", "written", "unchanged", "FAILED"):
        if stats[k]:
            print(f"  {k:<33}: {stats[k]}")

    if touched:
        print(f"\n  forms {'changed' if args.apply else 'that would change'}:")
        for t in touched:
            print("    " + t)

    if all_conflicts:
        print(f"\n  CONFLICTS ({len(all_conflicts)}) — fields disagreed, schema_def won:")
        for c in all_conflicts:
            print("    " + c)

    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for f in failures:
            print("    " + f)

    if not args.apply and stats["forms_to_change"]:
        print("\n  Dry run — nothing was written. Re-run with --apply.")

    return 1 if stats["FAILED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
