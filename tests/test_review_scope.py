"""Project review scope is prompt context, and it has to be cache-safe.

Three properties matter:
  - with no scope set every composed prompt is byte-identical to before, so
    existing forms cannot drift;
  - the scope is written *inside* sig_def, so the signature-class content hash
    covers it. Two projects with identical forms but different scopes can then
    never share a cached signature class, and existing forms pick the scope up
    without regeneration;
  - the two-stage table builders synthesize their own docstrings and never
    inherit the parent's, so they must re-apply the scope themselves — tables
    are exactly where scope matters most.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dspy_components.runtime_builders import (  # noqa: E402
    _build_record_discovery_sig_def,
    _build_row_slot_fill_sig_def,
    _content_hash,
    apply_review_scope,
)

SCOPE_A = (
    "Adults with chronic periodontitis. Comparison of interest: scaling and root "
    "planing alone versus SRP plus systemic antibiotics."
)
SCOPE_B = (
    "Children under 12 undergoing third molar extraction. Comparison of interest: "
    "ibuprofen versus placebo."
)


def _table_field():
    return {
        "name": "results",
        "type": "Dict[str, Any]",
        "description": "Every outcome row reported by the study.",
        "extraction_strategy": "row_then_columns",
        "anchor_columns": ["outcome_name"],
        "subform_fields": [
            {"field_name": "outcome_name", "field_type": "text",
             "field_description": "Outcome measured"},
            {"field_name": "central_value", "field_type": "text",
             "field_description": "Mean or median"},
        ],
    }


def _sig_def():
    return {
        "class_name": "ExtractResults",
        "docstring": "Extract outcome results from the study.",
        "input_fields": [
            {"name": "markdown_content", "type": "str", "desc": "Paper text"}
        ],
        "output_fields": [_table_field()],
    }


def _schema_def():
    return {"task_name": "trial", "signatures": [_sig_def()]}


VALUE_COLS = [{"field_name": "central_value", "field_description": "Mean or median"}]


class TestNoScopeIsANoOp:
    """The guarantee that this ships safely to every existing live form."""

    def test_none_returns_the_same_object(self):
        sd = _schema_def()
        assert apply_review_scope(sd, None) is sd

    def test_blank_returns_the_same_object(self):
        sd = _schema_def()
        assert apply_review_scope(sd, "   \n\t ") is sd

    def test_content_hash_is_unchanged(self):
        unscoped = _content_hash(_sig_def())
        passed_through = _content_hash(
            apply_review_scope(_schema_def(), "")["signatures"][0]
        )
        assert passed_through == unscoped

    def test_stage_docstrings_carry_no_scope_block(self):
        sig = _sig_def()
        record_discovery = _build_record_discovery_sig_def(sig, sig["output_fields"][0])
        stage2 = _build_row_slot_fill_sig_def(sig, sig["output_fields"][0], VALUE_COLS)
        assert record_discovery["docstring"] == (
            "Stage 1 row discovery for results. Anchor columns only."
        )
        assert stage2["docstring"].startswith("Stage 2 per-row extraction.")
        assert "REVIEW SCOPE" not in record_discovery["docstring"]
        assert "REVIEW SCOPE" not in stage2["docstring"]


class TestScopeReachesThePrompt:
    def test_docstring_carries_scope_and_keeps_the_original(self):
        scoped = apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0]
        assert SCOPE_A in scoped["docstring"]
        assert "Extract outcome results from the study." in scoped["docstring"]

    def test_scope_is_declared_as_context_not_a_filter(self):
        """Without this clause the model starts dropping rows on its own."""
        scoped = apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0]
        doc = scoped["docstring"]
        assert "Do NOT omit" in doc
        assert "including every row you find" in doc

    def test_scope_is_stored_as_a_structured_key(self):
        scoped = apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0]
        assert scoped["review_scope"] == SCOPE_A

    def test_original_schema_def_is_not_mutated(self):
        sd = _schema_def()
        apply_review_scope(sd, SCOPE_A)
        assert sd["signatures"][0]["docstring"] == "Extract outcome results from the study."
        assert "review_scope" not in sd["signatures"][0]


class TestCacheKeyCorrectness:
    """build_signature_class is LRU-cached on the content hash of sig_def.

    If the scope did not live inside sig_def, two projects with identical forms
    and different scopes would collide on one cache entry and each would extract
    under the other's scope.
    """

    def test_scope_changes_the_content_hash(self):
        unscoped = _content_hash(_sig_def())
        scoped = _content_hash(apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0])
        assert scoped != unscoped

    def test_two_different_scopes_never_share_a_hash(self):
        a = _content_hash(apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0])
        b = _content_hash(apply_review_scope(_schema_def(), SCOPE_B)["signatures"][0])
        assert a != b

    def test_same_scope_is_stable(self):
        a = _content_hash(apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0])
        b = _content_hash(apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0])
        assert a == b


class TestTwoStagePropagation:
    """Stage 1 and Stage 2 build fresh docstrings; they must re-apply the scope."""

    def _scoped_parent(self):
        return apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0]

    def test_stage1_docstring_carries_scope(self):
        parent = self._scoped_parent()
        record_discovery = _build_record_discovery_sig_def(parent, parent["output_fields"][0])
        assert SCOPE_A in record_discovery["docstring"]
        assert "Stage 1 row discovery" in record_discovery["docstring"]

    def test_stage2_docstring_carries_scope(self):
        parent = self._scoped_parent()
        stage2 = _build_row_slot_fill_sig_def(
            parent, parent["output_fields"][0], VALUE_COLS
        )
        assert SCOPE_A in stage2["docstring"]
        assert "Stage 2 per-row extraction." in stage2["docstring"]

    def test_stage1_still_demands_every_row(self):
        """Scope context must not displace the row-discovery contract."""
        parent = self._scoped_parent()
        record_discovery = _build_record_discovery_sig_def(parent, parent["output_fields"][0])
        desc = record_discovery["output_fields"][0]["description"]
        assert "Identify every distinct row" in desc

    def test_stage_hashes_differ_between_scopes(self):
        a_parent = apply_review_scope(_schema_def(), SCOPE_A)["signatures"][0]
        b_parent = apply_review_scope(_schema_def(), SCOPE_B)["signatures"][0]
        a = _content_hash(_build_record_discovery_sig_def(a_parent, a_parent["output_fields"][0]))
        b = _content_hash(_build_record_discovery_sig_def(b_parent, b_parent["output_fields"][0]))
        assert a != b
