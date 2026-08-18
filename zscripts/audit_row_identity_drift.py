"""READ-ONLY audit: does each table field's prose agree with its checkboxes?

Two things tell the model how to split rows, and they are written independently:

  · the composite key   — the columns ticked in the row-definition editor. This
                          is what `_compose_field_desc` renders as the
                          "Row Identity (authoritative)" block, and what the
                          keyed pipeline enforces structurally.
  · the authored prose  — the field description and its Rules, where an author
                          typed a tuple by hand ("one row per A x B x C").

The Row Identity block ends with "THIS list wins", so a disagreement is not a
correctness bug today. It is a prompt-quality bug: the model reads one answer,
then a contradicting one, and for several fields the stale tuple sits under
"Rules", which the UI presents as a hard constraint.

This script only reports. It never writes — changing a key changes how rows
split or merge in every future extraction, which is a research decision, not a
mechanical one.

Three findings are reported per field:

  DRIFT       prose names a tuple that differs from the ticked columns
  MEASURE     a measured result sits in the key (over-splits: the same finding
              reported as mean(SD) and median(IQR) becomes two rows)
  UNMATCHED   prose names a concept with no column that could carry it, so the
              instruction cannot be satisfied at all

Usage
-----
    python zscripts/audit_row_identity_drift.py
    python zscripts/audit_row_identity_drift.py --form-id <uuid>
    python zscripts/audit_row_identity_drift.py --verbose
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from core.generators.signature_gen import is_measured_result_column  # noqa: E402
from utils.table_schema import field_key_columns, resolve_strategy  # noqa: E402

# "one row per (a x b x c)" / "One row per a × b × c" — the shape authors type.
# Deliberately loose on the separator: authors use x, ×, and * interchangeably.
_TUPLE_RE = re.compile(
    r"one row per\s*\(?\s*([^.)\n]+?)\s*\)?\s*(?:[.)\n]|$)",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"\s*(?:×|\bx\b|\*)\s*", re.IGNORECASE)

# Words an author writes that are not part of the tuple itself.
_STOPWORDS = {"reported", "in the paper", "combination", "reported in the paper"}


def _normalise(token: str) -> str:
    """'intervention arm' -> 'intervention_arm', for comparison against columns."""
    return re.sub(r"[^a-z0-9]+", "_", token.strip().lower()).strip("_")


def _prose_tuple(text: str) -> Optional[List[str]]:
    """The tuple an author typed, or None if they didn't type one."""
    if not text:
        return None
    m = _TUPLE_RE.search(text)
    if not m:
        return None
    parts = [p for p in _SPLIT_RE.split(m.group(1)) if p.strip()]
    if len(parts) < 2:
        return None  # a lone phrase is prose, not a tuple
    out = []
    for p in parts:
        n = _normalise(p)
        if n and n not in _STOPWORDS:
            out.append(n)
    return out or None


def _matches_a_column(concept: str, columns: List[str]) -> bool:
    """Loose match: authors write 'intervention arm' for a column 'intervention'."""
    c = concept
    for col in columns:
        if c == col or c in col or col in c:
            return True
    # 'timepoint' should match 'followup_timepoint'; compare on word pieces too
    pieces = set(c.split("_"))
    for col in columns:
        if pieces & set(col.split("_")):
            return True
    return False


def audit_field(of: Dict[str, Any]) -> Dict[str, Any]:
    cols = [
        (sf or {}).get("field_name")
        for sf in (of.get("subform_fields") or [])
        if isinstance(sf, dict) and sf.get("field_name")
    ]
    key = field_key_columns(of)

    # Prose lives in the description AND in the rules; both reach the prompt.
    rules = of.get("rules") or []
    prose_sources = [of.get("description") or ""] + [str(r) for r in rules]

    prose = None
    prose_where = None
    for i, text in enumerate(prose_sources):
        t = _prose_tuple(text)
        if t:
            prose, prose_where = t, ("description" if i == 0 else f"rule #{i}")
            break

    measures = [
        sf.get("field_name")
        for sf in (of.get("subform_fields") or [])
        if isinstance(sf, dict)
        and sf.get("field_name") in set(key)
        and is_measured_result_column(sf)
    ]

    unmatched = [c for c in (prose or []) if not _matches_a_column(c, cols)]

    drift = False
    if prose:
        # Compare as concepts, not exact strings: the prose is prose.
        matched_key = {k for k in key if any(_matches_a_column(c, [k]) for c in prose)}
        drift = len(matched_key) != len(key) or bool(unmatched)

    return {
        "field": of.get("name"),
        "mode": resolve_strategy(of.get("extraction_strategy")),
        "n_cols": len(cols),
        "key": key,
        "prose": prose,
        "prose_where": prose_where,
        "drift": drift,
        "measures_in_key": measures,
        "unmatched": unmatched,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form-id")
    ap.add_argument("--verbose", action="store_true", help="print clean fields too")
    args = ap.parse_args()

    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    q = sb.table("forms").select("id, form_name, project_id, schema_def")
    if args.form_id:
        q = q.eq("id", args.form_id)
    forms = q.not_.is_("schema_def", "null").execute().data

    projects = {
        p["id"]: p["name"]
        for p in sb.table("projects").select("id, name").execute().data
    }

    stats: Counter = Counter()
    rows: List[tuple] = []

    for form in forms:
        pname = projects.get(form.get("project_id"), "?")
        for sig in (form.get("schema_def") or {}).get("signatures") or []:
            for of in sig.get("output_fields") or []:
                if not (isinstance(of, dict) and of.get("subform_fields")):
                    continue
                r = audit_field(of)
                stats["table_fields"] += 1
                flags = []
                if r["measures_in_key"]:
                    flags.append("MEASURE")
                    stats["MEASURE"] += 1
                if r["unmatched"]:
                    flags.append("UNMATCHED")
                    stats["UNMATCHED"] += 1
                if r["drift"] and not r["unmatched"]:
                    flags.append("DRIFT")
                    stats["DRIFT"] += 1
                if not flags:
                    stats["clean"] += 1
                    if not args.verbose:
                        continue
                rows.append((pname, form["form_name"], r, flags))

    print("=" * 78)
    print("ROW-IDENTITY AUDIT — prose vs checkboxes (READ ONLY, nothing written)")
    print("=" * 78)
    for k in ("table_fields", "clean", "DRIFT", "MEASURE", "UNMATCHED"):
        if stats[k]:
            print(f"  {k:<14}: {stats[k]}")
    print()

    for pname, fname, r, flags in sorted(rows, key=lambda x: (-len(x[3]), x[0], x[1])):
        print(f"  [{','.join(flags) or 'ok'}]  {pname} / {fname} / {r['field']}"
              f"  (mode={r['mode']}, {r['n_cols']} cols)")
        print(f"      checkboxes : {' x '.join(r['key']) if r['key'] else '(none)'}")
        if r["prose"]:
            print(f"      prose      : {' x '.join(r['prose'])}   [from {r['prose_where']}]")
        else:
            print("      prose      : (no tuple typed — nothing to contradict)")
        if r["unmatched"]:
            print(f"      !! prose names {r['unmatched']} — no column can carry it")
        if r["measures_in_key"]:
            print(f"      !! measured results in key: {r['measures_in_key']}")
        print()

    print("  Nothing was changed. Key edits change how rows split or merge —")
    print("  decide per field, then apply via the row-definition editor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
