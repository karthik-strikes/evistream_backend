"""
Best-effort DOI / bibliographic-identity extraction.

Not every uploaded PDF carries a resolvable DOI (scans, theses, grey
literature, arXiv preprints without a registered DOI). This module runs a
best-effort cascade and returns doi=None when nothing resolves —
`content_hash` remains the only *guaranteed* identity key (see
migrations/add_content_hash.sql). DOI is an additional layer for duplicate
detection, never a replacement for it, and is never used to auto-merge
documents (the same DOI can legitimately be an author-accepted manuscript vs.
the published version).

Cascade (cheapest / most reliable first), each step degrading gracefully:
  1. Embedded PDF metadata (Info dict + XMP) via PyMuPDF — offline, instant.
  2. Regex over page-1 text only (a DOI is almost always printed on the
     masthead/footer; scanning the whole document risks matching a
     *different* paper's DOI out of the references section). Prefers
     PyMuPDF's native embedded text layer — read directly from the PDF's
     content stream, so for born-digital PDFs it is exact, not a
     transcription. Only falls back to Datalab's blocks JSON / markdown
     (its own vision-model OCR) when the PDF has no usable text layer (e.g.
     a scan) — OCR can silently mis-transcribe a single character, which is
     far more damaging for a DOI than for body text: it turns into a
     different *real* DOI rather than an obviously garbled string. (Caught
     in production: Datalab OCR'd a printed "cjt053" as "cjw053" — a valid
     but wrong DOI belonging to an unrelated article — while PyMuPDF's
     native text layer had the correct string.)
  3. Crossref bibliographic-title lookup, last resort.
  4. Validate the winning candidate against Crossref's /works/{doi} and pull
     its canonical title.

Every step is wrapped defensively — this must never raise, and must never
block or fail the PDF parse pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import fitz  # PyMuPDF
import requests

logger = logging.getLogger(__name__)

CROSSREF_API_BASE = "https://api.crossref.org"
# Crossref's "polite pool" gives faster, more reliable service to requests
# that identify a contact — https://api.crossref.org/swagger-ui/index.html
USER_AGENT = "eviStreams/1.0 (mailto:noreply@evistreams.com)"
REQUEST_TIMEOUT = 8  # seconds; never let a DOI lookup stall the parse pipeline

# 10.NNNN(.NNNN...)/suffix — the DOI registrant-code pattern (ISO 26324).
DOI_REGEX = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_TRAILING_PUNCT = re.compile(r"[.,;:)\]]+$")  # doesn't belong to the identifier itself

# Similarity floor for accepting a Crossref title-search hit as a match.
TITLE_SIMILARITY_THRESHOLD = 0.72


@dataclass
class DoiResult:
    doi: Optional[str] = None
    source: str = "none"  # metadata | text | crossref | none
    title: Optional[str] = None


def _clean_doi(candidate: str) -> str:
    return _TRAILING_PUNCT.sub("", candidate.strip())


def _first_doi_in_text(text: str) -> Optional[str]:
    if not text:
        return None
    match = DOI_REGEX.search(text)
    return _clean_doi(match.group(0)) if match else None


def _extract_from_pdf_metadata(pdf_path: str) -> Optional[str]:
    """Step 1 — embedded metadata. Offline, instant. Publishers sometimes
    place the DOI in the Subject/Keywords/Title Info fields, or (more
    reliably) in XMP metadata under prism:doi / dc:identifier / Crossmark."""
    try:
        doc = fitz.open(pdf_path)
        try:
            info = doc.metadata or {}
            for field in ("subject", "keywords", "title"):
                found = _first_doi_in_text(info.get(field) or "")
                if found:
                    return found

            xml_xref = doc.xref_xml_metadata()
            if xml_xref:
                xml_bytes = doc.xref_stream(xml_xref)
                if xml_bytes:
                    found = _first_doi_in_text(xml_bytes.decode("utf-8", errors="ignore"))
                    if found:
                        return found
        finally:
            doc.close()
    except Exception as e:
        logger.debug(f"[doi] metadata extraction failed: {e}")
    return None


def _native_page1_text(pdf_path: str) -> Optional[str]:
    """Page 1's embedded text layer, read directly via PyMuPDF — not OCR.
    For born-digital PDFs this is exact; returns None (rather than empty
    string) on any failure or if the PDF has no text layer, so callers can
    tell "no text layer" apart from "text layer, but no DOI in it"."""
    try:
        doc = fitz.open(pdf_path)
        try:
            if len(doc) == 0:
                return None
            text = doc[0].get_text()
            return text if text and text.strip() else None
        finally:
            doc.close()
    except Exception as e:
        logger.debug(f"[doi] native page-1 text extraction failed: {e}")
        return None


def _ocr_page1_text(blocks_json: Optional[dict], markdown: Optional[str]) -> str:
    """Fallback plain text of page 1 via Datalab's own (vision-model) parse
    — used only when the PDF has no native text layer (e.g. a scan)."""
    if blocks_json and blocks_json.get("children"):
        first_page = blocks_json["children"][0]
        htmls = []

        def _walk(block: dict):
            html = block.get("html")
            if html:
                htmls.append(html)
            for child in block.get("children") or []:
                _walk(child)

        _walk(first_page)
        if htmls:
            return re.sub(r"<[^>]+>", " ", " ".join(htmls))

    # Markdown has no reliable page markers guaranteed, so just take a
    # generous leading slice — mastheads are always near the top.
    return (markdown or "")[:4000]


def _extract_from_page1_text(
    pdf_path: Optional[str], blocks_json: Optional[dict], markdown: Optional[str]
) -> Optional[str]:
    """Step 2 — regex the first page's text only. Prefers the PDF's own
    (exact) text layer; falls back to Datalab's OCR'd text only when there's
    no native layer to read (see module docstring for why OCR is untrusted
    here)."""
    native_text = _native_page1_text(pdf_path) if pdf_path else None
    if native_text:
        found = _first_doi_in_text(native_text)
        if found:
            return found
        # Has a real text layer but no DOI matched in it — trust that over
        # falling back to OCR, which could invent a false match.
        return None

    return _first_doi_in_text(_ocr_page1_text(blocks_json, markdown))


def _first_markdown_heading(markdown: Optional[str]) -> Optional[str]:
    if not markdown:
        return None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return None


def _crossref_title_lookup(title: str) -> Optional[dict]:
    """Step 3 — bibliographic search by title. Returns the raw Crossref
    'message.items[0]' dict if the top hit clears the similarity floor."""
    try:
        resp = requests.get(
            f"{CROSSREF_API_BASE}/works",
            params={"query.bibliographic": title, "rows": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        if not items:
            return None
        candidate = items[0]
        candidate_title = (candidate.get("title") or [""])[0]
        similarity = SequenceMatcher(None, title.lower(), candidate_title.lower()).ratio()
        if similarity < TITLE_SIMILARITY_THRESHOLD:
            return None
        return candidate
    except Exception as e:
        logger.debug(f"[doi] Crossref title lookup failed: {e}")
        return None


def _validate_doi(doi: str) -> Optional[dict]:
    """Step 4 — resolve the winning candidate to confirm it's real and pull
    its canonical title."""
    try:
        resp = requests.get(
            f"{CROSSREF_API_BASE}/works/{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("message")
    except Exception as e:
        logger.debug(f"[doi] Crossref validation failed for {doi}: {e}")
        return None


def extract_doi(
    pdf_path: Optional[str] = None,
    markdown: Optional[str] = None,
    blocks_json: Optional[dict] = None,
) -> DoiResult:
    """Best-effort DOI cascade. Never raises — always returns a DoiResult,
    with doi=None/source="none" if nothing resolves."""
    doi: Optional[str] = None
    source = "none"

    if pdf_path:
        doi = _extract_from_pdf_metadata(pdf_path)
        if doi:
            source = "metadata"

    if not doi:
        doi = _extract_from_page1_text(pdf_path, blocks_json, markdown)
        if doi:
            source = "text"

    candidate_title: Optional[str] = None
    if not doi:
        heading = _first_markdown_heading(markdown)
        if heading:
            crossref_hit = _crossref_title_lookup(heading)
            if crossref_hit:
                hit_doi = crossref_hit.get("DOI")
                if hit_doi:
                    doi = hit_doi
                    source = "crossref"
                    candidate_title = (crossref_hit.get("title") or [None])[0]

    if not doi:
        return DoiResult(doi=None, source="none", title=None)

    # Validate + fetch canonical title (skip the round-trip if the Crossref
    # search already gave us both).
    title = candidate_title
    if not title:
        validated = _validate_doi(doi)
        if validated:
            title = (validated.get("title") or [None])[0]
        # An embedded/printed DOI that doesn't resolve is still kept — it may
        # be a typo or a non-Crossref registrant (e.g. DataCite) — just
        # without a canonical title.

    return DoiResult(doi=doi.lower(), source=source, title=title)
