"""
EndNote .enlx library parser.

An `.enlx` is a ZIP archive of a *compressed* EndNote library:
  - sdb/sdb.eni       : the reference database (SQLite 3). Table `refs` holds
                        bibliographic metadata; table `file_res` maps a
                        reference to its attached file(s).
  - sdb/pdb.eni       : companion full-text index (unused here).
  - PDF/{hash}/*.pdf  : the attached PDFs, referenced by `file_res.file_path`
                        (a path RELATIVE to the PDF/ folder).

This module is pure stdlib (`zipfile` + `sqlite3`) — no network, no new deps —
so it can be unit-tested against a real `.enlx` offline. It deliberately does
NOT touch S3/Supabase; the importer worker (app/workers/import_tasks.py)
consumes EndNoteRecord objects and drives the existing document pipeline.

Real-world shape this handles (verified against a 30-reference library):
  - a reference with NO attached file  -> record.pdf_path_in_zip is None
    (metadata-only; the worker files it as `needs_pdf`).
  - a reference with MULTIPLE files    -> primary = min(file_pos); extras logged.
  - a reference with an empty DOI       -> record.doi is None.
  - EndNote stores the DOI in `electronic_resource_number`, the journal in
    `secondary_title`, and authors newline-separated in `author`.

DOI/PMID/PMCID mining (added so a PDF-less reference gets the same
open-access fetch chance as a RIS/PubMed import): EndNote's `url` field can
carry several CR/newline-separated links (a DOI resolver link, a direct PDF
link, a PubMed/PMC link, ...), and `electronic_resource_number` can be empty
even when one of those links plainly names the paper's DOI/PMID/PMCID. Every
link is kept (`record.urls`) and mined for whichever identifiers it names
(`_doi_from_urls`, `_extract_pmid`/`_extract_pmcid` via
utils.citation_identifiers) — the importer tries all of them via
fulltext_service before giving up and filing the reference as `needs_pdf`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional

from utils.citation_identifiers import extract_pmcid_from_urls, extract_pmid_from_urls

logger = logging.getLogger(__name__)

_PDF_ROOT = "PDF/"

# Columns we read from `refs` (only those that actually exist are selected).
_REFS_COLUMNS = (
    "id", "title", "author", "year", "abstract", "secondary_title",
    "electronic_resource_number", "pages", "volume", "number", "url",
)

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")


@dataclass
class EndNoteRecord:
    """One EndNote reference, flattened to the fields we care about."""
    ref_id: int
    title: str
    authors: List[str]
    year: Optional[str]
    journal: Optional[str]
    doi: Optional[str]
    abstract: Optional[str]
    pages: Optional[str]
    volume: Optional[str]
    issue: Optional[str]
    url: Optional[str]
    pdf_path_in_zip: Optional[str]           # e.g. "PDF/1234/Foo.pdf", or None
    extra_pdf_paths: List[str] = field(default_factory=list)
    # Every http(s) link in the `url` field (not just the first) — a fallback
    # fetch source, and the raw material _doi_from_urls/pmid/pmcid mine.
    urls: List[str] = field(default_factory=list)
    pmid: Optional[str] = None
    pmcid: Optional[str] = None

    @property
    def has_pdf(self) -> bool:
        return self.pdf_path_in_zip is not None


# --------------------------------------------------------------------------- #
# Value cleaning helpers
# --------------------------------------------------------------------------- #
def _clean(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    return s or None


def _get(row: sqlite3.Row, key: str):
    return row[key] if key in row.keys() else None


def _split_authors(raw) -> List[str]:
    """EndNote stores authors newline-separated ("Last, First\\nLast, First")."""
    if raw is None:
        return []
    parts = [a.strip() for a in str(raw).replace("\r", "\n").split("\n")]
    return [a for a in parts if a]


def _normalize_doi(raw) -> Optional[str]:
    """Extract a bare DOI from whatever the field holds (bare DOI, `doi:...`,
    or a full https://doi.org/... URL). Returns None if none present."""
    if raw is None:
        return None
    m = _DOI_RE.search(str(raw))
    if not m:
        return None
    return m.group(0).rstrip(".,;)")


def _doi_from_urls(urls: List[str]) -> Optional[str]:
    """Same DOI pattern, applied to every link in the `url` field — a
    fallback for the (common) case where `electronic_resource_number` is
    empty but the user's saved link is itself a doi.org URL."""
    for u in urls:
        doi = _normalize_doi(u)
        if doi:
            return doi
    return None


def _first_url(raw) -> Optional[str]:
    """The `url` field can pack several URLs separated by a carriage return —
    keep the first."""
    if raw is None:
        return None
    parts = [p.strip() for p in str(raw).replace("\r", "\n").split("\n")]
    parts = [p for p in parts if p]
    return parts[0] if parts else None


def _split_http_urls(raw) -> List[str]:
    """Every http(s) entry in the (CR/newline-separated) `url` field, in
    order, de-duplicated — the fetch fallback needs all of them, not just
    the first. Non-http(s) entries (e.g. a stale local `file://` path from
    the original desktop library) are dropped; they're not fetchable."""
    if raw is None:
        return []
    parts = [p.strip() for p in str(raw).replace("\r", "\n").split("\n")]
    seen: List[str] = []
    for p in parts:
        if p and p.lower().startswith(("http://", "https://")) and p not in seen:
            seen.append(p)
    return seen


# --------------------------------------------------------------------------- #
# Archive / SQLite reading
# --------------------------------------------------------------------------- #
def _find_sdb_name(zf: zipfile.ZipFile) -> Optional[str]:
    """Locate the reference SQLite db inside the archive (normally sdb/sdb.eni)."""
    for name in zf.namelist():
        if name.replace("\\", "/").split("/")[-1].lower() == "sdb.eni":
            return name
    return None


def parse_enlx(enlx_path: str) -> List[EndNoteRecord]:
    """Parse an `.enlx` into EndNoteRecord objects (metadata + attachment path).

    PDF bytes are NOT read here — open the archive with `zipfile.ZipFile` and
    `zf.read(record.pdf_path_in_zip)` when you need them, so a large library is
    never held entirely in memory. Raises ValueError on a malformed archive.
    """
    if not zipfile.is_zipfile(enlx_path):
        raise ValueError("Not a valid .enlx (expected a ZIP archive).")

    with zipfile.ZipFile(enlx_path) as zf:
        sdb_name = _find_sdb_name(zf)
        if not sdb_name:
            raise ValueError("No sdb.eni reference database found inside the .enlx.")
        archive_names = set(zf.namelist())

        # sqlite3 needs a real file on disk — extract the db to a temp file.
        tmp = tempfile.NamedTemporaryFile(prefix="enlx_sdb_", suffix=".eni", delete=False)
        try:
            tmp.write(zf.read(sdb_name))
            tmp.close()
            return _read_records(tmp.name, archive_names)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def _read_records(db_path: str, archive_names: set) -> List[EndNoteRecord]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # refs_id -> [(file_pos, file_path), ...]
        files_by_ref: dict = {}
        try:
            for row in conn.execute("SELECT refs_id, file_path, file_pos FROM file_res"):
                files_by_ref.setdefault(row["refs_id"], []).append(
                    (row["file_pos"] if row["file_pos"] is not None else 0, row["file_path"])
                )
        except sqlite3.OperationalError:
            logger.warning("[enlx] no file_res table; treating all references as metadata-only")

        cols = {r[1] for r in conn.execute("PRAGMA table_info(refs)")}
        select_cols = ", ".join(c for c in _REFS_COLUMNS if c in cols)
        where = "WHERE trash_state IS NULL OR trash_state = 0" if "trash_state" in cols else ""
        rows = conn.execute(f"SELECT {select_cols} FROM refs {where}").fetchall()
    finally:
        conn.close()

    records: List[EndNoteRecord] = []
    for r in rows:
        ref_id = _get(r, "id")
        primary_path: Optional[str] = None
        extras: List[str] = []
        for _pos, rel_path in sorted(files_by_ref.get(ref_id, []), key=lambda t: t[0]):
            full = _PDF_ROOT + str(rel_path).replace("\\", "/")
            if full not in archive_names:
                logger.warning("[enlx] ref %s points at missing attachment %s", ref_id, full)
                continue
            if primary_path is None:
                primary_path = full
            else:
                extras.append(full)

        raw_url = _get(r, "url")
        urls = _split_http_urls(raw_url)
        doi = _normalize_doi(_get(r, "electronic_resource_number")) or _doi_from_urls(urls)

        records.append(EndNoteRecord(
            ref_id=ref_id,
            title=_clean(_get(r, "title")) or f"EndNote reference {ref_id}",
            authors=_split_authors(_get(r, "author")),
            year=_clean(_get(r, "year")),
            journal=_clean(_get(r, "secondary_title")),
            doi=doi,
            abstract=_clean(_get(r, "abstract")),
            pages=_clean(_get(r, "pages")),
            volume=_clean(_get(r, "volume")),
            issue=_clean(_get(r, "number")),
            url=_first_url(raw_url),
            pdf_path_in_zip=primary_path,
            extra_pdf_paths=extras,
            urls=urls,
            pmid=extract_pmid_from_urls(urls),
            pmcid=extract_pmcid_from_urls(urls),
        ))
    return records


# --------------------------------------------------------------------------- #
# Bridges to the document pipeline (content_hash + metadata sidecar)
# --------------------------------------------------------------------------- #
def content_hash_for_pdf(pdf_bytes: bytes) -> str:
    """SHA-256 of the raw PDF bytes — IDENTICAL to what the browser computes for
    a manual upload (documents.service.ts computeSHA256), so an EndNote import
    dedups against the same PDF uploaded by hand or re-imported later."""
    return hashlib.sha256(pdf_bytes).hexdigest()


def content_hash_for_metadata(record: EndNoteRecord) -> str:
    """Deterministic content_hash for a metadata-only reference (no PDF bytes to
    hash) — keyed on the DOI when present, else the normalized title."""
    key = record.doi or (record.title or "").strip().lower()
    return hashlib.sha256(f"endnote:{key}".encode("utf-8")).hexdigest()


def build_document_content(record: EndNoteRecord, pmc_sections: Optional[list] = None) -> str:
    """Serialize a metadata-only reference to the JSON markdown sidecar — mirrors
    ris_service.build_document_content (pure JSON, no hand-formatted prose)."""
    payload = {
        "source": "endnote",
        "title": record.title,
        "authors": record.authors,
        "journal": record.journal,
        "year": record.year,
        "volume": record.volume,
        "issue": record.issue,
        "pages": record.pages,
        "doi": record.doi,
        "pmid": record.pmid,
        "url": record.url,
        "abstract": record.abstract,
    }
    if pmc_sections:
        payload["fullText"] = pmc_sections
    return json.dumps(payload, indent=2)
