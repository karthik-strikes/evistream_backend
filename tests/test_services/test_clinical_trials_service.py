"""
Unit tests for the ClinicalTrials.gov normalizer + stored-document serializer.

Uses a real, saved upstream response (backend/tests/fixtures/nct04307940.json —
NCT04307940, a Bayer Phase 4 dental-pain trial with posted results) so
`normalize()`/`build_document_content()` are exercised against actual API
shape, not a hand-rolled stand-in. No network access — pure fixture-driven.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.clinical_trials_service import build_document_content, normalize, phase_agg_filter  # noqa: E402

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "..", "fixtures", "nct04307940.json")


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


class TestNormalize:
    def test_basic_identity_and_status(self):
        n = normalize(_load_fixture())
        assert n["nctId"] == "NCT04307940"
        assert n["status"]["overall"] == "COMPLETED"
        assert n["status"]["hasResults"] is True

    def test_design_and_type(self):
        n = normalize(_load_fixture())
        assert n["phase"] == ["PHASE4"]
        assert n["studyType"] == "INTERVENTIONAL"
        assert n["design"]["masking"] == "QUADRUPLE"
        assert n["design"]["allocation"] == "RANDOMIZED"

    def test_enrollment(self):
        n = normalize(_load_fixture())
        assert n["enrollment"]["count"] == 221
        assert n["enrollment"]["type"] == "ACTUAL"

    def test_sponsor(self):
        n = normalize(_load_fixture())
        assert n["sponsor"]["lead"] == "Bayer"

    def test_interventions(self):
        n = normalize(_load_fixture())
        names = [i["name"] for i in n["interventions"]]
        assert len(names) == 3
        assert any("Naproxen" in name for name in names)
        assert any("Hydrocodone" in name or "Acetaminophen" in name for name in names)
        assert any("Placebo" in name for name in names)

    def test_primary_outcome(self):
        n = normalize(_load_fixture())
        assert n["outcomes"]["primary"][0]["measure"].startswith(
            "Sum of Pain Intensity Difference Over 12 Hours"
        )

    def test_locations(self):
        n = normalize(_load_fixture())
        assert n["locations"][0]["city"] == "Salt Lake City"
        assert n["locations"][0]["country"] == "United States"
        assert n["locations"][0]["zip"] == "84107"

    def test_org_study_id(self):
        n = normalize(_load_fixture())
        assert n["orgStudyId"] == "20536"

    def test_oversight_fda_flags(self):
        n = normalize(_load_fixture())
        assert n["oversight"]["fdaRegulatedDrug"] is True
        assert n["oversight"]["fdaRegulatedDevice"] is False
        assert n["oversight"]["usExport"] is False

    def test_mesh_terms_combine_conditions_and_interventions(self):
        n = normalize(_load_fixture())
        # Direct + ancestor MeSH terms from both condition and intervention
        # browse modules, deduped.
        expected = {
            "Pain", "Neurologic Manifestations", "Signs and Symptoms",
            "Pathological Conditions, Signs and Symptoms", "Naproxen",
            "oxycodone-acetaminophen", "Naphthaleneacetic Acids", "Naphthalenes",
            "Polycyclic Aromatic Hydrocarbons", "Hydrocarbons, Aromatic",
            "Hydrocarbons, Cyclic", "Hydrocarbons", "Organic Chemicals",
            "Polycyclic Compounds",
        }
        assert set(n["meshTerms"]) == expected
        assert len(n["meshTerms"]) == len(expected)  # no duplicates

    def test_conditions_and_keywords(self):
        n = normalize(_load_fixture())
        assert n["conditions"] == ["Pain"]
        assert n["keywords"] == ["Postsurgical Dental Pain"]

    def test_official_title_captured(self):
        n = normalize(_load_fixture())
        assert n["title"]["official"].startswith(
            "A Randomized, Double-Blind, Placebo-Controlled Trial"
        )

    def test_documents_have_valid_cdn_urls(self):
        n = normalize(_load_fixture())
        labels = {d["label"]: d["url"] for d in n["documents"]}
        assert any("Protocol" in label for label in labels)
        for url in labels.values():
            assert url.startswith("https://cdn.clinicaltrials.gov/large-docs/40/NCT04307940/")

    def test_results_present_and_non_empty(self):
        n = normalize(_load_fixture())
        assert n["results"] is not None
        assert len(n["results"]["outcomeMeasures"]) > 0

    def test_no_results_gate(self):
        """hasResults=false must gate resultsSection off entirely, even if
        (hypothetically) present in the raw payload — never a KeyError."""
        study = _load_fixture()
        study["hasResults"] = False
        n = normalize(study)
        assert n["results"] is None

    def test_null_safety_on_missing_modules(self):
        """A sparsely-populated study (many sponsors omit fields) must never raise."""
        n = normalize({"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}})
        assert n["nctId"] == "NCT00000001"
        assert n["results"] is None
        assert n["locations"] == []
        assert n["interventions"] == []


class TestBuildDocumentContent:
    """build_document_content() is a pure JSON serializer of the normalized
    record — no hand-formatted prose. An earlier iteration rendered a mix of
    markdown prose + an embedded JSON block, which read as visually
    inconsistent; this applies one format (JSON) throughout instead."""

    def test_output_is_valid_parseable_json(self):
        n = normalize(_load_fixture())
        content = build_document_content(n)
        assert content.strip()
        parsed = json.loads(content)  # raises if not valid JSON
        assert parsed["nctId"] == "NCT04307940"

    def test_round_trips_the_normalized_record_exactly(self):
        n = normalize(_load_fixture())
        content = build_document_content(n)
        assert json.loads(content) == n

    def test_contains_key_top_level_fields(self):
        n = normalize(_load_fixture())
        parsed = json.loads(build_document_content(n))
        assert parsed["sponsor"]["lead"] == "Bayer"
        assert parsed["title"]["official"].startswith("A Randomized, Double-Blind")
        assert parsed["orgStudyId"] == "20536"
        assert parsed["oversight"]["fdaRegulatedDrug"] is True
        assert "Naproxen" in parsed["meshTerms"]
        assert parsed["conditions"] == ["Pain"]
        assert parsed["locations"][0]["zip"] == "84107"

    def test_no_results_serializes_as_null(self):
        n = normalize(_load_fixture())
        n["results"] = None
        parsed = json.loads(build_document_content(n))
        assert parsed["results"] is None

    def test_deep_results_data_survives_intact(self):
        """Participant flow / baseline characteristics / per-arm outcome
        stats / adverse events are deeply nested — verify nothing is lost
        or reshaped by round-tripping through the stored JSON."""
        n = normalize(_load_fixture())
        parsed = json.loads(build_document_content(n))
        results = parsed["results"]
        assert results["participantFlow"]["preAssignmentDetails"].startswith("Overall, 221 participants")
        assert results["baselineCharacteristics"]["measures"][0]["title"] == "Age, Continuous"
        assert results["outcomeMeasures"][0]["analyses"][0]["pValue"] == "0.001"
        assert results["adverseEvents"]["eventGroups"][0]["otherNumAffected"] == 2
        assert results["moreInfo"]["pointOfContact"]["organization"] == "Bayer"


class TestPhaseAggFilter:
    """`filter.phase` is NOT a real upstream param (confirmed against the
    live API — returns 400 'unknown parameter'). The only working phase
    filter is aggFilters=phase:<space-separated numeric codes>. These tests
    lock in the translation so a future edit can't silently regress it back
    to the broken `filter.phase` param."""

    def test_single_phase(self):
        assert phase_agg_filter("PHASE3") == "phase:3"

    def test_multiple_phases_are_space_separated_not_comma(self):
        # Comma-separated codes are rejected by the upstream API (400) —
        # must be a single space-joined string.
        result = phase_agg_filter("PHASE1,PHASE2,PHASE3,PHASE4")
        assert result == "phase:1 2 3 4"
        assert "," not in result

    def test_early_phase1_maps_to_code_0(self):
        assert phase_agg_filter("EARLY_PHASE1") == "phase:0"

    def test_unknown_phase_values_are_dropped_not_passed_through(self):
        assert phase_agg_filter("NOT_A_REAL_PHASE") is None
        assert phase_agg_filter("PHASE2,NOT_A_REAL_PHASE") == "phase:2"

    def test_empty_input_returns_none(self):
        assert phase_agg_filter("") is None
