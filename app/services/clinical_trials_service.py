"""
ClinicalTrials.gov API v2 proxy + normalizer.

Thin wrapper over the public ClinicalTrials.gov API v2 (no API key required):
https://clinicaltrials.gov/api/v2 . We call the upstream single-study and
search endpoints, then flatten the deeply-nested module tree
(protocolSection/resultsSection/derivedSection/documentSection) into a
friendly, null-safe schema for our callers.

Critical rule: always gate on the top-level `hasResults` boolean before
touching `resultsSection` — it is entirely absent when no results have been
posted (roughly half of registered trials). Every accessor here is null-safe
because sponsors register wildly different subsets of fields.

Do not use any `clinicaltrials.gov/api/query/...` (v1) URL — that API is
retired. Verified against the live API using NCT04307940 (Bayer dental-pain
trial, has results) as the reference fixture — see
backend/tests/fixtures/nct04307940.json and
backend/tests/test_services/test_clinical_trials_service.py.
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

CLINICALTRIALS_API_BASE = "https://clinicaltrials.gov/api/v2"
# Identify ourselves per ClinicalTrials.gov etiquette (no auth required, but a
# descriptive UA + throttling is expected).
USER_AGENT = "eviStreams/1.0 (mailto:noreply@evistreams.com)"
REQUEST_TIMEOUT = 15  # seconds; never let an upstream stall block the request

NCT_RE = re.compile(r"^NCT\d{8}$")

# Study records change infrequently — cache the normalized object, not the raw
# payload, so the (cheap) flattening doesn't run on every cache hit.
CT_CACHE_TTL = 12 * 60 * 60  # 12 hours

# `filter.phase` is NOT a real ClinicalTrials.gov API v2 parameter — verified
# directly against the live API, it returns 400 "unknown parameter". The
# only working phase filter is `aggFilters=phase:<codes>`, using these
# numeric codes, space-separated for multiple values (comma-separated is
# rejected too). `filter.overallStatus` (status) IS a real, working param —
# confirmed separately, no translation needed for it.
PHASE_AGG_CODES = {
    "EARLY_PHASE1": "0",
    "PHASE1": "1",
    "PHASE2": "2",
    "PHASE3": "3",
    "PHASE4": "4",
}


def phase_agg_filter(phase_csv: str) -> Optional[str]:
    """Translate comma-separated phase enum names (e.g. "PHASE1,PHASE2")
    into the upstream aggFilters phase facet value (e.g. "phase:1 2")."""
    codes = [PHASE_AGG_CODES[p] for p in phase_csv.split(",") if p in PHASE_AGG_CODES]
    if not codes:
        return None
    return f"phase:{' '.join(codes)}"


def g(d: Any, *path: str, default: Any = None) -> Any:
    """Null-safe nested getter: g(study, 'protocolSection', 'statusModule', 'overallStatus')."""
    cur = d
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return default
    return cur if cur is not None else default


def doc_url(nct_id: str, filename: str) -> str:
    """Large-document PDFs have no ready URL in the API response — construct
    it from the last two digits of the NCT number."""
    return f"https://cdn.clinicaltrials.gov/large-docs/{nct_id[-2:]}/{nct_id}/{filename}"


def normalize(study: dict) -> dict:
    """Flatten one upstream study object into our schema."""
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    status_mod = ps.get("statusModule", {}) or {}
    design = ps.get("designModule", {}) or {}
    elig = ps.get("eligibilityModule", {}) or {}
    ai = ps.get("armsInterventionsModule", {}) or {}
    outcomes = ps.get("outcomesModule", {}) or {}
    locs = g(ps, "contactsLocationsModule", "locations", default=[]) or []
    docs = g(study, "documentSection", "largeDocumentModule", "largeDocs", default=[]) or []
    refs = g(ps, "referencesModule", "references", default=[]) or []
    nct = ident.get("nctId")
    has_results = bool(study.get("hasResults"))

    results = None
    if has_results:
        rs = study.get("resultsSection", {}) or {}
        results = {
            "participantFlow": rs.get("participantFlowModule"),
            "baselineCharacteristics": rs.get("baselineCharacteristicsModule"),
            "outcomeMeasures": g(rs, "outcomeMeasuresModule", "outcomeMeasures", default=[]),
            "adverseEvents": rs.get("adverseEventsModule"),
            "moreInfo": rs.get("moreInfoModule"),
        }

    # MeSH terms — combine condition + intervention browse modules (direct
    # terms and their ancestor hierarchy), deduped, order-preserving. Website
    # shows these under "Additional Relevant MeSH Terms".
    mesh_terms: list = []
    _seen_mesh = set()
    for module_name in ("conditionBrowseModule", "interventionBrowseModule"):
        module = g(study, "derivedSection", module_name, default={}) or {}
        for bucket in ("meshes", "ancestors"):
            for m in module.get(bucket, []) or []:
                term = m.get("term")
                if term and term not in _seen_mesh:
                    _seen_mesh.add(term)
                    mesh_terms.append(term)

    return {
        "nctId": nct,
        "sourceUrl": f"https://clinicaltrials.gov/study/{nct}" if nct else None,
        "title": {"brief": ident.get("briefTitle"), "official": ident.get("officialTitle")},
        "orgStudyId": g(ident, "orgStudyIdInfo", "id"),
        "oversight": {
            "fdaRegulatedDrug": g(ps, "oversightModule", "isFdaRegulatedDrug"),
            "fdaRegulatedDevice": g(ps, "oversightModule", "isFdaRegulatedDevice"),
            "usExport": g(ps, "oversightModule", "isUsExport"),
        },
        "meshTerms": mesh_terms,
        "status": {
            "overall": status_mod.get("overallStatus"),
            "hasResults": has_results,
            "startDate": g(status_mod, "startDateStruct", "date"),
            "primaryCompletionDate": g(status_mod, "primaryCompletionDateStruct", "date"),
            "completionDate": g(status_mod, "completionDateStruct", "date"),
            "firstPostedDate": g(status_mod, "studyFirstPostDateStruct", "date"),
            "resultsFirstPostedDate": g(status_mod, "resultsFirstPostDateStruct", "date"),
            "lastUpdatePostedDate": g(status_mod, "lastUpdatePostDateStruct", "date"),
        },
        "sponsor": {
            "lead": g(ps, "sponsorCollaboratorsModule", "leadSponsor", "name"),
            "class": g(ps, "sponsorCollaboratorsModule", "leadSponsor", "class"),
            "collaborators": [
                c.get("name") for c in g(ps, "sponsorCollaboratorsModule", "collaborators", default=[]) or []
            ],
        },
        "summary": g(ps, "descriptionModule", "briefSummary"),
        "conditions": g(ps, "conditionsModule", "conditions", default=[]),
        "keywords": g(ps, "conditionsModule", "keywords", default=[]),
        "studyType": design.get("studyType"),
        "phase": design.get("phases", []),
        "design": {
            "allocation": g(design, "designInfo", "allocation"),
            "interventionModel": g(design, "designInfo", "interventionModel"),
            "masking": g(design, "designInfo", "maskingInfo", "masking"),
            "primaryPurpose": g(design, "designInfo", "primaryPurpose"),
        },
        "enrollment": {
            "count": g(design, "enrollmentInfo", "count"),
            "type": g(design, "enrollmentInfo", "type"),
        },
        "eligibility": {
            "minAge": elig.get("minimumAge"),
            "maxAge": elig.get("maximumAge"),
            "sex": elig.get("sex"),
            "healthyVolunteers": elig.get("healthyVolunteers"),
            "criteria": elig.get("eligibilityCriteria"),
        },
        "arms": [
            {
                "label": a.get("label"),
                "type": a.get("type"),
                "description": a.get("description"),
                "interventionNames": a.get("interventionNames", []),
            }
            for a in ai.get("armGroups", []) or []
        ],
        "interventions": [
            {"type": i.get("type"), "name": i.get("name"), "description": i.get("description")}
            for i in ai.get("interventions", []) or []
        ],
        "outcomes": {
            "primary": [
                {"measure": o.get("measure"), "description": o.get("description"), "timeFrame": o.get("timeFrame")}
                for o in outcomes.get("primaryOutcomes", []) or []
            ],
            "secondary": [
                {"measure": o.get("measure"), "description": o.get("description"), "timeFrame": o.get("timeFrame")}
                for o in outcomes.get("secondaryOutcomes", []) or []
            ],
        },
        "locations": [
            {
                "facility": l.get("facility"),
                "city": l.get("city"),
                "state": l.get("state"),
                "country": l.get("country"),
                "zip": l.get("zip"),
            }
            for l in locs
        ],
        "references": [{"pmid": r.get("pmid"), "type": r.get("type"), "citation": r.get("citation")} for r in refs],
        "documents": (
            [
                {
                    "label": d.get("label"),
                    "url": doc_url(nct, d.get("filename", "")),
                    "date": d.get("date"),
                    "sizeBytes": d.get("size"),
                }
                for d in docs
            ]
            if nct
            else []
        ),
        "results": results,
    }


def build_document_content(normalized: dict) -> str:
    """Serialize a normalized trial record for storage — this is what gets
    stored as the document's `s3_markdown_path`, so a CT.gov import behaves
    like any other parsed document everywhere downstream (extraction,
    results, consensus) with zero special-casing.

    Pure JSON, not hand-formatted prose: the record includes deeply nested
    data (participant flow, baseline characteristics, per-arm outcome
    measurements, statistical analyses, adverse events) that's easy to
    silently mangle with custom markdown formatting. A raw, complete JSON
    dump is unambiguous for both humans and the LLM extraction pipeline
    that reads this file — and avoids a document that's part hand-written
    prose, part embedded JSON (an earlier iteration of this function did
    that; it read as inconsistent, so this now applies one format
    throughout)."""
    return json.dumps(normalized, indent=2)


async def _get_client() -> httpx.AsyncClient:
    """Per-call client, mirroring the house style (see app/api/v1/auth.py) —
    there is no shared/singleton AsyncClient in this codebase."""
    return httpx.AsyncClient(
        base_url=CLINICALTRIALS_API_BASE,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
    )


async def fetch_study(nct_id: str) -> dict:
    """Fetch one raw study record. Raises HTTPException on failure —
    404 passes through, upstream 5xx/timeout/network errors map to 502,
    a 429 passes through so callers can see the rate limit."""
    try:
        async with await _get_client() as client:
            resp = await client.get(f"/studies/{nct_id}", params={"format": "json"})
    except httpx.RequestError as e:
        logger.warning(f"[clinical_trials] upstream unreachable for {nct_id}: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ClinicalTrials.gov is unreachable.")

    if resp.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Trial {nct_id} not found.")
    if resp.status_code == 429:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="ClinicalTrials.gov rate limit hit.")
    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="ClinicalTrials.gov returned a server error."
        )
    resp.raise_for_status()
    return resp.json()


async def get_study_normalized(nct_id: str) -> dict:
    """Cached, normalized single-study lookup."""
    cache_key = f"ctgov:study:{nct_id}"
    cached = cache_service.get_json(cache_key)
    if cached is not None:
        return cached

    study = await fetch_study(nct_id)
    normalized = normalize(study)
    cache_service.set_json(cache_key, normalized, ttl=CT_CACHE_TTL)
    return normalized


async def fetch_search(params: dict) -> dict:
    """Proxy the upstream search endpoint. Returns
    {total, nextPageToken, results: [normalized studies]}."""
    try:
        async with await _get_client() as client:
            resp = await client.get("/studies", params=params)
    except httpx.RequestError as e:
        logger.warning(f"[clinical_trials] upstream search unreachable: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ClinicalTrials.gov is unreachable.")

    if resp.status_code == 429:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="ClinicalTrials.gov rate limit hit.")
    if resp.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="ClinicalTrials.gov returned a server error."
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="Upstream search error.")
    resp.raise_for_status()

    data = resp.json()
    return {
        "total": data.get("totalCount"),
        "nextPageToken": data.get("nextPageToken"),
        "results": [normalize(s) for s in data.get("studies", [])],
    }


async def fetch_version() -> dict:
    """Liveness probe + data-freshness check: GET /version."""
    try:
        async with await _get_client() as client:
            resp = await client.get("/version")
        resp.raise_for_status()
        return resp.json()
    except httpx.RequestError as e:
        logger.warning(f"[clinical_trials] version check failed: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ClinicalTrials.gov is unreachable.")
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ClinicalTrials.gov returned an error.")


def content_hash_for_import(nct_id: str) -> str:
    """Deterministic content_hash for a CT.gov import — there are no uploaded
    bytes to hash, so we hash the NCT ID itself. This reuses the exact
    existing storage_service S3 key convention unchanged, and doubles as a
    second, free dedup signal via the existing partial-unique
    (project_id, content_hash) index (the primary dedup key is nct_id)."""
    return hashlib.sha256(f"ctgov:{nct_id}".encode("utf-8")).hexdigest()
