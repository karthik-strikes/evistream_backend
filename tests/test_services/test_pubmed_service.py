"""
Unit tests for the PubMed normalizer + stored-document serializer.

Uses real, saved upstream responses (backend/tests/fixtures/pmid_34878953_*)
for PMID 34878953 — the same Cooper SA et al. 2022 paper that's linked from
our NCT04307940 ClinicalTrials.gov test fixture — so `normalize_summary()`
is exercised against actual API shape, not a hand-rolled stand-in. No
network access — pure fixture-driven.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.pubmed_service import (  # noqa: E402
    build_document_content,
    content_hash_for_import,
    normalize_summary,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _load_esummary_doc() -> dict:
    with open(os.path.join(FIXTURES_DIR, "pmid_34878953_esummary.json")) as f:
        data = json.load(f)
    return data["result"]["34878953"]


def _load_abstract_text() -> str:
    with open(os.path.join(FIXTURES_DIR, "pmid_34878953_abstract.txt")) as f:
        return f.read()


class TestNormalizeSummary:
    def test_basic_fields(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        assert n["pmid"] == "34878953"
        assert n["sourceUrl"] == "https://pubmed.ncbi.nlm.nih.gov/34878953/"
        assert n["title"].startswith("Analgesic efficacy of naproxen sodium")

    def test_authors(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        assert "Cooper SA" in n["authors"]
        assert "Desjardins PJ" in n["authors"]
        assert len(n["authors"]) == 9

    def test_journal_and_year(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        # Prefers the full journal name over the abbreviation when present.
        assert n["journal"] == "Postgraduate medicine"
        assert n["year"] == "2022"
        assert n["pubDate"] == "2022 Jun"

    def test_doi_extracted_from_articleids(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        assert n["doi"] == "10.1080/00325481.2021.2008180"

    def test_pub_types(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        assert "Journal Article" in n["pubTypes"]
        assert "Randomized Controlled Trial" in n["pubTypes"]

    def test_error_doc_has_no_crash_risk(self):
        """PubMed 200s an invalid PMID with an embedded error field —
        normalize_summary itself doesn't need to special-case this (the
        service layer filters these out before calling normalize), but it
        must not raise if called with a minimal/malformed doc."""
        n = normalize_summary("999999999999", {"error": "cannot get document summary"})
        assert n["pmid"] == "999999999999"
        assert n["title"] is None
        assert n["authors"] == []


class TestBuildDocumentContent:
    def test_output_is_valid_parseable_json(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        n["abstractText"] = _load_abstract_text()
        content = build_document_content(n)
        parsed = json.loads(content)
        assert parsed["pmid"] == "34878953"
        assert "Analgesic efficacy" in parsed["abstractText"]

    def test_round_trips_exactly(self):
        n = normalize_summary("34878953", _load_esummary_doc())
        content = build_document_content(n)
        assert json.loads(content) == n


class TestContentHashForImport:
    def test_deterministic(self):
        assert content_hash_for_import("34878953") == content_hash_for_import("34878953")

    def test_different_pmids_differ(self):
        assert content_hash_for_import("34878953") != content_hash_for_import("11111111")

    def test_matches_ctgov_convention(self):
        """Same sha256(f"{source}:{id}") shape as
        clinical_trials_service.content_hash_for_import — just a different
        prefix, so the two never collide even numerically overlapping ids."""
        import hashlib

        expected = hashlib.sha256(b"pubmed:34878953").hexdigest()
        assert content_hash_for_import("34878953") == expected
