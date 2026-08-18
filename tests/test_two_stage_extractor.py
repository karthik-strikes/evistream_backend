"""Unit tests for the two-stage (row-first + per-column) composite extractor.

Tests cover:
- Factory selection in build_schema_classes
- Stage 1 → Stage 2 execution and result zipping
- Failure paths (Stage 1 empty, Stage 2 column failure, short Stage 2 list)
- Backward compat: sigs without extraction_strategy use single-call path
"""

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Minimal schema_def fixtures
# ---------------------------------------------------------------------------

def _make_sig_def(extraction_strategy=None, anchor_columns=None, subform_fields=None):
    """Build a minimal schema_def signature with one List output field."""
    of = {
        "name": "results",
        "type": "List[Dict[str, Any]]",
        "description": "Test table field.",
        "subform_fields": subform_fields or [],
    }
    if extraction_strategy:
        of["extraction_strategy"] = extraction_strategy
    if anchor_columns is not None:
        of["anchor_columns"] = anchor_columns
    return {
        "class_name": "TestSig",
        "docstring": "Test signature.",
        "input_fields": [{"name": "markdown_content", "type": "str", "desc": "paper text"}],
        "output_fields": [of],
    }


def _make_schema_def(sig_def):
    return {
        "task_name": "test",
        "signatures": [sig_def],
        "pipeline_stages": [{"stage": 1, "signatures": ["TestSig"], "requires_fields": []}],
        "fallback_structures": {},
    }


SUBFIELDS = [
    {"field_name": "outcome_name", "field_type": "text", "field_description": "Name", "hints": [], "rules": []},
    {"field_name": "followup_point", "field_type": "text", "field_description": "Timepoint", "hints": [], "rules": []},
    {"field_name": "variability_sd", "field_type": "text", "field_description": "SD", "hints": ["find SD"], "rules": ["NR if absent"]},
    {"field_name": "central_value", "field_type": "text", "field_description": "Mean", "hints": [], "rules": []},
]



def _pred(**fields):
    """Mock LM returns must be dspy.Prediction, NOT a plain dict.

    Prediction subclasses Example, not dict. Mocking with dicts let a real
    production bug through for a whole day: `out.get(f) if isinstance(out, dict)`
    read None on every real call while passing every test.
    """
    import dspy
    return dspy.Prediction(**fields)

# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------

class TestFactorySelection:
    def test_two_stage_factory_selected(self):
        """build_schema_classes returns a _is_keyed_pipeline factory for row_then_columns."""
        import dspy  # noqa: must be importable
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_keyed_pipeline", False) is True
        assert cls._field_name == "results"
        assert set(cls._key_cols) == {"outcome_name", "followup_point"}
        assert set(cls._attr_cols) == {"variability_sd", "central_value"}

    def test_single_call_factory_when_no_strategy(self):
        """build_schema_classes falls back to single-call when no extraction_strategy."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(subform_fields=SUBFIELDS)
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        cls = factories["TestSig"]
        assert getattr(cls, "_is_keyed_pipeline", False) is False

    def test_single_call_factory_when_no_anchors(self):
        """Misconfigured row_then_columns (empty anchor_columns) falls back to single-call."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=[],
            subform_fields=SUBFIELDS,
        )
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        assert getattr(factories["TestSig"], "_is_keyed_pipeline", False) is False

    def test_single_call_factory_for_mixed_sig(self):
        """A sig with 2 output fields skips two-stage even if first is row_then_columns."""
        from dspy_components.runtime_builders import build_schema_classes

        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name"],
            subform_fields=SUBFIELDS,
        )
        # Add a second output field
        sig_def["output_fields"].append({"name": "study_id", "type": "Dict[str, Any]", "description": "ID"})
        factories = build_schema_classes(_make_schema_def(sig_def), "test")
        assert getattr(factories["TestSig"], "_is_keyed_pipeline", False) is False


# ---------------------------------------------------------------------------
# Execution logic
# ---------------------------------------------------------------------------

class TestTwoStageExecution:
    """Patch async_dspy_forward so we never hit a real LLM.

    NOTE these drive the PER-ROW FALLBACK, not the batched main pass. Their mocks
    answer only `row_anchor`, so the Phase 4 batch call returns nothing usable,
    repair also comes back empty, and the extractor drops to per-row calls — which
    is exactly the degradation path worth pinning. The batched main pass is
    covered by TestBatchedMainPass; per-row cell semantics (NR vs NA vs failure)
    are covered here because a single row is the clearest way to assert them.
    """

    def _make_extractor(self, subfields=None):
        from dspy_components.runtime_builders import build_keyed_extractor_class
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=subfields or SUBFIELDS,
        )
        return build_keyed_extractor_class(sig_def, sig_def["output_fields"][0], "test")

    @staticmethod
    def _run(ExtractorCls, mock_forward):
        async def go():
            with patch(
                "dspy_components.runtime_builders.async_dspy_forward",
                side_effect=mock_forward,
            ):
                return await ExtractorCls()("paper text here")

        # asyncio.run, not get_event_loop: the latter raises once any earlier
        # test in the session has consumed the thread's default loop.
        return asyncio.run(go())

    @staticmethod
    def _anchor(name, point):
        return {
            "outcome_name": {"value": name, "source_text": name},
            "followup_point": {"value": point, "source_text": point},
        }

    def test_stage1_and_stage2_zip_per_row(self):
        ExtractorCls = self._make_extractor()
        s1_rows = [self._anchor("Pain VAS", "7 days"), self._anchor("Bone level", "6 months")]
        per_row = {
            "Pain VAS": {
                "variability_sd": {"value": "12", "source_text": "SD 12"},
                "central_value": {"value": "34", "source_text": "mean 34"},
            },
            "Bone level": {
                "variability_sd": {"value": "0.1", "source_text": "SD 0.1"},
                "central_value": {"value": "0.3", "source_text": "mean 0.3"},
            },
        }

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return _pred(results=s1_rows)
            anchor = json.loads(kwargs["row_anchor"])
            return per_row[anchor["outcome_name"]]

        envelope = self._run(ExtractorCls, mock_forward)["results"]
        assert envelope["status"] == "reported"
        rows = envelope["value"]
        assert len(rows) == 2
        assert rows[0]["outcome_name"]["value"] == "Pain VAS"
        assert rows[0]["variability_sd"]["value"] == "12"
        assert rows[1]["central_value"]["value"] == "0.3"
        assert all(c["status"] == "reported" for r in rows for c in r.values())

    def test_stage1_empty_is_explicit_not_reported(self):
        """No rows found is an answer about the paper, not a silent failure."""
        ExtractorCls = self._make_extractor()

        async def mock_forward(predictor, **kwargs):
            return _pred(results=[])

        result = self._run(ExtractorCls, mock_forward)
        assert result == {
            "results": {"value": "NR", "source_text": "NR", "status": "not_reported"}
        }

    def test_stage1_exception_propagates(self):
        """Stage 1 failure reaches the retry machinery instead of looking empty."""
        ExtractorCls = self._make_extractor()

        async def mock_forward(predictor, **kwargs):
            raise RuntimeError("LLM timeout")

        with pytest.raises(RuntimeError, match="LLM timeout"):
            self._run(ExtractorCls, mock_forward)

    def test_stage2_row_failure_is_error_not_nr(self):
        """A Stage 2 crash must not masquerade as "the paper does not report it"."""
        ExtractorCls = self._make_extractor()
        s1_rows = [self._anchor("Pain VAS", "7 days")]

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return _pred(results=s1_rows)
            raise RuntimeError("stage 2 failure")

        rows = self._run(ExtractorCls, mock_forward)["results"]["value"]
        assert len(rows) == 1
        for col in ("variability_sd", "central_value"):
            cell = rows[0][col]
            assert cell["status"] == "error"
            assert cell["value"] == ""
            assert "stage 2 failure" in cell["error"]

    def test_stage2_omitted_column_is_missing_not_nr(self):
        ExtractorCls = self._make_extractor()
        s1_rows = [self._anchor("A", "7d")]

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return _pred(results=s1_rows)
            return {"central_value": {"value": "10", "source_text": "mean 10"}}

        row = self._run(ExtractorCls, mock_forward)["results"]["value"][0]
        assert row["central_value"]["status"] == "reported"
        assert row["variability_sd"]["status"] == "missing"
        assert row["variability_sd"]["value"] == ""

    def test_declared_absence_option_is_a_finding(self):
        """A column declaring "None" as an option: that answer is data, not a
        reporting gap. This is the 47-field bug in the shipped zforms/ fixtures."""
        subfields = [
            SUBFIELDS[0],
            SUBFIELDS[1],
            {"field_name": "variability_sd", "field_type": "text",
             "field_description": "SD", "hints": [], "rules": []},
            {"field_name": "funding", "field_type": "select",
             "field_description": "Funding source",
             "options": ["Industry", "Public", "None"], "hints": [], "rules": []},
        ]
        ExtractorCls = self._make_extractor(subfields)
        s1_rows = [self._anchor("A", "7d")]

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return _pred(results=s1_rows)
            return {
                "variability_sd": {"value": "5", "source_text": "SD 5"},
                "funding": {"value": "none", "source_text": "The study received no funding."},
            }

        row = self._run(ExtractorCls, mock_forward)["results"]["value"][0]
        assert row["funding"]["status"] == "reported"
        # canonicalized to the author's declared spelling
        assert row["funding"]["value"] == "None"

    def test_bare_na_needs_design_grounding(self):
        """NA claims the study design excludes the field, so it must quote that
        design statement; an ungrounded NA falls back to the safer NR."""
        ExtractorCls = self._make_extractor()
        s1_rows = [self._anchor("A", "7d")]

        async def mock_forward(predictor, **kwargs):
            if "row_anchor" not in kwargs:
                return _pred(results=s1_rows)
            return {
                "variability_sd": {"value": "NA", "source_text": ""},
                "central_value": {
                    "value": "NA",
                    "source_text": "This was a single-dose trial with no second timepoint.",
                },
            }

        row = self._run(ExtractorCls, mock_forward)["results"]["value"][0]
        assert row["variability_sd"]["status"] == "not_reported"
        assert row["central_value"]["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Census check (Phase 2): audit the candidate row plan before locking it
# ---------------------------------------------------------------------------

PAPER = """# A trial of two regimens

Pain was assessed on a VAS at 7 days and again at 6 months.

| Outcome | Timepoint | Mean | SD |
| --- | --- | --- | --- |
| Pain VAS | 7 days | 34 | 12 |
| Pain VAS | 6 months | 21 | 9 |

Bone level was also recorded at 6 months in both arms.
"""


class TestCensusCheck:
    """The checker may only ADD rows, and only rows whose quote resolves in the
    paper. Every failure path must leave Stage 1's plan exactly as it was —
    a checker can never make the table worse than row discovery left it.
    """

    def _make_extractor(self):
        from dspy_components.runtime_builders import build_keyed_extractor_class
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        ExtractorCls = build_keyed_extractor_class(
            sig_def, sig_def["output_fields"][0], "test"
        )
        # The checker is unconditional — assert it so these tests can never pass
        # vacuously by silently skipping the audit.
        assert ExtractorCls._recall_audit_class is not None, "census signature was not built"
        return ExtractorCls

    @staticmethod
    def _anchor(name, point):
        return {
            "outcome_name": {"value": name, "source_text": name},
            "followup_point": {"value": point, "source_text": point},
        }

    def _run(self, proposed, *, raise_in_checker=False):
        """One Stage-1 row; the checker proposes `proposed`. Returns final rows."""
        from dspy_components.runtime_builders import build_keyed_extractor_class
        ExtractorCls = self._make_extractor()
        s1_rows = [self._anchor("Pain VAS", "7 days")]

        async def mock_forward(predictor, **kwargs):
            if "candidate_row_plan" in kwargs:
                if raise_in_checker:
                    raise RuntimeError("checker exploded")
                return _pred(missing_rows=proposed)
            if "row_anchor" not in kwargs:
                return _pred(results=s1_rows)
            return {
                "variability_sd": {"value": "9", "source_text": "SD 9"},
                "central_value": {"value": "21", "source_text": "mean 21"},
            }

        async def go():
            with patch(
                "dspy_components.runtime_builders.async_dspy_forward",
                side_effect=mock_forward,
            ):
                return await ExtractorCls()(PAPER)

        return asyncio.run(go())["results"]["value"]

    @staticmethod
    def _keys(rows):
        return {(r["outcome_name"]["value"], r["followup_point"]["value"]) for r in rows}

    def test_verified_addition_is_merged(self):
        rows = self._run([{
            "outcome_name": "Pain VAS",
            "followup_point": "6 months",
            "evidence_quote": "| Pain VAS | 6 months | 21 | 9 |",
        }])
        assert len(rows) == 2
        assert ("Pain VAS", "6 months") in self._keys(rows)
        # The addition went through Stage 2 like any other row.
        added = next(r for r in rows if r["followup_point"]["value"] == "6 months")
        assert added["central_value"]["value"] == "21"

    def test_prose_quote_also_verifies(self):
        rows = self._run([{
            "outcome_name": "Bone level",
            "followup_point": "6 months",
            "evidence_quote": "Bone level was also recorded at 6 months in both arms.",
        }])
        assert ("Bone level", "6 months") in self._keys(rows)

    def test_unverifiable_quote_is_rejected(self):
        rows = self._run([{
            "outcome_name": "Swelling",
            "followup_point": "3 days",
            "evidence_quote": "Swelling was measured with calipers at three days.",
        }])
        assert len(rows) == 1, "a quote absent from the paper must not add a row"

    def test_missing_quote_is_rejected(self):
        rows = self._run([{"outcome_name": "Swelling", "followup_point": "3 days"}])
        assert len(rows) == 1

    def test_nr_quote_is_rejected(self):
        rows = self._run([{
            "outcome_name": "Swelling", "followup_point": "3 days", "evidence_quote": "NR",
        }])
        assert len(rows) == 1

    def test_incomplete_identity_is_rejected(self):
        rows = self._run([{
            "outcome_name": "Pain VAS",
            "followup_point": "",
            "evidence_quote": "| Pain VAS | 6 months | 21 | 9 |",
        }])
        assert len(rows) == 1, "a row missing an identity column is not a row"

    def test_duplicate_of_existing_row_is_ignored(self):
        rows = self._run([{
            "outcome_name": "pain vas",           # case-insensitive match
            "followup_point": "7 days",
            "evidence_quote": "| Pain VAS | 7 days | 34 | 12 |",
        }])
        assert len(rows) == 1

    def test_empty_additions_leave_plan_unchanged(self):
        assert len(self._run([])) == 1

    def test_non_list_output_leaves_plan_unchanged(self):
        assert len(self._run({"not": "a list"})) == 1

    def test_checker_failure_keeps_candidate_plan(self):
        rows = self._run([], raise_in_checker=True)
        assert len(rows) == 1
        assert ("Pain VAS", "7 days") in self._keys(rows)

    def test_real_quote_about_a_different_row_is_rejected(self):
        """The quote exists verbatim, but names none of the proposed row's
        identity values — the Cartesian-assembly failure. Verifying that text
        exists is not the same as verifying it supports THIS row."""
        rows = self._run([{
            "outcome_name": "Swelling",
            "followup_point": "3 days",
            "evidence_quote": "Bone level was also recorded at 6 months in both arms.",
        }])
        assert len(rows) == 1

    def test_quote_naming_one_identity_value_is_enough(self):
        """A table-row quote often carries only part of the identity (the rest
        lives in the column header), so one match is the floor, not all."""
        rows = self._run([{
            "outcome_name": "Bone level",
            "followup_point": "6 months",
            "evidence_quote": "Bone level was also recorded at 6 months in both arms.",
        }])
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Phase 3: plan-vs-output check + batched targeted repair
# ---------------------------------------------------------------------------

class TestPlanVsOutputRepair:
    """Every locked row must come back with values. A row whose value cells ALL
    failed is refilled in a batched call; a row that deliberately answered NR is
    left alone, because NR is an answer and repairing it would burn calls chasing
    numbers the paper never reported.
    """

    def _make_extractor(self, n_attr_cols=2):
        from dspy_components.runtime_builders import build_keyed_extractor_class
        subs = [
            {"field_name": "outcome_name", "field_type": "text", "field_description": "Name"},
            {"field_name": "followup_point", "field_type": "text", "field_description": "When"},
        ] + [
            {"field_name": f"v{i}", "field_type": "text", "field_description": f"Value {i}"}
            for i in range(n_attr_cols)
        ]
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=subs,
        )
        ExtractorCls = build_keyed_extractor_class(sig_def, sig_def["output_fields"][0], "test")
        assert ExtractorCls._set_slot_fill_class is not None, "batched value signature was not built"
        return ExtractorCls

    @staticmethod
    def _anchor(name, point):
        return {
            "outcome_name": {"value": name, "source_text": name},
            "followup_point": {"value": point, "source_text": point},
        }

    def _run(self, n_rows, stage2, repair, n_attr_cols=2, batch_size=10,
             main_attempts=None):
        """Drive one extraction and report what each phase was asked to do.

        Since Phase 4 the MAIN value pass is itself a batched `rows_to_fill` call,
        so main-pass and repair calls are told apart by order: the first batches
        are the main pass, anything after is repair. `batch_size` is pinned so the
        split does not depend on whichever model the test environment has
        configured.

        stage2(identity) -> dict of value cells, or an Exception to fail the batch
        repair(payload)  -> list of filled row dicts
        """
        ExtractorCls = self._make_extractor(n_attr_cols)
        s1_rows = [self._anchor(f"O{i}", "7d") for i in range(n_rows)]
        # A batch that lands nothing is retried once, so a failing main pass
        # issues twice as many batch calls before repair begins. Tests whose
        # main pass fails pass main_attempts explicitly.
        n_main = main_attempts if main_attempts is not None else -(-n_rows // batch_size)
        calls = {"main": 0, "repair": 0, "main_payloads": [], "repair_payloads": []}

        async def mock_forward(predictor, **kwargs):
            if "rows_to_fill" in kwargs:
                payload = json.loads(kwargs["rows_to_fill"])
                if calls["main"] < n_main:
                    calls["main"] += 1
                    calls["main_payloads"].append(payload)
                    filled = []
                    for p in payload:
                        vals = stage2(p)
                        if isinstance(vals, Exception):
                            return _pred(filled_rows=[])   # the whole batch failed
                        filled.append({**p, **vals})
                    return _pred(filled_rows=filled)
                calls["repair"] += 1
                calls["repair_payloads"].append(payload)
                return _pred(filled_rows=repair(payload))
            if "candidate_row_plan" in kwargs:
                return _pred(missing_rows=[])
            if "row_anchor" in kwargs:
                calls["per_row"] = calls.get("per_row", 0) + 1
                return {}                       # per-row fallback finds nothing
            return _pred(results=s1_rows)

        async def go():
            with patch(
                "dspy_components.runtime_builders.async_dspy_forward",
                side_effect=mock_forward,
            ), patch(
                "dspy_components.runtime_builders._records_per_call",
                return_value=batch_size,
            ), patch(
                # The batched main pass is opt-in in production; force it on so
                # these assert the batched path rather than the per-row fallback.
                "dspy_components.runtime_builders._SET_AT_A_TIME", True,
            ):
                return await ExtractorCls()("paper text here")

        return asyncio.run(go())["results"], calls

    def test_all_failed_row_is_repaired(self):
        def stage2(a):
            return RuntimeError("stage 2 died")

        def repair(payload):
            return [
                {"outcome_name": p["outcome_name"], "followup_point": p["followup_point"],
                 "v0": {"value": "12", "source_text": "SD 12"},
                 "v1": {"value": "34", "source_text": "mean 34"}}
                for p in payload
            ]

        env, calls = self._run(2, stage2, repair, main_attempts=2)
        assert calls["repair"] == 1, "two failed rows must cost ONE batched repair call"
        assert env["status"] == "reported"
        assert all(r["v0"]["value"] == "12" for r in env["value"])

    def test_deliberate_nr_is_not_repaired(self):
        def stage2(a):
            return {"v0": {"value": "NR", "source_text": "NR"},
                    "v1": {"value": "NR", "source_text": "NR"}}

        env, calls = self._run(3, stage2, lambda p: [])
        assert calls["repair"] == 0, "NR is an answer — it must not trigger repair"
        assert env["status"] == "reported"

    def test_partially_answered_row_is_not_repaired(self):
        def stage2(a):
            return {"v0": {"value": "12", "source_text": "SD 12"}}   # v1 omitted

        env, calls = self._run(1, stage2, lambda p: [])
        assert calls["repair"] == 0, "one real value means the row was answered"

    def test_repair_matches_by_identity_not_position(self):
        def stage2(a):
            return RuntimeError("boom")

        def repair(payload):
            rows = [
                {"outcome_name": p["outcome_name"], "followup_point": p["followup_point"],
                 "v0": {"value": p["outcome_name"], "source_text": "q"},
                 "v1": {"value": "x", "source_text": "q"}}
                for p in payload
            ]
            return list(reversed(rows))          # reordered on purpose

        env, _ = self._run(3, stage2, repair, main_attempts=2)
        for r in env["value"]:
            assert r["v0"]["value"] == r["outcome_name"]["value"], "values landed on the wrong row"

    def test_row_not_in_the_plan_is_ignored(self):
        def stage2(a):
            return RuntimeError("boom")

        def repair(payload):
            return [{"outcome_name": "INVENTED", "followup_point": "99d",
                     "v0": {"value": "1", "source_text": "q"},
                     "v1": {"value": "2", "source_text": "q"}}]

        env, _ = self._run(1, stage2, repair, main_attempts=2)
        assert len(env["value"]) == 1
        assert env["value"][0]["outcome_name"]["value"] == "O0"
        assert env["status"] == "partial", "the real row is still unfilled"

    def test_unrepairable_rows_mark_the_field_partial(self):
        def stage2(a):
            return RuntimeError("boom")

        env, calls = self._run(2, stage2, lambda p: [], main_attempts=2)
        assert env["status"] == "partial"
        assert env["unfilled_rows"] == 2
        assert calls["repair"] == 1, "an empty repair must not loop"

    def test_main_pass_splits_into_batches(self):
        def stage2(a):
            return {"v0": {"value": "1", "source_text": "q"},
                    "v1": {"value": "2", "source_text": "q"}}

        env, calls = self._run(12, stage2, lambda p: [], batch_size=10)
        assert calls["main"] == 2, "12 rows must split into 2 batched calls"
        assert [len(p) for p in calls["main_payloads"]] == [10, 2]
        assert calls["repair"] == 0
        assert len(env["value"]) == 12

    def test_repair_batches_too(self):
        def stage2(a):
            return RuntimeError("boom")

        def repair(payload):
            return [
                {"outcome_name": p["outcome_name"], "followup_point": p["followup_point"],
                 "v0": {"value": "1", "source_text": "q"}, "v1": {"value": "2", "source_text": "q"}}
                for p in payload
            ]

        _, calls = self._run(12, stage2, repair, batch_size=10, main_attempts=4)
        assert calls["main"] == 4, "2 batches, each retried once"
        assert calls["repair"] == 2, "12 unfilled rows must be repaired in 2 calls"
        assert [len(p) for p in calls["repair_payloads"]] == [10, 2]


class _FakeLM:
    def __init__(self, max_tokens):
        self.kwargs = {"max_tokens": max_tokens}
        self.model = "anthropic/claude-sonnet-5"


class _FakeCoT:
    def __init__(self, max_tokens):
        self.lm = _FakeLM(max_tokens)


class TestBatchSizing:
    """Batch size comes from the ceiling of the model ACTUALLY serving the call.
    Sizing against the worst-case fallback floor instead capped every batch at
    ~10 rows even on Claude, which has 6× the room.
    """

    def test_claude_carries_far_more_than_ten_rows(self):
        from dspy_components.runtime_builders import _records_per_call
        # 13 value columns is the widest live table.
        n = _records_per_call(13, _FakeCoT(64000))
        assert n >= 25, f"Claude's 64k budget should carry 25+ rows, got {n}"

    def test_smaller_ceilings_shrink_the_batch(self):
        from dspy_components.runtime_builders import _records_per_call
        claude = _records_per_call(13, _FakeCoT(64000))
        gpt4o = _records_per_call(13, _FakeCoT(16384))
        floor = _records_per_call(13, _FakeCoT(8192))
        assert claude > gpt4o > floor

    def test_wide_tables_shrink_the_batch(self):
        from dspy_components.runtime_builders import _records_per_call
        assert _records_per_call(60, _FakeCoT(8192)) < _records_per_call(2, _FakeCoT(8192))

    def test_never_zero(self):
        from dspy_components.runtime_builders import _records_per_call
        assert _records_per_call(5000, _FakeCoT(8192)) == 1

    def test_capped_so_one_call_never_carries_a_whole_huge_table(self):
        from dspy_components.runtime_builders import _MAX_RECORDS_PER_CALL, _records_per_call
        assert _records_per_call(1, _FakeCoT(128000)) == _MAX_RECORDS_PER_CALL

    def test_unknown_model_falls_back_to_the_conservative_floor(self):
        from dspy_components.runtime_builders import _active_output_ceiling, _FALLBACK_OUTPUT_CEILING

        class _Broken:
            @property
            def lm(self):
                raise RuntimeError("no lm here")

        assert _active_output_ceiling(_Broken()) == _FALLBACK_OUTPUT_CEILING


# ---------------------------------------------------------------------------
# Phase 4: batched value extraction is the main path
# ---------------------------------------------------------------------------

class TestBatchedMainPass:
    """The default path is now ONE call carrying many rows, with the locked plan
    supplied as an input so the call cannot rediscover rows. Per-row calls remain
    only as a fallback.
    """

    def _run(self, s1_rows, batch_handler, batch_size=10):
        from dspy_components.runtime_builders import build_keyed_extractor_class
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        ExtractorCls = build_keyed_extractor_class(sig_def, sig_def["output_fields"][0], "test")
        seen = {"batch": 0, "per_row": 0, "payloads": []}

        async def mock_forward(predictor, **kwargs):
            if "rows_to_fill" in kwargs:
                seen["batch"] += 1
                payload = json.loads(kwargs["rows_to_fill"])
                seen["payloads"].append(payload)
                return _pred(filled_rows=batch_handler(payload))
            if "candidate_row_plan" in kwargs:
                return _pred(missing_rows=[])
            if "row_anchor" in kwargs:
                seen["per_row"] += 1
                return {}
            return _pred(results=s1_rows)

        async def go():
            with patch(
                "dspy_components.runtime_builders.async_dspy_forward",
                side_effect=mock_forward,
            ), patch(
                "dspy_components.runtime_builders._records_per_call",
                return_value=batch_size,
            ), patch(
                # The batched main pass is opt-in in production; force it on so
                # these assert the batched path rather than the per-row fallback.
                "dspy_components.runtime_builders._SET_AT_A_TIME", True,
            ):
                return await ExtractorCls()("paper text here")

        return asyncio.run(go())["results"], seen

    @staticmethod
    def _anchor(name, point):
        return {
            "outcome_name": {"value": name, "source_text": name},
            "followup_point": {"value": point, "source_text": point},
        }

    @staticmethod
    def _fill(payload):
        return [
            {**p,
             "variability_sd": {"value": f"sd-{p['outcome_name']}", "source_text": "q"},
             "central_value": {"value": f"mean-{p['outcome_name']}", "source_text": "q"}}
            for p in payload
        ]

    def test_three_rows_cost_one_batched_call(self):
        s1 = [self._anchor(f"O{i}", "7d") for i in range(3)]
        env, seen = self._run(s1, self._fill)
        assert seen["batch"] == 1, "three rows must not cost three calls"
        assert seen["per_row"] == 0, "the per-row fallback must not fire"
        assert len(env["value"]) == 3
        assert env["status"] == "reported"

    def test_values_land_on_the_right_row_when_reordered(self):
        """Matched by identity, never position — a reordered response used to
        shift every later row's values onto the wrong row."""
        s1 = [self._anchor(f"O{i}", "7d") for i in range(4)]
        env, _ = self._run(s1, lambda p: list(reversed(self._fill(p))))
        for r in env["value"]:
            assert r["variability_sd"]["value"] == f"sd-{r['outcome_name']['value']}"

    def test_rows_dropped_by_the_batch_are_repaired(self):
        """A truncated batch returns fewer rows than asked. The plan-vs-output
        check must notice and refill them rather than shipping a short table."""
        s1 = [self._anchor(f"O{i}", "7d") for i in range(4)]
        state = {"n": 0}

        def handler(payload):
            state["n"] += 1
            rows = self._fill(payload)
            return rows[:2] if state["n"] == 1 else rows   # first call drops half

        env, seen = self._run(s1, handler)
        assert seen["batch"] == 2, "the dropped rows must trigger a repair call"
        assert len(env["value"]) == 4
        assert all(r["variability_sd"]["status"] == "reported" for r in env["value"])
        assert env["status"] == "reported"

    def test_row_invented_by_the_batch_is_ignored(self):
        s1 = [self._anchor("O0", "7d")]

        def handler(payload):
            return self._fill(payload) + [
                {"outcome_name": "INVENTED", "followup_point": "99d",
                 "variability_sd": {"value": "x", "source_text": "q"},
                 "central_value": {"value": "y", "source_text": "q"}}
            ]

        env, _ = self._run(s1, handler)
        assert len(env["value"]) == 1
        assert env["value"][0]["outcome_name"]["value"] == "O0"

    def test_escalates_to_per_row_when_batches_yield_nothing(self):
        """Two identical batch failures mean the INPUT is the problem, so the
        third attempt must change shape rather than repeat — per-row, which
        succeeded on both production occurrences."""
        s1 = [self._anchor(f"O{i}", "7d") for i in range(2)]
        env, seen = self._run(s1, lambda p: [])
        assert seen["batch"] >= 2, "one batch attempt plus its retry"
        assert seen["per_row"] >= 2, "every unfilled row gets a per-row attempt"
        assert env["status"] == "partial", "still unfilled → not a complete answer"
        assert env["unfilled_rows"] == 2


class TestPerRowMainPassIsDefault:
    """The live configuration: per-row value extraction, batching opt-in.

    Reverted after the batched pass returned `filled_rows: None` on its first real
    production run and cost 12 calls where per-row cost 9.
    """

    def _build(self):
        from dspy_components.runtime_builders import build_keyed_extractor_class
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        return build_keyed_extractor_class(sig_def, sig_def["output_fields"][0], "test")

    @staticmethod
    def _anchor(name):
        return {
            "outcome_name": {"value": name, "source_text": name},
            "followup_point": {"value": "7d", "source_text": "7d"},
        }

    def _run(self, n_rows, per_row_handler):
        ExtractorCls = self._build()
        s1 = [self._anchor(f"O{i}") for i in range(n_rows)]
        seen = {"batch": 0, "per_row": 0, "order": []}

        async def mock_forward(predictor, **kwargs):
            if "rows_to_fill" in kwargs:
                seen["batch"] += 1
                seen["order"].append("batch")
                return _pred(filled_rows=[])
            if "candidate_row_plan" in kwargs:
                return _pred(missing_rows=[])
            if "row_anchor" in kwargs:
                seen["per_row"] += 1
                seen["order"].append("per_row")
                return per_row_handler(json.loads(kwargs["row_anchor"]))
            return _pred(results=s1)

        async def go():
            with patch(
                "dspy_components.runtime_builders.async_dspy_forward",
                side_effect=mock_forward,
            ), patch("dspy_components.runtime_builders._SET_AT_A_TIME", False):
                return await ExtractorCls()("paper text here")

        return asyncio.run(go())["results"], seen

    def test_main_pass_is_per_row_and_batch_is_not_called(self):
        env, seen = self._run(3, lambda a: {
            "variability_sd": {"value": "1", "source_text": "q"},
            "central_value": {"value": "2", "source_text": "q"},
        })
        assert seen["per_row"] == 3, "one call per row is the live default"
        assert seen["batch"] == 0, "the batched pass must not run unless opted in"
        assert len(env["value"]) == 3
        assert env["status"] == "reported"

    def test_first_call_runs_alone_to_warm_the_prompt_cache(self):
        """Eight fallback calls once each logged `prompt_cache write=... read=0`
        because they all fired at once. The first must complete alone."""
        order = []

        def handler(a):
            order.append(a["outcome_name"])
            return {"variability_sd": {"value": "1", "source_text": "q"},
                    "central_value": {"value": "2", "source_text": "q"}}

        env, seen = self._run(4, handler)
        assert order[0] == "O0", "row 0 must be the first call issued"
        assert seen["per_row"] == 4
        assert len(env["value"]) == 4

    def test_a_failed_row_still_gets_repaired(self):
        def handler(a):
            if a["outcome_name"] == "O1":
                return {}                     # answered, but no columns → failure
            return {"variability_sd": {"value": "1", "source_text": "q"},
                    "central_value": {"value": "2", "source_text": "q"}}

        env, seen = self._run(3, handler)
        assert seen["batch"] >= 1, "repair still batches, on top of the per-row main pass"
        row = next(r for r in env["value"] if r["outcome_name"]["value"] == "O1")
        assert row["variability_sd"]["status"] in ("missing", "error")
        assert env["status"] == "partial"


class TestBatchRetry:
    """One empty batch must cost ONE more call, not the whole per-row fan-out.

    Production job e5ecbfe5 burned 12 calls for 8 rows because an empty batch
    dropped straight to 8 per-row calls. The failure did not reproduce in 15
    retries across 10 papers, so retrying the batch once is the cheap fix.
    """

    def _run(self, n_rows, batch_replies):
        """batch_replies: list of returns, one per batch call, in order."""
        from dspy_components.runtime_builders import build_keyed_extractor_class
        sig_def = _make_sig_def(
            extraction_strategy="row_then_columns",
            anchor_columns=["outcome_name", "followup_point"],
            subform_fields=SUBFIELDS,
        )
        ExtractorCls = build_keyed_extractor_class(sig_def, sig_def["output_fields"][0], "test")
        s1 = [{
            "outcome_name": {"value": f"O{i}", "source_text": "q"},
            "followup_point": {"value": "7d", "source_text": "q"},
        } for i in range(n_rows)]
        seen = {"batch": 0, "per_row": 0}

        async def mock_forward(predictor, **kwargs):
            if "rows_to_fill" in kwargs:
                i = seen["batch"]
                seen["batch"] += 1
                reply = batch_replies[i] if i < len(batch_replies) else []
                payload = json.loads(kwargs["rows_to_fill"])
                if reply == "FILL":
                    return _pred(filled_rows=[
                        {**p,
                         "variability_sd": {"value": "1", "source_text": "q"},
                         "central_value": {"value": "2", "source_text": "q"}}
                        for p in payload
                    ])
                return _pred(filled_rows=reply)
            if "candidate_row_plan" in kwargs:
                return _pred(missing_rows=[])
            if "row_anchor" in kwargs:
                seen["per_row"] += 1
                return {}
            return _pred(results=s1)

        async def go():
            with patch(
                "dspy_components.runtime_builders.async_dspy_forward",
                side_effect=mock_forward,
            ), patch("dspy_components.runtime_builders._SET_AT_A_TIME", True):
                return await ExtractorCls()("paper text here")

        return asyncio.run(go())["results"], seen

    def test_no_retry_when_the_first_batch_works(self):
        env, seen = self._run(4, ["FILL"])
        assert seen["batch"] == 1, "a working batch must not be retried"
        assert seen["per_row"] == 0
        assert len(env["value"]) == 4

    def test_empty_batch_is_retried_once_and_recovers(self):
        env, seen = self._run(8, [[], "FILL"])
        assert seen["batch"] == 2, "one empty batch → exactly one retry"
        assert seen["per_row"] == 0, "the retry must prevent the per-row fan-out"
        assert len(env["value"]) == 8
        assert env["status"] == "reported"

    def test_two_empty_batches_escalate_to_per_row(self):
        env, seen = self._run(3, [[], [], [], []])
        assert seen["batch"] >= 2, "main pass tried twice"
        assert seen["per_row"] >= 3, "then every row is attempted individually"
        assert env["status"] == "partial"

    def test_escalation_happens_before_repair_not_after(self):
        """Regression guard: the batch shape must not be retried a THIRD time.
        Production job 4c23c525 failed identically three times — main, retry, and
        again inside repair — before per-row finally ran."""
        _, seen = self._run(3, [[], [], [], [], []])
        assert seen["per_row"] >= 3
        assert seen["batch"] <= 3, (
            f"batch attempted {seen['batch']}x — escalation should stop the repeat"
        )

    def test_retry_costs_far_less_than_the_fan_out_it_replaces(self):
        """The point of the change, stated as a number."""
        _, retried = self._run(8, [[], "FILL"])
        _, fanned = self._run(8, [[], [], [], []])
        assert retried["batch"] + retried["per_row"] == 2
        assert fanned["batch"] + fanned["per_row"] > 8


class TestPromptTextIsPinned:
    """The words the MODEL reads are frozen; the words humans read are not.

    Aug 12 2026: a terminology sweep renamed identifiers, log strings, comments
    and docstrings from "Stage 1 / Stage 2 / anchor columns" to "record discovery
    / slot filling / key columns". The sweep also caught four *prompt* literals —
    a behavioural change to extraction, with no accuracy baseline to re-verify
    against — so those four were reverted and are pinned here.

    Two of them were saved only by an unrelated test in test_review_scope.py that
    happens to assert the synthesized docstrings verbatim; the other two were
    found by an AST audit of every non-docstring string literal. This class
    removes the luck. Reword the prompt only when the change has been measured on
    real papers, and update these strings in the commit that publishes the numbers.
    """

    ANCHORS = ["outcome_name", "followup_point"]

    def _field(self):
        sig = _make_sig_def(
            extraction_strategy="discover_then_fill",
            anchor_columns=self.ANCHORS,
            subform_fields=SUBFIELDS,
        )
        return sig, sig["output_fields"][0]

    def _attrs(self, field):
        return [sf for sf in field["subform_fields"]
                if sf["field_name"] not in self.ANCHORS]

    def test_record_discovery_prompt_wording(self):
        from dspy_components.runtime_builders import _build_record_discovery_sig_def
        sig, field = self._field()
        d = _build_record_discovery_sig_def(sig, field)
        of = d["output_fields"][0]
        desc = of.get("desc") or of.get("description") or ""
        assert "Populate only the anchor columns listed below." in desc
        assert d["docstring"].startswith("Stage 1 row discovery for ")
        assert d["docstring"].endswith("Anchor columns only.")

    def test_row_slot_fill_prompt_wording(self):
        from dspy_components.runtime_builders import _build_row_slot_fill_sig_def
        sig, field = self._field()
        d = _build_row_slot_fill_sig_def(sig, field, self._attrs(field))
        row_anchor = next(i for i in d["input_fields"] if i["name"] == "row_anchor")
        assert row_anchor["desc"].startswith(
            "JSON dict of anchor column values identifying this specific row"
        )
        assert d["docstring"].startswith("Stage 2 per-row extraction.")
        assert "extract all non-anchor columns for that row." in d["docstring"]

    def test_signature_field_names_are_wire(self):
        """These names are in the prompt AND are the response parse keys.

        `row_anchor` is additionally matched as a literal in
        utils/caching_adapter.py to decide where to split the user message for
        prompt caching, so renaming it silently disables Branch A.
        """
        from dspy_components.runtime_builders import (
            _build_recall_audit_sig_def,
            _build_row_slot_fill_sig_def,
            _build_set_slot_fill_sig_def,
        )
        sig, field = self._field()
        attrs = self._attrs(field)
        rowf = _build_row_slot_fill_sig_def(sig, field, attrs)
        assert any(i["name"] == "row_anchor" for i in rowf["input_fields"])
        audit = _build_recall_audit_sig_def(sig, field)
        assert any(i["name"] == "candidate_row_plan" for i in audit["input_fields"])
        assert any(o["name"] == "missing_rows" for o in audit["output_fields"])
        setfill = _build_set_slot_fill_sig_def(sig, field, attrs)
        assert any(i["name"] == "rows_to_fill" for i in setfill["input_fields"])
        assert any(o["name"] == "filled_rows" for o in setfill["output_fields"])
