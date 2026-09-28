#!/usr/bin/env python3
"""Baseline check for the Synthesis analysis-mapping suggester.

    python backend/scripts/check_synthesis_mapping.py

Runs two things offline, without touching Supabase or Bedrock:

  1. The deterministic fallback (`_heuristic_mapping`) against every form fixture
     in `resources/forms/`, asserting the verdict + layout it reaches for the forms whose
     shape we know. This is the path that runs when the mapping model is
     unavailable, so it needs to keep working even though it is the second
     choice.
  2. The guardrails in `_validate`, fed a deliberately bad model answer, to
     confirm invented columns, invented roles, duplicate mappings and label
     columns proposed for measurement slots are all refused — and that a
     legitimately text-typed value column still survives, with a warning.

Exits non-zero if any expectation fails.
"""

import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/scripts/ -> project root
sys.path.insert(0, os.path.join(REPO, "backend"))

# Settings validates at import and the supabase client parses its URL at import,
# so both need placeholder values. The functions under test are pure and never
# reach the network.
os.environ.setdefault("SUPABASE_URL", "https://dummy.supabase.co")
for _key in (
    "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY",
    "SECRET_KEY", "JWT_SECRET_KEY", "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
):
    os.environ.setdefault(_key, "dummy")

from app.api.v1.synthesis import (  # noqa: E402
    _columns_of, _heuristic_mapping, _pick_table_field, _validate, REQUIRED_SLOTS, ALL_SLOTS,
)


class Slot:
    """Stand-in for core.generators.models.MappedSlot."""

    def __init__(self, role, column, reasoning=""):
        self.role, self.column, self.reasoning = role, column, reasoning

# fixture filename -> (verdict, layout, slots allowed to be missing)
EXPECTED = {
    "cd015432_outcomes_dichotomous.json": ("dichotomous", "wide", []),
    "cd015432_outcomes_continuous.json": ("continuous", "wide", []),
    "cd010266_outcomes_dichotomous.json": ("dichotomous", "wide", []),
    "cd004714_outcomes_continuous.json": ("continuous", "wide", []),
    "acute_dental_pain_dichotomous_outcomes.json": ("dichotomous", "long", []),
    "acute_dental_pain_continuous_outcomes.json": ("continuous", "long", []),
    "local_anesthetics_dichotomous_outcomes.json": ("dichotomous", "long", []),
    "local_anesthetics_continuous_outcomes.json": ("continuous", "long", []),
    "corticosteroids_dichotomous_outcomes.json": ("dichotomous", "long", []),
    "corticosteroids_continuous_outcomes.json": ("continuous", "long", []),
    # This table has no arm column at all — the arms live in the separate
    # interventions form — so an unfilled `arm` slot is the correct answer.
    "dental_implant_outcomes_continuous.json": ("continuous", "long", ["arm"]),
    "dental_implant_outcomes_dichotomous.json": ("dichotomous", "long", ["arm"]),
    # tp/fp/fn/tn is a real meta-analysis, but not one this screen implements.
    "oral_cancer_outcomes.json": ("diagnostic_accuracy", None, []),
    # No effect estimate anywhere in these tables.
    "acute_dental_pain_risk_of_bias.json": ("not_poolable", None, []),
    "local_anesthetics_risk_of_bias.json": ("not_poolable", None, []),
    "corticosteroids_risk_of_bias.json": ("not_poolable", None, []),
    "cd015432_interventions.json": ("not_poolable", None, []),
    "oral_cancer_index_test.json": ("not_poolable", None, []),
}

failures = []
checked = 0


def check(condition, message):
    if not condition:
        failures.append(message)


print("\nFallback mapping over resources/forms/ fixtures\n" + "-" * 78)
for path in sorted(glob.glob(os.path.join(REPO, "resources", "forms", "*", "*.json"))):
    try:
        doc = json.load(open(path))
    except Exception:
        continue
    if not isinstance(doc, dict):
        continue
    table = _pick_table_field(doc.get("fields") or [], None)
    if table is None:
        continue

    columns = _columns_of(table)
    mapping = _heuristic_mapping(columns)
    required = REQUIRED_SLOTS.get((mapping["verdict"], mapping["layout"] or ""), [])
    missing = [r for r in required if r not in mapping["slots"]]
    name = os.path.basename(path)

    note = ""
    if name in EXPECTED:
        checked += 1
        want_verdict, want_layout, allowed_missing = EXPECTED[name]
        got = (mapping["verdict"], mapping["layout"])
        unexpected = [m for m in missing if m not in allowed_missing]
        if got != (want_verdict, want_layout):
            note = f"  <== expected {(want_verdict, want_layout)}"
            check(False, f"{name}: got {got}, expected {(want_verdict, want_layout)}")
        elif unexpected:
            note = f"  <== missing {unexpected}"
            check(False, f"{name}: slots not filled: {unexpected}")
        else:
            note = "  ok"

    print(f"{name[:44]:<46} {mapping['verdict']:<20} {mapping['layout'] or '-':<5}{note}")


print("\nGuardrails on a bad model answer\n" + "-" * 78)


class BadAnswer:
    verdict = "dichotomous"
    layout = "wide"
    slots = [
        Slot("events_treatment", "arm1_events", "the only event count"),
        Slot("total_treatment", "arm1_n"),
        Slot("events_comparator", "ghost_column"),   # does not exist
        Slot("total_comparator", "arm2_n"),
        Slot("outcome", "outcome"),
        Slot("timepoint", "arm1_n"),                 # already mapped
        Slot("bogus_role", "outcome", "nonsense"),   # not an analysis role
        Slot("mean_treatment", "arm1_label"),        # a label, not a measurement
    ]
    variability_measure_column = "also_missing"
    comparator_value = None
    reasoning = "deliberately wrong"


fixture = json.load(open(os.path.join(REPO, "resources", "forms", "ibuprofen", "cd015432_outcomes_dichotomous.json")))
cols = _columns_of(_pick_table_field(fixture["fields"], None))
result = _validate(BadAnswer(), cols)
for line in result["dropped"]:
    print(f"  dropped  {line}")

check("events_comparator" not in result["slots"], "an invented column survived validation")
check("bogus_role" not in result["slots"], "an invented role survived validation")
check("mean_treatment" not in result["slots"], "a label column was accepted for a measurement slot")
check("timepoint" not in result["slots"], "a duplicate column mapping survived validation")
check(result["variability_measure_column"] is None, "an invented variability column survived")
check(result["partial"] and "events_comparator" in result["missing_slots"],
      "a partial mapping was not reported as partial")
check(set(result["per_slot_reasoning"]) <= set(result["slots"]),
      "reasoning was kept for a slot that was dropped")

# A text-typed column that does not read as a label is legitimate — real forms
# declare numeric outcome columns as text — and must survive, with a warning.
fixture2 = json.load(
    open(os.path.join(REPO, "resources", "forms", "other_forms", "dental_implant_outcomes_continuous.json"))
)
cols2 = _columns_of(_pick_table_field(fixture2["fields"], None))


class TextValued:
    verdict = "continuous"
    layout = "long"
    slots = [
        Slot("value", "central_tendency_value"),
        Slot("denominator", "total_n_analyzed"),
    ]
    variability_measure_column = None
    comparator_value = None
    reasoning = ""


result2 = _validate(TextValued(), cols2)
for line in result2["warnings"]:
    print(f"  warned   {line}")
check(result2["slots"].get("value") == "central_tendency_value",
      "a legitimate text-typed value column was dropped")
check(any("central_tendency_value" in w for w in result2["warnings"]),
      "a text-typed column was accepted with no warning")

# ── The pre-computed-effect branch ───────────────────────────────────────────
# No resources/forms fixture reports an already-computed effect yet, so the branch is
# exercised against synthetic columns of the shape it exists for. Both halves
# matter: recognising the effect, and REFUSING when there is no precision to
# weight it by (an effect with no CI or SE cannot enter a meta-analysis, so
# proposing a mapping for it would only waste the reviewer's time).
print("\nPre-computed effect columns\n" + "-" * 78)


def cols_from(names):
    return [{"name": n, "type": "number", "description": "", "options": []} for n in names]


with_ci = _heuristic_mapping(cols_from(
    ["adjusted_or", "ci_lower", "ci_upper", "outcome", "followup_timepoint"]
))
print(f"  adjusted_or + CI bounds        -> {with_ci['verdict']} / {with_ci['layout']}")
check(with_ci["verdict"] == "effect" and with_ci["layout"] == "wide",
      f"an effect with CI bounds was classified {with_ci['verdict']}")
check(with_ci["slots"].get("effect_value") == "adjusted_or",
      "the effect estimate column was not mapped")
check(with_ci["slots"].get("effect_ci_lower") == "ci_lower"
      and with_ci["slots"].get("effect_ci_upper") == "ci_upper",
      "the CI bounds were not mapped")
check(with_ci["slots"].get("outcome") == "outcome", "the outcome column was not mapped")

with_se = _heuristic_mapping(cols_from(["hazard_ratio", "se", "outcome"]))
print(f"  hazard_ratio + se              -> {with_se['verdict']} / {with_se['layout']}")
check(with_se["verdict"] == "effect" and with_se["slots"].get("effect_se") == "se",
      f"an effect with a standard error was classified {with_se['verdict']}")

no_precision = _heuristic_mapping(cols_from(["hazard_ratio", "outcome", "notes"]))
print(f"  hazard_ratio alone             -> {no_precision['verdict']}")
check(no_precision["verdict"] == "not_poolable",
      "an effect with no precision column should not be offered as poolable")

# Arm data always outranks a reported effect: raw arms can produce any measure.
both = _heuristic_mapping(cols_from(
    ["arm1_events", "arm1_n", "arm2_events", "arm2_n", "adjusted_or", "ci_lower", "ci_upper", "outcome"]
))
print(f"  arm counts alongside an OR     -> {both['verdict']} / {both['layout']}")
check(both["verdict"] == "dichotomous",
      f"arm-level data must win over a reported effect, got {both['verdict']}")

# ── The single-group branches ────────────────────────────────────────────────
print("\nSingle-group columns\n" + "-" * 78)

prevalence = _heuristic_mapping(cols_from(["prevalence_cases", "n_assessed", "outcome"]))
print(f"  prevalence_cases + n_assessed  -> {prevalence['verdict']} / {prevalence['layout']}")
check(prevalence["verdict"] == "proportion" and prevalence["layout"] == "wide",
      f"a prevalence with a denominator was classified {prevalence['verdict']}")
check(prevalence["slots"].get("prop_events") == "prevalence_cases"
      and prevalence["slots"].get("prop_total") == "n_assessed",
      "the prevalence columns were not mapped")

correlation = _heuristic_mapping(cols_from(["correlation_r", "sample_size", "outcome"]))
print(f"  correlation_r + sample_size     -> {correlation['verdict']} / {correlation['layout']}")
check(correlation["verdict"] == "correlation" and correlation["layout"] == "wide",
      f"a correlation with its n was classified {correlation['verdict']}")
check(correlation["slots"].get("corr_r") == "correlation_r"
      and correlation["slots"].get("corr_n") == "sample_size",
      "the correlation columns were not mapped")

# Two groups' counts on one row is a contrast, not a prevalence — the arm-based
# branches must keep winning.
two_group = _heuristic_mapping(cols_from(
    ["arm1_events", "arm1_n", "arm2_events", "arm2_n", "outcome"]
))
print(f"  two arms of counts              -> {two_group['verdict']} / {two_group['layout']}")
check(two_group["verdict"] == "dichotomous",
      f"two arms of counts must stay dichotomous, got {two_group['verdict']}")

# A correlation with no sample size cannot be weighted, so it is not offered.
lonely_r = _heuristic_mapping(cols_from(["correlation_r", "notes", "author"]))
print(f"  correlation with no n           -> {lonely_r['verdict']}")
check(lonely_r["verdict"] == "not_poolable",
      f"a correlation with no sample size should not be poolable, got {lonely_r['verdict']}")

# And the validator must accept the new roles rather than dropping them as invented.
class EffectAnswer:
    verdict = "effect"
    layout = "wide"
    slots = [
        Slot("effect_value", "adjusted_or", "the reported adjusted odds ratio"),
        Slot("effect_ci_lower", "ci_lower"),
        Slot("effect_ci_upper", "ci_upper"),
        Slot("outcome", "outcome"),
    ]
    variability_measure_column = None
    comparator_value = None
    reasoning = ""


class SingleGroupAnswer:
    verdict = "proportion"
    layout = "wide"
    slots = [
        Slot("prop_events", "prevalence_cases", "the count of affected participants"),
        Slot("prop_total", "n_assessed"),
        Slot("outcome", "outcome"),
    ]
    variability_measure_column = None
    comparator_value = None
    reasoning = ""


single = _validate(SingleGroupAnswer(), cols_from(["prevalence_cases", "n_assessed", "outcome"]))
for line in single["dropped"]:
    print(f"  dropped  {line}")
check(not single["dropped"], "a valid proportion mapping had slots dropped")
check(single["missing_slots"] == [], f"proportion mapping reported missing: {single['missing_slots']}")

validated = _validate(EffectAnswer(), cols_from(["adjusted_or", "ci_lower", "ci_upper", "outcome"]))
for line in validated["dropped"]:
    print(f"  dropped  {line}")
check(not validated["dropped"], "a valid effect mapping had slots dropped")
check(validated["missing_slots"] == [], f"effect mapping reported missing: {validated['missing_slots']}")

# ── Optional Wan (2014) inputs: quartiles / extremes beside a median ─────────
print("\nMedian spread columns (q1/q3/min/max)\n" + "-" * 78)

median_long = _heuristic_mapping(cols_from(
    ["arm", "outcome", "central_tendency", "variability", "n_analyzed", "q1", "q3"]
))
print(f"  long continuous + q1/q3         -> {median_long['verdict']} / {median_long['layout']}")
check(median_long["verdict"] == "continuous" and median_long["layout"] == "long",
      f"a long continuous table with quartiles was classified {median_long['verdict']}")
check(median_long["slots"].get("q1") == "q1" and median_long["slots"].get("q3") == "q3",
      f"the quartile columns were not mapped: {median_long['slots']}")
check(median_long["slots"].get("value") == "central_tendency",
      "quartile columns displaced the central tendency")

ranged = _heuristic_mapping(cols_from(
    ["arm", "outcome", "central_tendency", "variability", "n_analyzed", "range_min", "range_max",
     "duration_minutes"]
))
print(f"  long continuous + min/max       -> {ranged['slots'].get('min')} / {ranged['slots'].get('max')}")
check(ranged["slots"].get("min") == "range_min" and ranged["slots"].get("max") == "range_max",
      f"the extreme columns were not mapped: {ranged['slots']}")
check("duration_minutes" not in ranged["slots"].values(), "`minutes` was read as a minimum")

lonely_q1 = _heuristic_mapping(cols_from(
    ["arm", "outcome", "central_tendency", "variability", "n_analyzed", "q1"]
))
check("q1" not in lonely_q1["slots"], "a lone quartile was mapped — it converts nothing on its own")

check(all(s in ALL_SLOTS for s in ("q1", "q3", "min", "max")),
      "q1/q3/min/max must be in ALL_SLOTS or _validate drops them as invented")


class MedianAnswer:
    verdict = "continuous"
    layout = "long"
    slots = [
        Slot("value", "central_tendency"),
        Slot("variability", "variability"),
        Slot("denominator", "n_analyzed"),
        Slot("arm", "arm"),
        Slot("outcome", "outcome"),
        Slot("q1", "q1"),
        Slot("q3", "q3"),
    ]
    variability_measure_column = None
    comparator_value = "Placebo"
    reasoning = ""


med = _validate(MedianAnswer(), cols_from(
    ["arm", "outcome", "central_tendency", "variability", "n_analyzed", "q1", "q3"]
))
for line in med["dropped"]:
    print(f"  dropped  {line}")
check(med["slots"].get("q1") == "q1" and med["slots"].get("q3") == "q3",
      f"a model-proposed q1/q3 was dropped: {med['slots']}")
check(not med["dropped"], "a valid median mapping with quartiles had slots dropped")

if failures:
    print(f"\n{len(failures)} FAILED:\n")
    for f in failures:
        print(f"  x {f}")
    print()
    sys.exit(1)

print(f"\n  {checked} fixtures matched their expected verdict; all guardrails held\n")
