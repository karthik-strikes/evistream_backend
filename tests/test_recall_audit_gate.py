"""What the recall audit is allowed to add to a frozen record set.

The gate in `_reconcile_record_set` decides which proposed rows survive. It has
two jobs that pull against each other: let real missed rows in, and keep
fabricated rows out. Until Aug 13 2026 it did only the second, and did it to
everything — three consecutive production runs proposed 20, 16, and 16 rows and
accepted **zero**, every rejection filed as `incomplete_identity`.

The cause was one predicate. `is_absent` is true for both flavours of absence,
so a key column reading "NA" — *not applicable*, a real statement about which
record this is — counted as an unfilled identity. On the Acute Dental Pain form
(7 key columns) almost every row has at least one NA, so almost every row was
unidentifiable by construction.

The fixture below is the actual `missing_rows` payload from job
`3ba0c293-da39-4f78-b0de-11d68c5dbd70`, trimmed to the rows that matter. Tests
that mock the model's output cannot catch this class of bug: the shape was
always right, and the judgement about that shape was wrong.
"""

import pytest

from dspy_components.runtime_builders import (
    _canonical_record_key,
    _quote_mentions_identity,
    _reconcile_record_set,
)

KEY_COLS = [
    "adverse_effect", "followup_timepoint", "intervention", "intervention_detail",
    "outcome_other", "outcome_type", "population_type",
]

# One real sentence from the paper, and enough surrounding prose for the source
# linker to index. Quotes are verified against this, not against a mock.
PAPER = """
# A single-tablet fixed-dose combination of ibuprofen and paracetamol

## Results

Patients were eligible if they were aged 16 to 40 years and required surgical
removal of at least 3 impacted third molars. During stage 1, rescue medication
was required by 53 of 73 (72.6%) patients in the placebo group.

The patient's global assessment of study medication was rated as excellent,
very good, or good by 61 of 71 patients (85.9%), 125 of 142 (88.0%), and 138 of
148 (93.2%) in the ibuprofen 100-mg/paracetamol 250-mg, ibuprofen
200-mg/paracetamol 500-mg, and ibuprofen 400-mg/paracetamol 1000-mg treatment
arms, respectively.

Adverse events between the first and second doses of study medication were
reported with similar frequency across the 8 treatment groups.
"""

GLOBAL_ASSESSMENT_QUOTE = (
    "by 61 of 71 patients (85.9%), 125 of 142 (88.0%), and 138 of 148 (93.2%) in the "
    "ibuprofen 100-mg/paracetamol 250-mg, ibuprofen 200-mg/paracetamol 500-mg, and "
    "ibuprofen 400-mg/paracetamol 1000-mg treatment arms"
)


def _proposal(**overrides):
    """A verbatim proposal from the live run; override one field per test."""
    row = {
        "population_type": "Surgical tooth extraction (third molar / wisdom teeth)",
        "intervention": "Ibuprofen 200-400 mg plus Acetaminophen 500-1,000 mg",
        "intervention_detail": "Ibuprofen 100 mg/paracetamol 250 mg",
        "outcome_type": "Other",
        "outcome_other": "Patient's global assessment rated as excellent, very good, or good",
        "followup_timepoint": "8 hours",
        # This row is about a global assessment, so there is no adverse effect.
        # NA is the correct answer, not a gap.
        "adverse_effect": "NA",
        "evidence_quote": GLOBAL_ASSESSMENT_QUOTE,
    }
    row.update(overrides)
    return row


def _run(proposed, candidate_rows=()):
    return _reconcile_record_set(list(candidate_rows), proposed, KEY_COLS, PAPER)


# ── The regression ────────────────────────────────────────────────────────

def test_a_row_whose_identity_includes_NA_is_accepted():
    """The live failure: 16 of 16 rejected for an `adverse_effect` of "NA"."""
    rows, report = _run([_proposal()])
    assert report["accepted"] == 1, report
    assert report["incomplete_identity"] == 0
    assert len(rows) == 1
    assert rows[0]["adverse_effect"]["value"] == "NA"


@pytest.mark.parametrize("na_spelling", ["NA", "N.A.", "Not applicable", "not_applicable"])
def test_every_spelling_of_not_applicable_is_a_value(na_spelling):
    """The NA vocabulary is `utils/absence.py`'s, not this test's.

    Note `N/A` is deliberately NOT in it — evistream files that under
    *not reported*. Surprising, but it is one decision in one place, and this
    gate must follow it rather than invent a second vocabulary.
    """
    _, report = _run([_proposal(adverse_effect=na_spelling)])
    assert report["accepted"] == 1, f"{na_spelling}: {report}"


# ── The other direction: the gate still has to hold ───────────────────────

@pytest.mark.parametrize("gap", [None, "", "   ", "NR", "not reported", "N/A", "none"])
def test_a_genuine_gap_is_still_not_an_identity(gap):
    """NA says which row this is. NR says nobody knows. Only NA identifies."""
    _, report = _run([_proposal(adverse_effect=gap)])
    assert report["accepted"] == 0, f"{gap!r}: {report}"
    assert report["incomplete_identity"] == 1


def test_a_real_quote_about_a_different_row_is_rejected():
    """The fabricated-row case: quote exists, but not for this identity."""
    _, report = _run([_proposal(
        intervention_detail="Ibuprofen 800 mg/paracetamol 2000 mg",
        outcome_other="Nausea requiring treatment",
        intervention="Placebo",
        population_type="Chronic dental pain",
        followup_timepoint="72 hours",
        evidence_quote="rescue medication was required by 53 of 73 (72.6%) patients",
    )])
    assert report["accepted"] == 0, report
    assert report["quote_unrelated"] == 1


def test_NA_alone_cannot_satisfy_the_quote_check():
    """Guards the hole that accepting NA would otherwise open.

    "NA" is two characters and lives inside *analgesia*, *nausea*, *management*.
    If an absent value counted as evidence, every NA-bearing row would pass the
    identity check for free and the fabrication guard above would be decorative.
    """
    values = {c: "NA" for c in KEY_COLS}
    assert _quote_mentions_identity("rescue analgesia was required in 12 patients", values) is False


def test_a_quote_that_is_not_in_the_paper_is_rejected():
    _, report = _run([_proposal(
        evidence_quote="by 99 of 99 patients (100%) in the aspirin 900-mg treatment arm",
    )])
    assert report["accepted"] == 0, report
    assert report["quote_unverified"] == 1


def test_a_row_already_in_the_frozen_set_is_not_added_twice():
    existing = [{c: {"value": v} for c, v in _proposal().items() if c in KEY_COLS}]
    rows, report = _run([_proposal()], candidate_rows=existing)
    assert report["already_present"] == 1, report
    assert len(rows) == 1


def test_NA_spelling_does_not_split_one_row_into_two():
    """"NA" and "Not applicable" are the same claim — so, the same record."""
    a = _canonical_record_key({**_proposal(), "adverse_effect": "NA"}, KEY_COLS)
    b = _canonical_record_key({**_proposal(), "adverse_effect": "Not applicable"}, KEY_COLS)
    assert a == b


def test_not_applicable_and_not_reported_are_different_records():
    """Canonicalising absence must not merge "doesn't apply" with "unknown"."""
    na = _canonical_record_key({**_proposal(), "adverse_effect": "NA"}, KEY_COLS)
    nr = _canonical_record_key({**_proposal(), "adverse_effect": "NR"}, KEY_COLS)
    assert na != nr


# ── The whole live payload ────────────────────────────────────────────────

def test_the_run_that_accepted_nothing_now_accepts_its_rows():
    """All 16 proposals from job 3ba0c293 differed only by arm and outcome."""
    proposals = [
        _proposal(intervention_detail=d, outcome_other=o)
        for d in ("Ibuprofen 100 mg/paracetamol 250 mg",
                  "Ibuprofen 200 mg/paracetamol 500 mg",
                  "Ibuprofen 400 mg/paracetamol 1000 mg")
        for o in ("Patient's global assessment rated as excellent, very good, or good",
                  "Patient's global assessment rated as excellent or very good")
    ]
    rows, report = _run(proposals)
    assert report["proposed"] == 6
    assert report["accepted"] == 6, report
    assert len({_canonical_record_key(r, KEY_COLS) for r in rows}) == 6
