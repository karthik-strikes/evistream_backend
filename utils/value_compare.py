"""
Canonical value comparison — "did two sources record the same thing?"

Companion to `utils/absence.py`, deliberately separate from it. The split rule is
**one cell vs. two cells**:

  * `absence.py` answers **"what does this one cell claim?"** — the status
    vocabulary, `classify`, `cell_status`, labels, `field_is_empty`, and the
    single-cell token `compare_key`. It is mirrored 1:1 by
    `frontend/lib/absence.ts`, and that mirror is a maintained contract. It is
    also imported at extraction time by `dspy_components.*`.
  * this module answers **"do these two cells agree?"** — everything that needs
    *both* sides or a *verdict*: multi-select alignment (inherently pairwise),
    boolean synonyms, numeric strings, order-insensitive table rows, and the
    definition of which keys are even fields.

Import direction is one-way (`value_compare` → `absence`); there is no cycle and
no shim.

These bodies were previously private to `app/services/adjudication_service.py`,
which held the most complete of the four independent agreement implementations in
the codebase. The others (`api/v1/results.py`'s /compare and consensus-summary,
and `services/irr_service.py`) now call in here, so "do these agree?" has exactly
one answer. `adjudication_service` keeps `_unwrap_for_compare` / `_canon` /
`_align_multiselect` as re-export aliases, because
`tests/test_services/test_adjudication_absence.py` imports them by name.

**Out of scope on purpose.** The eval harness has its own comparator and its own
NR vocabulary — `eval/engine/core/comparator.py` and
`eval/engine/config/nr_synonyms.py`, whose token set deliberately includes
"none"/""/"unclear" — and it feeds measured numbers with a published baseline.
Exactly one file under `eval/` imports `absence` at all
(`eval/studies/ablation/run_extraction.py:81`, pinned on purpose). Nothing here
touches `absence.classify`, `absence.normalize_status`, or the token sets, so it
is structurally impossible for this module to move a published number.

Three rules callers should not have to remember, so this module owns all three:

  1. **A failed extraction is not a rating.** Two cells that both faulted are not
     two reviewers agreeing. Note that `absence.compare_key` signals this by
     returning `None`, which is a trap — `None == None` is True, so a caller that
     forgets the guard scores two failures as perfect agreement. Here the verdict
     functions guard, and `agreement_token` returns `None` only for callers that
     need to *drop* the pair (kappa) rather than score it.
  2. **An unfilled cell is not a claim.** `absence.compare_key("")` returns "NR",
     so a bare empty string would otherwise agree with another bare empty string.
     Nobody recording anything is not both reviewers asserting a reporting gap.
     An *explicit* `status: not_reported` still is a claim, and still agrees with
     another one. (`absence.failure_envelope` writes `value: ""` precisely so no
     downstream flatten can resurrect it as an assertion about the paper; the
     status-less comparison path is where that invariant was being lost.)
  3. **Not every key in `extracted_data` is a field.** `_partial` is a control
     flag stored inside the payload (`api/v1/results.py:476`), so a naive
     `set(a) | set(b)` inflates the denominator, always counts as one disputed
     field, and renders as a literal row in the review UI. `comparable_fields`
     is the one place the comparison denominator is defined.
"""

import json
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from utils import absence

# ── Verdicts ────────────────────────────────────────────────────────────────
#
# Tri-state rather than a boolean because the three call sites each dispose of an
# incomparable pair differently, and all three are right for their surface:
#   consensus-summary  → not agreed, stays in the denominator ("not verified")
#   adjudication       → surfaced to the adjudicator ("a human must look")
#   IRR                → pair dropped from the sample ("not a rating")
# A boolean silently picks one of those for everyone.
AGREE = "agree"
DISAGREE = "disagree"
INCOMPARABLE = "incomparable"

#: Keys stored *inside* `extracted_data` that are control flags, not fields.
CONTROL_KEYS = frozenset({"_partial"})

# Grounding metadata travels with a value but is never part of it: two reviewers
# who recorded the same number while citing different sentences agree.
_METADATA_KEYS = frozenset({
    "source_text", "source_location", "page", "section", "confidence", "reasoning",
    "status", "error", "off_options",
})

#: Suffixes of the flattened AI key form (`age.value`, `age.source_text`, …) that
#: are metadata about a field rather than fields of their own.
_AI_METADATA_SUFFIXES = (".source_location", ".source_text", ".confidence", ".reasoning")


class _Failed:
    """Never equal to anything, including another failure: two failed cells are
    not two reviewers agreeing.

    Deliberately a sentinel object rather than `None`, so a failure nested inside
    a table row or a list still poisons the comparison — plain dict/list equality
    propagates the inequality for us, with no per-caller guard needed. This is
    the compositional form of rule 1: one failed cell inside a table makes the
    whole field incomparable.
    """

    def __eq__(self, other):  # noqa: D105
        return False

    def __hash__(self):
        return id(self)

    def __repr__(self):
        return "<extraction-failed>"


FAILED_SENTINEL = _Failed()


# ── Field sets ──────────────────────────────────────────────────────────────

def comparable_fields(*sources: Optional[Dict[str, Any]]) -> Set[str]:
    """The union of field names across sources, minus control keys.

    The one definition of the comparison denominator. Rule 3 above.
    """
    out: Set[str] = set()
    for src in sources:
        if src:
            out.update(src.keys())
    return out - CONTROL_KEYS


def normalize_ai_keys(extracted: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten AI extraction keys onto plain field names.

    AI rows historically store the flattened form `field.value` alongside
    `field.source_text` / `field.confidence` / … . Three call sites each did this
    differently; this is the union of the correct halves:

      * `field.value` → `field` (the value wins);
      * a plain `field` is kept **only** when there is no `field.value` sibling —
        this is the denominator fix. `api/v1/results.py` used to drop every plain
        key as soon as *any* `.value` key existed, so the dashboard counted fewer
        fields than the review screen;
      * `.source_text` / `.source_location` / `.confidence` / `.reasoning` keys
        are dropped. `consensus/page.tsx` kept them (nothing has a
        `field.source_text.value` sibling), so they surfaced in the review screen
        as pseudo-fields to adjudicate.

    A payload with no flattened keys at all (manual/consensus rows, or the
    `{value, source_text, status}` envelope form) passes through unchanged.
    """
    if not extracted:
        return {}
    normalized: Dict[str, Any] = {}
    saw_flattened = False
    for key, val in extracted.items():
        if key.endswith(".value"):
            saw_flattened = True
            normalized[key[: -len(".value")]] = val
        elif key.endswith(_AI_METADATA_SUFFIXES):
            saw_flattened = True
        elif f"{key}.value" not in extracted:
            normalized[key] = val
    # Nothing flattened and nothing dropped → this was never an AI row.
    return normalized if saw_flattened or normalized else dict(extracted)


# ── Canonicalization ────────────────────────────────────────────────────────

def unwrap_for_compare(v: Any) -> Any:
    """Recursively strip {value, source_text} envelopes and grounding metadata so
    two reviewers with identical column values but different cited quotes are
    treated as agreement, not conflict. Mirrors the rule that source_text is
    metadata about a value — never the value itself."""
    if isinstance(v, dict):
        if "value" in v:
            # Project the status into the compared value BEFORE unwrapping, so a
            # pipeline failure can never read as agreement with a genuine NR, and
            # NA vs NR stays a conflict (they are different claims about the
            # study). Legacy rows carry no status and are untouched.
            st = absence.normalize_status(v.get("status"))
            if st in absence.FAILURE_STATUSES:
                return FAILED_SENTINEL
            if st == absence.NOT_APPLICABLE:
                return absence.NA_LABEL
            if st == absence.NOT_REPORTED:
                return absence.NR_LABEL
            return unwrap_for_compare(v["value"])
        # Row dict inside a table: drop metadata keys, recurse on the rest.
        return {k: unwrap_for_compare(val) for k, val in v.items() if k not in _METADATA_KEYS}
    if isinstance(v, list):
        return [unwrap_for_compare(x) for x in v]
    return v


def canon(v: Any, options: Any = None) -> Any:
    """Canonicalize an unwrapped value for agreement checks: case/whitespace-
    insensitive strings, numeric strings equal to numbers ("3" == 3), and table
    row lists compared as order-insensitive multisets — the same leniency
    scalar fields already get, so row order never counts as a conflict.

    Absence folding here goes through `absence.canonical_absence_label`, **not**
    `absence.classify`. That is the deliberate difference from the single-cell
    token: `classify("None")` and `classify("")` both return `not_reported`, so a
    reviewer who picked a declared "None" option (a study with no funding) would
    otherwise be scored as agreeing with a reviewer who wrote "NR".
    `canonical_absence_label` folds only the unambiguous spellings, which is what
    its own docstring says it is for. Failing to fold can at worst raise a
    conflict for a human to resolve; over-folding silently asserts agreement.

    `options` is the field's declared vocabulary, honoured when a caller has it to
    hand. Because ambiguous tokens are no longer folded, it changes the outcome
    only for a declared *inapplicability* option, so no call site fetches form
    definitions just to pass it. Threaded into list items (a multi-select's
    members share one option list) but deliberately not into dict values: a dict
    here is a *table row*, whose options are per-column, so a single field-level
    list would be the wrong vocabulary for every cell in it.
    """
    if isinstance(v, bool):
        # Compare as the lowercase string so True == "true" (manual reviewers
        # save strings; AI may save real booleans).
        return "true" if v else "false"
    if isinstance(v, str):
        if options and absence.matches_option(v, options):
            # The form author's vocabulary wins. `classify` keeps the NA meaning
            # for a declared inapplicability option and treats everything else
            # the author declared as a substantive answer.
            if absence.classify(v, options) == absence.NOT_APPLICABLE:
                return absence.NA_LABEL
            return v.strip().lower()
        s = v.strip().lower()
        # NR spellings agree with each other and NA spellings agree with each
        # other, while NR never equals NA. Does NOT fold "None"/"" — see above.
        absence_label = absence.canonical_absence_label(v)
        if absence_label is not None:
            return absence_label
        # Boolean synonyms — mirror the consensus UI's displayBoolean, which
        # renders yes/true/y as "Yes": values that display identically must
        # never count as a conflict.
        if s in ("true", "yes", "y"):
            return "true"
        if s in ("false", "no"):
            return "false"
        try:
            f = float(s)
            if f == f and f not in (float("inf"), float("-inf")):
                return f
        except ValueError:
            pass
        return s
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return {k: canon(val) for k, val in v.items()}
    if isinstance(v, list):
        items = [canon(x, options) for x in v]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except TypeError:
            return items
    return v


def align_multiselect(a: Any, b: Any) -> Tuple[Any, Any]:
    """When one side saved a multi-select as a list and the other as a
    comma-joined string, split the string so both compare as multisets.

    Guarded so an absence token is never split: `(["a","b"], "NR")` must stay a
    disagreement between a list and a reporting gap, not become
    `["a","b"]` vs `["NR"]` — which would give the same claim a different
    grouping token depending on what it was compared against.
    """
    def _split(s: str) -> list:
        return [p.strip() for p in s.split(",") if p.strip()]

    def _splittable(s: Any) -> bool:
        return isinstance(s, str) and absence.canonical_absence_label(s) is None

    def _scalar_list(x) -> bool:
        return isinstance(x, list) and all(not isinstance(i, (dict, list)) for i in x)

    if _scalar_list(a) and _splittable(b):
        return a, _split(b)
    if _scalar_list(b) and _splittable(a):
        return _split(a), b
    return a, b


def is_unfilled(cell: Any) -> bool:
    """Nobody recorded anything here — as distinct from an explicit "not
    reported" claim, which is a finding about the paper and compares equal to
    another one. Rule 2.

    An envelope carrying any recognised `status` is a claim by definition, so it
    is never unfilled: that is what keeps `{value: "", status: not_reported}`
    comparable while a bare `""` is not.
    """
    if isinstance(cell, dict) and absence.normalize_status(cell.get("status")) is not None:
        return False
    v = cell.get("value") if isinstance(cell, dict) and "value" in cell else cell
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) == 0
    return False


# ── The public comparison API ───────────────────────────────────────────────

def compare_pair(
    a: Any, b: Any, options: Any = None
) -> Tuple[Optional[str], Optional[str]]:
    """Aligned canonical tokens for a pair. `None` on a side means that side is
    incomparable (a failure, or nothing recorded).

    This — not a single-value function — is the sanctioned way to get tokens for
    *grouping* (kappa categories, dedupe), because multi-select alignment is
    inherently pairwise: no single-value tokenizer can know that `["a","b"]`
    should agree with `"a, b"`.
    """
    a_unfilled, b_unfilled = is_unfilled(a), is_unfilled(b)
    a_n = unwrap_for_compare(a) if not a_unfilled else FAILED_SENTINEL
    b_n = unwrap_for_compare(b) if not b_unfilled else FAILED_SENTINEL
    a_dead = a_unfilled or a_n is FAILED_SENTINEL
    b_dead = b_unfilled or b_n is FAILED_SENTINEL
    if not a_dead and not b_dead:
        a_n, b_n = align_multiselect(a_n, b_n)
    return (
        None if a_dead else _token(canon(a_n, options)),
        None if b_dead else _token(canon(b_n, options)),
    )


def _token(canonical: Any) -> str:
    """Stable string form of a canonical value, so callers can use it as a dict
    key or a kappa category (a canonical value may be a float, dict or list)."""
    try:
        return json.dumps(canonical, sort_keys=True, default=str)
    except TypeError:
        return str(canonical)


def agreement(a: Any, b: Any, options: Any = None) -> str:
    """AGREE | DISAGREE | INCOMPARABLE — the one verdict function.

    Defined in terms of `compare_pair`, so the boolean verdict and the grouping
    token can never diverge. That divergence is the actual bug in the four-way
    split this module replaces.
    """
    ka, kb = compare_pair(a, b, options)
    if ka is None or kb is None:
        return INCOMPARABLE
    return AGREE if ka == kb else DISAGREE


def values_agree(a: Any, b: Any, options: Any = None) -> bool:
    """`agreement(...) == AGREE`. For callers that fold INCOMPARABLE into
    "not agreed" — which is correct for a percentage denominator, and wrong for
    a kappa sample."""
    return agreement(a, b, options) == AGREE


def agreement_token(cell: Any, options: Any = None) -> Optional[str]:
    """Single-cell grouping token; `None` when the cell is not a claim.

    Prefer `compare_pair` when both sides are in hand — this cannot see
    multi-select alignment.
    """
    if is_unfilled(cell):
        return None
    unwrapped = unwrap_for_compare(cell)
    if unwrapped is FAILED_SENTINEL:
        return None
    return _token(canon(unwrapped, options))
