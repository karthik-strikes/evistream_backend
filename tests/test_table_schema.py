"""The composite-key accessor and pipeline alias map.

These pin the two properties that make the anchor_columns → key_columns rename
survivable: reads accept either spelling, and an unrecognised pipeline resolves
to None rather than silently defaulting.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.table_schema import (  # noqa: E402
    AGENTIC,
    DISCOVER_THEN_FILL,
    SINGLE_PASS,
    attribute_columns,
    field_key_columns,
    field_strategy,
    resolve_strategy,
    set_field_key_columns,
)


class TestCompositeKeyAccessor:
    def test_reads_the_new_key(self):
        assert field_key_columns({"key_columns": ["a", "b"]}) == ["a", "b"]

    def test_falls_back_to_the_old_key(self):
        """48 live table fields still carry only anchor_columns."""
        assert field_key_columns({"anchor_columns": ["a", "b"]}) == ["a", "b"]

    def test_new_key_wins_when_both_present(self):
        got = field_key_columns({"key_columns": ["new"], "anchor_columns": ["old"]})
        assert got == ["new"]

    def test_blank_entries_are_dropped(self):
        """A key column with an empty name cannot identify a row, and one that
        slipped through produced row keys with an empty component."""
        assert field_key_columns({"key_columns": ["a", "", "  ", None, "b"]}) == ["a", "b"]

    def test_missing_and_malformed_are_empty(self):
        assert field_key_columns({}) == []
        assert field_key_columns(None) == []
        assert field_key_columns({"key_columns": "not-a-list"}) == []

    def test_empty_new_key_falls_through_to_old(self):
        assert field_key_columns({"key_columns": [], "anchor_columns": ["a"]}) == ["a"]


class TestDualWrite:
    def test_writes_both_spellings(self):
        """Dual-write makes a read site we failed to migrate harmless."""
        f = {}
        set_field_key_columns(f, ["a", "b"])
        assert f["key_columns"] == ["a", "b"]
        assert f["anchor_columns"] == ["a", "b"]

    def test_the_two_are_independent_lists(self):
        f = {}
        set_field_key_columns(f, ["a"])
        f["key_columns"].append("mutated")
        assert f["anchor_columns"] == ["a"]


class TestAttributeColumns:
    def test_non_key_columns_only(self):
        field = {
            "anchor_columns": ["arm", "timepoint"],
            "subform_fields": [
                {"field_name": "arm"}, {"field_name": "timepoint"},
                {"field_name": "mean"}, {"field_name": "sd"},
            ],
        }
        assert attribute_columns(field) == ["mean", "sd"]

    def test_no_key_means_every_column_is_an_attribute(self):
        field = {"subform_fields": [{"field_name": "x"}, {"field_name": "y"}]}
        assert attribute_columns(field) == ["x", "y"]


class TestStrategyAliases:
    def test_old_spellings_resolve(self):
        assert resolve_strategy("row_then_columns") == DISCOVER_THEN_FILL
        assert resolve_strategy("single_call") == SINGLE_PASS

    def test_new_spellings_resolve(self):
        assert resolve_strategy("discover_then_fill") == DISCOVER_THEN_FILL
        assert resolve_strategy("single_pass") == SINGLE_PASS
        assert resolve_strategy("agentic") == AGENTIC

    def test_unrecognised_returns_none_not_a_default(self):
        """The whole point. build_schema_classes selects by exact match and falls
        through to single-pass SILENTLY, so an unknown value must be visible as
        None and never quietly become the default."""
        for bad in ("row_then_colums", "batched", "", "  ", None, 42, "TWO_STAGE"):
            assert resolve_strategy(bad) is None, bad

    def test_whitespace_tolerated(self):
        assert resolve_strategy("  row_then_columns  ") == DISCOVER_THEN_FILL

    def test_field_strategy_reads_the_field(self):
        assert field_strategy({"extraction_strategy": "row_then_columns"}) == DISCOVER_THEN_FILL
        assert field_strategy({}) is None
        assert field_strategy(None) is None

    def test_every_alias_maps_to_a_canonical_value(self):
        from utils.table_schema import CANONICAL_STRATEGIES, STRATEGY_ALIASES
        assert set(STRATEGY_ALIASES.values()) <= CANONICAL_STRATEGIES
        # Canonical names must be self-resolving, or a round-trip breaks.
        for name in CANONICAL_STRATEGIES:
            assert resolve_strategy(name) == name
