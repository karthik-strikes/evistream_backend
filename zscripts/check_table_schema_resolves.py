"""Does every live table field still reach its intended pipeline?

Read-only. Run this before and after any rename of `extraction_strategy` values
or the `anchor_columns` key. `build_schema_classes` selects the pipeline by exact
string match and falls through to single-pass SILENTLY on an unrecognised value,
so a half-finished rename is invisible in production — this is the check that
makes it visible.

Reports, per live form with a compiled schema_def:
  · table fields whose extraction_strategy does not resolve   → would silently downgrade
  · keyed pipelines with no composite key                     → rows unidentifiable
  · which spelling each field currently stores                → migration progress

Usage: python zscripts/check_table_schema_resolves.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from utils.table_schema import (  # noqa: E402
    AGENTIC,
    DISCOVER_THEN_FILL,
    field_key_columns,
    resolve_strategy,
)


def main() -> int:
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    forms = sb.table("forms").select(
        "id, form_name, status, schema_def"
    ).not_.is_("schema_def", "null").execute().data

    stats = Counter()
    unresolved: list[str] = []
    keyless: list[str] = []
    stored_spelling = Counter()

    for form in forms:
        for sig in (form["schema_def"].get("signatures") or []):
            for field in (sig.get("output_fields") or []):
                if not (field.get("subform_fields") or []):
                    continue
                stats["table_fields"] += 1
                label = f"{form['form_name']} · {field.get('name')}"

                if "key_columns" in field and "anchor_columns" in field:
                    stored_spelling["both (migrated, dual-written)"] += 1
                elif "key_columns" in field:
                    stored_spelling["key_columns only"] += 1
                elif "anchor_columns" in field:
                    stored_spelling["anchor_columns only (not migrated)"] += 1
                else:
                    stored_spelling["neither"] += 1

                raw = field.get("extraction_strategy")
                if raw is None:
                    stats["strategy_unset"] += 1
                    continue
                pipeline = resolve_strategy(raw)
                if pipeline is None:
                    stats["strategy_UNRESOLVED"] += 1
                    unresolved.append(f"{label}  strategy={raw!r}")
                    continue
                stats[f"pipeline:{pipeline}"] += 1
                if pipeline in (DISCOVER_THEN_FILL, AGENTIC) and not field_key_columns(field):
                    stats["keyed_but_NO_KEY"] += 1
                    keyless.append(label)

    print("=" * 74)
    print("LIVE TABLE FIELD RESOLUTION")
    print("=" * 74)
    for k in sorted(stats):
        print(f"  {k:<34} {stats[k]}")
    print("\nstored key spelling:")
    for k, v in stored_spelling.most_common():
        print(f"  {k:<40} {v}")

    problems = stats["strategy_UNRESOLVED"] + stats["keyed_but_NO_KEY"]
    if unresolved:
        print("\nUNRESOLVED STRATEGY — would silently fall back to single-pass:")
        for u in unresolved:
            print("  " + u)
    if keyless:
        print("\nKEYED PIPELINE WITH NO COMPOSITE KEY:")
        for k in keyless:
            print("  " + k)

    print("\n" + ("PASS — every live table field resolves." if problems == 0
                  else f"FAIL — {problems} field(s) need attention."))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
