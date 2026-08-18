"""
Tests for `utils/consensus_summary.build_consensus_summary`.

The first coverage this logic has ever had. It previously lived inline in
`app/api/v1/results.py:get_consensus_summary`, which no test can import —
`storage_service` calls `sts:GetCallerIdentity` at module import, so the module
errors out without real AWS credentials. Four separate counting bugs lived there
undetected as a result; each one is pinned below.
"""

import pytest

from utils import absence
from utils.consensus_summary import build_consensus_summary


def _doc(doc_id, filename="paper.pdf", ref_id=1):
    return {"id": doc_id, "filename": filename, "ref_id": ref_id}


def _ai(doc_id, data):
    return {"document_id": doc_id, "extraction_type": "ai", "reviewer_role": None,
            "extracted_data": data}


def _manual(doc_id, data, role=None):
    return {"document_id": doc_id, "extraction_type": "manual", "reviewer_role": role,
            "extracted_data": data}


def _role_row(doc_id, role, data, extracted_by=None):
    return {"document_id": doc_id, "reviewer_role": role, "extracted_data": data,
            "extracted_by": extracted_by}


def _build(**kw):
    kw.setdefault("documents", [_doc("d1")])
    kw.setdefault("extraction_rows", [])
    kw.setdefault("role_rows", [])
    kw.setdefault("consensus_rows", [])
    kw.setdefault("adjudication_rows", [])
    kw.setdefault("assignment_rows", [])
    return build_consensus_summary(**kw)


def _only(result):
    assert len(result["documents"]) == 1
    return result["documents"][0]


# ═══════════════════════════════════════════════════════════════════════════
# Which row wins
# ═══════════════════════════════════════════════════════════════════════════

class TestRowSelection:
    def test_newest_ai_row_wins(self):
        """Rows arrive ordered created_at ASC. The old loop kept the FIRST one it
        saw, so the dashboard showed the oldest AI run while the review screen —
        which reads GET /results ordered created_at DESC — showed the newest. A
        document re-extracted after a prompt fix reported both numbers at once."""
        # The newest AI row says RCT, and the reviewer agrees with it. Comparing
        # against the stale row would report 0% instead of 100%.
        out = _only(_build(extraction_rows=[
            _ai("d1", {"design": "cohort"}),
            _ai("d1", {"design": "RCT"}),
            _manual("d1", {"design": "RCT"}),
        ]))
        assert out["has_ai"] is True
        assert out["agreement_pct"] == 100, "compared against the stale AI row"

    def test_partial_manual_draft_is_not_a_result(self):
        """Saving a half-filled form used to set has_manual and drag the document
        into the AI-vs-manual agreement math. The role path already skipped these;
        this path did not."""
        out = _only(_build(extraction_rows=[
            _ai("d1", {"design": "RCT"}),
            _manual("d1", {"design": "RCT", "_partial": True}),
        ]))
        assert out["has_manual"] is False
        assert out["agreement_pct"] is None

    def test_partial_r1_row_does_not_set_has_r1(self):
        out = _only(_build(role_rows=[
            _role_row("d1", "reviewer_1", {"design": "RCT", "_partial": True}),
        ]))
        assert out["has_r1"] is False

    def test_ai_vs_manual_prefers_the_r1_row(self):
        """With two manual rows the old code compared whichever sorted first, so
        the number moved depending on which reviewer saved first."""
        out = _only(_build(extraction_rows=[
            _ai("d1", {"design": "RCT"}),
            _manual("d1", {"design": "cohort"}, role="reviewer_1"),
            _manual("d1", {"design": "RCT"}),
        ]))
        assert out["agreement_pct"] == 0, "did not compare against the R1 row"

    def test_legacy_consensus_extraction_rows_are_ignored(self):
        rows = [{"document_id": "d1", "extraction_type": "consensus",
                 "reviewer_role": None, "extracted_data": {"design": "RCT"}}]
        out = _only(_build(extraction_rows=rows))
        assert out["has_ai"] is False and out["has_manual"] is False


# ═══════════════════════════════════════════════════════════════════════════
# The comparison denominator
# ═══════════════════════════════════════════════════════════════════════════

class TestFieldDenominator:
    def test_partial_flag_is_not_counted_as_a_field(self):
        """`_partial` lives inside extracted_data, so a naive key union counted it
        as a field: it inflated the total and, being absent on the AI side, always
        read as one disputed field."""
        out = _only(_build(extraction_rows=[
            _ai("d1", {"design": "RCT"}),
            _manual("d1", {"design": "RCT", "_partial": False}),
        ]))
        assert out["total_fields"] == 1
        assert out["agreement_pct"] == 100

    def test_plain_ai_key_without_a_value_sibling_still_counts(self):
        """The denominator fix: this path used to drop every plain key as soon as
        any `.value` key existed, so the dashboard counted fewer fields than the
        review screen."""
        out = _only(_build(extraction_rows=[
            _ai("d1", {"age.value": 58, "design": "RCT"}),
            _manual("d1", {"age": 58, "design": "RCT"}),
        ]))
        assert out["total_fields"] == 2
        assert out["agreement_pct"] == 100

    def test_ai_grounding_metadata_keys_are_not_fields(self):
        out = _only(_build(extraction_rows=[
            _ai("d1", {"age.value": 58, "age.source_text": "mean age 58",
                       "age.confidence": 0.9}),
            _manual("d1", {"age": 58}),
        ]))
        assert out["total_fields"] == 1
        assert out["agreement_pct"] == 100

    def test_agreement_uses_the_shared_comparator(self):
        """Two reviewers who both recorded a reporting gap agree; a failed cell is
        not agreement with a genuine NR."""
        nr = {"value": "NR", "status": absence.NOT_REPORTED}
        failed = {"value": "", "status": absence.ERROR}
        out = _only(_build(extraction_rows=[
            _ai("d1", {"a": nr, "b": failed, "c": "3"}),
            _manual("d1", {"a": nr, "b": nr, "c": 3}),
        ]))
        assert out["total_fields"] == 3
        # a agrees (both NR), c agrees ("3" == 3), b is incomparable → not agreed.
        assert out["agreement_pct"] == 67
        assert out["disputed_fields"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# A reviewed document reports the human's numbers
# ═══════════════════════════════════════════════════════════════════════════

class TestConsensusOverride:
    def test_whole_triple_comes_from_the_stored_row(self):
        """Only agreement_pct used to be overridden, so a reviewed row showed a
        human percentage next to machine-computed conflict counts."""
        out = _only(_build(
            extraction_rows=[
                _ai("d1", {"a": "x", "b": "y"}),
                _manual("d1", {"a": "x", "b": "DIFFERENT"}),
            ],
            consensus_rows=[{"document_id": "d1", "agreement_pct": 100,
                             "disputed_count": 0, "total_fields": 2}],
        ))
        assert (out["agreement_pct"], out["disputed_fields"], out["total_fields"]) == (100, 0, 2)

    def test_missing_stored_counts_fall_back_to_computed(self):
        out = _only(_build(
            extraction_rows=[
                _ai("d1", {"a": "x"}),
                _manual("d1", {"a": "x"}),
            ],
            consensus_rows=[{"document_id": "d1", "agreement_pct": None,
                             "disputed_count": None, "total_fields": None}],
        ))
        assert out["has_consensus"] is True
        assert (out["agreement_pct"], out["total_fields"]) == (100, 1)


# ═══════════════════════════════════════════════════════════════════════════
# Done counts
# ═══════════════════════════════════════════════════════════════════════════

class TestDoneCounts:
    def test_dual_reviewer_doc_is_counted_once(self):
        """One dual-reviewer submit writes BOTH a consensus row and an
        adjudication row. The frontend summed consensus_done + adjudication_done,
        so four finished papers read as "Consensus 8" — a number that can exceed
        total_docs. docs_done is the count to show."""
        result = _build(
            consensus_rows=[{"document_id": "d1", "agreement_pct": 90,
                             "disputed_count": 1, "total_fields": 10}],
            adjudication_rows=[{"document_id": "d1", "agreement_pct": 90,
                                "status": "completed"}],
        )
        s = result["summary"]
        assert s["consensus_done"] + s["adjudication_done"] == 2, "the old double count"
        assert s["docs_done"] == 1
        assert s["docs_done"] <= s["total_docs"]

    def test_in_progress_adjudication_is_not_done(self):
        result = _build(adjudication_rows=[
            {"document_id": "d1", "agreement_pct": None, "status": "in_progress"},
        ])
        out = _only(result)
        # has_adjudication stays "a row exists" on purpose: the review screen uses
        # it to decide whether to fetch saved resolutions back into the form, so
        # narrowing it would drop an adjudicator's in-progress work on reload.
        assert out["has_adjudication"] is True
        assert out["adjudication_status"] == "in_progress"
        assert out["has_adjudication_completed"] is False
        assert result["summary"]["docs_done"] == 0

    def test_completed_adjudication_alone_counts_as_done(self):
        result = _build(adjudication_rows=[
            {"document_id": "d1", "agreement_pct": 88.5, "status": "completed"},
        ])
        assert result["summary"]["docs_done"] == 1
        assert _only(result)["r1_r2_agreement_pct"] == pytest.approx(88.5)

    def test_avg_agreement_ignores_documents_with_no_number(self):
        result = build_consensus_summary(
            documents=[_doc("d1"), _doc("d2")],
            extraction_rows=[_ai("d1", {"a": "x"}), _manual("d1", {"a": "x"})],
            role_rows=[], consensus_rows=[], adjudication_rows=[], assignment_rows=[],
        )
        assert result["summary"]["avg_agreement_pct"] == 100
        assert result["summary"]["total_docs"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# Swap-aware reviewer credit (behaviour preserved verbatim — pin it)
# ═══════════════════════════════════════════════════════════════════════════

class TestReviewerRoleResolution:
    def test_role_tag_is_trusted_when_the_doc_has_no_assignments(self):
        out = _only(_build(role_rows=[
            _role_row("d1", "reviewer_2", {"a": "x"}, extracted_by="u1"),
        ]))
        assert (out["has_r1"], out["has_r2"]) == (False, True)

    def test_work_is_credited_to_the_authors_current_role(self):
        """Someone moved from R2 to R1 keeps credit for what they saved."""
        out = _only(_build(
            role_rows=[_role_row("d1", "reviewer_2", {"a": "x"}, extracted_by="u1")],
            assignment_rows=[{"document_id": "d1", "reviewer_role": "reviewer_1",
                              "reviewer_user_id": "u1"}],
        ))
        assert (out["has_r1"], out["has_r2"]) == (True, False)

    def test_stranded_row_counts_for_nobody(self):
        """The author is no longer assigned to this document at all."""
        out = _only(_build(
            role_rows=[_role_row("d1", "reviewer_1", {"a": "x"}, extracted_by="u1")],
            assignment_rows=[{"document_id": "d1", "reviewer_role": "reviewer_1",
                              "reviewer_user_id": "u2"}],
        ))
        assert (out["has_r1"], out["has_r2"]) == (False, False)

    def test_non_reviewer_roles_are_ignored(self):
        out = _only(_build(role_rows=[
            _role_row("d1", "adjudicator", {"a": "x"}, extracted_by="u1"),
        ]))
        assert (out["has_r1"], out["has_r2"]) == (False, False)


# ═══════════════════════════════════════════════════════════════════════════
# Shape
# ═══════════════════════════════════════════════════════════════════════════

class TestShape:
    def test_empty_project(self):
        result = build_consensus_summary(
            documents=[], extraction_rows=[], role_rows=[],
            consensus_rows=[], adjudication_rows=[], assignment_rows=[],
        )
        assert result["documents"] == []
        assert result["summary"]["total_docs"] == 0
        assert result["summary"]["docs_done"] == 0
        assert result["summary"]["avg_agreement_pct"] is None

    def test_document_order_is_preserved(self):
        result = build_consensus_summary(
            documents=[_doc("d2", "b.pdf"), _doc("d1", "a.pdf")],
            extraction_rows=[], role_rows=[], consensus_rows=[],
            adjudication_rows=[], assignment_rows=[],
        )
        assert [d["document_id"] for d in result["documents"]] == ["d2", "d1"]

    def test_every_documented_key_is_present(self):
        """The frontend's ConsensusSummaryDoc type depends on this shape."""
        out = _only(_build())
        assert set(out) == {
            "document_id", "filename", "ref_id", "has_ai", "has_manual",
            "has_consensus", "agreement_pct", "disputed_fields", "total_fields",
            "has_r1", "has_r2", "has_adjudication", "adjudication_status",
            "has_adjudication_completed", "r1_r2_agreement_pct",
        }

    def test_summary_keys(self):
        assert set(_build()["summary"]) == {
            "total_docs", "ai_done", "manual_done", "consensus_done",
            "avg_agreement_pct", "r1_done", "r2_done", "adjudication_done",
            "docs_done",
        }
