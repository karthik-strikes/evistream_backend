"""
Unified literature search — fans out to ClinicalTrials.gov + PubMed in
parallel, merges/interleaves results round-robin, and degrades gracefully
if one source fails. Server-side implementation of the merge/interleave/
partial-failure logic from the shared design mockup's client-side prototype
(`Documents Registry Search.dc.html`) — moved server-side per this app's
existing architecture (no direct browser-to-third-party calls; see
clinical_trials_service.py / pubmed_service.py for the two proxies this
orchestrates, and their own route modules for single-source lookup/import).

Efficiency departure from the literal mockup: the mockup always calls both
upstream APIs regardless of the `scope` selector (there, scope only
pre-picks which UI tab opens active). Here, scope genuinely controls which
upstream(s) get called — narrowing scope means the other real, externally
rate-limited API is never hit.
"""

import asyncio
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_current_user
from app.services import clinical_trials_service as ct_service
from app.services import pubmed_service

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_SCOPES = {"all", "ctgov", "pubmed"}


async def _search_ctgov(
    term: str, status_filter: Optional[str], phase: Optional[str], page_size: int, page_token: Optional[str]
) -> dict:
    # An exact NCT ID gets a precise single-study lookup instead of a term
    # search — mirrors both the mockup's searchCtgov() and the original
    # single-source drawer's client-side NCT_RE detection (now moved here
    # so it applies consistently regardless of scope). A single record has
    # no next page.
    upper = term.strip().upper()
    if ct_service.NCT_RE.match(upper):
        try:
            study = await ct_service.get_study_normalized(upper)
            return {"results": [{"source": "ctgov", **study}], "total": 1, "nextPageToken": None}
        except HTTPException as e:
            if e.status_code == status.HTTP_404_NOT_FOUND:
                return {"results": [], "total": 0, "nextPageToken": None}
            raise

    params = {"format": "json", "pageSize": str(page_size), "countTotal": "true", "query.term": term}
    if status_filter:
        params["filter.overallStatus"] = status_filter
    if phase:
        agg_phase = ct_service.phase_agg_filter(phase)
        if agg_phase:
            params["aggFilters"] = agg_phase
    if page_token:
        params["pageToken"] = page_token
    data = await ct_service.fetch_search(params)
    results = [{"source": "ctgov", **r} for r in data.get("results", [])]
    return {"results": results, "total": data.get("total"), "nextPageToken": data.get("nextPageToken")}


async def _search_pubmed(term: str, page_size: int, offset: int) -> dict:
    stripped = term.strip()

    # An exact PMID gets a precise single-article lookup instead of esearch.
    # A single record has no next page.
    if pubmed_service.PMID_RE.match(stripped):
        try:
            article = await pubmed_service.get_article_normalized(stripped)
            return {"results": [{"source": "pubmed", **article}], "total": 1, "nextOffset": None}
        except HTTPException as e:
            if e.status_code == status.HTTP_404_NOT_FOUND:
                return {"results": [], "total": 0, "nextOffset": None}
            raise

    # An NCT ID as the search term: look up papers that reference this
    # trial via PubMed's Secondary Source ID field ([si]) rather than a
    # literal-text search — mirrors the mockup's searchPubmed() exactly.
    upper = stripped.upper()
    search_term = f"{upper}[si]" if ct_service.NCT_RE.match(upper) else term

    data = await pubmed_service.fetch_search(search_term, retmax=page_size, retstart=offset)
    results = [{"source": "pubmed", **r} for r in data.get("results", [])]
    total = data.get("total")
    next_offset = offset + len(results)
    has_more = bool(results) and total is not None and next_offset < total
    return {"results": results, "total": total, "nextOffset": next_offset if has_more else None}


def _interleave(a: list, b: list) -> list:
    """Round-robin merge — matches the mockup's merge loop exactly, so
    results don't read as "all trials, then all articles"."""
    merged = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            merged.append(a[i])
        if i < len(b):
            merged.append(b[i])
    return merged


@router.get("/search")
async def search_literature(
    term: str,
    scope: str = Query("all"),
    status_filter: Optional[str] = Query(None, alias="status"),
    phase: Optional[str] = Query(None),
    pageSize: int = Query(15, ge=1, le=100),
    ctgovPageToken: Optional[str] = Query(None),
    pubmedOffset: int = Query(0, ge=0),
    user_id: UUID = Depends(get_current_user),
):
    """Fan out to ClinicalTrials.gov and/or PubMed based on `scope`, merge
    results round-robin, and degrade gracefully (never hard-fail) if one
    source errors — mirrors the mockup's Promise.allSettled resilience.

    Pagination: `counts.{ctgov,pubmed}` are the upstream APIs' REAL total
    match counts, which can vastly exceed one page (e.g. searching "oral"
    can report 1M+ PubMed hits) — a single call here only ever returns one
    page per source. To fetch the next page, the frontend re-calls this
    route passing `ctgovPageToken`/`pubmedOffset` back from
    `nextCtgovPageToken`/`nextPubmedOffset` on the previous response (both
    null once a source is exhausted), and APPENDS the new `results` to what
    it already has — see LiteratureSearchDrawer's `loadMore()`.
    """
    scope = scope.lower()
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scope. Expected one of {sorted(VALID_SCOPES)}.",
        )

    want_ctgov = scope in ("all", "ctgov")
    want_pubmed = scope in ("all", "pubmed")

    tasks = []
    if want_ctgov:
        tasks.append(_search_ctgov(term, status_filter, phase, pageSize, ctgovPageToken))
    if want_pubmed:
        tasks.append(_search_pubmed(term, pageSize, pubmedOffset))

    settled = await asyncio.gather(*tasks, return_exceptions=True)

    idx = 0
    ct_result = pm_result = None
    errors: dict = {}
    if want_ctgov:
        r = settled[idx]
        idx += 1
        if isinstance(r, Exception):
            errors["ctgov"] = "ClinicalTrials.gov is unreachable."
            logger.warning(f"[literature] ctgov search failed: {r}")
        else:
            ct_result = r
    if want_pubmed:
        r = settled[idx]
        idx += 1
        if isinstance(r, Exception):
            errors["pubmed"] = "PubMed is unreachable."
            logger.warning(f"[literature] pubmed search failed: {r}")
        else:
            pm_result = r

    merged = _interleave(ct_result["results"] if ct_result else [], pm_result["results"] if pm_result else [])

    message = None
    if errors.get("ctgov") and errors.get("pubmed"):
        message = "Could not reach ClinicalTrials.gov or PubMed. Check your connection and try again."
    elif errors.get("ctgov"):
        message = "ClinicalTrials.gov is unreachable — showing PubMed results only."
    elif errors.get("pubmed"):
        message = "PubMed is unreachable — showing trial results only."

    return {
        "results": merged,
        "counts": {
            "ctgov": ct_result["total"] if ct_result else None,
            "pubmed": pm_result["total"] if pm_result else None,
        },
        "errors": errors,
        "message": message,
        "nextCtgovPageToken": ct_result["nextPageToken"] if ct_result else None,
        "nextPubmedOffset": pm_result["nextOffset"] if pm_result else None,
    }
