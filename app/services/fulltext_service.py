"""
Free full-text acquisition for PubMed imports — Unpaywall (open-access PDF)
+ PubMed Central (structured full text), both best-effort.

Kept separate from pubmed_service.py: that module is the PubMed *metadata*
proxy (esearch/esummary/efetch abstract); this module hits two entirely
different upstream APIs (Unpaywall, PMC) to try to get the actual paper, not
just its citation. Neither function ever raises — a network hiccup, a wrong
content-type, or "no OA copy exists" all just fall through to None so the
import cascade in pubmed.py can degrade gracefully to the next tier.

Verified live this session:
  - Unpaywall (api.unpaywall.org/v2/{doi}) — is_oa / oa_status /
    best_oa_location.url_for_pdf. A known-OA DOI
    (10.1186/s12903-026-09296-1) returns a real Springer PDF URL; a
    known-closed DOI (10.1080/00325481.2021.2008180) returns is_oa=false.
  - Publisher PDF URLs often 303-redirect and reject a bare/non-browser
    User-Agent (confirmed on the Springer link above) — must follow
    redirects and send a browser-like UA.
  - Many "url_for_pdf" values actually serve an HTML landing page, not a
    PDF — always validate the magic bytes before trusting the download.
  - PMC ID Converter (pmc.ncbi.nlm.nih.gov/tools/idconv) — a PMID with no
    PMC copy returns {"status": "error", "errmsg": "Identifier not found in
    PMC"} (HTTP 200), not a 404 — same "check the payload, not just the
    status code" gotcha as pubmed_service's esummary.

Verified live Aug 5 2026, while fixing "badge says PDF available but the
import lands no PDF" (PMID 36631957 / doi 10.1177/00220345221139230):
  - An Unpaywall `url_for_pdf` existing does NOT mean it is fetchable. That
    DOI is is_oa=true with two candidates and BOTH fail: the publisher one
    (journals.sagepub.com) returns HTTP 403 (bot block), and the PMC one
    (pmc.ncbi.nlm.nih.gov/.../pdf/...) returns HTTP 200 with an HTML
    "Preparing to download ..." page — NCBI now gates PDF downloads behind
    a JS proof-of-work + secure-cookie challenge no plain client can pass.
    So availability must be decided by *trying the download*, not by
    reading metadata — hence resolve_pdf_url below. Filtering on Unpaywall's
    `host_type` would not have helped: the "trustworthy" repository
    candidate is exactly the one behind the proof-of-work page.
  - Europe PMC (europepmc.org/articles/{pmcid}?pdf=render) serves that same
    article as a real 818 KB application/pdf with no challenge. It is the
    single highest-yield PDF source for anything PMC-deposited and is tried
    after Unpaywall's own candidates are exhausted.
  - A PMCID existing does not mean full text is retrievable either — the
    same existence-vs-content trap. probe_full_text_availability therefore
    checks for a real <body>, not just a successful ID lookup.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from app.services.cache_service import cache_service

logger = logging.getLogger(__name__)

UNPAYWALL_API_BASE = "https://api.unpaywall.org/v2"
PMC_IDCONV_BASE = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# Europe PMC mirrors PMC's content but serves PDFs directly, without the
# proof-of-work interstitial NCBI now puts in front of pmc.ncbi.nlm.nih.gov
# PDF URLs (see module docstring).
EUROPE_PMC_PDF_TEMPLATE = "https://europepmc.org/articles/{pmcid}?pdf=render"

CONTACT_EMAIL = "noreply@evistreams.com"
TOOL_NAME = "eviStreams"
# Publisher sites frequently block/redirect requests that don't look like a
# real browser (confirmed on link.springer.com — a bare UA got nothing,
# `Mozilla/5.0 (eviStreams/1.0; mailto:...)` + follow_redirects got a 200).
PDF_USER_AGENT = "Mozilla/5.0 (eviStreams/1.0; mailto:noreply@evistreams.com)"
REQUEST_TIMEOUT = 20  # publisher sites can be slow; more generous than the metadata APIs
# Measured against Europe PMC Aug 5 2026: time-to-first-byte is usually under
# a second, but ~1 request in 5 stalls — one of five sequential probes blew an
# 8s ceiling, and a full 818 KB download took 12-14s twice and timed out once
# at 20s. These hosts are slow, not broken, so both budgets are generous and
# both calls retry once; the alternative is randomly reporting "no PDF" for a
# paper that has one, which is the exact bug this module is fixing.
PROBE_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60
TRANSIENT_ATTEMPTS = 2
# Ceiling on the whole availability probe. Per-candidate timeouts alone don't
# bound it — several candidates across two hosts, each retried once, sum to
# minutes. A user is watching a spinner on this, so it answers "unknown"
# rather than keeping them waiting. Measured real answers: 0.14-1.24s.
PROBE_BUDGET = 20
# OA status changes on the order of months, so a day of staleness is fine and
# keeps repeat probes (and the import that follows one) essentially free.
RESOLVE_CACHE_TTL = 24 * 60 * 60
PDF_MAGIC_BYTES = b"%PDF-"
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB — generous for a research paper, guards a runaway download
# Best-effort denylist for fetch_direct_pdf, which (unlike Unpaywall/PMC) fetches
# arbitrary URLs a source record carried. Not exhaustive — just refuses the
# well-known piracy mirrors outright rather than fetching from them.
_UNSAFE_URL_HOST_MARKERS = (
    "sci-hub", "libgen", "library.lol", "1lib.", "z-lib", "zlibrary", "annas-archive",
)

_NS_STRIP = re.compile(r"\{[^}]*\}")  # strip XML namespace prefixes when reading tags


async def _unpaywall_candidate_urls(doi: str) -> list:
    """Ask Unpaywall for `doi`'s OA PDF location(s) — metadata only, no
    download. Returns an empty list if the DOI is unknown to Unpaywall, has
    no OA copy, or the lookup fails for any reason. Shared by
    fetch_open_access_pdf (which downloads the first working one) and
    probe_full_text_availability (which only needs to know one exists)."""
    doi = (doi or "").strip()
    if not doi:
        return []

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT)) as client:
            uw_resp = await client.get(
                f"{UNPAYWALL_API_BASE}/{doi}",
                params={"email": CONTACT_EMAIL},
            )
    except httpx.RequestError as e:
        logger.debug(f"[fulltext] Unpaywall unreachable for {doi}: {e}")
        return []

    if uw_resp.status_code != 200:
        # 404 = DOI unknown to Unpaywall; anything else — treat as "no copy".
        # This is a best-effort enhancement, never fatal to the import.
        return []

    try:
        uw_data = uw_resp.json()
    except Exception:
        return []

    if not uw_data.get("is_oa"):
        return []

    candidate_urls = []
    best = uw_data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        candidate_urls.append(best["url_for_pdf"])
    for loc in uw_data.get("oa_locations") or []:
        url = loc.get("url_for_pdf")
        if url and url not in candidate_urls:
            candidate_urls.append(url)

    return candidate_urls


async def _is_pdf_url(url: str) -> Optional[bool]:
    """Does this URL actually serve PDF bytes? Streams the response and
    aborts after the first chunk, so it costs ~1 KB rather than a whole
    paper — cheap enough to run before the user commits to importing.

    Streaming rather than a `Range: bytes=0-1023` header on purpose: Range
    is a request a server may ignore (and several publishers do, starting to
    send the full file); closing the stream after the first chunk is a
    guarantee. Catches both known failure modes — a non-200 (Sage's 403) on
    the status check, and an HTML challenge page (NCBI's "Preparing to
    download ...") on the magic bytes.

    Three-valued on purpose: True (real PDF), False (definitively not one),
    or None when the check couldn't be completed at all. Callers must not
    treat None as "no PDF" — a timeout against a slow mirror is not evidence
    that a paper is unavailable, and resolve_pdf_url would otherwise memoize
    that non-answer for a day."""
    for attempt in range(TRANSIENT_ATTEMPTS):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(PROBE_TIMEOUT),
                follow_redirects=True,
                headers={"User-Agent": PDF_USER_AGENT},
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        return False
                    async for chunk in resp.aiter_bytes(1024):
                        return chunk.startswith(PDF_MAGIC_BYTES)
                    return False  # 200 with an empty body
        except httpx.RequestError as e:
            logger.debug(f"[fulltext] PDF probe attempt {attempt + 1} failed for {url}: {e}")

    return None


async def _pdf_candidate_urls(doi: Optional[str], pmid: Optional[str], pmcid: Optional[str] = None) -> list:
    """Every URL worth trying for a free PDF, best-first: Unpaywall's own
    locations, then Europe PMC (which serves PMC-deposited papers without
    NCBI's proof-of-work gate, and is frequently the only copy that actually
    downloads). The Europe PMC candidate normally costs a PMC ID lookup from a
    PMID; a caller that already has the PMCID itself (e.g. mined straight out
    of a PMC/Europe PMC URL in a source record) can pass it directly and skip
    that hop entirely."""
    urls = list(await _unpaywall_candidate_urls(doi)) if doi else []

    resolved_pmcid = pmcid or (await _pmc_id_lookup(pmid) if pmid else None)
    if resolved_pmcid:
        epmc_url = EUROPE_PMC_PDF_TEMPLATE.format(pmcid=resolved_pmcid)
        if epmc_url not in urls:
            urls.append(epmc_url)

    return urls


async def resolve_pdf_url(doi: Optional[str], pmid: Optional[str] = None, pmcid: Optional[str] = None) -> Optional[str]:
    """The first candidate that actually serves PDF bytes, or None. See
    resolve_pdf_url_verbose — this drops the certainty flag for the callers
    (the importer) that can only act on a URL either way."""
    url, _ = await resolve_pdf_url_verbose(doi, pmid, pmcid)
    return url


async def resolve_pdf_url_verbose(doi: Optional[str], pmid: Optional[str] = None, pmcid: Optional[str] = None) -> tuple:
    """(url, conclusive). `conclusive` is False when at least one candidate
    could not be checked at all, i.e. "no URL" means "we don't know" rather
    than "there isn't one" — the probe surfaces that distinction to the UI
    instead of quietly reporting nothing available.

    Single source of truth for both the pre-import probe and the import
    itself, so the "PDF available" badge can never promise something the
    importer then fails to deliver — they used to run different logic (list
    the candidates vs. list-then-download), which is structurally why they
    could disagree. Cached by (doi, pmid, pmcid) for a day: OA status changes
    on the order of months, and this is the only expensive step in the probe.
    An empty string is a cached negative — distinct from a cache miss."""
    doi = (doi or "").strip()
    pmid = (pmid or "").strip()
    pmcid = (pmcid or "").strip()
    if not doi and not pmid and not pmcid:
        return None, True

    cache_key = f"fulltext:pdf_url:{doi}|{pmid}|{pmcid}"
    cached = cache_service.get(cache_key)
    if cached is not None:
        return (cached or None), True

    inconclusive = False
    for url in await _pdf_candidate_urls(doi, pmid, pmcid):
        verdict = await _is_pdf_url(url)
        if verdict:
            cache_service.set(cache_key, url, ttl=RESOLVE_CACHE_TTL)
            return url, True
        if verdict is None:
            inconclusive = True

    # Only remember a "no" we're actually sure of. If any candidate timed out
    # we genuinely don't know, and pinning that guess for a day would hide a
    # real PDF from every later probe and import — the failure mode this whole
    # change exists to remove. Leaving it uncached costs one repeated check.
    if not inconclusive:
        cache_service.set(cache_key, "", ttl=RESOLVE_CACHE_TTL)

    return None, not inconclusive


async def fetch_open_access_pdf(
    doi: Optional[str], pmid: Optional[str] = None, pmcid: Optional[str] = None
) -> Optional[bytes]:
    """Find a free copy of this paper and download it. Returns the raw PDF
    bytes, or None if no fetchable OA copy exists. `pmid`/`pmcid` are
    optional — without either, only the Unpaywall/DOI path runs. RIS/PubMed
    records supply pmid directly; EndNote records can too, mined from a
    PubMed/PMC URL in the reference's `url` field (see endnote_service.py)."""
    url = await resolve_pdf_url(doi, pmid, pmcid)
    return await _download_pdf(url) if url else None


async def fetch_direct_pdf(urls: Optional[list]) -> Optional[bytes]:
    """Last-resort fallback for a reference with no DOI/PMID/PMCID at all, or
    one Unpaywall/PMC simply have no copy of: try the raw URL(s) the source
    record itself carried — e.g. a link a user pasted into EndNote's or a
    RIS `UR` field — directly, validating real PDF bytes before trusting any
    of it (the same landing-page trap _is_pdf_url guards against elsewhere in
    this module). Skips known piracy-mirror domains outright rather than
    fetching from them. Unindexed by Unpaywall by definition, so only worth
    trying after that path is exhausted."""
    for url in urls or []:
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        if any(marker in url.lower() for marker in _UNSAFE_URL_HOST_MARKERS):
            logger.debug(f"[fulltext] skipping disallowed host: {url}")
            continue
        if await _is_pdf_url(url):
            pdf = await _download_pdf(url)
            if pdf:
                return pdf
    return None


async def _download_pdf(url: str) -> Optional[bytes]:
    """Download one candidate PDF URL, following redirects with a
    browser-like User-Agent (many publishers reject/redirect otherwise), and
    validate the result is actually a PDF (many OA links land on an HTML
    page instead). Retries once on a transport error: the OA mirrors that
    hold most of these papers are reliably slow and occasionally stall, and
    a single flaky read shouldn't downgrade an import to abstract-only."""
    resp = None
    for attempt in range(TRANSIENT_ATTEMPTS):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(DOWNLOAD_TIMEOUT),
                follow_redirects=True,
                headers={"User-Agent": PDF_USER_AGENT},
            ) as client:
                resp = await client.get(url)
            break
        except httpx.RequestError as e:
            logger.debug(f"[fulltext] PDF download attempt {attempt + 1} failed for {url}: {e}")

    if resp is None:
        return None

    if resp.status_code != 200:
        return None

    if not resp.content.startswith(PDF_MAGIC_BYTES):
        logger.debug(f"[fulltext] {url} did not return a PDF (content-type={resp.headers.get('content-type')!r})")
        return None

    if len(resp.content) > MAX_PDF_BYTES:
        logger.warning(f"[fulltext] PDF at {url} exceeds {MAX_PDF_BYTES} bytes, skipping")
        return None

    return resp.content


async def _pmc_id_lookup(pmid: str) -> Optional[str]:
    """Check whether `pmid` has a PubMed Central copy — the ID Converter
    call only, no full-text fetch. Returns the PMCID (e.g. "PMC13383132"),
    or None if there's no PMC copy or the lookup fails. Shared by
    fetch_pmc_fulltext and probe_full_text_availability."""
    pmid = (pmid or "").strip()
    if not pmid:
        return None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT)) as client:
            idconv_resp = await client.get(
                PMC_IDCONV_BASE,
                params={"ids": pmid, "format": "json", "tool": TOOL_NAME, "email": CONTACT_EMAIL},
            )
    except httpx.RequestError as e:
        logger.debug(f"[fulltext] PMC idconv unreachable for {pmid}: {e}")
        return None

    if idconv_resp.status_code != 200:
        return None

    try:
        records = idconv_resp.json().get("records") or []
    except Exception:
        return None

    if not records or records[0].get("status") == "error":
        return None

    return records[0].get("pmcid")


async def fetch_pmc_fulltext(pmid: str) -> Optional[list]:
    """If `pmid` has a PubMed Central copy, fetch its JATS full text and
    flatten it into [{section, text}, ...]. Returns None if there's no PMC
    copy, or the fetch/parse fails for any reason."""
    xml_text = await _fetch_pmc_jats(pmid)
    return _parse_jats_sections(xml_text) if xml_text else None


async def pmc_has_fulltext(pmid: str) -> bool:
    """Does PMC hold a real, retrievable *body* for this PMID — not merely a
    PMCID? A PMC deposit can be front-matter only (restricted publishers),
    in which case fetch_pmc_fulltext correctly degrades to None and the
    import lands abstract-only. Without this check the probe would promise
    "Full text via PMC" for those, repeating the same existence-vs-content
    mistake the PDF path used to make. Cached, since it costs a full JATS
    fetch and the answer is stable."""
    pmid = (pmid or "").strip()
    if not pmid:
        return False

    cache_key = f"fulltext:pmc_body:{pmid}"
    cached = cache_service.get(cache_key)
    if cached is not None:
        return bool(cached)

    has_body = bool(await fetch_pmc_fulltext(pmid))
    cache_service.set(cache_key, has_body, ttl=RESOLVE_CACHE_TTL)
    return has_body


async def _fetch_pmc_jats(pmid: str) -> Optional[str]:
    """Raw JATS XML for `pmid`'s PMC copy, or None if there isn't one."""
    pmcid = await _pmc_id_lookup(pmid)
    if not pmcid:
        return None

    numeric_pmcid = pmcid[3:] if pmcid.upper().startswith("PMC") else pmcid

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT)) as client:
            xml_resp = await client.get(
                PUBMED_EFETCH_URL,
                params={
                    "db": "pmc",
                    "id": numeric_pmcid,
                    "rettype": "full",
                    "retmode": "xml",
                    "tool": TOOL_NAME,
                    "email": CONTACT_EMAIL,
                },
            )
    except httpx.RequestError as e:
        logger.debug(f"[fulltext] PMC efetch unreachable for {pmcid}: {e}")
        return None

    if xml_resp.status_code != 200:
        return None

    return xml_resp.text


def _parse_jats_sections(xml_text: str) -> Optional[list]:
    """Flatten a JATS <body> into [{section, text}] — section heading (if
    any) + concatenated paragraph text. Deliberately simple (no attempt to
    preserve figures/tables/citation markup, which naturally fall out since
    we only ever read <p> text) — matches the house preference for raw/plain
    content over fragile hand-parsed structure (see pubmed_service.py's
    docstring on why abstracts are used as-is rather than regex-split)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.debug(f"[fulltext] JATS parse failed: {e}")
        return None

    body = None
    for el in root.iter():
        if _NS_STRIP.sub("", el.tag) == "body":
            body = el
            break
    if body is None:
        return None

    sections = []
    for child in body:
        tag = _NS_STRIP.sub("", child.tag)
        if tag == "sec":
            title = None
            paras = []
            for desc in child.iter():
                d_tag = _NS_STRIP.sub("", desc.tag)
                if d_tag == "title" and title is None:
                    title = "".join(desc.itertext()).strip()
                elif d_tag == "p":
                    text = "".join(desc.itertext()).strip()
                    if text:
                        paras.append(text)
            joined = "\n\n".join(paras)
            if joined:
                sections.append({"section": title, "text": joined})
        elif tag == "p":
            # A bare top-level paragraph not wrapped in a <sec> — rare, but
            # keep it rather than silently dropping content.
            text = "".join(child.itertext()).strip()
            if text:
                sections.append({"section": None, "text": text})

    return sections or None


async def probe_full_text_availability(
    doi: Optional[str],
    pmid: Optional[str] = None,
    pmcid: Optional[str] = None,
    urls: Optional[list] = None,
) -> str:
    """Pre-import check: what will the import actually get? Runs the exact
    same resolution the import runs — resolve_pdf_url (doi/pmid/pmcid), then a
    real PMC body check, then the raw URL(s) fallback (fetch_direct_pdf's
    tier) — so the answer shown in the UI is a promise the importer keeps.
    Returns one of:
      "pdf"     — a URL that really serves PDF bytes was found and verified.
      "pmc"     — no fetchable PDF, but PubMed Central has real full text.
      "none"    — neither; import will land the citation/abstract only.
      "unknown" — the check couldn't be completed (an upstream host stalled,
                  or the whole probe blew its budget). Callers must render
                  this differently from "none": it means we don't know, not
                  that nothing is available, and the import may still find a
                  PDF. Nothing is cached in this case, so the next look
                  re-checks.

    This deliberately does more work than "ask Unpaywall if a URL exists":
    that cheaper version was wrong often enough to be misleading (see the
    module docstring — an is_oa DOI whose only two PDF URLs were a 403 and a
    JS challenge page still reported "PDF available"). Cost is bounded by
    the day-long caches in resolve_pdf_url/pmc_has_fulltext, so the second
    look at any article — including the import that follows a probe — is
    essentially free. The bulk caller (citations.py) already bounds its own
    concurrency.
    """
    try:
        return await asyncio.wait_for(_probe(doi, pmid, pmcid, urls), PROBE_BUDGET)
    except asyncio.TimeoutError:
        # A hard ceiling on how long the UI can sit on "Checking…". Without
        # it the worst case is the sum of every candidate's timeout and retry
        # across two hosts — minutes, not seconds — and a spinner that long
        # reads as broken.
        logger.debug(f"[fulltext] availability probe exceeded {PROBE_BUDGET}s for {pmid}")
        return "unknown"


async def _probe(
    doi: Optional[str], pmid: Optional[str], pmcid: Optional[str] = None, urls: Optional[list] = None
) -> str:
    url, conclusive = await resolve_pdf_url_verbose(doi, pmid, pmcid)
    if url:
        return "pdf"

    if pmid and await pmc_has_fulltext(pmid):
        return "pmc"

    # Raw URL(s) the source record carried, same last-resort tier the
    # importer falls back to (fetch_direct_pdf) — without this, the preview
    # would promise less than the import can actually deliver for a
    # reference whose only real copy is a link Unpaywall doesn't index.
    for raw_url in urls or []:
        if not raw_url or not raw_url.lower().startswith(("http://", "https://")):
            continue
        if any(marker in raw_url.lower() for marker in _UNSAFE_URL_HOST_MARKERS):
            continue
        if await _is_pdf_url(raw_url):
            return "pdf"

    # Every candidate failed to answer, so "nothing available" would be a
    # guess dressed as a finding.
    return "none" if conclusive else "unknown"
