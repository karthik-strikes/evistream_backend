"""
Celery tasks for reference imports.

Two importers, one shared shape (dedup → create `documents` row → hand PDFs to the
existing Datalab pipeline, or file PDF-less references as `needs_pdf`):

  * import_endnote_library — parse a `.enlx` (metadata + attached PDFs) and import.
  * import_citations       — take parsed RIS/DOI citations and fetch each one's
                             open-access PDF (Unpaywall) or PMC full text, reusing
                             app/services/fulltext_service.py.

Dedup (`_is_duplicate`) is by content_hash OR DOI OR PMID within the project, so
an exact file AND a same-paper-different-file both count as duplicates. Progress
streams over Redis pub/sub (ws_jobs:{job_id}) via CeleryLogBroadcaster.

Both importers resolve a PDF through the same tiered search (`_resolve_oa_pdf`):
Unpaywall/PMC via whatever identifiers the source record carries (DOI, PMID,
PMCID), then — only if that comes up empty — the record's own raw URL(s) as a
last resort (fulltext_service.fetch_direct_pdf), since not every OA copy is
indexed by Unpaywall. EndNote records rarely carry a structured PMID/PMCID, so
endnote_service mines both out of any PubMed/PMC link in the `url` field.
"""

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import zipfile

from supabase import create_client

from app.config import settings
from app.models.enums import JobStatus, JobType
from app.services import endnote_service, fulltext_service, ris_service
from app.services.storage_service import storage_service
from app.workers.celery_app import celery_app
from app.workers.log_broadcaster import CeleryLogBroadcaster

logger = logging.getLogger(__name__)

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _is_duplicate(project_id: str, content_hash: str, doi=None, pmid=None) -> bool:
    """A reference is a duplicate if an existing document in the project has the
    same file bytes (content_hash), the same DOI, or the same PMID (same paper,
    different file/source — e.g. a PubMed import and an EndNote reference for
    the same PMID)."""
    hit = (
        supabase.table("documents").select("id")
        .eq("project_id", project_id).eq("content_hash", content_hash).limit(1).execute()
    )
    if hit.data:
        return True
    if doi:
        by_doi = (
            supabase.table("documents").select("id")
            .eq("project_id", project_id).eq("doi", doi).limit(1).execute()
        )
        if by_doi.data:
            return True
    if pmid:
        by_pmid = (
            supabase.table("documents").select("id")
            .eq("project_id", project_id).eq("pmid", pmid).limit(1).execute()
        )
        if by_pmid.data:
            return True
    return False


# `documents.filename` is varchar(255); `documents.title` is text. Real paper
# titles do exceed 255 chars (two references in an 82-reference EndNote library
# did, Aug 11 2026), and the raw title used to go straight into `filename` —
# Postgres rejected the row with 22001 and the reference was counted as an
# `errors` and silently dropped from the import. Clamp what goes into the
# varchar column only; `title` keeps the full string.
_FILENAME_MAX = 255


def _document_filename(title) -> str:
    return (title or "")[:_FILENAME_MAX]


def _enqueue_pdf_processing(document_id: str, project_id: str, user_id: str, title: str) -> None:
    """Create a jobs row and dispatch the shared Datalab pipeline for a PDF-backed doc."""
    job = supabase.table("jobs").insert({
        "user_id": user_id,
        "project_id": project_id,
        "job_type": JobType.PDF_PROCESSING.value,
        "status": JobStatus.PENDING.value,
        "progress": 0,
        "input_data": {"document_id": document_id, "filename": title},
    }).execute()
    if not job.data:
        logger.warning("Failed to create processing job for import; doc %s stuck pending", document_id)
        return
    pdf_job_id = job.data[0]["id"]
    from app.workers.pdf_tasks import process_pdf_document

    task = process_pdf_document.delay(document_id=document_id, job_id=str(pdf_job_id))
    supabase.table("jobs").update({"celery_task_id": task.id}).eq("id", pdf_job_id).execute()


def _run_async(coro):
    """Run an async fulltext_service coroutine from a sync Celery task."""
    try:
        return asyncio.run(coro)
    except Exception:
        logger.warning("Async full-text fetch failed", exc_info=True)
        return None


# Ceiling on the *whole* multi-tier OA search for one reference (Unpaywall/PMC
# candidates, then the raw-URL fallback). Libraries are imported one reference
# at a time, so a single slow/hanging host must not be able to stall the rest
# of a large batch for minutes — this caps the damage to one skipped reference.
OA_FETCH_BUDGET = 60  # seconds


async def _resolve_oa_pdf(doi=None, pmid=None, pmcid=None, fallback_urls=None):
    """Every OA tier for one reference, best-trust-first: Unpaywall/PMC via
    whatever identifiers are known (identical to fetch_open_access_pdf), then
    the reference's own raw URL(s) — e.g. a link pasted into EndNote's `url`
    field, or a RIS `UR` tag — as a last resort for papers Unpaywall doesn't
    index. Bounded so one hanging host can't stall a whole library import."""
    async def _run():
        if doi or pmid or pmcid:
            pdf = await fulltext_service.fetch_open_access_pdf(doi, pmid, pmcid)
            if pdf:
                return pdf
        return await fulltext_service.fetch_direct_pdf(fallback_urls)

    try:
        return await asyncio.wait_for(_run(), OA_FETCH_BUDGET)
    except asyncio.TimeoutError:
        logger.debug(f"[import] OA fetch exceeded {OA_FETCH_BUDGET}s for doi={doi} pmid={pmid}")
        return None


# --------------------------------------------------------------------------- #
# EndNote (.enlx) import
# --------------------------------------------------------------------------- #
@celery_app.task(bind=True, name="import_endnote_library", max_retries=1, default_retry_delay=15)
def import_endnote_library(self, job_id: str, project_id: str, s3_key: str, user_id: str, ref_ids=None):
    """Download, parse, and import an EndNote .enlx library. `ref_ids` (optional
    list) restricts the import to the references the user selected in the
    preview; None imports all. Returns counters."""
    broadcaster = CeleryLogBroadcaster(job_id)
    tmp_dir = tempfile.mkdtemp(prefix=f"enlx_{job_id}_")
    local_enlx = os.path.join(tmp_dir, "library.enlx")
    counters = {"total": 0, "with_pdf": 0, "needs_pdf": 0, "duplicates": 0, "errors": 0}

    try:
        supabase.table("jobs").update(
            {"status": JobStatus.PROCESSING.value, "progress": 5}
        ).eq("id", job_id).execute()

        storage_service.download_to_temp(s3_key, local_enlx)
        records = endnote_service.parse_enlx(local_enlx)
        if ref_ids is not None:
            wanted = {int(x) for x in ref_ids}
            records = [r for r in records if r.ref_id in wanted]
        counters["total"] = len(records)
        broadcaster.progress(10, f"Parsed {len(records)} references from the EndNote library")
        if not records:
            broadcaster.warning("No references found in the library.")

        with zipfile.ZipFile(local_enlx) as zf:
            for idx, rec in enumerate(records):
                try:
                    _import_one_record(zf, rec, project_id, user_id, counters)
                except Exception as e:  # one bad reference must not sink the batch
                    counters["errors"] += 1
                    logger.exception("Failed to import EndNote ref %s", rec.ref_id)
                    broadcaster.warning(f"Skipped '{(rec.title or '')[:60]}': {e}")
                pct = 10 + int(85 * (idx + 1) / max(1, len(records)))
                broadcaster.progress(pct, f"Imported {idx + 1}/{len(records)} references")

        supabase.table("jobs").update(
            {"status": JobStatus.COMPLETED.value, "progress": 100, "result_data": counters}
        ).eq("id", job_id).execute()
        broadcaster.data(counters, "EndNote import complete")
        broadcaster.success(
            f"Imported {counters['with_pdf']} with PDF, {counters['needs_pdf']} metadata-only, "
            f"{counters['duplicates']} duplicate(s) skipped."
        )
        return counters

    except Exception as e:
        logger.exception("EndNote import job %s failed", job_id)
        supabase.table("jobs").update(
            {"status": JobStatus.FAILED.value, "error_message": str(e)[:500]}
        ).eq("id", job_id).execute()
        broadcaster.error(f"Import failed: {e}")
        raise
    finally:
        try:
            storage_service.delete_object(s3_key)  # temp upload; not needed after parse
        except Exception:
            logger.debug("Could not delete temp import object %s", s3_key, exc_info=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _import_one_record(zf, rec, project_id: str, user_id: str, counters: dict) -> None:
    if rec.has_pdf:
        pdf_bytes = zf.read(rec.pdf_path_in_zip)
        content_hash = endnote_service.content_hash_for_pdf(pdf_bytes)
    else:
        # No PDF embedded in the library — try every OA tier the RIS/DOI
        # importer uses (Unpaywall via DOI, Europe PMC via PMID/PMCID), then
        # the reference's own raw URL(s) as a last resort, before giving up
        # and landing the reference as metadata-only `needs_pdf`. PMID/PMCID
        # come from mining the `url` field (endnote_service.py) — EndNote
        # itself has no structured PMID field the way PubMed/RIS do.
        pdf_bytes = (
            _run_async(_resolve_oa_pdf(rec.doi, rec.pmid, rec.pmcid, rec.urls))
            if (rec.doi or rec.pmid or rec.pmcid or rec.urls) else None
        )
        # content_hash_for_pdf is plain sha256, so a PDF fetched here dedups
        # against the same PDF embedded in a library or uploaded by hand.
        content_hash = (
            endnote_service.content_hash_for_pdf(pdf_bytes) if pdf_bytes
            else endnote_service.content_hash_for_metadata(rec)
        )

    if _is_duplicate(project_id, content_hash, rec.doi, rec.pmid):
        counters["duplicates"] += 1
        return

    if pdf_bytes is not None:
        s3_pdf_path = storage_service.upload_pdf(pdf_bytes, project_id, content_hash)
        result = supabase.table("documents").insert({
            "project_id": project_id,
            "filename": _document_filename(rec.title),
            "unique_filename": None,
            "content_hash": content_hash,
            "s3_pdf_path": s3_pdf_path,
            "s3_markdown_path": None,
            "processing_status": "pending",
            "title": rec.title,
            "labels": [],
            "source_type": "endnote",
            "doi": rec.doi,
            "pmid": rec.pmid,
        }).execute()
        if not result.data:
            raise RuntimeError("Failed to insert document row.")
        _enqueue_pdf_processing(result.data[0]["id"], project_id, user_id, rec.title)
        counters["with_pdf"] += 1
        return

    # No PDF anywhere — PMC structured full text (mirrors the RIS path) is
    # still worth trying when a PMID was found, since it needs no PDF at all.
    pmc_sections = _run_async(fulltext_service.fetch_pmc_fulltext(rec.pmid)) if rec.pmid else None
    content = endnote_service.build_document_content(rec, pmc_sections)
    s3_markdown_path = storage_service.upload_markdown(content, project_id, content_hash)
    result = supabase.table("documents").insert({
        "project_id": project_id,
        "filename": _document_filename(rec.title),
        "unique_filename": None,
        "content_hash": content_hash,
        "s3_pdf_path": None,
        "s3_markdown_path": s3_markdown_path,
        # PMC body → real full text. Else an abstract is thin but readable →
        # metadata_only, which a reviewer may accept (identical evidence to an
        # abstract-only PubMed record, so it must get identical rights).
        # Nothing but a citation → there is literally no text to extract from,
        # so attaching a PDF is the only remedy and needs_pdf is right.
        "processing_status": (
            "completed" if pmc_sections
            else "metadata_only" if (rec.abstract or "").strip()
            else "needs_pdf"
        ),
        "title": rec.title,
        "labels": [],
        "source_type": "endnote",
        "doi": rec.doi,
        "pmid": rec.pmid,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to insert metadata-only document row.")
    if pmc_sections:
        counters["with_pdf"] += 1  # full text obtained (via PMC), just not a PDF
    else:
        counters["needs_pdf"] += 1


# --------------------------------------------------------------------------- #
# RIS / DOI import (open-access auto-fetch — "Tier C")
# --------------------------------------------------------------------------- #
@celery_app.task(bind=True, name="import_citations", max_retries=1, default_retry_delay=15)
def import_citations(self, job_id: str, project_id: str, records, user_id: str):
    """Import parsed RIS/DOI citations, fetching each one's open-access full text
    (Unpaywall PDF → PMC) where available; the rest land as `needs_pdf`."""
    broadcaster = CeleryLogBroadcaster(job_id)
    records = records or []
    counters = {"total": len(records), "with_pdf": 0, "needs_pdf": 0, "duplicates": 0, "errors": 0}

    try:
        supabase.table("jobs").update(
            {"status": JobStatus.PROCESSING.value, "progress": 5}
        ).eq("id", job_id).execute()
        broadcaster.progress(5, f"Importing {counters['total']} references")

        for idx, rec in enumerate(records):
            try:
                _import_one_citation(rec, project_id, user_id, counters, broadcaster)
            except Exception as e:
                counters["errors"] += 1
                label = (rec.get("title") or rec.get("doi") or "")[:60]
                logger.exception("Failed to import citation %s", idx)
                broadcaster.warning(f"Skipped '{label}': {e}")
            pct = 5 + int(90 * (idx + 1) / max(1, counters["total"]))
            broadcaster.progress(pct, f"Processed {idx + 1}/{counters['total']} references")

        supabase.table("jobs").update(
            {"status": JobStatus.COMPLETED.value, "progress": 100, "result_data": counters}
        ).eq("id", job_id).execute()
        broadcaster.data(counters, "Citation import complete")
        broadcaster.success(
            f"{counters['with_pdf']} full text fetched, {counters['needs_pdf']} need a PDF, "
            f"{counters['duplicates']} duplicate(s) skipped."
        )
        return counters

    except Exception as e:
        logger.exception("Citation import job %s failed", job_id)
        supabase.table("jobs").update(
            {"status": JobStatus.FAILED.value, "error_message": str(e)[:500]}
        ).eq("id", job_id).execute()
        broadcaster.error(f"Import failed: {e}")
        raise


def _import_one_citation(rec: dict, project_id: str, user_id: str, counters: dict, broadcaster) -> None:
    doi = (rec.get("doi") or "").strip() or None
    pmid = (rec.get("pmid") or "").strip() or None
    pmcid = (rec.get("pmcid") or "").strip() or None
    urls = rec.get("urls") or []
    title = (rec.get("title") or doi or "Untitled reference").strip()

    # DOI/PMID-first dedup: a same-paper document already in the project is a duplicate.
    if (doi or pmid) and _is_duplicate(project_id, ris_service.content_hash_for_metadata(rec), doi, pmid):
        counters["duplicates"] += 1
        return

    # 1) Open-access PDF via Unpaywall/PMC (doi/pmid/pmcid), then the citation's
    # own raw URL(s) as a last resort → the full Datalab pipeline.
    pdf_bytes = (
        _run_async(_resolve_oa_pdf(doi, pmid, pmcid, urls))
        if (doi or pmid or pmcid or urls) else None
    )
    if pdf_bytes:
        content_hash = hashlib.sha256(pdf_bytes).hexdigest()
        if _is_duplicate(project_id, content_hash, doi, pmid):
            counters["duplicates"] += 1
            return
        s3_pdf_path = storage_service.upload_pdf(pdf_bytes, project_id, content_hash)
        result = supabase.table("documents").insert({
            "project_id": project_id,
            "filename": _document_filename(title),
            "unique_filename": None,
            "content_hash": content_hash,
            "s3_pdf_path": s3_pdf_path,
            "s3_markdown_path": None,
            "processing_status": "pending",
            "title": title,
            "labels": [],
            "source_type": "ris",
            "doi": doi,
            "pmid": pmid,
        }).execute()
        if not result.data:
            raise RuntimeError("Failed to insert document row.")
        _enqueue_pdf_processing(result.data[0]["id"], project_id, user_id, title)
        counters["with_pdf"] += 1
        return

    # 2) PMC structured full text (when a PMID is present), else metadata-only.
    pmc_sections = _run_async(fulltext_service.fetch_pmc_fulltext(pmid)) if pmid else None
    content_hash = ris_service.content_hash_for_metadata(rec)
    if _is_duplicate(project_id, content_hash, doi, pmid):
        counters["duplicates"] += 1
        return
    content = ris_service.build_document_content(rec, pmc_sections)
    s3_markdown_path = storage_service.upload_markdown(content, project_id, content_hash)
    result = supabase.table("documents").insert({
        "project_id": project_id,
        "filename": _document_filename(title),
        "unique_filename": None,
        "content_hash": content_hash,
        "s3_pdf_path": None,
        "s3_markdown_path": s3_markdown_path,
        # PMC body → real full text. Else an abstract is thin-but-readable
        # (metadata_only, acceptable); a bare citation has nothing to read at all.
        "processing_status": (
            "completed" if pmc_sections
            else "metadata_only" if (rec.get("abstract") or "").strip()
            else "needs_pdf"
        ),
        "title": title,
        "labels": [],
        "source_type": "ris",
        "doi": doi,
        "pmid": pmid,
    }).execute()
    if not result.data:
        raise RuntimeError("Failed to insert metadata-only document row.")
    if pmc_sections:
        counters["with_pdf"] += 1  # full text obtained (via PMC), just not a PDF
    else:
        counters["needs_pdf"] += 1
