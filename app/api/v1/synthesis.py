"""Synthesis endpoints — suggesting how a form's table maps onto meta-analysis roles.

The reviewer picks a form on the Synthesis screen and we ask a model which column
holds the event counts, which holds the denominators, whether rows are one-per-
comparison or one-per-arm, and so on. The reviewer then confirms each slot before
anything is pooled.

This deliberately copies the shape of ``signature_gen.py:_classify_anchors_with_llm``,
which solves nearly the same problem for table key columns:

  - a markdown prompt template with a ``[[COLUMNS_JSON]]`` placeholder,
  - ``with_structured_output(..., method="json_schema")`` for a validated answer,
  - **every returned column checked against the real column list** before use,
  - the model's reasoning logged,
  - and a deterministic fallback when the model is unavailable or answers badly.

That last point matters: an LLM-first mapper that dies with Bedrock would take the
whole screen down with it. The heuristic below is not as good, and it is not meant
to be — it is what keeps the page usable while the model is unreachable.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from supabase import create_client

from app.config import settings
from app.dependencies import get_current_user
from app.rate_limit import limiter
from app.services.cache_service import cache_service
from app.services.project_access import check_project_access
from config.models import CODEGEN_SIGNATURE_MODEL

logger = logging.getLogger(__name__)
router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# Small, cheap classification — the signature model is already sized for this.
SYNTHESIS_MAPPING_MODEL = os.environ.get("SYNTHESIS_MAPPING_MODEL", CODEGEN_SIGNATURE_MODEL)

CACHE_TTL_SECONDS = 24 * 60 * 60

# Roles that must be filled before an analysis can run, per verdict + layout.
# `timepoint` is deliberately absent from every list: several real forms encode
# the timepoint inside the outcome option text ("Pain relief at 6 hours"), and
# requiring a separate column would block those forms outright.
REQUIRED_SLOTS: Dict[tuple, List[str]] = {
    ("dichotomous", "wide"): [
        "events_treatment", "total_treatment", "events_comparator", "total_comparator", "outcome",
    ],
    ("continuous", "wide"): [
        "mean_treatment", "sd_treatment", "n_treatment",
        "mean_comparator", "sd_comparator", "n_comparator", "outcome",
    ],
    ("dichotomous", "long"): ["value", "denominator", "arm", "outcome"],
    ("continuous", "long"): ["value", "variability", "denominator", "arm", "outcome"],
    # A table holding an already-computed effect (adjusted OR, hazard ratio) with
    # its own precision. Always wide — an effect is already a contrast, so there
    # is no second row to pair it against. Precision is required too, but it can
    # come from `effect_se` OR both CI bounds, which a flat required-list cannot
    # express; the frontend's `missingSlots` owns that choice.
    ("effect", "wide"): ["effect_value", "outcome"],
    # Single-group shapes: one row is one study's own prevalence, or its own
    # correlation, with nothing to compare against. Always wide.
    ("proportion", "wide"): ["prop_events", "prop_total", "outcome"],
    ("correlation", "wide"): ["corr_r", "corr_n", "outcome"],
}

# Optional roles never named in REQUIRED_SLOTS still have to be in the accepted
# vocabulary, or `_validate` drops them as invented — which is how a mapped CI
# would silently vanish between the model and the browser.
OPTIONAL_SLOTS = {"timepoint", "effect_se", "effect_ci_lower", "effect_ci_upper"}

ALL_SLOTS = sorted({s for slots in REQUIRED_SLOTS.values() for s in slots} | OPTIONAL_SLOTS)

# Roles that hold a measurement rather than a label. `variability` is pointedly
# NOT here — it legitimately holds text like "1.2 to 3.4" on real forms.
#
# These are checked against the column's declared type, but only a `select` is
# rejected outright. A select carries a declared vocabulary
# (`central_tendency_measure` → Mean / Median / NA), so it names a measure and
# can never BE one. A `text` column is accepted with a warning, because real
# forms do declare numeric outcome columns as text — `dental_implant_outcomes_
# continuous.central_tendency_value` is text and holds the mean. Rejecting those
# would make the suggestion useless on exactly the forms that need it, and the
# downstream parser already routes anything unparseable to the exclusion ledger.
NUMERIC_SLOTS = {
    "events_treatment", "total_treatment", "events_comparator", "total_comparator",
    "mean_treatment", "sd_treatment", "n_treatment",
    "mean_comparator", "sd_comparator", "n_comparator",
    "value", "denominator",
    "effect_value", "effect_se", "effect_ci_lower", "effect_ci_upper",
    "prop_events", "prop_total", "corr_r", "corr_n",
}

# ...but a text column whose NAME reads as an annotation is still refused for a
# measurement slot. This is the realistic failure mode — `arm1_label` proposed as
# `mean_treatment` — and it is exactly the mistake a reviewer might wave through,
# because the slot looks filled. `central_tendency_value` survives it.
_LABEL_LIKE = re.compile(
    r"(_|^)(label|name|type|measure|category|comment|note|definition|description|"
    r"detail|reported|source|unit)(_|$)",
    re.I,
)


class SuggestMappingRequest(BaseModel):
    form_id: UUID
    """Which table field to map. Defaults to the widest table in the form."""
    field_name: Optional[str] = None
    """Skip the cache and re-ask the model."""
    force: bool = False


class SuggestMappingResponse(BaseModel):
    form_id: str
    form_name: str
    field_name: Optional[str]
    columns: List[Dict[str, Any]]
    verdict: str
    layout: Optional[str] = None
    slots: Dict[str, str] = Field(default_factory=dict)
    variability_measure_column: Optional[str] = None
    comparator_value: Optional[str] = None
    reasoning: str = ""
    per_slot_reasoning: Dict[str, str] = Field(default_factory=dict)
    missing_slots: List[str] = Field(default_factory=list)
    partial: bool = False
    dropped: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    source: str = "llm"
    cached: bool = False


# ── Column extraction ────────────────────────────────────────────────────────


def _table_fields(fields: List[dict]) -> List[dict]:
    # `forms.fields` is user-authored JSONB and is not guaranteed to hold objects
    # — a live demo form stores a bare string in the array, which crashed this
    # endpoint with AttributeError before the isinstance guard. Skip anything
    # that isn't a field definition rather than trusting the column's shape.
    return [
        f for f in (fields or [])
        if isinstance(f, dict) and f.get("field_type") == "array" and f.get("subform_fields")
    ]


def _pick_table_field(fields: List[dict], requested: Optional[str]) -> Optional[dict]:
    tables = _table_fields(fields)
    if not tables:
        return None
    if requested:
        for t in tables:
            if t.get("field_name") == requested:
                return t
    # Mirrors longFormatTransform's fallback: the widest table is the detail table.
    return max(tables, key=lambda t: len(t.get("subform_fields") or []))


def _columns_of(table: dict) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sf in table.get("subform_fields") or []:
        if not isinstance(sf, dict):
            continue
        name = sf.get("field_name")
        if not name:
            continue
        out.append({
            "name": name,
            "type": sf.get("field_type") or "text",
            "description": sf.get("field_description") or sf.get("description") or "",
            "options": sf.get("options") or [],
        })
    return out


# ── Deterministic fallback ───────────────────────────────────────────────────

_ARM1 = re.compile(r"(^|_)(arm)?1(_|$)|treat|interven|experim", re.I)
_ARM2 = re.compile(r"(^|_)(arm)?2(_|$)|control|compar|placebo|referen", re.I)
_EVENT = re.compile(r"event|responder|success|n_with|cases", re.I)
_TOTAL = re.compile(r"(^|_)n(_|$)|total|analyz|randomi|sample|denom", re.I)
_MEAN = re.compile(r"mean|central_tendency|average", re.I)
_SD = re.compile(r"(^|_)sd(_|$)|std|deviation", re.I)
_VARIABILITY = re.compile(r"variabilit|dispersion|spread", re.I)
_ARMCOL = re.compile(r"^(arm|intervention|group|treatment|drug)(_|$)|_arm$|arm_(name|label)", re.I)
_OUTCOME = re.compile(r"outcome", re.I)
_TIME = re.compile(r"timepoint|time_point|followup|follow_up|(^|_)time($|_)", re.I)

_DIAGNOSTIC_CELLS = {"tp", "fp", "fn", "tn"}

# A published effect and its precision. Deliberately narrow: this branch only
# runs after every arm-based shape has failed, and a false positive here would
# hand the reviewer a mapping onto columns that are not effects at all.
_EFFECT = re.compile(
    r"(^|_)(effect|estimate|adjusted_(or|rr|hr)|(or|rr|hr|irr)_adjusted|hazard_ratio|"
    r"odds_ratio|risk_ratio|rate_ratio|mean_difference|smd|beta|coefficient)(_|$)", re.I)
_EFFECT_SE = re.compile(r"(^|_)(se|std_?err\w*|standard_error)(_|$)", re.I)
# Single-group shapes. `_PREVALENCE` is deliberately narrow — a bare event count
# is far more likely to be one arm of a comparison, which the branches above
# handle, so this only fires on wording that names a single-group quantity.
_PREVALENCE = re.compile(r"prevalen|affected|positive_n|proportion_n|(^|_)rate_n(_|$)", re.I)
_CORRELATION = re.compile(r"correlat|(^|_)r_value(_|$)|pearson|spearman|(^|_)rho(_|$)|(^|_)r(_|$)", re.I)
_SAMPLE_N = re.compile(r"(^|_)n(_|$)|sample_size|total|analyz|assessed|participants", re.I)
_CI_LOWER = re.compile(r"(ci_?low|low\w*_?ci|lower_?(bound|limit|ci)|_lcl$|^lcl$)", re.I)
_CI_UPPER = re.compile(r"(ci_?up|up\w*_?ci|upper_?(bound|limit|ci)|_ucl$|^ucl$)", re.I)

# Names checked before any pattern, most specific first. Real forms cluster on a
# small vocabulary, and an exact hit beats a regex that also catches a
# neighbouring column (`intervention` vs `intervention_detail`,
# `outcome_type` vs `time_outcome_name`).
_PREFERRED: Dict[str, List[str]] = {
    "arm": ["arm", "arm_name", "arm_label", "intervention", "group", "treatment", "study_arm"],
    "outcome": ["outcome", "outcome_type", "outcome_name", "outcome_reported", "outcome_measure"],
    "timepoint": ["timepoint", "time_point", "followup_timepoint", "follow_up_timepoint",
                  "followup_point", "follow_up_duration", "followup", "time"],
    "value_dich": ["events_n", "n_events", "events", "event_count", "n_with_event"],
    "value_cont": ["central_tendency", "central_tendency_value", "mean", "mean_value"],
    "denominator": ["n_analyzed", "total_n_analyzed", "n_assessed", "denominator", "total", "n"],
    "variability": ["variability", "variability_sd", "sd", "std_dev", "standard_deviation"],
}


def _heuristic_mapping(columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Name-based mapping, used only when the model is unavailable or invalid.

    Conservative on purpose. Each slot is filled from an exact preferred name if
    one is present, and otherwise only when exactly one column matches the
    pattern — a fallback that guesses wrong is worse than one that leaves the
    reviewer a blank dropdown to fill in themselves.
    """
    names = [c["name"] for c in columns]
    nameset = set(names)
    by_name = {c["name"]: c for c in columns}

    if sum(1 for n in names if n.lower() in _DIAGNOSTIC_CELLS) >= 3:
        return {
            "verdict": "diagnostic_accuracy", "layout": None, "slots": {},
            "reasoning": "This table reports test performance as true/false positives and negatives, "
                         "which is pooled as sensitivity and specificity rather than as a risk ratio "
                         "or mean difference. That method is not available on this screen yet.",
        }

    def measurable(n: str) -> bool:
        """A select carries a declared vocabulary, so it labels a measure rather than being one."""
        col = by_name[n]
        return not (col["type"] == "select" and col.get("options"))

    used: set = set()

    def pick(key: Optional[str], pattern, numeric: bool = True, extra=None) -> Optional[str]:
        def ok(n: str) -> bool:
            if n in used:
                return False
            if numeric and not measurable(n):
                return False
            return extra(n) if extra else True

        for candidate in _PREFERRED.get(key or "", []):
            if candidate in nameset and ok(candidate):
                used.add(candidate)
                return candidate
        hits = [n for n in names if ok(n) and pattern.search(n)]
        if len(hits) == 1:
            used.add(hits[0])
            return hits[0]
        return None

    slots: Dict[str, str] = {}

    def paired(pattern, extra=None) -> tuple:
        a = pick(None, pattern, extra=lambda n: _ARM1.search(n) and (not extra or extra(n)))
        b = pick(None, pattern, extra=lambda n: _ARM2.search(n) and (not extra or extra(n)))
        return a, b

    # Wide dichotomous — paired event columns are the giveaway.
    ev1, ev2 = paired(_EVENT)
    if ev1 and ev2:
        verdict, layout = "dichotomous", "wide"
        slots["events_treatment"], slots["events_comparator"] = ev1, ev2
        n1, n2 = paired(_TOTAL, extra=lambda n: not _EVENT.search(n))
        if n1:
            slots["total_treatment"] = n1
        if n2:
            slots["total_comparator"] = n2
    else:
        m1, m2 = paired(_MEAN, extra=lambda n: not _SD.search(n))
        if m1 and m2:
            verdict, layout = "continuous", "wide"
            slots["mean_treatment"], slots["mean_comparator"] = m1, m2
            sd1, sd2 = paired(_SD)
            if sd1:
                slots["sd_treatment"] = sd1
            if sd2:
                slots["sd_comparator"] = sd2
            nn1, nn2 = paired(_TOTAL, extra=lambda n: not _MEAN.search(n) and not _SD.search(n))
            if nn1:
                slots["n_treatment"] = nn1
            if nn2:
                slots["n_comparator"] = nn2
        else:
            # Single-group shapes come first, but only when the table has NO arm
            # column at all and the count column actually names a single-group
            # quantity. A generic `events_n` with no arm column deliberately stays
            # dichotomous/long — its arms may live in a separate form, which is how
            # the dental-implant forms in zforms/ are built.
            has_arm_column = any(_ARMCOL.search(n) for n in names)
            if not has_arm_column:
                    # A correlation with its sample size: one row, one r.
                    corr = pick(None, _CORRELATION)
                    if corr:
                        corr_n = pick(None, _SAMPLE_N)
                        if corr_n:
                            corr_slots = {"corr_r": corr, "corr_n": corr_n}
                            outcome_col = pick("outcome", _OUTCOME, numeric=False,
                                               extra=lambda n: "other" not in n.lower())
                            if outcome_col:
                                corr_slots["outcome"] = outcome_col
                            time_col = pick("timepoint", _TIME, numeric=False)
                            if time_col:
                                corr_slots["timepoint"] = time_col
                            return {
                                "verdict": "correlation", "layout": "wide", "slots": corr_slots,
                                "reasoning": "This table reports a correlation and the sample it came "
                                             "from, one row per study, so it is mapped as a "
                                             "single-group correlation pooled on Fisher's z.",
                            }

                    # A prevalence: a single group's count out of a denominator.
                    prev = pick(None, _PREVALENCE)
                    if prev:
                        prev_n = pick(None, _SAMPLE_N)
                        if prev_n:
                            prop_slots = {"prop_events": prev, "prop_total": prev_n}
                            outcome_col = pick("outcome", _OUTCOME, numeric=False,
                                               extra=lambda n: "other" not in n.lower())
                            if outcome_col:
                                prop_slots["outcome"] = outcome_col
                            time_col = pick("timepoint", _TIME, numeric=False)
                            if time_col:
                                prop_slots["timepoint"] = time_col
                            return {
                                "verdict": "proportion", "layout": "wide", "slots": prop_slots,
                                "reasoning": "This table reports one group's count out of a "
                                             "denominator with no comparator, so it is mapped as a "
                                             "single-group proportion.",
                            }

            # Long layout — one set of outcome columns plus a column naming the arm.
            ev = pick("value_dich", _EVENT, extra=lambda n: "pct" not in n.lower()
                      and "percent" not in n.lower())
            if ev:
                verdict, layout = "dichotomous", "long"
                slots["value"] = ev
            else:
                mean = pick("value_cont", _MEAN, extra=lambda n: not _SD.search(n))
                if not mean:
                    # Last resort before refusing: the table may report the effect
                    # itself rather than the arms behind it. Requires a precision
                    # column as well — an effect with no CI or SE cannot be
                    # weighted, so proposing it would only waste the reviewer's time.
                    eff = pick(None, _EFFECT)
                    eff_se = pick(None, _EFFECT_SE) if eff else None
                    eff_lo = pick(None, _CI_LOWER) if eff else None
                    eff_hi = pick(None, _CI_UPPER) if eff else None
                    if eff and (eff_se or (eff_lo and eff_hi)):
                        effect_slots = {"effect_value": eff}
                        if eff_se:
                            effect_slots["effect_se"] = eff_se
                        if eff_lo and eff_hi:
                            effect_slots["effect_ci_lower"] = eff_lo
                            effect_slots["effect_ci_upper"] = eff_hi
                        outcome_col = pick("outcome", _OUTCOME, numeric=False,
                                           extra=lambda n: "other" not in n.lower())
                        if outcome_col:
                            effect_slots["outcome"] = outcome_col
                        time_col = pick("timepoint", _TIME, numeric=False)
                        if time_col:
                            effect_slots["timepoint"] = time_col
                        return {
                            "verdict": "effect", "layout": "wide", "slots": effect_slots,
                            "reasoning": "This table reports an already-computed effect with its own "
                                         "precision rather than arm-level counts, so it is mapped as "
                                         "a reported effect. Confirm which scale the effect column is "
                                         "printed on before running.",
                        }
                    return {
                        "verdict": "not_poolable", "layout": None, "slots": {},
                        "reasoning": "No column in this table holds an event count or a mean, so "
                                     "there is no effect estimate here to pool.",
                    }
                verdict, layout = "continuous", "long"
                slots["value"] = mean
                var = pick("variability", _VARIABILITY, numeric=False,
                           extra=lambda n: "measure" not in n.lower())
                if var:
                    slots["variability"] = var
            den = pick("denominator", _TOTAL,
                       extra=lambda n: not _EVENT.search(n) and not _MEAN.search(n))
            if den:
                slots["denominator"] = den
            arm = pick("arm", _ARMCOL, numeric=False)
            if arm:
                slots["arm"] = arm

    outcome = pick("outcome", _OUTCOME, numeric=False, extra=lambda n: "other" not in n.lower())
    if outcome:
        slots["outcome"] = outcome
    time = pick("timepoint", _TIME, numeric=False)
    if time:
        slots["timepoint"] = time

    return {
        "verdict": verdict,
        "layout": layout,
        "slots": slots,
        "reasoning": "Suggested by matching column names, because the mapping model was "
                     "unavailable. Check each slot carefully before confirming.",
    }


# ── LLM call ─────────────────────────────────────────────────────────────────


def _ask_model(table_name: str, table_desc: str, columns: List[Dict[str, Any]]) -> Any:
    """Blocking — callers must push this off the event loop."""
    from core.generators.models import AnalysisMappingSuggestion
    from utils.langchain_cost_callback import make_callback_config
    from utils.lm_config import get_langchain_model

    payload = {
        "table_field": table_name,
        "table_description": table_desc or "",
        "columns": columns,
    }
    template = Path(__file__).resolve().parents[3] / "core" / "generators" / "prompts" / "suggest_analysis_mapping.md"
    prompt = template.read_text(encoding="utf-8").replace(
        "[[COLUMNS_JSON]]", json.dumps(payload, indent=2)
    )
    model = get_langchain_model(SYNTHESIS_MAPPING_MODEL, temperature=0.0, max_tokens=2000)
    structured = model.with_structured_output(AnalysisMappingSuggestion, method="json_schema")
    return structured.invoke(
        prompt,
        config=make_callback_config("synthesis:mapping", schema_name=table_name),
    )


def _validate(suggestion: Any, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip anything the model made up, then report what survived.

    The model is not trusted to have used real column names, to have respected the
    declared types, or to have filled every required slot. Each of those is checked
    here rather than in the browser, so the frontend only ever receives a mapping
    that at least refers to columns that exist.
    """
    valid = {c["name"]: c for c in columns}
    dropped: List[str] = []
    warnings: List[str] = []
    slots: Dict[str, str] = {}
    used: Dict[str, str] = {}

    per_slot_reasoning: Dict[str, str] = {}

    for entry in (suggestion.slots or []):
        role = getattr(entry, "role", None)
        col = getattr(entry, "column", None)
        if not role or not col:
            continue
        per_slot_reasoning[role] = getattr(entry, "reasoning", "") or ""
        if role not in ALL_SLOTS:
            dropped.append(f"{role}: not an analysis role")
            continue
        if col not in valid:
            dropped.append(f"{role}: '{col}' is not a column in this table")
            continue
        if col in used:
            dropped.append(f"{role}: '{col}' is already mapped to {used[col]}")
            continue
        if role in NUMERIC_SLOTS:
            col_type = valid[col]["type"]
            if col_type == "select" and valid[col].get("options"):
                dropped.append(
                    f"{role}: '{col}' is a select with fixed options, so it labels a measure "
                    f"rather than holding one"
                )
                continue
            if col_type != "number" and _LABEL_LIKE.search(col):
                dropped.append(
                    f"{role}: '{col}' is a {col_type} column whose name reads as a label, "
                    f"not a measurement"
                )
                continue
            if col_type != "number":
                warnings.append(
                    f"{role}: '{col}' is declared as {col_type}, so its values are parsed as "
                    f"numbers — anything unparseable is listed in the exclusion ledger"
                )
        slots[role] = col
        used[col] = role

    verdict = suggestion.verdict
    layout = suggestion.layout
    required = REQUIRED_SLOTS.get((verdict, layout or ""), [])
    missing = [r for r in required if r not in slots]

    vm = suggestion.variability_measure_column
    if vm and vm not in valid:
        dropped.append(f"variability_measure_column: '{vm}' is not a column in this table")
        vm = None

    return {
        "verdict": verdict,
        "layout": layout,
        "slots": slots,
        "variability_measure_column": vm,
        "comparator_value": suggestion.comparator_value,
        "reasoning": suggestion.reasoning or "",
        "per_slot_reasoning": {k: v for k, v in per_slot_reasoning.items() if k in slots and v},
        "missing_slots": missing,
        "partial": bool(missing),
        "dropped": dropped,
        "warnings": warnings,
    }


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/suggest-mapping", response_model=SuggestMappingResponse)
@limiter.limit("30/minute")
async def suggest_mapping(
    request: Request,
    data: SuggestMappingRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Propose how this form's table maps onto meta-analysis roles."""
    result = supabase.table("forms").select(
        "id, form_name, project_id, fields"
    ).eq("id", str(data.form_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    form = result.data[0]

    # Read-only: must keep working on archived projects, like the other
    # can_view_results endpoints.
    await check_project_access(
        UUID(form["project_id"]), user_id, "can_view_results", mutating=False
    )

    table = _pick_table_field(form.get("fields") or [], data.field_name)
    if table is None:
        return SuggestMappingResponse(
            form_id=str(form["id"]),
            form_name=form.get("form_name") or "",
            field_name=None,
            columns=[],
            verdict="not_poolable",
            reasoning="This form has no repeating table. A meta-analysis needs one row per "
                      "comparison or per study arm, so there is nothing here to pool.",
            source="deterministic",
        )

    columns = _columns_of(table)
    field_name = table.get("field_name")

    # Key on the columns themselves, so regenerating the form invalidates the
    # suggestion rather than serving a mapping for columns that no longer exist.
    fingerprint = hashlib.sha256(
        json.dumps([(c["name"], c["type"]) for c in columns], sort_keys=True).encode()
    ).hexdigest()[:16]
    cache_key = f"synthesis_mapping:{form['id']}:{field_name}:{fingerprint}"

    if not data.force:
        cached = cache_service.get(cache_key)
        if cached:
            return SuggestMappingResponse(**{**cached, "cached": True})

    try:
        suggestion = await asyncio.to_thread(
            _ask_model, field_name, table.get("field_description") or "", columns
        )
        if suggestion is None:
            raise ValueError("mapping model returned None")
        body = _validate(suggestion, columns)
        source = "llm"
        logger.info(
            "Synthesis mapping for form=%s field=%s: verdict=%s layout=%s slots=%s "
            "missing=%s dropped=%s warnings=%s | %s",
            form["id"], field_name, body["verdict"], body["layout"],
            sorted(body["slots"]), body["missing_slots"], body["dropped"], body["warnings"],
            body["reasoning"],
        )
    except Exception as exc:
        logger.warning(
            "Synthesis mapping model failed for form=%s field=%s (%s) — "
            "falling back to the name heuristic",
            form["id"], field_name, exc,
        )
        fallback = _heuristic_mapping(columns)
        required = REQUIRED_SLOTS.get((fallback["verdict"], fallback["layout"] or ""), [])
        missing = [r for r in required if r not in fallback["slots"]]
        body = {
            **fallback,
            "variability_measure_column": next(
                (c["name"] for c in columns if re.search(r"variab.*measure", c["name"], re.I)), None
            ),
            "comparator_value": None,
            "per_slot_reasoning": {},
            "missing_slots": missing,
            "partial": bool(missing),
            "dropped": [],
            "warnings": [],
        }
        source = "heuristic"

    payload = {
        "form_id": str(form["id"]),
        "form_name": form.get("form_name") or "",
        "field_name": field_name,
        "columns": columns,
        "source": source,
        **body,
    }
    # Only a model answer is worth caching; a fallback should be retried next time
    # in case the model has come back.
    if source == "llm":
        cache_service.set(cache_key, payload, ttl=CACHE_TTL_SECONDS)

    return SuggestMappingResponse(**payload, cached=False)
