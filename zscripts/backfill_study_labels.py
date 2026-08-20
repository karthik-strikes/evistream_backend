"""Backfill `first_author` / `pub_year` on existing documents.

Every document uploaded before add_study_label.sql has no study identity, so
the UI falls back to the filename — which for an EndNote/RIS import is the
full article title, the exact thing this feature exists to replace.

Only documents that NEED it are touched. A filename that already parses as a
study ID ("Raslan 2021.pdf") produces the right label with no metadata at all
(see utils/study_label.derive_label), so those are skipped rather than burning
a Crossref round-trip to re-derive what we can already read.

Sources, in order, per document:
  1. `doi` on the row      -> Crossref /works/{doi}      (authoritative)
  2. `title` on the row    -> Crossref bibliographic search, similarity-gated
                              by doi_service._crossref_title_lookup — a loose
                              title match would attach a DIFFERENT paper's
                              author to this document, which is worse than
                              leaving it blank.
  3. --from-pdf only: the stored PDF -> the full doi_service cascade (embedded
                              metadata, page-1 text, then a Crossref title
                              search on the markdown heading). This is the one
                              that matters for the legacy corpus: 88 of the 89
                              unlabelled documents have `doi_source IS NULL`,
                              i.e. DOI extraction was never ATTEMPTED on them —
                              they pre-date the DOI pipeline entirely. They are
                              not Crossref failures; nobody ever looked.

This issues no Datalab calls and never re-parses a PDF (--from-pdf downloads
the stored PDF and reads the stored markdown, exactly as
pdf_tasks.backfill_pdf_doi does — that task is mirrored here rather than
enqueued so a corpus-wide sweep is one observable run instead of ~90 job rows).

DRY RUN BY DEFAULT (repo convention — see zscripts/migrate_key_columns.py):
    python zscripts/backfill_study_labels.py                # report only
    python zscripts/backfill_study_labels.py --apply        # write
    python zscripts/backfill_study_labels.py --apply --from-pdf   # + read PDFs
    python zscripts/backfill_study_labels.py --apply --project <uuid>
    python zscripts/backfill_study_labels.py --limit 25
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AWS_SECRETS_NAME", "evistream/production")
os.environ.setdefault("AWS_REGION", "us-east-1")

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from app.database import get_supabase_client  # noqa: E402
from app.services.doi_service import (  # noqa: E402
    _crossref_title_lookup,
    _crossref_year,
    _validate_doi,
    extract_doi,
)
from app.services.storage_service import storage_service  # noqa: E402
from app.services.study_identity_service import identify_study  # noqa: E402
from utils.study_label import (  # noqa: E402
    build_label_map,
    derive_label,
    filename_stem,
    first_surname,
)

# Crossref's polite pool asks for <= 50 requests/second; we are nowhere near
# that, but a small gap keeps a 500-document sweep from looking like a scrape.
CROSSREF_PAUSE_SECONDS = 0.2


def needs_backfill(doc: dict, from_pdf: bool = False) -> bool:
    """True when this row would render as a filename today AND we have somewhere
    to look. With --from-pdf that includes documents carrying no bibliographic
    metadata at all, since the PDF itself is then the source."""
    if doc.get("first_author"):
        return False
    label, _ = derive_label(doc)
    if label:  # filename already yields a study ID, or it is a registry record
        return False
    if from_pdf and doc.get("s3_pdf_path"):
        return True
    return bool(doc.get("doi") or doc.get("title"))


def _stored_markdown(doc: dict) -> Optional[str]:
    """The document's parsed markdown, if it has any. Feeds the cascade's
    last step (Crossref search on the first heading) — worth the fetch,
    because for a scanned PDF it is the only readable text there is."""
    key = doc.get("s3_markdown_path")
    if not key:
        return None
    try:
        # `.bucket`, not `.bucket_name` — an earlier version of this helper used
        # the wrong attribute, and because the failure was swallowed silently it
        # looked like "the cascade found nothing" rather than "the cascade never
        # saw the text". Loud on failure for exactly that reason.
        obj = storage_service.s3_client.get_object(Bucket=storage_service.bucket, Key=key)
        return obj["Body"].read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"      ! markdown fetch failed for {key}: {type(e).__name__}: {e}")
        return None


def lookup_from_pdf(doc: dict, tmp_dir: str) -> tuple[Optional[str], Optional[str], str, Optional[dict]]:
    """Run the full doi_service cascade against the stored PDF.

    Returns (first_author, year, how, extras) where `extras` carries a newly
    discovered doi/title to persist — the whole point of reading the PDF is
    that these documents had neither."""
    local_pdf = os.path.join(tmp_dir, "source.pdf")
    try:
        storage_service.download_to_temp(doc["s3_pdf_path"], local_pdf)
    except Exception:
        return None, None, "no-pdf", None

    try:
        result = extract_doi(
            pdf_path=local_pdf,
            markdown=_stored_markdown(doc),
            blocks_json=None,
        )
    except Exception:
        return None, None, "error", None
    finally:
        try:
            os.remove(local_pdf)
        except OSError:
            pass

    extras = {}
    if result.doi and not doc.get("doi"):
        extras["doi"] = result.doi
        extras["doi_source"] = result.source
    if result.title and not doc.get("title"):
        extras["title"] = result.title
    return result.first_author, result.year, f"pdf:{result.source}", extras


def lookup(doc: dict) -> tuple[str | None, str | None, str]:
    """(first_author, year, how) for one document."""
    doi = (doc.get("doi") or "").strip()
    if doi:
        work = _validate_doi(doi)
        time.sleep(CROSSREF_PAUSE_SECONDS)
        if work:
            return first_surname(work.get("author")), _crossref_year(work), "doi"

    title = (doc.get("title") or "").strip()
    if title:
        hit = _crossref_title_lookup(title)
        time.sleep(CROSSREF_PAUSE_SECONDS)
        if hit:
            return first_surname(hit.get("author")), _crossref_year(hit), "title"

    return None, None, "miss"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to the database")
    ap.add_argument(
        "--use-llm",
        action="store_true",
        help="last resort: read the paper's own first page for author + year "
             "(only for documents no cheaper source resolved)",
    )
    ap.add_argument(
        "--from-pdf",
        action="store_true",
        help="also read the stored PDF for documents with no DOI and no title "
             "(the legacy corpus — DOI extraction was never attempted on them)",
    )
    ap.add_argument("--project", help="restrict to one project_id")
    ap.add_argument("--limit", type=int, default=0, help="cap documents examined")
    args = ap.parse_args()

    sb = get_supabase_client()
    query = sb.table("documents").select(
        "id,project_id,ref_id,filename,title,doi,doi_source,pmid,nct_id,"
        "first_author,pub_year,study_label,s3_pdf_path,s3_markdown_path"
    )
    if args.project:
        query = query.eq("project_id", args.project)
    docs = query.limit(args.limit or 5000).execute().data or []

    todo = [d for d in docs if needs_backfill(d, args.from_pdf)]
    print(f"{len(docs)} documents examined, {len(todo)} need a study label\n")

    filled = 0
    tmp_dir = tempfile.mkdtemp(prefix="evistream_studylabel_")
    for i, doc in enumerate(todo, 1):
        extras: Optional[dict] = None
        author, year, how = lookup(doc)
        # The PDF is the last resort, not the first: a DOI already on the row is
        # authoritative and costs one request, while reading a PDF costs a
        # download plus a parse.
        if not author and args.from_pdf and doc.get("s3_pdf_path"):
            author, year, how, extras = lookup_from_pdf(doc, tmp_dir)

        # Truly last: these documents have no DOI anywhere and no Crossref
        # match, so the paper's own first page is the only remaining source.
        if not author and args.use_llm:
            markdown = _stored_markdown(doc)
            llm_author, llm_year = identify_study(markdown)
            if llm_author:
                author, year, how = llm_author, llm_year or year, "first-page"

        stem = filename_stem(doc.get("filename"))[:52]
        if not author:
            print(f"  [{i}/{len(todo)}] MISS ({how})  {stem}")
            continue

        label = f"{author} {year}" if year else author
        print(f"  [{i}/{len(todo)}] {label:<24} <- {how:<11} {stem}")
        filled += 1
        if args.apply:
            update = {"first_author": author}
            if year:
                update["pub_year"] = year
            # A DOI/title discovered while reading the PDF is worth keeping on
            # its own — duplicate detection and the DOI link both use it.
            update.update(extras or {})
            sb.table("documents").update(update).eq("id", doc["id"]).execute()

    print(f"\n{filled}/{len(todo)} resolved" + ("" if args.apply else "  (dry run — pass --apply to write)"))

    # Show what the affected projects will actually render, suffixes included —
    # a label map is a per-project computation, so a per-row report cannot show
    # the a/b outcome that the user will see.
    if filled and not args.apply:
        print("\nProjects touched (labels shown are the CURRENT stored state):")
        for project_id in sorted({d["project_id"] for d in todo}):
            rows = [d for d in docs if d["project_id"] == project_id]
            labels = build_label_map(rows)
            unresolved = sum(1 for d in rows if labels[str(d["id"])] == filename_stem(d.get("filename")))
            print(f"  {project_id}  {len(rows)} docs, {unresolved} still falling back to the filename")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
