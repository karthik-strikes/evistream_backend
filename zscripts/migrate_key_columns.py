"""Step E — dual-write `key_columns` alongside `anchor_columns` on every live form.

What this does
--------------
For each form with a compiled schema_def, for each table field, write the field's
composite key under BOTH keys. Nothing is removed: `anchor_columns` keeps its
exact current value.

Why dual-write instead of a rename
----------------------------------
61 sites across 22 files read this key. The production readers now go through
`utils.table_schema.field_key_columns()`, which prefers `key_columns` and falls
back to `anchor_columns` — but "the readers now go through the accessor" is a
claim about a grep, and a grep is not a proof. Dual-write makes a missed reader
*harmless* rather than *silent*: it sees `anchor_columns`, unchanged, and behaves
exactly as it did yesterday. The old key is dropped in a later change, once logs
over a full extraction cycle show nothing reading it.

Three places hold the same key and all three must move together, or a form's
behaviour depends on which one a code path happens to consult:
  · forms.schema_def   — what the extractor compiles from
  · forms.fields       — what the form editor renders
  · schemas.schema_def — the L3 cache mirror that survives a restart

Safety
------
· Dry run by default. `--apply` is required to write.
· Per-form try/except: one bad form cannot abort the run (this is exactly how the
  EndNote importer silently lost two references, so failures are counted AND
  printed, never swallowed).
· Skips forms already carrying identical values under both keys, so it is
  idempotent and safe to re-run.
· Does NOT touch extraction_strategy. Renaming stored strategy values is a
  separate decision: `resolve_strategy` already accepts both spellings, so
  there is no forcing reason to rewrite 48 rows of live data.
· Prints the Redis keys to drop. The L2 cache holds a 1 h copy of schema_def; a
  worker that has one cached keeps serving the pre-migration copy until it
  expires. Harmless here (dual-write means both copies behave identically) but
  worth dropping so the change is observable immediately.

Usage
-----
    python zscripts/migrate_key_columns.py             # dry run
    python zscripts/migrate_key_columns.py --apply     # write
    python zscripts/migrate_key_columns.py --apply --form-id <uuid>
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
from utils.table_schema import field_key_columns, set_field_key_columns  # noqa: E402


def _migrate_field(field: Dict[str, Any]) -> Tuple[bool, str]:
    """Dual-write one table field's composite key. Returns (changed, note)."""
    if not (field.get("subform_fields") or []):
        return False, "not-a-table"

    key = field_key_columns(field)
    if not key:
        # Nothing to mirror. A keyed pipeline with no key is a real problem, but
        # inventing a key here would be worse than leaving it visible to the
        # startup audit in registry._audit_table_fields.
        return False, "no-key"

    if field.get("key_columns") == key and field.get("anchor_columns") == key:
        return False, "already-migrated"

    set_field_key_columns(field, key)
    return True, "dual-written"


def _walk_schema_def(schema_def: Dict[str, Any]) -> Tuple[int, Counter]:
    changed = 0
    notes: Counter = Counter()
    for sig in (schema_def.get("signatures") or []):
        for field in (sig.get("output_fields") or []):
            did, note = _migrate_field(field)
            notes[note] += 1
            changed += int(did)
    return changed, notes


def _walk_fields(fields: Any) -> Tuple[int, Counter]:
    """forms.fields is the editor's copy — a flat list of user-designed fields."""
    changed = 0
    notes: Counter = Counter()
    if not isinstance(fields, list):
        return 0, notes
    for field in fields:
        if not isinstance(field, dict):
            continue
        did, note = _migrate_field(field)
        notes[note] += 1
        changed += int(did)
    return changed, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default is dry run)")
    ap.add_argument("--form-id", help="restrict to one form")
    args = ap.parse_args()

    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    q = sb.table("forms").select("id, form_name, status, schema_def, fields, schema_name")
    if args.form_id:
        q = q.eq("id", args.form_id)
    forms = q.not_.is_("schema_def", "null").execute().data

    stats: Counter = Counter()
    notes_total: Counter = Counter()
    touched: List[str] = []
    failures: List[str] = []
    redis_keys: List[str] = []

    for form in forms:
        fid, fname = form["id"], form.get("form_name") or "(unnamed)"
        try:
            schema_def = form.get("schema_def") or {}
            fields = form.get("fields")

            sd_changed, sd_notes = _walk_schema_def(schema_def)
            f_changed, f_notes = _walk_fields(fields)
            notes_total += sd_notes

            if not (sd_changed or f_changed):
                stats["unchanged"] += 1
                continue

            stats["forms_to_change"] += 1
            stats["fields_changed"] += sd_changed + f_changed
            touched.append(f"{fname}  ({sd_changed} in schema_def, {f_changed} in fields)")
            if form.get("schema_name"):
                redis_keys.append(f"schema:{form['schema_name']}")

            if not args.apply:
                continue

            payload: Dict[str, Any] = {"schema_def": schema_def}
            if f_changed:
                payload["fields"] = fields
            sb.table("forms").update(payload).eq("id", fid).execute()

            # The L3 mirror. Keyed by schema_name, not form id.
            if form.get("schema_name"):
                sb.table("schemas").update({"schema_def": schema_def}) \
                  .eq("schema_name", form["schema_name"]).execute()
            stats["written"] += 1

        except Exception as exc:                     # noqa: BLE001
            # Counted AND printed. A swallowed per-row exception is how two
            # EndNote references disappeared without a trace.
            stats["FAILED"] += 1
            failures.append(f"{fname} ({fid}): {type(exc).__name__}: {exc}")

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 74)
    print(f"KEY-COLUMN DUAL-WRITE — {mode}")
    print("=" * 74)
    print(f"  forms with a compiled schema_def : {len(forms)}")
    for k in ("forms_to_change", "fields_changed", "written", "unchanged", "FAILED"):
        if stats[k]:
            print(f"  {k:<33}: {stats[k]}")
    print("\n  table-field outcomes (schema_def):")
    for note, n in notes_total.most_common():
        if note != "not-a-table":
            print(f"    {note:<20} {n}")

    if touched:
        print(f"\n  forms {'changed' if args.apply else 'that would change'}:")
        for t in touched:
            print("    " + t)

    if failures:
        print("\n  FAILURES:")
        for f in failures:
            print("    " + f)

    if redis_keys and args.apply:
        print("\n  drop these L2 cache keys so workers see it now (else ≤1 h TTL):")
        print("    redis-cli -p 6380 DEL " + " ".join(sorted(set(redis_keys))))

    if not args.apply and stats["forms_to_change"]:
        print("\n  re-run with --apply to write.")
    print()
    return 1 if stats["FAILED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
