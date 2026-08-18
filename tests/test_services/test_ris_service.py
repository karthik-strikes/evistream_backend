"""
Unit tests for ris_service.py's RIS parsing — specifically the identifier
mining added alongside the EndNote importer's (see test_endnote_service.py):
keeping every `UR` link (not just the first) and mining a DOI/PMID/PMCID out
of them when the record's own DO/AN tags don't have one.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.ris_service import parse_ris  # noqa: E402


def _ris(*lines: str) -> str:
    return "TY  - JOUR\n" + "\n".join(lines) + "\nER  - \n"


class TestUrlList:
    def test_all_ur_lines_kept_not_just_first(self):
        text = _ris(
            "TI  - A paper",
            "UR  - https://publisher.example/landing",
            "UR  - https://publisher.example/paper.pdf",
        )
        rec = parse_ris(text)[0]
        assert rec["urls"] == ["https://publisher.example/landing", "https://publisher.example/paper.pdf"]
        assert rec["url"] == "https://publisher.example/landing"  # first-for-display, unchanged behavior

    def test_non_http_ur_dropped_from_fallback_list(self):
        text = _ris("TI  - A paper", "UR  - file:///local/paper.pdf")
        rec = parse_ris(text)[0]
        assert rec["urls"] == []
        assert rec["url"] == "file:///local/paper.pdf"


class TestDoiFallback:
    def test_do_tag_wins_over_url_doi(self):
        text = _ris(
            "TI  - A paper",
            "DO  - 10.1000/real",
            "UR  - https://doi.org/10.9999/decoy",
        )
        assert parse_ris(text)[0]["doi"] == "10.1000/real"

    def test_falls_back_to_doi_org_link_when_do_tag_absent(self):
        text = _ris("TI  - A paper", "UR  - https://doi.org/10.1016/j.example.2021.01.001")
        assert parse_ris(text)[0]["doi"] == "10.1016/j.example.2021.01.001"


class TestPmidPmcidFallback:
    def test_an_tag_pmid_wins_over_url_pmid(self):
        text = _ris(
            "TI  - A paper",
            "AN  - 11112222",
            "UR  - https://pubmed.ncbi.nlm.nih.gov/99998888/",
        )
        assert parse_ris(text)[0]["pmid"] == "11112222"

    def test_falls_back_to_pubmed_url_when_an_tag_absent(self):
        text = _ris("TI  - A paper", "UR  - https://pubmed.ncbi.nlm.nih.gov/38472919/")
        assert parse_ris(text)[0]["pmid"] == "38472919"

    def test_pmcid_mined_from_pmc_url(self):
        text = _ris("TI  - A paper", "UR  - https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/")
        assert parse_ris(text)[0]["pmcid"] == "PMC1234567"

    def test_no_pmc_url_leaves_pmcid_none(self):
        text = _ris("TI  - A paper", "UR  - https://publisher.example/paper.pdf")
        assert parse_ris(text)[0]["pmcid"] is None
