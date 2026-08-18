"""The NA prompt clause is gated on the field's own declared options.

Two properties matter:
  - a field that declares no NA option sees the original NR-only contract
    verbatim, so the vast majority of existing fields get no prompt change
    (and therefore no scoring drift);
  - the gate lives inside sig_def, so the signature-class content hash covers
    it and existing forms pick the change up without regeneration.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dspy_components.runtime_builders import (  # noqa: E402
    _compose_field_desc,
    _content_hash,
    _declared_na_option,
)


def _field(options=None, subform_fields=None):
    f = {
        "name": "d2_3_deviations_arose",
        "type": "Dict[str, Any]",
        "description": "Were there deviations from the intended intervention?",
        "source_grounded": True,
        "rules": ["Answer from the Methods section only."],
    }
    if options:
        f["options"] = options
    if subform_fields:
        f["subform_fields"] = subform_fields
    return f


NR_ONLY_CONTRACT = '- If value is "NR", set source_text to "NR".'


class TestDeclaredNaOption:
    def test_detects_the_authors_spelling(self):
        assert _declared_na_option(["Yes", "No", "Not applicable"]) == "Not applicable"
        assert _declared_na_option(["Yes", "NA"]) == "NA"

    def test_none_is_not_an_na_option(self):
        """"None" is a reported finding, not an inapplicability claim."""
        assert _declared_na_option(["Industry", "Public", "None"]) is None

    def test_no_options_no_na(self):
        assert _declared_na_option(None) is None
        assert _declared_na_option(["Yes", "No"]) is None


class TestPromptGating:
    def test_field_without_na_option_is_unchanged(self):
        desc = _compose_field_desc(_field(options=["Yes", "No"]))
        assert NR_ONLY_CONTRACT in desc
        assert "Not applicable vs not reported" not in desc

    def test_free_text_field_can_never_reach_na(self):
        """NA is unreachable unless declared, so prose fields cannot over-apply it."""
        desc = _compose_field_desc(_field())
        assert "Not applicable vs not reported" not in desc

    def test_declared_na_option_adds_the_clause(self):
        desc = _compose_field_desc(_field(options=["Yes", "No", "Not applicable"]))
        assert "Not applicable vs not reported" in desc
        # Named verbatim, so it reads as option selection, not a new escape hatch.
        assert '"Not applicable" means this field CANNOT apply' in desc
        # The deflators must be present.
        assert "study DESIGN" in desc
        assert "Silence is always NR" in desc
        assert 'quote that statement in "source_text"' in desc

    def test_clause_follows_the_authors_rules(self):
        """Author rules stay ahead of system guidance in the composed desc."""
        desc = _compose_field_desc(_field(options=["Yes", "Not applicable"]))
        assert desc.index("Answer from the Methods section only.") < desc.index(
            "Not applicable vs not reported"
        )

    def test_table_column_declaring_na_gets_per_column_guidance(self):
        field = _field(
            subform_fields=[
                {
                    "field_name": "reporter",
                    "field_type": "select",
                    "field_description": "Who reported it",
                    "options": ["Patient", "Clinician", "NA"],
                },
                {
                    "field_name": "central_value",
                    "field_type": "text",
                    "field_description": "Mean",
                },
            ]
        )
        desc = _compose_field_desc(field)
        assert 'Use "NA" only when the study design makes' in desc
        # Only the column that declared it.
        assert desc.count("only when the study design makes") == 1


class TestCacheInvalidation:
    def test_content_hash_covers_the_options_gate(self):
        """So an option edit rebuilds the signature class instead of serving a
        stale LRU entry with the old prompt."""
        without = {"class_name": "S", "output_fields": [_field(options=["Yes", "No"])]}
        with_na = {"class_name": "S", "output_fields": [_field(options=["Yes", "No", "NA"])]}
        assert _content_hash(without) != _content_hash(with_na)
