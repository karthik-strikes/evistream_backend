"""
Bulk upload PDFs + markdowns to S3 and create document records in DB.
Only processes PDFs that have matching markdown folders.
"""

import os
import sys
import json
import hashlib
import logging

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.storage_service import storage_service
from supabase import create_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

# Config
PROJECT_ID = os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    logger.error("PROJECT_ID environment variable is required")
    sys.exit(1)
PDF_DIR = os.environ.get("PDF_SOURCE_DIR", "/data/pdfs")
MD_DIR = os.environ.get("MD_SOURCE_DIR", "/data/markdowns")

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_content_hash(filepath: str) -> str:
    """SHA256 hash of file content."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def find_markdown(md_folder: str) -> str | None:
    """Extract markdown text from the _md.json file in the folder."""
    md_json = os.path.join(md_folder, os.path.basename(md_folder) + ".json")
    if not os.path.exists(md_json):
        # Try any .json file that ends with _md.json
        for f in os.listdir(md_folder):
            if f.endswith("_md.json"):
                md_json = os.path.join(md_folder, f)
                break
        else:
            return None

    try:
        with open(md_json, "r") as f:
            data = json.load(f)
        # The markdown is in data.marker.markdown
        md_text = data.get("marker", {}).get("markdown")
        if md_text:
            return md_text
        # Fallback: try top-level markdown key
        return data.get("markdown")
    except Exception as e:
        logger.warning(f"Failed to read markdown from {md_json}: {e}")
        return None


def get_matching_pairs():
    """Find PDFs that have matching MD folders."""
    pdfs = {f.replace(".pdf", ""): f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")}
    md_folders = {d.replace("_md", ""): d for d in os.listdir(MD_DIR) if os.path.isdir(os.path.join(MD_DIR, d))}

    pairs = []
    for stem, pdf_name in sorted(pdfs.items()):
        if stem in md_folders:
            pairs.append({
                "stem": stem,
                "pdf_path": os.path.join(PDF_DIR, pdf_name),
                "md_folder": os.path.join(MD_DIR, md_folders[stem]),
                "pdf_name": pdf_name,
            })
        else:
            # Try fuzzy match (e.g., Al-Sukhun vs AlSukhun)
            stem_clean = stem.replace("-", "")
            for md_stem, md_folder_name in md_folders.items():
                if md_stem.replace("-", "") == stem_clean:
                    pairs.append({
                        "stem": stem,
                        "pdf_path": os.path.join(PDF_DIR, pdf_name),
                        "md_folder": os.path.join(MD_DIR, md_folder_name),
                        "pdf_name": pdf_name,
                    })
                    break

    return pairs


def check_existing():
    """Get existing document filenames for this project to avoid duplicates."""
    result = supabase.table("documents")\
        .select("filename")\
        .eq("project_id", PROJECT_ID)\
        .execute()
    return {r["filename"] for r in (result.data or [])}


def main():
    pairs = get_matching_pairs()
    logger.info(f"Found {len(pairs)} PDF+MD pairs to upload")

    existing = check_existing()
    logger.info(f"Already uploaded: {len(existing)} documents")

    uploaded = 0
    skipped = 0
    failed = 0

    for pair in pairs:
        pdf_name = pair["pdf_name"]

        if pdf_name in existing:
            logger.info(f"  SKIP (exists): {pdf_name}")
            skipped += 1
            continue

        try:
            # 1. Hash the PDF
            content_hash = get_content_hash(pair["pdf_path"])

            # 2. Upload PDF to S3
            s3_pdf_key = f"pdfs/{PROJECT_ID}/{content_hash}.pdf"
            storage_service.s3_client.upload_file(
                pair["pdf_path"],
                settings.S3_BUCKET,
                s3_pdf_key,
                ExtraArgs={"ContentType": "application/pdf"},
            )

            # 3. Extract and upload markdown
            md_text = find_markdown(pair["md_folder"])
            s3_md_key = None
            if md_text:
                s3_md_key = storage_service.upload_markdown(md_text, PROJECT_ID, content_hash)

            # 4. Insert document record
            doc_data = {
                "project_id": PROJECT_ID,
                "filename": pdf_name,
                "unique_filename": pair["stem"],
                "s3_pdf_path": s3_pdf_key,
                "s3_markdown_path": s3_md_key,
                "content_hash": content_hash,
                "processing_status": "completed" if md_text else "pending",
            }

            result = supabase.table("documents").insert(doc_data).execute()

            if result.data:
                status = "completed" if md_text else "pending (no MD)"
                logger.info(f"  OK [{status}]: {pdf_name}")
                uploaded += 1
            else:
                logger.error(f"  FAIL (DB insert): {pdf_name}")
                failed += 1

        except Exception as e:
            logger.error(f"  FAIL: {pdf_name} — {e}")
            failed += 1

    logger.info(f"\nDone: {uploaded} uploaded, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
