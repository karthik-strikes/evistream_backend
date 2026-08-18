"""What an LLM call was for: paper, step, retry.

Fixtures mirror the message shapes of a real run — job
`05f205c7-b16d-49c8-8e8c-c1c4211df417`, the 4-paper CD015432 Dichotomous
Outcomes extraction that cost 9 calls while the UI predicted 12. Two facts from
that run drive most of these tests:

* Papers with no rows stop after record discovery, so "papers × steps" is never
  the call count.
* One recall audit ran twice: the first response omitted `[[ ## reasoning ## ]]`
  so DSPy re-asked under JSONAdapter. The *first* attempt is the wasted one.
"""

import json

import pytest

from utils.llm_call_labels import (
    SHAPE_FIELDS,
    SHAPE_JSON,
    STEP_RECALL_AUDIT,
    STEP_RECORD_DISCOVERY,
    STEP_REFILL,
    STEP_SLOT_FILL,
    STEP_EXTRACT,
    classify_step,
    duration_ms,
    label_run_calls,
    response_shape,
    user_fields,
)

FIELD = "dichotomous_outcomes"

# Two papers whose middles differ — the probe is taken from offset 600, so the
# shared journal banner in the first 600 chars must not decide attribution.
BANNER = "Check for updates\nReceived: 4 January 2021 | Revised: 2 March 2021\n" + ("boilerplate " * 45)
PAPER_A = BANNER + "SANTOS 2020 three-arm trial of placebo, paracetamol and ibuprofen. " * 12
PAPER_B = BANNER + "ROVE 2022 two-arm randomised trial of ibuprofen versus placebo in children. " * 12

PAPERS = [
    {"doc_id": "doc-a", "markdown_content": PAPER_A, "path": "/tmp/x/Santos 2020.pdf"},
    {"doc_id": "doc-b", "markdown_content": PAPER_B, "path": "/tmp/x/Rove 2022.pdf"},
]


def _system(*, inputs, outputs=(FIELD,)):
    """A DSPy ChatAdapter system message: it enumerates the signature's fields.

    Both the input markers and the numbered output block matter — the step comes
    from the former, the reported field name from the latter.
    """
    numbered = "\n".join(
        f"{i + 1}. `{name}` (dict)" for i, name in enumerate(("reasoning",) + tuple(outputs))
    )
    markers = "\n\n".join(f"[[ ## {name} ## ]]\n{{{name}}}" for name in inputs + tuple(outputs))
    return {
        "role": "system",
        "content": (
            "Your input fields are:\n"
            + "\n".join(f"{i + 1}. `{n}`" for i, n in enumerate(inputs))
            + "\n\nYour output fields are:\n"
            + numbered
            + "\n\nAll interactions will be structured in the following way:\n\n"
            + markers
        ),
    }


def _user(pairs, paper=None):
    """A user message: `[[ ## field ## ]]` then the value, then the trailer.

    CachingChatAdapter hoists the paper into its own block, which is why content
    is a list here — the parser has to cope with both shapes.
    """
    body = "\n\n".join(f"[[ ## {k} ## ]]\n{v}" for k, v in pairs)
    body += "\n\nRespond with the corresponding output fields, starting with the field `[[ ## reasoning ## ]]`."
    blocks = []
    if paper is not None:
        blocks.append({"type": "text", "text": f"[[ ## markdown_content ## ]]\n{paper}"})
    blocks.append({"type": "text", "text": body})
    return {"role": "user", "content": blocks}


class _Resp:
    """Minimal stand-in for a litellm ModelResponse."""

    class _Msg:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})()

    def __init__(self, content, ms=None):
        self.choices = [self._Msg(content)]
        if ms is not None:
            self._response_ms = ms


def _call(messages, response_text, ts, ms=None):
    return {
        "messages": messages,
        "response": _Resp(response_text, ms),
        "timestamp": ts,
    }


def _discovery(paper, ts, rows_found=2, ms=1000):
    msgs = [_system(inputs=("markdown_content",)), _user([], paper=paper)]
    body = "[[ ## reasoning ## ]]\nfound rows\n\n[[ ## %s ## ]]\n%s\n\n[[ ## completed ## ]]" % (
        FIELD, json.dumps([{"outcome": f"o{i}"} for i in range(rows_found)]),
    )
    return _call(msgs, body, ts, ms)


def _audit(paper, plan, ts, response_text, ms=1000):
    msgs = [
        _system(inputs=("markdown_content", "candidate_row_plan"), outputs=("missing_rows",)),
        _user([("candidate_row_plan", json.dumps(plan))], paper=paper),
    ]
    return _call(msgs, response_text, ts, ms)


def _fill(paper, records, ts, ms=1000):
    msgs = [
        _system(inputs=("markdown_content", "rows_to_fill"), outputs=("filled_rows",)),
        _user([("rows_to_fill", json.dumps(records))], paper=paper),
    ]
    body = "[[ ## reasoning ## ]]\nok\n\n[[ ## filled_rows ## ]]\n[]\n\n[[ ## completed ## ]]"
    return _call(msgs, body, ts, ms)


# ── Step classification ───────────────────────────────────────────────────

def test_step_comes_from_the_signature_field_set():
    assert classify_step(_discovery(PAPER_A, "t1")["messages"]) == STEP_EXTRACT
    assert classify_step(_audit(PAPER_A, [], "t2", "x")["messages"]) == STEP_RECALL_AUDIT
    assert classify_step(_fill(PAPER_A, [], "t3")["messages"]) == STEP_SLOT_FILL


def test_json_fallback_keeps_its_step():
    """The JSONAdapter retry re-renders the output format, not the input labels.

    If this regressed, the retry would be filed as a different operation and the
    two attempts would never be recognised as the same work.
    """
    call = _audit(PAPER_A, [{"outcome": "o1"}], "t2", '{"reasoning": "x", "missing_rows": []}')
    assert classify_step(call["messages"]) == STEP_RECALL_AUDIT
    assert response_shape('{"reasoning": "x", "missing_rows": []}') == SHAPE_JSON


def test_the_closing_instruction_is_not_read_as_input():
    """"Respond with … `[[ ## reasoning ## ]]`, then `[[ ## field ## ]]`" names
    OUTPUT fields. When the paper is hoisted into the system message the whole
    user message is that sentence — and reading its markers as inputs made four
    different papers' discovery calls look like four attempts at one (caught on
    the real job 05f205c7 rows, not by the mocked tests).
    """
    trailer_only = {
        "role": "user",
        "content": "Respond with the corresponding output fields, starting with the field "
                   "`[[ ## reasoning ## ]]`, then `[[ ## dichotomous_outcomes ## ]]`, "
                   "and then ending with the marker for `[[ ## completed ## ]]`.",
    }
    assert user_fields([trailer_only]) == {}


def test_two_papers_discovery_is_never_one_call_retried():
    """The regression the trailer bug caused, stated as a rule."""
    a = _call([_system(inputs=("markdown_content",)),
               {"role": "system", "content": f"[[ ## markdown_content ## ]]\n{PAPER_A}"},
               {"role": "user", "content": "Respond with the corresponding output fields, starting with the field `[[ ## reasoning ## ]]`."}],
              "[[ ## reasoning ## ]]\nx\n\n[[ ## %s ## ]]\n[]" % FIELD, "t1")
    b = _call([_system(inputs=("markdown_content",)),
               {"role": "system", "content": f"[[ ## markdown_content ## ]]\n{PAPER_B}"},
               {"role": "user", "content": "Respond with the corresponding output fields, starting with the field `[[ ## reasoning ## ]]`."}],
              "[[ ## reasoning ## ]]\nx\n\n[[ ## %s ## ]]\n[]" % FIELD, "t2")
    labels = label_run_calls([a, b], PAPERS)
    assert [l.get("filename") for l in labels] == ["Santos 2020.pdf", "Rove 2022.pdf"]
    assert all("superseded" not in l for l in labels)


def test_user_fields_ignores_the_system_template_placeholders():
    """The system message lists `[[ ## rows_to_fill ## ]]\\n{rows_to_fill}`.

    Reading values from it would make every call's inputs look identical, which
    would collapse unrelated calls into "retries" of each other.
    """
    fields = user_fields(_fill(PAPER_A, [{"outcome": "o1"}], "t")["messages"])
    assert json.loads(fields["rows_to_fill"]) == [{"outcome": "o1"}]
    assert "{rows_to_fill}" not in fields.get("rows_to_fill", "")


# ── Paper attribution ─────────────────────────────────────────────────────

def test_papers_are_named_from_their_own_text():
    labels = label_run_calls([_discovery(PAPER_A, "t1"), _discovery(PAPER_B, "t2")], PAPERS)
    assert [l["filename"] for l in labels] == ["Santos 2020.pdf", "Rove 2022.pdf"]
    assert [l["document_id"] for l in labels] == ["doc-a", "doc-b"]


def test_unknown_paper_is_left_blank_not_guessed():
    labels = label_run_calls([_discovery("a completely unrelated document " * 40, "t1")], PAPERS)
    assert "filename" not in labels[0] and "document_id" not in labels[0]


def test_identical_papers_are_left_blank_rather_than_picked_arbitrarily():
    dupes = [
        {"doc_id": "d1", "markdown_content": PAPER_A, "path": "/x/One.pdf"},
        {"doc_id": "d2", "markdown_content": PAPER_A, "path": "/x/Two.pdf"},
    ]
    labels = label_run_calls([_discovery(PAPER_A, "t1")], dupes)
    assert "filename" not in labels[0]


# ── The run-level relabelling ─────────────────────────────────────────────

def test_discovery_is_recognised_only_when_the_keyed_pipeline_followed():
    """A single-pass table field and a keyed pipeline's first call look alike."""
    keyed = label_run_calls(
        [_discovery(PAPER_A, "t1"), _fill(PAPER_A, [{"outcome": "o1"}], "t2")], PAPERS
    )
    assert keyed[0]["step"] == STEP_RECORD_DISCOVERY

    single = label_run_calls([_discovery(PAPER_A, "t1")], PAPERS)
    assert single[0]["step"] == STEP_EXTRACT


def test_second_batch_of_a_wide_table_is_not_called_a_refill():
    """Two fills, different records — a >40-row table, not a repair."""
    labels = label_run_calls([
        _discovery(PAPER_A, "t1"),
        _fill(PAPER_A, [{"outcome": "o1"}], "t2"),
        _fill(PAPER_A, [{"outcome": "o2"}], "t3"),
    ], PAPERS)
    assert [l["step"] for l in labels[1:]] == [STEP_SLOT_FILL, STEP_SLOT_FILL]


def test_refilling_records_already_asked_for_is_a_refill():
    labels = label_run_calls([
        _discovery(PAPER_A, "t1"),
        _fill(PAPER_A, [{"outcome": "o1"}, {"outcome": "o2"}], "t2"),
        _fill(PAPER_A, [{"outcome": "o2"}], "t3"),
    ], PAPERS)
    assert labels[1]["step"] == STEP_SLOT_FILL
    assert labels[2]["step"] == STEP_REFILL


def test_keyed_steps_report_the_table_field_not_the_synthesized_one():
    """`missing_rows` / `filled_rows` are plumbing; the reader wants the field.

    Reporting the synthesized names is what made the dialog claim "3 fields" for
    one table field's three steps.
    """
    labels = label_run_calls([
        _discovery(PAPER_A, "t1"),
        _audit(PAPER_A, [{"outcome": "o1"}], "t2", "[[ ## reasoning ## ]]\nx\n\n[[ ## missing_rows ## ]]\n[]"),
        _fill(PAPER_A, [{"outcome": "o1"}], "t3"),
    ], PAPERS)
    assert [l["field_name"] for l in labels] == [FIELD, FIELD, FIELD]
    assert [l.get("output_field") for l in labels[1:]] == ["missing_rows", "filled_rows"]


def test_two_table_fields_on_one_paper_stay_separate():
    """With two candidates, the field named in the prompt wins."""
    other = "adverse_event_rows"
    disc_a = _discovery(PAPER_A, "t1")
    disc_b = _discovery(PAPER_A, "t2")
    disc_b["messages"][0]["content"] = disc_b["messages"][0]["content"].replace(
        f"`{FIELD}`", f"`{other}`"
    ).replace(f"## {FIELD} ##", f"## {other} ##")

    fill = _fill(PAPER_A, [{"outcome": "o1"}], "t3")
    # The synthesized docstring names the field it fills.
    fill["messages"][0]["content"] += f"\n\nyour objective is: Fill the value columns of {other} for a given set of rows."

    labels = label_run_calls([disc_a, disc_b, fill], PAPERS)
    assert labels[2]["table_field"] == other
    # Only the field that was actually followed up gets relabeled.
    assert labels[0]["step"] == STEP_EXTRACT
    assert labels[1]["step"] == STEP_RECORD_DISCOVERY


# ── Retries and waste ─────────────────────────────────────────────────────

def test_the_earlier_attempt_is_the_wasted_one():
    """The real Santos 2020 case: prose, no reasoning header, then a JSON retry.

    Attempt 1 ($0.099 live) was discarded; attempt 2 ($0.071) was used. Marking
    the retry as the waste instead would point the reader at the wrong call.
    """
    plan = [{"arm1_label": "ibuprofen", "outcome": "rescue_medication"}]
    bad = "Looking at the document, I searched for all mentions...\n\n[[ ## missing_rows ## ]]\n[]\n\n[[ ## completed ## ]]"
    good = '{"reasoning": "complete", "missing_rows": []}'
    labels = label_run_calls([
        _discovery(PAPER_A, "t1"),
        _audit(PAPER_A, plan, "t2", bad),
        _audit(PAPER_A, plan, "t3", good),
    ], PAPERS)

    first, second = labels[1], labels[2]
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert first["attempts_total"] == 2
    assert first["superseded"] is True
    assert "required field" in first["superseded_reason"]
    assert "superseded" not in second
    assert (first["response_shape"], second["response_shape"]) == (SHAPE_FIELDS, SHAPE_JSON)


def test_different_inputs_are_never_treated_as_attempts_at_the_same_work():
    labels = label_run_calls([
        _audit(PAPER_A, [{"outcome": "o1"}], "t1", "[[ ## reasoning ## ]]\nx\n\n[[ ## missing_rows ## ]]\n[]"),
        _audit(PAPER_A, [{"outcome": "o2"}], "t2", "[[ ## reasoning ## ]]\nx\n\n[[ ## missing_rows ## ]]\n[]"),
    ], PAPERS)
    assert all("superseded" not in l for l in labels)


def test_two_papers_with_the_same_step_are_not_attempts_of_each_other():
    labels = label_run_calls([_discovery(PAPER_A, "t1"), _discovery(PAPER_B, "t2")], PAPERS)
    assert all("attempt" not in l for l in labels)


# ── Shape of the whole run ────────────────────────────────────────────────

def test_the_nine_call_run_reads_back_as_it_happened():
    """4 papers, 9 calls: two stopped at discovery, one full, one with a retry."""
    plan = [{"outcome": "rescue_medication"}]
    ok_audit = "[[ ## reasoning ## ]]\nnothing missing\n\n[[ ## missing_rows ## ]]\n[]\n\n[[ ## completed ## ]]"
    bad_audit = "prose only\n\n[[ ## missing_rows ## ]]\n[]\n\n[[ ## completed ## ]]"

    calls = [
        _discovery(PAPER_B, "t1", rows_found=0),          # Rove-shaped: stops here
        _discovery(PAPER_A, "t2"),                        # Santos: full path
        _audit(PAPER_A, plan, "t3", bad_audit),
        _audit(PAPER_A, plan, "t4", '{"missing_rows": []}'),
        _fill(PAPER_A, plan, "t5"),
    ]
    labels = label_run_calls(calls, PAPERS)
    steps = [l["step"] for l in labels]
    assert steps == [
        STEP_EXTRACT,              # no follow-up calls → stopped after discovery
        STEP_RECORD_DISCOVERY,
        STEP_RECALL_AUDIT,
        STEP_RECALL_AUDIT,
        STEP_SLOT_FILL,
    ]
    wasted = [l for l in labels if l.get("superseded")]
    assert len(wasted) == 1 and wasted[0]["step"] == STEP_RECALL_AUDIT


# ── Timing ────────────────────────────────────────────────────────────────

def test_duration_comes_from_the_litellm_response():
    assert duration_ms(_call([], "x", "t", ms=1234.6)) == 1235
    assert duration_ms({"response": _Resp("x")}) is None
    assert duration_ms({}) is None


def test_duration_falls_back_to_hidden_params():
    resp = _Resp("x")
    resp._hidden_params = {"_response_ms": 900}
    assert duration_ms({"response": resp}) == 900


# ── Never break an extraction ─────────────────────────────────────────────

@pytest.mark.parametrize("entry", [
    {},
    {"messages": None, "response": None},
    {"messages": "not a list", "response": "not a response"},
    {"messages": [{"role": "user", "content": None}]},
])
def test_malformed_entries_yield_a_label_not_an_exception(entry):
    labels = label_run_calls([entry], PAPERS)
    assert len(labels) == 1 and isinstance(labels[0], dict)
