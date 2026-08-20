"""
Unit tests for fulltext_service — the Unpaywall PDF + PMC full-text
acquisition used by the PubMed import cascade (see app/api/v1/pubmed.py).

Following the same convention as test_pubmed_service.py: the network-calling
async functions (fetch_open_access_pdf, fetch_pmc_fulltext) are exercised at
the route level in test_pubmed.py (the whole function mocked out there, no
httpx-mocking library is installed in this project) — here we test the pure,
network-free logic directly: _parse_jats_sections, against a REAL saved JATS
response (backend/tests/fixtures/pmc13383132_full.xml — PMC13383132 / PMID
42472793, BMC Oral Health, fetched live this session via
`efetch?db=pmc&id=13383132&rettype=full&retmode=xml`), not a hand-rolled stub.
"""

import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.fulltext_service import (  # noqa: E402
    _parse_jats_sections,
    _pdf_candidate_urls,
    fetch_direct_pdf,
    probe_full_text_availability,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _load_real_jats() -> str:
    with open(os.path.join(FIXTURES_DIR, "pmc13383132_full.xml")) as f:
        return f.read()


class TestParseJatsSections:
    def test_extracts_body_sections_not_abstract_sections(self):
        """The fixture's <front><abstract> also has Introduction/Methods/
        Results/Conclusion sub-sections — must not be confused with the
        actual <body> content."""
        sections = _parse_jats_sections(_load_real_jats())
        assert sections is not None
        assert len(sections) > 0

    def test_section_titles_include_expected_headings(self):
        sections = _parse_jats_sections(_load_real_jats())
        titles = [s["section"] for s in sections]
        assert "Introduction" in titles
        assert "Results" in titles or "Conclusions" in titles or "Conclusion" in titles

    def test_each_section_has_nonempty_text(self):
        sections = _parse_jats_sections(_load_real_jats())
        for s in sections:
            assert isinstance(s["text"], str)
            assert s["text"].strip() != ""

    def test_malformed_xml_returns_none(self):
        assert _parse_jats_sections("<not><valid xml") is None

    def test_no_body_tag_returns_none(self):
        assert _parse_jats_sections("<article><front><title>No body here</title></front></article>") is None

    def test_empty_body_returns_none(self):
        assert _parse_jats_sections("<article><body></body></article>") is None

    def test_bare_paragraph_not_wrapped_in_sec_is_kept(self):
        xml = "<article><body><p>A stray intro paragraph.</p></body></article>"
        sections = _parse_jats_sections(xml)
        assert sections == [{"section": None, "text": "A stray intro paragraph."}]


def _run(coro):
    """No pytest-asyncio in this project (see test_pubmed.py's docstring —
    FastAPI's TestClient drives async routes without it); for testing an
    async function directly, asyncio.run() is the plain-pytest equivalent."""
    return asyncio.run(coro)


class TestPdfCandidateUrls:
    """Which URLs get tried, and in what order. Europe PMC is the fallback
    that rescues PMC-deposited papers whose pmc.ncbi.nlm.nih.gov PDF sits
    behind a proof-of-work page (see the module docstring)."""

    def test_unpaywall_candidates_come_first(self):
        with patch(
            "app.services.fulltext_service._unpaywall_candidate_urls",
            return_value=["https://publisher.example/paper.pdf"],
        ), patch("app.services.fulltext_service._pmc_id_lookup", return_value="PMC10031629"):
            urls = _run(_pdf_candidate_urls("10.1234/some-doi", "36631957"))
        assert urls == [
            "https://publisher.example/paper.pdf",
            "https://europepmc.org/articles/PMC10031629?pdf=render",
        ]

    def test_europe_pmc_is_offered_even_with_no_doi(self):
        """A PMID alone is enough — this is why the import cascade now passes
        the PMID through: without it, a paper whose only fetchable copy is on
        Europe PMC silently lands as abstract-only."""
        with patch("app.services.fulltext_service._unpaywall_candidate_urls") as mock_unpaywall, patch(
            "app.services.fulltext_service._pmc_id_lookup", return_value="PMC10031629"
        ):
            urls = _run(_pdf_candidate_urls(None, "36631957"))
        assert urls == ["https://europepmc.org/articles/PMC10031629?pdf=render"]
        mock_unpaywall.assert_not_called()

    def test_no_pmc_copy_leaves_only_unpaywall_candidates(self):
        with patch(
            "app.services.fulltext_service._unpaywall_candidate_urls",
            return_value=["https://publisher.example/paper.pdf"],
        ), patch("app.services.fulltext_service._pmc_id_lookup", return_value=None):
            urls = _run(_pdf_candidate_urls("10.1234/some-doi", "36631957"))
        assert urls == ["https://publisher.example/paper.pdf"]

    def test_nothing_to_go_on_returns_empty(self):
        with patch("app.services.fulltext_service._unpaywall_candidate_urls", return_value=[]), patch(
            "app.services.fulltext_service._pmc_id_lookup", return_value=None
        ):
            assert _run(_pdf_candidate_urls(None, None)) == []

    def test_explicit_pmcid_skips_the_id_lookup_hop(self):
        """A caller that already knows the PMCID (e.g. mined from a PMC URL
        in a source record — see endnote_service.py) shouldn't pay for a
        _pmc_id_lookup round trip it doesn't need."""
        with patch(
            "app.services.fulltext_service._unpaywall_candidate_urls", return_value=[]
        ), patch("app.services.fulltext_service._pmc_id_lookup") as mock_lookup:
            urls = _run(_pdf_candidate_urls(None, None, "PMC10031629"))
        assert urls == ["https://europepmc.org/articles/PMC10031629?pdf=render"]
        mock_lookup.assert_not_called()

    def test_explicit_pmcid_combines_with_doi_candidates(self):
        with patch(
            "app.services.fulltext_service._unpaywall_candidate_urls",
            return_value=["https://publisher.example/paper.pdf"],
        ):
            urls = _run(_pdf_candidate_urls("10.1234/some-doi", None, "PMC10031629"))
        assert urls == [
            "https://publisher.example/paper.pdf",
            "https://europepmc.org/articles/PMC10031629?pdf=render",
        ]


class TestFetchDirectPdf:
    """The last-resort fallback for a reference with no usable identifier at
    all — tries the source record's own raw URL(s) directly."""

    def test_returns_bytes_from_first_valid_pdf_url(self):
        with patch("app.services.fulltext_service._is_pdf_url", return_value=True), patch(
            "app.services.fulltext_service._download_pdf", return_value=b"%PDF-1.4 fake"
        ):
            result = _run(fetch_direct_pdf(["https://publisher.example/paper.pdf"]))
        assert result == b"%PDF-1.4 fake"

    def test_skips_non_pdf_url_and_tries_the_next(self):
        with patch(
            "app.services.fulltext_service._is_pdf_url", side_effect=[False, True]
        ), patch("app.services.fulltext_service._download_pdf", return_value=b"%PDF-1.4 real"):
            result = _run(fetch_direct_pdf(["https://a.example/landing", "https://b.example/paper.pdf"]))
        assert result == b"%PDF-1.4 real"

    def test_no_urls_returns_none(self):
        assert _run(fetch_direct_pdf([])) is None
        assert _run(fetch_direct_pdf(None)) is None

    def test_non_http_url_is_skipped_without_a_network_call(self):
        with patch("app.services.fulltext_service._is_pdf_url") as mock_check:
            result = _run(fetch_direct_pdf(["file:///local/paper.pdf"]))
        assert result is None
        mock_check.assert_not_called()

    def test_known_piracy_mirror_is_skipped_without_a_network_call(self):
        with patch("app.services.fulltext_service._is_pdf_url") as mock_check:
            result = _run(fetch_direct_pdf(["https://sci-hub.se/10.1234/some-doi"]))
        assert result is None
        mock_check.assert_not_called()

    def test_all_candidates_fail_returns_none(self):
        with patch("app.services.fulltext_service._is_pdf_url", return_value=False):
            result = _run(fetch_direct_pdf(["https://a.example/landing", "https://b.example/landing"]))
        assert result is None


class TestProbeFullTextAvailability:
    """The pre-import check surfaced in the UI so a user can decide whether to
    import BEFORE committing.

    Patched at resolve_pdf_url / pmc_has_fulltext — the probe's actual
    collaborators — rather than at the raw lookups underneath them. Those two
    memoize into Redis, so reaching through them would make these tests both
    network-dependent AND order-dependent (one test's cached answer leaking
    into the next), which is exactly how they failed when this seam moved.
    """

    def test_verified_pdf_short_circuits_before_pmc_check(self):
        with patch(
            "app.services.fulltext_service.resolve_pdf_url_verbose",
            return_value=("https://example.com/paper.pdf", True),
        ), patch("app.services.fulltext_service.pmc_has_fulltext") as mock_pmc:
            result = _run(probe_full_text_availability("10.1234/has-oa-pdf", "12345"))
        assert result == "pdf"
        mock_pmc.assert_not_called()

    def test_inconclusive_pdf_check_reports_unknown_not_none(self):
        """A candidate that timed out is not evidence of absence. Saying
        "none" here would show the user "Link only" for a paper that may well
        have a PDF — the same false confidence, one layer up."""
        with patch(
            "app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, False)
        ), patch("app.services.fulltext_service.pmc_has_fulltext", return_value=False):
            result = _run(probe_full_text_availability("10.1234/all-candidates-stalled", "12345"))
        assert result == "unknown"

    def test_probe_that_blows_its_budget_reports_unknown(self):
        """Bounds how long the UI can sit on "Checking…" — per-candidate
        timeouts alone sum to minutes across several hosts."""
        async def _never_returns(*_args, **_kwargs):
            await asyncio.sleep(60)

        with patch("app.services.fulltext_service.PROBE_BUDGET", 0.05), patch(
            "app.services.fulltext_service.resolve_pdf_url_verbose", new=_never_returns
        ):
            result = _run(probe_full_text_availability("10.1234/slow", "12345"))
        assert result == "unknown"

    def test_no_fetchable_pdf_falls_back_to_pmc(self):
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service.pmc_has_fulltext", return_value=True
        ):
            result = _run(probe_full_text_availability("10.1234/no-oa-pdf", "12345"))
        assert result == "pmc"

    def test_neither_returns_none(self):
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service.pmc_has_fulltext", return_value=False
        ):
            result = _run(probe_full_text_availability("10.1234/nothing-available", "12345"))
        assert result == "none"

    def test_pmcid_without_a_body_is_not_reported_as_pmc(self):
        """The bug this check exists for: a PMC deposit can be front-matter
        only, so a successful ID lookup is not evidence of readable text. The
        old probe answered "pmc" on the ID alone and the import then landed
        abstract-only — the same existence-vs-content mistake the PDF path
        used to make."""
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service._pmc_id_lookup", return_value="PMC13383132"
        ), patch("app.services.fulltext_service._fetch_pmc_jats", return_value="<article><front/></article>"), patch(
            "app.services.fulltext_service.cache_service"
        ) as mock_cache:
            mock_cache.get.return_value = None  # force a real evaluation, not a cached one
            result = _run(probe_full_text_availability("10.1234/restricted-pmc", "12345"))
        assert result == "none"

    def test_no_doi_still_checks_pmc(self):
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service.pmc_has_fulltext", return_value=True
        ):
            result = _run(probe_full_text_availability(None, "12345"))
        assert result == "pmc"

    def test_raw_url_fallback_reports_pdf_when_unpaywall_and_pmc_fail(self):
        """The EndNote/RIS case: no DOI/PMID at all, but the source record
        carries a link that's a real PDF — the preview must promise the same
        thing the import's fetch_direct_pdf tier would actually deliver."""
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service.pmc_has_fulltext", return_value=False
        ), patch("app.services.fulltext_service._is_pdf_url", return_value=True):
            result = _run(probe_full_text_availability(None, None, None, ["https://publisher.example/paper.pdf"]))
        assert result == "pdf"

    def test_raw_url_that_isnt_a_pdf_still_reports_none(self):
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service.pmc_has_fulltext", return_value=False
        ), patch("app.services.fulltext_service._is_pdf_url", return_value=False):
            result = _run(probe_full_text_availability(None, None, None, ["https://publisher.example/landing"]))
        assert result == "none"

    def test_piracy_mirror_url_is_never_probed(self):
        with patch("app.services.fulltext_service.resolve_pdf_url_verbose", return_value=(None, True)), patch(
            "app.services.fulltext_service.pmc_has_fulltext", return_value=False
        ), patch("app.services.fulltext_service._is_pdf_url") as mock_check:
            result = _run(probe_full_text_availability(None, None, None, ["https://sci-hub.se/10.1234/x"]))
        assert result == "none"
        mock_check.assert_not_called()
