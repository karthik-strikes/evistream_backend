"""Force a fresh Datalab parse for every PDF in one project, then let our own
vision pass read the figures.

Why this exists
---------------
Datalab's synthetic image captions were written into the stored markdown as
prose, and 36% of them quoted numbers read off a chart. Those flags are now off
(`DISABLE_IMAGE_INFO` in pdf_processor), but the response cache key is
`{endpoint}{format}::{sha256-of-pdf}` — the flags are NOT in it. So a plain
reprocess re-serves the old captioned payload (and `_stitch_split_cache` will
even rebuild one from the pre-merge `marker_md`/`marker_json` halves).

Hence: evict every cache entry for these PDFs first, then reprocess. That sha256
is exactly `documents.content_hash`, verified against the live cache, so the
eviction is precise — no other project's parses are touched.

Deleting ALL endpoint variants per hash is required, not tidiness: leave
`marker_md` behind and the legacy/stitch fallback quietly serves the captioned
parse again and the whole run is a no-op that still costs a reprocess.

Dry-run by default. Pass --execute to act.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

import diskcache  # noqa: E402
from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.storage_service import storage_service  # noqa: E402

#: The worker runs with WorkingDirectory=/home/ubuntu/evistream/backend and
#: PDFProcessor defaults cache_dir to the relative string "cache", so this — not
#: the repo-root `cache/` that pdf_processor.CACHE_DIR computes — is the live one.
DEFAULT_CACHE_DIR = BACKEND / "cache"

ANALGESICS = "dc4dfd37-0a73-4822-b113-fce614797b82"

#: A document with no stored PDF has nothing to re-parse.
REPARSEABLE = {"completed", "failed"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project-id", default=ANALGESICS)
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N documents (pilot a single paper first)")
    ap.add_argument("--only-doc", default=None,
                    help="comma-separated document ids; only these are touched")
    ap.add_argument("--exclude", default="", help="comma-separated document ids to leave alone")
    ap.add_argument("--execute", action="store_true",
                    help="actually evict the cache and enqueue; otherwise dry-run")
    args = ap.parse_args()

    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    proj = sb.table("projects").select("id,name,user_id").eq(
        "id", args.project_id).execute().data
    if not proj:
        print(f"No project {args.project_id}")
        return 1
    project = proj[0]

    docs = sb.table("documents").select(
        "id,filename,content_hash,processing_status,s3_pdf_path"
    ).eq("project_id", args.project_id).order("created_at").execute().data

    targets = [
        d for d in docs
        if d.get("s3_pdf_path") and d.get("content_hash")
        and d["processing_status"] in REPARSEABLE
    ]
    if args.only_doc:
        wanted = {x.strip() for x in args.only_doc.split(",") if x.strip()}
        targets = [d for d in targets if d["id"] in wanted]
    excluded = {x.strip() for x in args.exclude.split(",") if x.strip()}
    if excluded:
        targets = [d for d in targets if d["id"] not in excluded]
    if args.limit:
        targets = targets[: args.limit]

    skipped = len(docs) - len([
        d for d in docs
        if d.get("s3_pdf_path") and d.get("content_hash")
        and d["processing_status"] in REPARSEABLE
    ])

    cache = diskcache.Cache(args.cache_dir)
    by_hash: dict[str, list[str]] = defaultdict(list)
    for key in cache.iterkeys():
        _ep, _sep, file_hash = str(key).partition("::")
        if file_hash:
            by_hash[file_hash].append(str(key))

    print(f"Project : {project['name']} ({project['id']})")
    print(f"Cache   : {args.cache_dir} ({len(by_hash)} distinct PDFs cached)")
    print(f"Docs    : {len(docs)} total, {len(targets)} to reparse, {skipped} skipped (no PDF / not parseable)")
    print()

    # `documents.content_hash` is usually sha256 of the PDF, which is exactly what
    # CacheManager.make_key hashes — but not always. 8 of 81 Analgesics documents
    # carried a content_hash that did not match their stored file, their entries
    # were missed, and the stitch/legacy fallback re-served the captioned parse.
    # So hash the object we would actually submit, and evict under both.
    def true_hash(doc: dict) -> str | None:
        try:
            body = storage_service.s3_client.get_object(
                Bucket=settings.S3_BUCKET, Key=doc["s3_pdf_path"]
            )["Body"].read()
            return hashlib.sha256(body).hexdigest()
        except Exception as err:                      # noqa: BLE001
            print(f"  WARN could not hash {doc['filename'][:40]}: {err}")
            return None

    to_delete: list[str] = []
    no_cache = 0
    mismatched = 0
    for doc in targets:
        keys = list(by_hash.get(doc["content_hash"], []))
        real = true_hash(doc)
        if real and real != doc["content_hash"]:
            mismatched += 1
            keys.extend(by_hash.get(real, []))
        to_delete.extend(keys)
        if not keys:
            no_cache += 1

    print(f"Cache entries to evict : {len(to_delete)} across {len(targets) - no_cache} documents")
    print(f"Documents with no cache: {no_cache} (they re-parse fresh anyway)")
    print(f"content_hash != file hash: {mismatched} (evicted under both)")
    print()

    if not args.execute:
        for doc in targets[:5]:
            keys = by_hash.get(doc["content_hash"], [])
            eps = ", ".join(sorted(k.split("::")[0] for k in keys)) or "-none-"
            print(f"  DRY  {doc['filename'][:44]:44s} {eps}")
        if len(targets) > 5:
            print(f"  ...  and {len(targets) - 5} more")
        print("\nDry run. Re-run with --execute to evict and enqueue.")
        return 0

    for key in to_delete:
        cache.delete(key)
    print(f"Evicted {len(to_delete)} cache entries.")

    from app.models.enums import JobStatus, JobType
    from app.workers.pdf_tasks import process_pdf_document

    queued = 0
    for doc in targets:
        try:
            # figures_* cleared so the document detail page reads "pending"
            # rather than showing last run's tables while the new parse lands.
            sb.table("documents").update({
                "processing_status": "pending",
                "processing_error": None,
                "figures_status": None,
                "figures_error": None,
            }).eq("id", doc["id"]).execute()

            job = sb.table("jobs").insert({
                "user_id": project["user_id"],
                "project_id": args.project_id,
                "job_type": JobType.PDF_PROCESSING.value,
                "status": JobStatus.PENDING.value,
                "progress": 0,
                "input_data": {
                    "document_id": doc["id"],
                    "filename": doc["filename"],
                    "mode": "figure_reparse",
                },
            }).execute().data[0]

            task = process_pdf_document.delay(document_id=doc["id"], job_id=job["id"])
            sb.table("jobs").update({"celery_task_id": task.id}).eq("id", job["id"]).execute()
            queued += 1
        except Exception as err:                      # noqa: BLE001 — per-doc isolation
            print(f"  FAILED to enqueue {doc['filename'][:40]}: {err}")

    print(f"Queued {queued} of {len(targets)} documents on the pdf_processing queue (worker concurrency 2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
