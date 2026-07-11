"""
Sync the 43 oral-cancer paper markdowns from S3 to a local cache.

The full ablation sweep needs each markdown ~5-6 times (one per variant) ×
4 forms. Fetching from S3 each time would (a) hit the network 928+ times,
(b) make runs non-reproducible across machines, (c) waste budget. Instead
we fetch once into `eval/sheets/markdown/{doc_id}.md` and let every later
script read from disk.

Auth: relies on env vars (SUPABASE_URL, SUPABASE_SERVICE_KEY, AWS_*).
Loads them from `eval/.env` or `backend/.env` if present, else expects the
shell to have them.

Usage:
    python -m eval.studies.ablation.fetch_markdown --project-name "Oral Cancer"
    python -m eval.studies.ablation.fetch_markdown --project-id <uuid> --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .config import CACHE_DIR, GT_EXCEL_PATH, MANIFEST_PATH


def _load_env() -> None:
    """Best-effort: read eval/.env then backend/.env into os.environ."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("WARN: python-dotenv not installed; assuming env vars already set")
        return
    for p in (Path("/home/ubuntu/evistream/eval/.env"),
              Path("/home/ubuntu/evistream/backend/.env")):
        if p.exists():
            load_dotenv(p, override=False)


def _supabase_client():
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        sys.exit("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set "
                 "(in eval/.env, backend/.env, or shell env)")
    return create_client(url, key)


def _s3_client():
    import boto3
    region = os.environ.get("AWS_REGION", "us-east-1")
    kwargs = {"region_name": region}
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        kwargs["aws_access_key_id"] = os.environ["AWS_ACCESS_KEY_ID"]
        kwargs["aws_secret_access_key"] = os.environ["AWS_SECRET_ACCESS_KEY"]
    return boto3.client("s3", **kwargs)


def _gt_authors() -> list[str]:
    """Unique GT _gt_author values from the oral-cancer combined sheet."""
    # Reuse the eval loader so we get the same normalization as the rest of the harness.
    sys.path.insert(0, "/home/ubuntu/evistream")
    from eval.engine.core.loader import load_gt_deduped
    df = load_gt_deduped("ALL", excel_path=str(GT_EXCEL_PATH))
    return sorted({str(a).strip() for a in df["_gt_author"] if a})


def _resolve_project_id(supabase, project_name: str | None, project_id: str | None) -> str:
    if project_id:
        return project_id
    if not project_name:
        sys.exit("ERROR: pass either --project-id or --project-name")
    rows = supabase.table("projects").select("id, name").execute().data or []
    matches = [r for r in rows if project_name.lower() in r["name"].lower()]
    if not matches:
        sys.exit(f"ERROR: no project matched name '{project_name}'. "
                 f"Available: {[r['name'] for r in rows]}")
    if len(matches) > 1:
        sys.exit(f"ERROR: multiple projects matched '{project_name}': "
                 f"{[r['name'] for r in matches]}. Use --project-id instead.")
    print(f"  Resolved project: {matches[0]['name']} ({matches[0]['id']})")
    return matches[0]["id"]


def _match_docs_to_gt(docs: list[dict], gt_authors: list[str]) -> list[tuple[str, dict]]:
    """
    Fuzzy-match each GT author to a document (by filename). Returns
    [(gt_author, doc_row), ...]. Drops unmatched.
    Reuses eval/core/matcher._norm + rapidfuzz to stay consistent with the
    rest of the eval harness.
    """
    from eval.engine.core.matcher import _norm
    from rapidfuzz import process, fuzz

    doc_keys = [_norm(d.get("filename") or "") for d in docs]

    matched: list[tuple[str, dict]] = []
    unmatched: list[str] = []
    for author in gt_authors:
        a_norm = _norm(author)
        hit = process.extractOne(
            a_norm, doc_keys, scorer=fuzz.token_sort_ratio, score_cutoff=75,
        )
        if not hit:
            unmatched.append(author)
            continue
        _, _, idx = hit
        matched.append((author, docs[idx]))
    if unmatched:
        print(f"  WARN: {len(unmatched)} GT authors not matched to any document:")
        for a in unmatched:
            print(f"    • {a}")
    return matched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-name", help="case-insensitive substring match in projects.name")
    ap.add_argument("--project-id",   help="exact project UUID (overrides --project-name)")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve + match, but don't download or write")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if local file exists")
    args = ap.parse_args()

    _load_env()

    supabase = _supabase_client()
    project_id = _resolve_project_id(supabase, args.project_name, args.project_id)

    print(f"  Loading GT authors from {GT_EXCEL_PATH}")
    gt_authors = _gt_authors()
    print(f"  Found {len(gt_authors)} unique GT authors")

    print(f"  Fetching documents for project {project_id}")
    docs = (
        supabase.table("documents")
        .select("id, filename, content_hash, s3_markdown_path, processing_status")
        .eq("project_id", project_id)
        .eq("processing_status", "completed")
        .execute()
        .data
        or []
    )
    docs = [d for d in docs if d.get("s3_markdown_path")]
    print(f"  Got {len(docs)} processed documents with s3_markdown_path")

    pairs = _match_docs_to_gt(docs, gt_authors)
    print(f"  Matched {len(pairs)} / {len(gt_authors)} GT authors to documents")

    if args.dry_run:
        print("  --dry-run set; not downloading.")
        for author, doc in pairs[:5]:
            print(f"    {author!r}  →  {doc['filename']!r}  ({doc['s3_markdown_path']})")
        if len(pairs) > 5:
            print(f"    ... and {len(pairs) - 5} more")
        return 0

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    s3 = _s3_client()
    bucket = os.environ.get("S3_BUCKET", "evistream-production")

    manifest: dict[str, dict] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text())

    downloaded = skipped = failed = 0
    for author, doc in pairs:
        doc_id = doc["id"]
        s3_key = doc["s3_markdown_path"]
        local_path = CACHE_DIR / f"{doc_id}.md"

        if local_path.exists() and not args.force:
            existing_md5 = hashlib.md5(local_path.read_bytes()).hexdigest()
            cached = manifest.get(doc_id)
            if cached and cached.get("md5") == existing_md5:
                skipped += 1
                continue

        try:
            obj = s3.get_object(Bucket=bucket, Key=s3_key)
            body = obj["Body"].read()
            local_path.write_bytes(body)
            md5 = hashlib.md5(body).hexdigest()
            manifest[doc_id] = {
                "author":       author,
                "filename":     doc.get("filename"),
                "content_hash": doc.get("content_hash"),
                "s3_key":       s3_key,
                "local_path":   str(local_path),
                "md5":          md5,
                "bytes":        len(body),
            }
            downloaded += 1
            print(f"    ✓ {author!r} → {doc_id} ({len(body):,} bytes)")
        except Exception as e:
            failed += 1
            print(f"    ✗ {author!r} → {s3_key}: {e}")

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    print(f"\n  Done. downloaded={downloaded} skipped={skipped} failed={failed}")
    print(f"  Manifest: {MANIFEST_PATH}")
    print(f"  Markdown cache: {CACHE_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
