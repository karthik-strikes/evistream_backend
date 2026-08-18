"""
PubMed (NCBI E-utilities) proxy + normalizer.

Thin wrapper over the public PubMed E-utilities API (no API key required):
https://eutils.ncbi.nlm.nih.gov/entrez/eutils . Three calls compose one
article lookup:
  - esearch  — term -> list of PMIDs (+ total count)
  - esummary — PMID(s) -> lightweight citation metadata (title, authors,
               journal, pubdate, DOI, pub types) — NO abstract
  - efetch   — PMID -> full record; only this call returns the abstract.
    Verified live: `rettype=abstract&retmode=text` gives a clean,
    NCBI-formatted plain-text block (title/authors/affiliations/structured
    abstract/DOI/PMID) — used as-is rather than hand-parsed, since
    abstract structure (labeled OBJECTIVES/METHODS/RESULTS/CONCLUSION vs.
    unstructured) varies per article and regex-splitting it would be fragile.

Critical gotcha (verified live): an invalid/unknown PMID makes esummary
return HTTP 200 with an embedded `{"error": "cannot get document summary"}`
on that uid — NOT a 404. Every call site here must check for that field;
relying on the HTTP status code alone silently treats "not found" as success.

See backend/app/services/clinical_trials_service.py for the parallel
ClinicalTrials.gov integration this mirrors — same per-call httpx client
(no shared singleton), same cache_service usage, same pure-JSON storage
philosophy (build_document_content just json.dumps's the normalized record,
no hand-formatted prose — see that module's docstring for why).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status

from app.services.cache_service import cache_service

logger = logging.getLogger(__name__)

PUBMED_API_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# NCBI etiquette: tool + email identify us; polite, not enforced (no key
# required for light use — see E-utilities docs).
TOOL_NAME = "eviStreams"
CONTACT_EMAIL = "noreply@evistreams.com"
USER_AGENT = "eviStreams/1.0 (mailto:noreply@evistreams.com)"
REQUEST_TIMEOUT = 15

PMID_RE = re.compile(r"^\d{1,8}$")

PUBMED_CACHE_TTL = 12 * 60 * 60  # 12 hours, mirrors CT_CACHE_TTL


def _etiquette_params() -> dict:
    return {"tool": TOOL_NAME, "email": CONTACT_EMAIL}


async def _get_client() -> httpx.AsyncClient:
    """Per-call client, mirroring the house style (see app/api/v1/auth.py
    and clinical_trials_service._get_client) — no shared singleton."""
    return httpx.AsyncClient(
        base_url=PUBMED_API_BASE,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
    )


def _extract_doi(esummary_doc: dict) -> Optional[str]:
    for aid in esummary_doc.get("articleids", []) or []:
        if aid.get("idtype") == "doi":
            return aid.get("value")
    return None


def normalize_summary(pmid: str, doc: dict) -> dict:
    """Flatten one esummary result doc into our lightweight schema — used
    for search-result-list rows. No abstract (esummary doesn't have one)."""
    authors = [a.get("name") for a in doc.get("authors", []) or [] if a.get("name")]
    pubdate = doc.get("pubdate") or ""
    year = pubdate.split(" ")[0] if pubdate else None
    return {
        "pmid": pmid,
        "sourceUrl": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "title": doc.get("title"),
        "authors": authors,
        "journal": doc.get("fulljournalname") or doc.get("source"),
        "pubDate": pubdate or None,
        "year": year,
        "doi": _extract_doi(doc),
        "pubTypes": doc.get("pubtype", []) or [],
    }


def build_document_content(normalized: dict) -> str:
    """Serialize a normalized article record for storage — pure JSON, no
    hand-formatted prose (see module docstring; mirrors the same decision
    made for clinical_trials_service.build_document_content)."""
    return json.dumps(normalized, indent=2)


def content_hash_for_import(pmid: str) -> str:
    """Deterministic content_hash for a PubMed import — no uploaded bytes
    to hash, so we hash the PMID itself, exactly mirroring
    clinical_trials_service.content_hash_for_import for NCT IDs."""
    return hashlib.sha256(f"pubmed:{pmid}".encode("utf-8")).hexdigest()


async def fetch_search(term: str, retmax: int = 15, retstart: int = 0) -> dict:
    """esearch (get matching PMIDs) -> batched esummary (lightweight
    metadata for each). Returns {total, results: [normalize_summary(...)]}.
    `retstart` is esearch's native pagination offset — pass the number of
    results already fetched to get the next page (see literature.py's
    `_search_pubmed`, which threads this through as `pubmedOffset`)."""
    try:
        async with await _get_client() as client:
            es_resp = await client.get(
                "/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": term,
                    "retmode": "json",
                    "retmax": str(retmax),
                    "retstart": str(retstart),
                    "sort": "relevance",
                    **_etiquette_params(),
                },
            )
    except httpx.RequestError as e:
        logger.warning(f"[pubmed] esearch unreachable: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="PubMed is unreachable.")

    if es_resp.status_code == 429:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="PubMed rate limit hit.")
    if es_resp.status_code >= 500:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="PubMed returned a server error.")
    if es_resp.status_code >= 400:
        raise HTTPException(status_code=es_resp.status_code, detail="PubMed search error.")

    es_data = es_resp.json()
    result = es_data.get("esearchresult", {}) or {}
    ids = result.get("idlist", []) or []
    try:
        total = int(result.get("count", "0") or "0")
    except ValueError:
        total = 0

    if not ids:
        return {"total": total, "results": []}

    try:
        async with await _get_client() as client:
            sm_resp = await client.get(
                "/esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json", **_etiquette_params()},
            )
    except httpx.RequestError as e:
        logger.warning(f"[pubmed] esummary unreachable: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="PubMed is unreachable.")

    if sm_resp.status_code >= 500:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="PubMed returned a server error.")
    if sm_resp.status_code >= 400:
        raise HTTPException(status_code=sm_resp.status_code, detail="PubMed search error.")

    sm_data = sm_resp.json()
    docs = (sm_data.get("result", {}) or {})
    results = []
    for pmid in ids:
        doc = docs.get(pmid)
        # PubMed 200s with an embedded error field for uids it can't
        # summarize — skip those rather than surfacing a broken row.
        if not doc or doc.get("error"):
            continue
        results.append(normalize_summary(pmid, doc))

    return {"total": total, "results": results}


async def get_article_full(pmid: str) -> dict:
    """Full article record for detail/import: esummary (structured fields)
    + efetch (abstract text). Raises 404 if the PMID doesn't resolve —
    checking the embedded error field, since esummary 200s on bad PMIDs."""
    try:
        async with await _get_client() as client:
            sm_resp = await client.get(
                "/esummary.fcgi",
                params={"db": "pubmed", "id": pmid, "retmode": "json", **_etiquette_params()},
            )
    except httpx.RequestError as e:
        logger.warning(f"[pubmed] esummary unreachable for {pmid}: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="PubMed is unreachable.")

    if sm_resp.status_code == 429:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="PubMed rate limit hit.")
    if sm_resp.status_code >= 500:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="PubMed returned a server error.")
    if sm_resp.status_code >= 400:
        raise HTTPException(status_code=sm_resp.status_code, detail="PubMed error.")

    sm_data = sm_resp.json()
    doc = (sm_data.get("result", {}) or {}).get(pmid)
    if not doc or doc.get("error"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"PMID {pmid} not found.")

    summary = normalize_summary(pmid, doc)

    abstract_text: Optional[str] = None
    try:
        async with await _get_client() as client:
            ab_resp = await client.get(
                "/efetch.fcgi",
                params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text", **_etiquette_params()},
            )
        if ab_resp.status_code == 200:
            abstract_text = ab_resp.text
    except httpx.RequestError as e:
        # Best-effort — a missing abstract shouldn't fail the whole lookup,
        # citation metadata from esummary is still useful on its own.
        logger.debug(f"[pubmed] efetch abstract fetch failed for {pmid}: {e}")

    return {**summary, "abstractText": abstract_text}


async def get_article_normalized(pmid: str) -> dict:
    """Cached, full article lookup — the primary detail/import entry point."""
    cache_key = f"pubmed:article:{pmid}"
    cached = cache_service.get_json(cache_key)
    if cached is not None:
        return cached

    article = await get_article_full(pmid)
    cache_service.set_json(cache_key, article, ttl=PUBMED_CACHE_TTL)
    return article


async def has_abstract(pmid: str) -> bool:
    """Does this record actually carry an abstract?

    `abstractText` can't answer this: efetch's `rettype=abstract&
    retmode=text` always returns the MEDLINE citation block (source, title,
    authors, affiliations, DOI/PMID trailer) whether or not an abstract
    exists, so it is never empty — an editorial with no abstract still comes
    back as ~900 characters of pure metadata. Truth-testing that blob would
    mean hand-parsing NCBI's text layout; asking for the same record as XML
    and looking for a populated <AbstractText> is exact.

    Costs one extra efetch, so it is called only where the answer changes a
    decision — the import cascade's last tier, deciding between
    `metadata_only` (thin but readable) and `needs_pdf` (nothing to read at
    all). Cached alongside the article itself. Defaults to True on any
    failure: mislabelling a real abstract as unreadable is the worse error.
    """
    import xml.etree.ElementTree as ET

    pmid = (pmid or "").strip()
    if not pmid:
        return False

    cache_key = f"pubmed:has_abstract:{pmid}"
    cached = cache_service.get(cache_key)
    if cached is not None:
        return bool(cached)

    try:
        async with await _get_client() as client:
            resp = await client.get(
                "/efetch.fcgi",
                params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "xml", **_etiquette_params()},
            )
        if resp.status_code != 200:
            return True
        root = ET.fromstring(resp.text)
        result = any((el.text or "").strip() for el in root.iter("AbstractText"))
    except (httpx.RequestError, ET.ParseError) as e:
        logger.debug(f"[pubmed] abstract presence check failed for {pmid}: {e}")
        return True

    cache_service.set(cache_key, result, ttl=PUBMED_CACHE_TTL)
    return result
