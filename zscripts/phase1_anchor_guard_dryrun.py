"""Phase 1 dry run — what the anchor measurement guard would change on live forms.

Applies `core.generators.signature_gen.is_measured_result_column` to every table
field's persisted `anchor_columns` and prints the diff. WRITES NOTHING.

Input: a JSON array of
    {form_id, form_name, schema_name, field_name, anchor_columns, subform_fields}
either as a plain .json file, or as a saved Supabase-MCP tool-result wrapper
(the payload is unwrapped automatically).

Usage:
    python zscripts/phase1_anchor_guard_dryrun.py <path>
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.generators.signature_gen import (  # noqa: E402
    _auto_detect_key_columns,
    is_measured_result_column,
)


def load_rows(path: str) -> list:
    """Accept either a bare JSON array or a saved MCP tool-result wrapper."""
    raw = open(path, encoding="utf-8").read().strip()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(f"{path} is not JSON")

    # Bare array of field dicts.
    if isinstance(doc, list):
        return doc

    # MCP wrapper: {"result": "...<untrusted-data-X>\n[{\"payload\": \"[...]\"}]\n..."}
    if isinstance(doc, dict) and "result" in doc:
        text = doc["result"]
        start = text.find("[{")
        end = text.rfind("}]")
        if start == -1 or end == -1:
            raise SystemExit("Could not locate the JSON array inside the MCP wrapper")
        inner = json.loads(text[start:end + 2])
        if inner and isinstance(inner[0], dict) and "payload" in inner[0]:
            return json.loads(inner[0]["payload"])
        return inner

    raise SystemExit("Unrecognised input shape")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rows = load_rows(sys.argv[1])

    changed, numeric_kept, clean, no_anchors = [], [], 0, []

    for r in rows:
        anchors = list(r.get("anchor_columns") or [])
        cols = list(r.get("subform_fields") or [])
        by_name = {
            c.get("field_name"): c
            for c in cols
            if isinstance(c, dict) and c.get("field_name")
        }

        if not anchors:
            no_anchors.append(r)
            continue

        stripped = [a for a in anchors if is_measured_result_column(by_name.get(a, {}))]
        kept_numeric = [
            a for a in anchors
            if a not in stripped
            and (by_name.get(a, {}).get("field_type") or "").lower() == "number"
        ]

        if stripped:
            corrected = [a for a in anchors if a not in stripped]
            # Mirror the runtime guard: if the strip empties the anchor set, the
            # LLM path raises and falls back to the keyword heuristic.
            fallback = None
            if not corrected:
                fallback = sorted(_auto_detect_key_columns(cols))
                corrected = fallback
            changed.append({
                **{k: r.get(k) for k in ("form_id", "form_name", "schema_name", "field_name")},
                "before": sorted(anchors),
                "after": sorted(corrected),
                "stripped": sorted(stripped),
                "n_cols": len(cols),
                "used_fallback": fallback is not None,
                "value_cols_before": len(cols) - len(anchors),
                "value_cols_after": len(cols) - len(corrected),
            })
        else:
            clean += 1
            if kept_numeric:
                numeric_kept.append({
                    "form_name": r.get("form_name"),
                    "field_name": r.get("field_name"),
                    "numeric_anchors": sorted(kept_numeric),
                })

    print("=" * 78)
    print("PHASE 1 DRY RUN — anchor measurement guard")
    print("=" * 78)
    print(f"table fields inspected : {len(rows)}")
    print(f"  would CHANGE         : {len(changed)}")
    print(f"  already clean        : {clean}")
    print(f"  no anchors (skipped) : {len(no_anchors)}")
    print()

    if changed:
        print("-" * 78)
        print("FIELDS THAT WOULD CHANGE")
        print("-" * 78)
        for c in changed:
            print(f"\n{c['form_name']}  ·  field={c['field_name']}  ({c['n_cols']} cols)")
            print(f"  schema_name : {c['schema_name']}")
            print(f"  form_id     : {c['form_id']}")
            print(f"  REMOVE      : {c['stripped']}")
            print(f"  before      : {c['before']}")
            print(f"  after       : {c['after']}")
            print(
                f"  value cols  : {c['value_cols_before']} -> {c['value_cols_after']}"
                + ("   [heuristic fallback used]" if c["used_fallback"] else "")
            )

    if numeric_kept:
        print()
        print("-" * 78)
        print("NUMERIC ANCHORS KEPT (review — legitimate identity, or a miss?)")
        print("-" * 78)
        for n in numeric_kept:
            print(f"  {n['form_name']} · {n['field_name']} : {n['numeric_anchors']}")

    if no_anchors:
        print()
        print("-" * 78)
        print("NO ANCHOR_COLUMNS (not touched by this phase)")
        print("-" * 78)
        for r in no_anchors:
            print(f"  {r.get('form_name')} · {r.get('field_name')}")

    print()
    print("Nothing was written. Distinct forms affected:",
          len({c["form_id"] for c in changed}))


if __name__ == "__main__":
    main()
