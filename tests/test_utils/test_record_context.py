"""
Unit tests for record_context — the note prepended to imported documents so the
model knows it's reading a registry/citation record rather than a paper.

Network-free by construction (the module only parses a string), so unlike the
fulltext/pubmed services there's nothing to mock here.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services import extraction_service  # noqa: E402,F401  (import smoke-check for the wiring)
from utils.record_context import preamble_for  # noqa: E402


class TestPassthrough:
    """Anything that isn't a JSON object must come back with no preamble —
    a PDF-derived document has to reach the model byte-for-byte."""

    def test_markdown_gets_no_preamble(self):
        assert preamble_for("# A Paper\n\nMethods: we did things.") == ""

    def test_markdown_starting_with_a_brace_is_still_not_json(self):
        assert preamble_for("{ this is not json, just a brace") == ""

    def test_empty_input(self):
        assert preamble_for("") == ""

    def test_json_array_root_gets_no_preamble(self):
        # Only object records carry the keys we identify on.
        assert preamble_for('[{"nctId": "NCT1"}]') == ""

    def test_empty_object(self):
        assert preamble_for("{}") == ""


class TestRecordKinds:
    def test_ctgov_record_is_named_and_warned_about_thinness(self):
        out = preamble_for(json.dumps({"nctId": "NCT04307940", "title": {"brief": "x"}}))
        assert "ClinicalTrials.gov trial registry record" in out
        assert "return NR" in out

    def test_pubmed_with_pmc_full_text_points_at_the_body(self):
        out = preamble_for(json.dumps({"pmid": "36631957", "fullText": [{"section": "Intro", "text": "..."}]}))
        assert "PubMed Central" in out
        assert "fullText" in out

    def test_pubmed_abstract_only_says_so(self):
        out = preamble_for(json.dumps({"pmid": "36631957", "abstractText": "Some abstract."}))
        assert "abstract" in out.lower()
        assert "return NR" in out

    def test_empty_full_text_is_treated_as_abstract_only(self):
        """`fullText: []` means PMC had no body — the record is thin, and
        saying otherwise would tell the model to look for text that isn't there."""
        out = preamble_for(json.dumps({"pmid": "1", "fullText": [], "abstractText": "a"}))
        assert "PubMed Central" not in out

    def test_bibliographic_reference(self):
        out = preamble_for(json.dumps({"title": "A paper", "authors": ["X"], "doi": "10.1/x"}))
        assert "bibliographic reference" in out

    def test_unrecognised_record_still_gets_the_quoting_rule(self):
        out = preamble_for(json.dumps({"someKey": "someValue"}))
        assert out != ""
        assert "quote the VALUE" in out or "quote the field's value" in out.lower()


class TestPreambleShape:
    def test_every_kind_carries_the_quote_rule(self):
        for record in (
            {"nctId": "NCT1"},
            {"pmid": "1", "fullText": [{"section": None, "text": "t"}]},
            {"pmid": "1"},
            {"title": "t"},
        ):
            out = preamble_for(json.dumps(record))
            assert "JSON syntax" in out, record

    def test_preamble_is_fenced_so_it_cannot_be_quoted_as_evidence(self):
        out = preamble_for(json.dumps({"nctId": "NCT1"}))
        assert out.startswith("<!--")
        assert "END DOCUMENT NOTE" in out

    def test_preamble_ends_with_a_blank_line_so_it_never_fuses_to_the_record(self):
        out = preamble_for(json.dumps({"nctId": "NCT1"}))
        assert out.endswith("\n\n")

    def test_record_body_is_untouched(self):
        """The whole point of prepending rather than rewriting: quotes stay
        literal substrings of the stored file."""
        raw = json.dumps({"nctId": "NCT1", "summary": "A study of things."})
        combined = preamble_for(raw) + raw
        assert combined.endswith(raw)
