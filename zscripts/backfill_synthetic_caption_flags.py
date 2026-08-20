"""Label already-stored quotes that came from an AI figure description.

Why a backfill is needed at all
------------------------------
`source_linker.enrich_extraction_results` now marks any quote that lands inside
one of Datalab's machine-generated figure descriptions
(`source_location.synthetic_caption = true`). But enrichment runs at extraction
time, so rows already in `extraction_results` carry a `source_location` written
before the flag existed. Without this script the fix covers future extractions
only, and the affected historical citations stay invisible — which is exactly
the failure mode we set out to remove.

Measured exposure at the time of writing (sample of 400 AI results, 5,755
quotes): 18 quotes (0.3%) across 11 documents land inside a caption, e.g. the
value `placebo` grounded in "Line graph showing postoperative pain reduction
over time (30, 60, 90, 120 min post-op) for Placebo (N=65)…". Rare, but each
one is a citation that reads like the paper and isn't.

Safety
------
· Dry run by default. `--apply` is required to write.
· Purely additive: sets `synthetic_caption: true` inside an existing
  `source_location`. No value, quote, status, or location offset is touched, so
  a reviewer's recorded data cannot change — only its provenance label.
· Never removes the flag. If a row already carries it, the row is skipped, so
  re-running is idempotent and cannot thrash.
· Per-row try/except with counted-and-printed failures; one unreadable markdown
  cannot abort the run.
· Only `extraction_type = 'ai'` rows. A human reviewer's quote was typed by a
  person reading the PDF, so a string match against a caption would be
  coincidence, not provenance.

Usage
-----
    python zscripts/backfill_synthetic_caption_flags.py --project-id <uuid>
    python zscripts/backfill_synthetic_caption_flags.py --project-id <uuid> --apply
    python zscripts/backfill_synthetic_caption_flags.py --all-projects
"""

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
from app.services.storage_service import storage_service  # noqa: E402
from utils.synthetic_captions import find_caption_spans, overlaps_caption  # noqa: E402

#: Quotes are truncated before matching: a very long source_text may have been
#: normalized on the way in, and the leading window is enough to place it.
MATCH_WINDOW = 200


def iter_cells(obj: Any):
    """Yield every {value, source_text} envelope, including inside table rows."""
    if isinstance(obj, dict):
        if "value" in obj and "source_text" in obj:
            yield obj
            if isinstance(obj.get("value"), list):
                for row in obj["value"]:
                    if isinstance(row, dict):
                        for cell in row.values():
                            yield from iter_cells(cell)
        else:
            for value in obj.values():
                yield from iter_cells(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_cells(value)


def flag_cells(extracted: Dict[str, Any], markdown: str, spans) -> int:
    """Mutate `extracted` in place, flagging caption-sourced quotes.

    Returns how many cells were newly flagged.
    """
    changed = 0
    for cell in iter_cells(extracted):
        loc = cell.get("source_location")
        if not isinstance(loc, dict) or loc.get("synthetic_caption") is True:
            continue
        quote = cell.get("source_text")
        if not isinstance(quote, str):
            continue
        quote = quote.strip()
        if not quote or quote.upper() in ("NR", "NA"):
            continue

        needle = quote[:MATCH_WINDOW]
        # Prefer the stored offsets when they still line up with the markdown —
        # they are what the flag would have used at extraction time. Fall back
        # to locating the quote, since older rows may predate offset storage.
        start = loc.get("start_char")
        end = loc.get("end_char")
        if not (isinstance(start, int) and isinstance(end, int)
                and markdown[start:end].strip()[:40] == needle.strip()[:40]):
            start = markdown.find(needle)
            if start == -1:
                continue
            end = start + len(needle)

        if overlaps_caption(spans, start, end):
            loc["synthetic_caption"] = True
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project-id", help="Project UUID to process")
    group.add_argument("--all-projects", action="store_true", help="Process every project")
    parser.add_argument("--apply", action="store_true", help="Actually write to Supabase")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N results")
    args = parser.parse_args()

    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    query = sb.table("extraction_results").select(
        "id,project_id,document_id,extracted_data"
    ).eq("extraction_type", "ai").order("created_at")
    if args.project_id:
        query = query.eq("project_id", args.project_id)
    if args.limit:
        query = query.limit(args.limit)
    rows = query.execute().data or []

    mode = "APPLY" if args.apply else "DRY RUN"
    scope = args.project_id or "ALL PROJECTS"
    print(f"\n=== Synthetic-caption flag backfill [{mode}] — {scope} ===")
    print(f"{len(rows)} AI extraction result(s)\n")

    # Markdown + caption spans are per document and reused across results.
    cache: Dict[str, Tuple[str, List[Tuple[int, int]]]] = {}

    def doc_spans(document_id: str):
        if document_id not in cache:
            doc = sb.table("documents").select("s3_markdown_path").eq(
                "id", document_id).execute().data
            key = doc[0]["s3_markdown_path"] if doc else None
            markdown = ""
            if key:
                markdown = storage_service.s3_client.get_object(
                    Bucket=settings.S3_BUCKET, Key=key
                )["Body"].read().decode("utf-8")
            cache[document_id] = (markdown, find_caption_spans(markdown) if markdown else [])
        return cache[document_id]

    stats = Counter()
    total_cells = 0
    failures = []

    for row in rows:
        try:
            markdown, spans = doc_spans(row["document_id"])
            if not markdown or not spans:
                stats["no_captions_in_doc"] += 1
                continue
            extracted = row.get("extracted_data") or {}
            changed = flag_cells(extracted, markdown, spans)
            if not changed:
                stats["already_clean"] += 1
                continue
            total_cells += changed
            if args.apply:
                sb.table("extraction_results").update(
                    {"extracted_data": extracted}
                ).eq("id", row["id"]).execute()
                stats["updated"] += 1
            else:
                stats["would_update"] += 1
            print(f"  - {row['id']}: {changed} quote(s) flagged")
        except Exception as err:
            stats["failed"] += 1
            failures.append((row["id"], str(err)))
            print(f"  - {row['id']}: FAILED ({err})")

    print(f"\n--- Summary [{mode}] ---")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")
    print(f"  quotes {'flagged' if args.apply else 'to flag'}: {total_cells}")
    if failures:
        print(f"\n  {len(failures)} failure(s):")
        for row_id, err in failures:
            print(f"    · {row_id}: {err}")
    if not args.apply:
        print("\n  Dry run — nothing written. Re-run with --apply to commit.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
