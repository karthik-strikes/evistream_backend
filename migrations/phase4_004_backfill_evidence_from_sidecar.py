#!/usr/bin/env python3
"""Completes phase4_004_document_evidence_gate.sql.

Evidence sufficiency is decided by facts that live in each document's JSON
sidecar in S3, which SQL cannot read:

  * ctgov  → status.hasResults   a results-posted trial carries per-arm outcomes,
                                 participant flow and adverse events — richer
                                 than most PDFs, so it is genuinely `completed`
  * others → fullText            PMC article body ⇒ `completed`
             abstract            thin but readable ⇒ `metadata_only` (a reviewer
                                 may accept it)
             neither             nothing to read at all ⇒ `needs_pdf`, where
                                 attaching a PDF is the only remedy

The SQL half therefore had to classify bluntly, which produced two errors this
script corrects:

  1. results-posted trials were held back as `metadata_only`
  2. EndNote/RIS records WITH an abstract were left as `needs_pdf`, which denied
     them the acceptance that an identically-thin PubMed record was given —
     same evidence must get the same rights

Idempotent and safe to re-run. Only ever moves a document toward the status its
stored evidence supports; never touches anything already `completed`.

Run:
    cd backend
    AWS_SECRETS_NAME=evistream/production AWS_REGION=us-east-1 \
      /home/ubuntu/miniconda3/envs/topics/bin/python3.11 \
      migrations/phase4_004_backfill_evidence_from_sidecar.py [--apply]

Without --apply it reports what it would change and writes nothing.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# This file lives in migrations/, so put the backend root on the path before any
# app.* import. load_secrets() must run FIRST — app.config reads its values from
# the environment, which the loader populates from AWS Secrets Manager. Same
# ordering as repair_decomposition.py and app/main.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.storage_service import storage_service  # noqa: E402

# Statuses this script is allowed to reconsider. `completed` is deliberately
# excluded — it is never demoted here.
MUTABLE = ("metadata_only", "needs_pdf")


def correct_status(source_type: str, payload: dict) -> tuple[str, str]:
    """The status this document should have, judged from what is actually stored."""
    if source_type == "ctgov":
        if (payload.get("status") or {}).get("hasResults"):
            return "completed", "status.hasResults=true"
        return "metadata_only", "registration only, no posted results"

    full_text = payload.get("fullText")
    if isinstance(full_text, list) and full_text:
        return "completed", f"fullText: {len(full_text)} PMC sections"

    # The abstract key differs by source: pubmed_service writes `abstractText`,
    # endnote_service and ris_service write `abstract`. Check both — reading only
    # one silently reports "no text at all" for the other and would demote a
    # perfectly readable record to needs_pdf.
    abstract = ""
    for key in ("abstract", "abstractText"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            abstract = value.strip()
            break
    if abstract:
        return "metadata_only", f"abstract only ({len(abstract)} chars)"

    return "needs_pdf", "citation only — no text at all"


def main() -> int:
    apply = "--apply" in sys.argv
    supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    rows = (
        supabase.table("documents")
        .select("id, source_type, s3_markdown_path, processing_status, nct_id, pmid, filename")
        .in_("processing_status", list(MUTABLE))
        .execute()
        .data
        or []
    )

    print(f"{len(rows)} document(s) in {MUTABLE}\n")
    changes, unchanged, broken = [], 0, []

    for d in rows:
        key = d.get("s3_markdown_path")
        label = d.get("nct_id") or d.get("pmid") or (d.get("filename") or "")[:44]
        if not key:
            broken.append((d, "no s3_markdown_path"))
            continue

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            storage_service.download_to_temp(key, tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as e:  # one unreadable sidecar must not sink the run
            broken.append((d, f"{type(e).__name__}: {e}"))
            continue
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        target, why = correct_status(d.get("source_type") or "", payload)
        current = d["processing_status"]
        if target == current:
            unchanged += 1
            print(f"  ok       {d['source_type']:<7} {label:<46} {current} — {why}")
        else:
            changes.append((d, target))
            print(f"  CHANGE   {d['source_type']:<7} {label:<46} {current} -> {target} — {why}")

    print(f"\n→ {len(changes)} to change, {unchanged} already correct, {len(broken)} unreadable")
    for d, err in broken:
        print(f"   ! {d['id']}: {err}")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to commit.")
        return 0

    for d, target in changes:
        update = {"processing_status": target}
        # The approval flag only means anything for metadata_only; clear it when
        # leaving that state so it can't linger and silently pre-approve later.
        if target != "metadata_only":
            update["metadata_extraction_approved"] = False
        supabase.table("documents").update(update).eq("id", d["id"]).execute()

    print(f"\nApplied: {len(changes)} document(s) reclassified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
