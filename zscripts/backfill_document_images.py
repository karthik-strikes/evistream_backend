"""Backfill S3 image objects for documents parsed before images were stored.

What this does
--------------
For each document in the target project, recovers the figure bytes Datalab
already extracted and writes them to S3 as one object per image under
`images/{project_id}/{content_hash}/{filename}`, then records `image_count`.

Where the bytes come from (in priority order, cheapest first)
------------------------------------------------------------
1. **The blocks sidecar already in S3.** `blocks/{project_id}/{hash}.json`
   embeds a per-block `images: {filename: base64}` map — roughly half of every
   sidecar's bytes. This is the primary source: no Datalab call, no local
   filesystem dependency, and it covers 212 of 287 documents bucket-wide.
2. **This box's local Datalab cache.** `backend/output/{hash16}_md/*.json`
   holds the whole /convert response including `marker.images`. Covers a
   further 47 documents whose blocks sidecar was never uploaded.
3. Nothing — reported as needing a Datalab re-call, which this script does NOT
   do. Re-parsing is billed per page, so that stays an explicit separate
   decision rather than something a backfill quietly spends money on.

Safety
------
· Dry run by default. `--apply` is required to write to S3 or Supabase.
· Idempotent: `upload_images` HEADs each key first, so a re-run re-counts
  rather than re-uploads, and a run interrupted halfway can simply be repeated.
· Per-document try/except: one unreadable sidecar cannot abort the run, and
  failures are counted AND printed, never swallowed.
· Never deletes or rewrites the blocks sidecar. The sidecar is currently the
  *only* copy of these bytes for most documents, so stripping the duplicate
  base64 out of it is only safe after this backfill has been verified — and is
  deliberately not part of this script.
· Writes only `image_count`; no other document column is touched.

Usage
-----
    python zscripts/backfill_document_images.py --project-id <uuid>
    python zscripts/backfill_document_images.py --project-id <uuid> --apply
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()

from supabase import create_client  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.storage_service import storage_service  # noqa: E402

LOCAL_CACHE_DIR = Path(__file__).resolve().parent.parent / "output"


def collect_images_from_blocks(node: Any, out: Dict[str, str]) -> None:
    """Walk a Datalab block tree, merging every per-block `images` map into `out`.

    Images hang off individual blocks (`children[].children[].images`), not off
    the root, so this has to recurse rather than read one well-known key.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "images" and isinstance(value, dict):
                for name, b64 in value.items():
                    if isinstance(b64, str):
                        out.setdefault(str(name), b64)
            else:
                collect_images_from_blocks(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_images_from_blocks(item, out)


def images_from_s3_blocks(blocks_key: str) -> Dict[str, str]:
    """Pull the embedded base64 images out of a blocks sidecar object in S3."""
    response = storage_service.s3_client.get_object(
        Bucket=settings.S3_BUCKET, Key=blocks_key
    )
    tree = json.loads(response["Body"].read().decode("utf-8"))
    images: Dict[str, str] = {}
    collect_images_from_blocks(tree, images)
    return images


def images_from_local_cache(content_hash: str) -> Dict[str, str]:
    """Pull images out of this box's cached Datalab response, if present.

    The cache directory is named by the first 16 hex chars of the PDF's sha256,
    which is the prefix of `documents.content_hash`.
    """
    if not content_hash:
        return {}
    dump = LOCAL_CACHE_DIR / f"{content_hash[:16]}_md" / f"{content_hash[:16]}_md.json"
    if not dump.exists():
        return {}
    with open(dump) as fh:
        payload = json.load(fh)
    for section in ("marker", "marker_json"):
        images = (payload.get(section) or {}).get("images")
        if isinstance(images, dict) and images:
            return {str(k): v for k, v in images.items() if isinstance(v, str)}
    return {}


def resolve_images(doc: Dict[str, Any]) -> tuple[Dict[str, str], str]:
    """Find this document's image bytes. Returns (images, source-label)."""
    blocks_key: Optional[str] = doc.get("s3_blocks_path")
    if blocks_key:
        try:
            images = images_from_s3_blocks(blocks_key)
            if images:
                return images, "s3_blocks"
        except Exception as err:
            print(f"    ! could not read blocks sidecar {blocks_key}: {err}")

    images = images_from_local_cache(doc.get("content_hash") or "")
    if images:
        return images, "local_cache"

    return {}, "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True, help="Project UUID to backfill")
    parser.add_argument(
        "--apply", action="store_true", help="Actually write to S3 and Supabase"
    )
    parser.add_argument("--limit", type=int, default=0, help="Process at most N documents")
    args = parser.parse_args()

    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

    query = (
        sb.table("documents")
        .select("id,filename,project_id,content_hash,s3_blocks_path,s3_markdown_path,image_count")
        .eq("project_id", args.project_id)
        .not_.is_("s3_markdown_path", "null")
        .order("created_at")
    )
    docs = query.execute().data or []
    if args.limit:
        docs = docs[: args.limit]

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\n=== Image backfill [{mode}] — project {args.project_id} ===")
    print(f"{len(docs)} document(s) with markdown\n")

    stats = Counter()
    total_images = 0
    failures = []

    for doc in docs:
        label = f"{doc.get('filename') or doc['id']}"[:60]
        content_hash = doc.get("content_hash")
        if not content_hash:
            print(f"  - {label}: SKIP (no content_hash — S3 prefix undefined)")
            stats["skipped_no_hash"] += 1
            continue

        try:
            images, source = resolve_images(doc)
        except Exception as err:
            print(f"  - {label}: FAILED ({err})")
            stats["failed"] += 1
            failures.append((label, str(err)))
            continue

        if not images:
            print(f"  - {label}: no image bytes available (needs Datalab re-parse)")
            stats["unavailable"] += 1
            continue

        if not args.apply:
            print(f"  - {label}: would store {len(images)} image(s) from {source}")
            stats["would_store"] += 1
            total_images += len(images)
            continue

        try:
            keys = storage_service.upload_images(images, doc["project_id"], content_hash)
            sb.table("documents").update({"image_count": len(keys)}).eq(
                "id", doc["id"]
            ).execute()
            print(f"  - {label}: stored {len(keys)} image(s) from {source}")
            stats["stored"] += 1
            total_images += len(keys)
        except Exception as err:
            print(f"  - {label}: FAILED during upload ({err})")
            stats["failed"] += 1
            failures.append((label, str(err)))

    print(f"\n--- Summary [{mode}] ---")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")
    print(f"  image objects {'to store' if not args.apply else 'stored'}: {total_images}")
    if failures:
        print(f"\n  {len(failures)} failure(s):")
        for label, err in failures:
            print(f"    · {label}: {err}")
    if not args.apply:
        print("\n  Dry run — nothing written. Re-run with --apply to commit.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
