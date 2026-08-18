"""
Unit tests for utils/citation_identifiers.py — PMID/PMCID mining from URLs
carried by EndNote/RIS reference records (see endnote_service.py /
ris_service.py). The regexes are deliberately anchored to specific NCBI/Europe
PMC URL shapes; the false-positive tests guard against a loose "any digits in
the URL" match that would misfire on page counts, years, or tracking params.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from utils.citation_identifiers import extract_pmcid_from_urls, extract_pmid_from_urls  # noqa: E402


class TestExtractPmidFromUrls:
    def test_matches_modern_pubmed_url(self):
        assert extract_pmid_from_urls(["https://pubmed.ncbi.nlm.nih.gov/38472919/"]) == "38472919"

    def test_matches_legacy_ncbi_pubmed_url(self):
        assert extract_pmid_from_urls(["https://www.ncbi.nlm.nih.gov/pubmed/12345678"]) == "12345678"

    def test_ignores_unrelated_url_with_digits(self):
        """A publisher landing page with a numeric article ID must not be
        mistaken for a PMID — only recognized PubMed hosts/paths count."""
        assert extract_pmid_from_urls(["https://journals.example.com/article/12345678"]) is None

    def test_returns_first_match_across_multiple_urls(self):
        urls = ["https://doi.org/10.1016/j.example.2021", "https://pubmed.ncbi.nlm.nih.gov/99988877/"]
        assert extract_pmid_from_urls(urls) == "99988877"

    def test_empty_or_none_list_returns_none(self):
        assert extract_pmid_from_urls([]) is None
        assert extract_pmid_from_urls(None) is None


class TestExtractPmcidFromUrls:
    def test_matches_ncbi_pmc_url(self):
        assert extract_pmcid_from_urls(
            ["https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"]
        ) == "PMC1234567"

    def test_matches_pmc_ncbi_url(self):
        assert extract_pmcid_from_urls(["https://pmc.ncbi.nlm.nih.gov/articles/PMC7654321/"]) == "PMC7654321"

    def test_matches_europe_pmc_url(self):
        assert extract_pmcid_from_urls(["https://europepmc.org/articles/PMC1111111"]) == "PMC1111111"

    def test_lowercase_pmc_is_normalized_uppercase(self):
        assert extract_pmcid_from_urls(["https://europepmc.org/articles/pmc2222222"]) == "PMC2222222"

    def test_no_pmc_url_returns_none(self):
        assert extract_pmcid_from_urls(["https://publisher.example/paper.pdf"]) is None
