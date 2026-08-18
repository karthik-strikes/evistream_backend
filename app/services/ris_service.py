"""
RIS citation parser (stdlib only).

RIS is a simple line-based citation interchange format:

    TY  - JOUR
    TI  - Some article title
    AU  - Smith, John
    AU  - Doe, Jane
    PY  - 2021
    DO  - 10.1016/j.foo.2021.01.001
    JO  - Journal of Things
    AB  - Abstract text…
    ER  -

One record per `TY … ER` block. This parser returns plain dicts (JSON-safe, so
they pass straight through Celery) and is pure-stdlib so it can be unit-tested
offline. It carries citation METADATA only — RIS never contains PDFs, so the
importer (app/workers/import_tasks.py:import_citations) fetches open-access PDFs
via fulltext_service at import time.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import List, Optional

from utils.citation_identifiers import extract_pmcid_from_urls, extract_pmid_from_urls

_LINE = re.compile(r"^([A-Z0-9]{2})\s{1,}-\s?(.*)$")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")

# RIS tag -> our scalar field (first non-empty wins).
_SCALAR = {
    "TI": "title", "T1": "title",
    "PY": "year", "Y1": "year",
    "DO": "doi",
    "JO": "journal", "JF": "journal", "T2": "journal", "JA": "journal",
    "AB": "abstract", "N2": "abstract",
}
# RIS tag -> our list field.
_LIST = {"AU": "authors", "A1": "authors", "A2": "authors", "A3": "authors", "UR": "_urls"}


def _norm_doi(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = _DOI_RE.search(str(raw))
    return m.group(0).rstrip(".,;)") if m else None


def _doi_from_urls(urls: List[str]) -> Optional[str]:
    """Same DOI pattern, applied to every `UR` link — a fallback for records
    whose `DO` tag is missing but whose URL is itself a doi.org link."""
    for u in urls:
        doi = _norm_doi(u)
        if doi:
            return doi
    return None


def _http_urls(raw_urls: List[str]) -> List[str]:
    """The fetch fallback and identifier mining only make sense for real
    links — drop anything that isn't http(s), de-duplicated, order kept."""
    seen: List[str] = []
    for u in raw_urls:
        if u and u.lower().startswith(("http://", "https://")) and u not in seen:
            seen.append(u)
    return seen


def _new_record() -> dict:
    return {
        "title": None, "authors": [], "year": None, "journal": None,
        "doi": None, "pmid": None, "url": None, "abstract": None, "_urls": [],
    }


def parse_ris(text: str) -> List[dict]:
    """Parse RIS text into a list of citation dicts. Skips records with neither
    a title nor a DOI (nothing we could act on)."""
    records: List[dict] = []
    cur: Optional[dict] = None
    last_scalar_key: Optional[str] = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m:
            # Continuation of a wrapped scalar value.
            if cur is not None and last_scalar_key and cur.get(last_scalar_key):
                cur[last_scalar_key] = f"{cur[last_scalar_key]} {line.strip()}".strip()
            continue

        tag, value = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = _new_record()
            records.append(cur)
            last_scalar_key = None
            continue
        if cur is None:  # malformed file with no leading TY — start a record anyway
            cur = _new_record()
            records.append(cur)
        if tag == "ER":
            cur = None
            last_scalar_key = None
            continue

        if tag in _SCALAR:
            key = _SCALAR[tag]
            if key == "year":
                yr = re.search(r"\d{4}", value)
                value = yr.group(0) if yr else value
            elif key == "doi":
                value = _norm_doi(value)
            if value and not cur.get(key):
                cur[key] = value
            last_scalar_key = key
        elif tag in _LIST:
            key = _LIST[tag]
            if value:
                cur[key].append(value)
            last_scalar_key = None
        elif tag == "AN" and value.isdigit() and len(value) <= 8 and not cur.get("pmid"):
            cur["pmid"] = value  # PubMed RIS sometimes puts the PMID in AN
            last_scalar_key = None
        else:
            last_scalar_key = None

    out: List[dict] = []
    for r in records:
        raw_urls = r.pop("_urls", [])
        r["url"] = raw_urls[0] if raw_urls else None
        urls = _http_urls(raw_urls)
        r["urls"] = urls
        if not r.get("doi"):
            r["doi"] = _doi_from_urls(urls)
        if not r.get("pmid"):
            r["pmid"] = extract_pmid_from_urls(urls)
        r["pmcid"] = extract_pmcid_from_urls(urls)
        if r.get("title") or r.get("doi"):
            out.append(r)
    return out


def build_document_content(record: dict, pmc_sections: Optional[list] = None) -> str:
    """Serialize a metadata-only citation to the JSON markdown sidecar — mirrors
    pubmed_service.build_document_content."""
    payload = {
        "source": "ris",
        "title": record.get("title"),
        "authors": record.get("authors"),
        "journal": record.get("journal"),
        "year": record.get("year"),
        "doi": record.get("doi"),
        "pmid": record.get("pmid"),
        "url": record.get("url"),
        "abstract": record.get("abstract"),
    }
    if pmc_sections:
        payload["fullText"] = pmc_sections
    return json.dumps(payload, indent=2)


def content_hash_for_metadata(record: dict) -> str:
    """Deterministic content_hash for a citation with no fetched PDF — keyed on
    the DOI when present, else the normalized title."""
    key = record.get("doi") or (record.get("title") or "").strip().lower()
    return hashlib.sha256(f"ris:{key}".encode("utf-8")).hexdigest()
