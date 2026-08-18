"""
DSPy Signature Generator

Handles LLM-powered generation of DSPy signature classes using structured output.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Any
from utils.table_schema import field_key_columns, set_field_key_columns
from .models import SignatureGenerationState, SignatureSpec, AddFieldSpec
from utils.lm_config import get_langchain_model
from config.models import CODEGEN_SIGNATURE_MODEL

logger = logging.getLogger(__name__)

# Role-specific instructions injected into the per-column enrichment prompt.
_ROLE_CONTEXT: Dict[str, str] = {
    "anchor": (
        "This column is a ROW IDENTIFIER (extraction_role: anchor).\n"
        "It is used in Stage 1 to discover all unique rows in the paper.\n"
        "→ Write hints/rules focused on: locating and copying the LABEL or NAME verbatim, "
        "handling abbreviations or synonyms, and cases where the value is implicit rather than explicit.\n"
        "→ Do NOT write hints about numeric formatting or measurement values."
    ),
    "value": (
        "This column is a VALUE column (extraction_role: value).\n"
        "The row identity (anchor columns) is already known when this column is extracted.\n"
        "→ Write hints/rules focused on: WHERE in the results tables or text to find this specific "
        "measurement for a known row, how to format it, and NR/NA conditions.\n"
        "→ Reference that the row is already identified — focus on locating the value for that row."
    ),
    "parent_discovery": (
        "This is a TABLE PARENT field (extraction_role: parent_discovery).\n"
        "Stage 1 identifies rows using anchor columns; Stage 2 extracts value columns per row.\n"
        "→ Write hints/rules focused on: WHAT TYPES OF ROWS exist in the paper, "
        "WHERE they appear (sections, tables), and how to count distinct rows.\n"
        "→ Do NOT write per-column value rules here — those belong to each column's hints."
    ),
}

# A table field's extraction mode is a user choice made per field in the form
# builder (see FieldEditUpdate.extraction_strategy), not inferred from column
# count. New/unset fields default to "single_call" at every width.
_VALID_TABLE_STRATEGIES = {"single_call", "row_then_columns", "agentic"}


# Stray control characters and doubled words have shipped into a live prompt
# (one form of 118 carried a literal CR mid-sentence — "repeated-measures data
# \r ow a distinct timepoint" — plus a doubled "use use"). Rare, but it lands in
# every extraction that form runs, so normalize at the point of generation
# instead of discovering it in a rendered prompt weeks later.
_CTRL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f]")
_DOUBLED_WORD = re.compile(r"\b(\w{3,})(\s+)\1\b", re.I)


def _sanitize_generated_prose(value, field_label: str, key: str):
    """Strip control characters and collapse doubled words in LLM-authored prose.

    Applied to hints/rules/examples/options as they are produced. Logs loudly so
    a systematic generation problem is visible rather than silently repaired.
    Non-string members (e.g. example dicts) pass through untouched.
    """
    def _clean(s: str) -> str:
        original = s
        s = _CTRL_CHARS.sub(" ", s)
        s = _DOUBLED_WORD.sub(r"\1", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        if s != original:
            logger.warning(
                "Sanitized malformed generated %s on '%s': %r -> %r",
                key, field_label, original[:120], s[:120],
            )
        return s

    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        return [_clean(v) if isinstance(v, str) else v for v in value]
    return value


# Matches a hand-typed row key such as
#   "One row per (comparison × outcome_type × reporter × timepoint)"
# so it can be compared against the computed anchor set. Accepts ×, x or * as
# the separator and tolerates the tuple being unparenthesised.
_PROSE_ROW_KEY = re.compile(
    r"(?:one|1)\s+(?:row|entry|record)\s+per\s*\(?([A-Za-z0-9_\s×x*]+?)\)?[.\n]",
    re.I,
)


def _warn_on_row_key_mismatch(field_name: str, key_cols, texts: list) -> None:
    """Log when a hand-typed row key disagrees with the computed anchor set.

    The row key is composed from ``anchor_columns`` at prompt-render time
    (``runtime_builders._compose_field_desc``), which is authoritative. Many
    forms still carry an older hand-typed sentence; where the two disagree the
    prompt contains a contradiction, so surface it at codegen rather than
    silently editing the author's prose.
    """
    anchors = {str(a).strip().lower() for a in (key_cols or []) if a}
    if not anchors:
        return
    for text in texts:
        if not text:
            continue
        m = _PROSE_ROW_KEY.search(str(text))
        if not m:
            continue
        prose = {
            t.strip().lower()
            for t in re.split(r"[×x*]", m.group(1))
            if t.strip() and len(t.strip()) > 1
        }
        # Only meaningful when the prose actually names columns.
        if not prose or not (prose & anchors):
            continue
        if prose != anchors:
            logger.warning(
                "Row-key mismatch on '%s': prose says {%s} but the composite key is "
                "{%s}. The composed Row Identity block (from the composite key) wins "
                "at runtime — consider removing the stale sentence. Missing from "
                "prose: %s",
                field_name, ", ".join(sorted(prose)), ", ".join(sorted(anchors)),
                sorted(anchors - prose) or "none",
            )
        return


# ── Row-identity guard (shared by BOTH anchor detectors) ─────────────────────
# A measured result can never be part of row identity. When it is, a value
# disagreement becomes an identity disagreement: `arm1_n = 25` and `arm1_n = 26`
# are two DIFFERENT rows, so a plan/output row-coverage check reports a phantom
# missing row, and R1-vs-R2 adjudication reports a phantom conflict, on nothing
# but a one-digit read difference.
#
# Found live (Aug 11 2026): `Dichotomous Outcomes v2` had 8 of 11 columns as
# anchors including every numeric measurement (arm1_events, arm1_n, arm2_events,
# arm2_n), leaving slot filling with only metadata columns — the keyed split
# inverted, with the numeric extraction happening in the row-DISCOVERY call.
# `_auto_detect_key_columns` would have excluded all of them (`field_type == number`
# → value), but it is only the FALLBACK; the primary path for every >5-column
# table is `_llm_detect_key_columns`, whose sole guard was "≥1 anchor AND ≥1 value",
# which 8-of-11 passes.
#
# Deliberately a CONJUNCTION (numeric AND result-named), not `field_type ==
# "number"` alone. Stripping an anchor MERGES rows — the direction that silently
# destroys data — and legitimate identity dimensions can be numeric (dose_mg,
# visit_number, timepoint-in-hours, year). A numeric column whose name carries
# no statistical token is therefore KEPT and logged for review rather than
# removed.
_RESULT_TOKENS = {
    "mean", "median", "sd", "std", "stdev", "se", "sem", "iqr", "ci",
    "n", "num", "count", "events", "event", "pct", "percent", "proportion",
    "rate", "effect", "estimate", "pvalue", "pval", "p", "change", "delta",
    "diff", "var", "variance", "ratio", "rr", "hr", "md", "smd", "nnt",
}


def is_measured_result_column(sf: dict) -> bool:
    """True when a subform column is a measured RESULT, never a row identity.

    Requires both signals: a numeric field_type AND a statistical token
    anywhere in the column name. See the comment above for why the numeric
    test alone is not used.
    """
    if not isinstance(sf, dict):
        return False
    if (sf.get("field_type") or "").lower() != "number":
        return False
    fname = (sf.get("field_name", "") or "").lower()
    tokens = {t for t in re.split(r"[_\s]+", fname) if t}
    return bool(tokens & _RESULT_TOKENS)


def _auto_detect_key_columns(subfields: list) -> set:
    """Heuristic anchor (row-identity) detection — the fallback for
    ``_llm_detect_key_columns``.

    **A column is an anchor unless it looks like a measurement.** The rule used to
    be the other way round — a column had to match an identity keyword to become
    an anchor — and that under-detected badly: on the CD015432 continuous-outcomes
    table it returned only ``{outcome_type, timepoint}``, silently dropping
    ``comparison`` and ``reporter`` because neither word appears in the keyword
    list. Anchors are the row key, so missing one MERGES rows that should be
    distinct: all four comparator arms would have collapsed into a single row and
    three quarters of the table would have been discarded with no warning.

    The asymmetry is deliberate. Too FEW anchors silently destroys data; too many
    only splits rows more finely (more Stage-2 calls, no loss) and, in the
    degenerate case, is caught by the guard below and by the splicer's demotion
    to ``single_call``. When guessing, guess toward over-splitting.

    Per-arm/per-group MEASUREMENTS (``mean_arm1``, ``sd_arm2``, ``n_arm1``) must
    still be excluded explicitly: the ``"arm"`` keyword otherwise matched the
    ``*_arm<N>`` suffix on every numeric column, leaving ``attr_cols`` empty so
    slot filling extracted nothing and every cell came back NR.
    """
    ANCHOR_KEYWORDS = {
        "name", "label", "type", "subtype", "point", "timepoint", "period",
        "arm", "group", "unit", "category", "intervention", "outcome", "visit",
        "event", "treatment", "identifier", "measure",
        # Identity words the keyword-only rule missed on real forms.
        "comparison", "comparator", "reporter", "population", "subgroup",
        "cohort", "condition", "instrument", "scale", "drug", "regimen",
    }
    # Numeric measurement broken out per arm/group: mean_arm1, sd_arm2, n_arm1, change_group2.
    PER_ARM_VALUE = re.compile(r".*_(arm|group|grp|g)\s*\d+$")
    # A leading statistic token means the column holds a measurement, not a row identity.
    MEASURE_TOKENS = {
        "mean", "median", "sd", "std", "stdev", "se", "sem", "iqr", "range",
        "ci", "n", "num", "count", "pct", "percent", "proportion", "rate",
        "value", "score", "change", "delta", "diff", "pvalue", "pval", "p",
        "min", "max", "sum", "total", "avg", "baseline",
    }

    def _is_value(sf: dict) -> bool:
        fname = (sf.get("field_name", "") or "").lower()
        if PER_ARM_VALUE.match(fname):
            return True
        tokens = re.split(r"[_\s]+", fname)
        if tokens and tokens[0] in MEASURE_TOKENS:
            return True
        # A numeric column is a measured quantity, not a row identity. This is
        # what lets the rule classify `mean_arm1`-style columns as values even
        # when their names carry no statistic token.
        if (sf.get("field_type") or "").lower() == "number":
            return True
        return False

    names = [
        sf.get("field_name", "") for sf in subfields
        if isinstance(sf, dict) and sf.get("field_name")
    ]
    if not names:
        return set()

    value_names = {
        sf.get("field_name", "") for sf in subfields
        if isinstance(sf, dict) and sf.get("field_name") and _is_value(sf)
    }
    anchors = {n for n in names if n not in value_names}

    # A keyed pipeline needs at least one attribute, or slot filling has no outputs
    # at all. If every column read as an anchor, fall back to the narrower
    # keyword rule so the remainder become values.
    if not (set(names) - anchors):
        keyword_key_cols = {
            n for n in names
            if any(kw in n.lower() for kw in ANCHOR_KEYWORDS)
        }
        if keyword_key_cols and keyword_key_cols != set(names):
            anchors = keyword_key_cols
        else:
            anchors = {names[0]}

    return anchors or {names[0]}


class SignatureGenerator:
    """
    Generates DSPy Signature classes using LLM with validation.
    """

    def __init__(self, model_name: str = CODEGEN_SIGNATURE_MODEL):
        """
        Initialize signature generator.

        Args:
            model_name: LLM model identifier
        """
        self.model_name = model_name
        self.model = get_langchain_model(
            model_name, temperature=0.3, max_tokens=8000)

    def _generate_spec_from_enriched_sig(
        self, enriched_sig: Dict[str, Any], validation_feedback: str = "", semaphore=None
    ) -> SignatureSpec:
        """
        Generate SignatureSpec using structured output (LLM → validated JSON).

        Args:
            enriched_sig: Enriched signature with name, fields dict, depends_on
            validation_feedback: Optional feedback from previous validation failures

        Returns:
            SignatureSpec Pydantic model
        """
        # Load prompt template
        template_path = Path(__file__).parent / \
            "prompts" / "signature_prompt.md"
        base_prompt = template_path.read_text(encoding="utf-8")

        prompt = base_prompt.replace(
            "[[ENRICHED_SIGNATURE_JSON]]",
            json.dumps(enriched_sig, indent=2)
        )

        if validation_feedback:
            prompt += f"\n\nVALIDATION FEEDBACK (FIX THESE):\n{validation_feedback}\n"

        try:
            import contextlib
            structured_model = self.model.with_structured_output(
                SignatureSpec,
                method="json_schema"
            )
            from utils.langchain_cost_callback import make_callback_config
            with (semaphore if semaphore is not None else contextlib.nullcontext()):
                spec = structured_model.invoke(
                    prompt,
                    config=make_callback_config(
                        "codegen:signatures",
                        schema_name=enriched_sig.get("name"),
                    ),
                )
            if spec is None:
                raise ValueError("LLM returned None — structured output did not match SignatureSpec schema")
            return spec
        except Exception as e:
            raise ValueError(f"Structured output generation failed: {str(e)}")

    def _coverage_feedback(self, missing: list, required: list) -> str:
        """Build retry feedback listing output fields the LLM failed to emit.

        The signature-spec LLM occasionally returns fewer output_fields than the
        signature was given — especially for multi-field signatures, where it
        tends to keep only the first field. Nothing downstream re-checks
        coverage, so a dropped field silently never gets extracted and shows
        empty in the field editor. This feedback is injected into the retry.
        """
        return (
            "YOUR PREVIOUS OUTPUT WAS INCOMPLETE — you dropped output fields.\n"
            "Rule 1 requires EXACTLY ONE output_field per entry in the fields dict, "
            "with field_names matching the keys exactly.\n"
            f"Required field_names ({len(required)}): {required}\n"
            f"MISSING output_fields you MUST add (do not rename or drop any): {missing}\n"
            f"Return ALL {len(required)} output_fields this time."
        )

    def _synthesize_output_field(self, name: str, enriched_sig: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministically build an output_field for a field the LLM dropped.

        Last-resort fallback, used only after the LLM has failed to emit this
        field across every retry attempt. Sourced entirely from the user's
        enriched field metadata so the field survives into schema_def (and is
        therefore extractable) even when the LLM never complies. hints/rules/
        examples/options are re-merged from enriched metadata by spec_to_sig_def,
        but `description` has no user fallback there, so it is set here.
        """
        meta = (enriched_sig.get("fields", {}) or {}).get(name, {}) or {}
        desc = (meta.get("field_description") or "").strip()
        if not desc:
            desc = f"Extract {name.replace('_', ' ')} from the document."
        return {
            "field_name": name,
            # All output fields use the source-grounded envelope regardless of
            # the user's field_type — matches every LLM-emitted output field.
            # Subform columns (if this is a table field) are rebuilt from the
            # enriched metadata by _enrich_subform_columns_independently below.
            "field_type": "Dict[str, Any]",
            "description": desc,
            "hints": list(meta.get("hints") or []),
            "rules": list(meta.get("rules") or []),
            "examples": list(meta.get("examples") or []),
            "options": list(meta.get("options") or []),
            "subform_fields": [],
            "extraction_strategy": None,
            "anchor_columns": None,
        }

    def _drop_extraneous_output_fields(
        self, spec_dict: Dict[str, Any], enriched_sig: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Remove output_fields the LLM invented that aren't real signature fields.

        The spec LLM occasionally hallucinates extra output_fields — e.g. a
        `<field>_placeholder_remove` field whose description is literally
        "placeholder". These are not part of the signature; they pollute
        schema_def, the field editor (/field-prompts), and runtime extraction.
        Keep only genuine fields: the signature's own fields plus their subform
        column names (promoted-orphan columns are re-attached downstream by
        spec_to_sig_def, so they must survive this filter).
        """
        allowed = set(enriched_sig.get("fields", {}).keys())

        def _collect_cols(containers):
            for fdata in containers:
                for sf in (fdata.get("subform_fields") or []):
                    cn = sf.get("field_name") if isinstance(sf, dict) else getattr(sf, "field_name", None)
                    if cn:
                        allowed.add(cn)

        _collect_cols(enriched_sig.get("fields", {}).values())
        _collect_cols(spec_dict.get("output_fields", []))

        original = spec_dict.get("output_fields", [])
        dropped = [of.get("field_name", "") for of in original if of.get("field_name", "") not in allowed]
        if dropped:
            spec_dict["output_fields"] = [of for of in original if of.get("field_name", "") in allowed]
            logger.warning(
                "Signature '%s' emitted %d hallucinated output_field(s) %s — dropped",
                enriched_sig.get("name"), len(dropped), dropped,
            )
        return spec_dict

    def generate_signature(
        self,
        enriched_sig: Dict[str, Any],
        max_attempts: int = 3,
        semaphore=None,
    ) -> Dict[str, Any]:
        """
        Generate a single DSPy signature with validation and retry.

        Args:
            enriched_sig: Enriched signature with name, fields dict, depends_on
            max_attempts: Maximum validation retry attempts
            semaphore: Optional threading.Semaphore shared across all parallel
                       signature + column calls to cap total in-flight LLM calls.

        Returns:
            dict with 'code', 'spec', 'is_valid', 'attempts', 'errors', 'warnings'
            'spec' is the SignatureSpec as a plain dict (for schema_def building).
        """
        validation_feedback = ""
        for attempt in range(max_attempts):
            try:
                spec = self._generate_spec_from_enriched_sig(
                    enriched_sig=enriched_sig,
                    validation_feedback=validation_feedback,
                    semaphore=semaphore,
                )
                spec_dict = spec.model_dump()

                # ── Drop hallucinated output fields ──────────────────────────
                # The LLM sometimes invents extra output_fields not in the
                # signature (e.g. 'trial_design_placeholder_remove'). Strip them
                # before the coverage check so they never reach schema_def.
                spec_dict = self._drop_extraneous_output_fields(spec_dict, enriched_sig)

                # ── Field-coverage gate ──────────────────────────────────────
                # The LLM sometimes emits fewer output_fields than the signature
                # was given (notably for multi-field signatures, where it tends
                # to keep only the first). Nothing downstream re-checks this, so
                # a dropped field silently never gets extracted AND shows empty
                # in the field editor. Retry with explicit feedback; synthesize
                # from spec metadata as a last resort so a field is NEVER lost.
                required = list(enriched_sig.get("fields", {}).keys())
                produced = {
                    of.get("field_name", "")
                    for of in spec_dict.get("output_fields", [])
                }
                missing = [n for n in required if n not in produced]

                if missing and attempt < max_attempts - 1:
                    validation_feedback = self._coverage_feedback(missing, required)
                    logger.warning(
                        "Signature '%s' dropped output_fields %s "
                        "(attempt %d/%d) — retrying with coverage feedback",
                        enriched_sig.get("name"), missing, attempt + 1, max_attempts,
                    )
                    continue

                warnings = []
                if missing:
                    spec_dict.setdefault("output_fields", []).extend(
                        self._synthesize_output_field(n, enriched_sig) for n in missing
                    )
                    warnings.append(
                        f"LLM dropped {len(missing)} field(s) after {max_attempts} "
                        f"attempts; synthesized from spec metadata: {missing}"
                    )
                    logger.warning(
                        "Signature '%s' still missing %s after %d attempts — "
                        "synthesized deterministically from enriched metadata",
                        enriched_sig.get("name"), missing, max_attempts,
                    )

                # Per-column independent enrichment for subform fields.
                # All columns within a table run in parallel; semaphore shared
                # so column calls + sibling signature calls stay within rate limit.
                spec_dict = self._enrich_subform_columns_independently(
                    spec_dict, enriched_sig, semaphore=semaphore
                )

                return {
                    "code": "",
                    "spec": spec_dict,
                    "is_valid": True,
                    "attempts": attempt + 1,
                    "errors": [],
                    "warnings": warnings,
                }
            except Exception as e:
                error_msg = f"Generation failed: {str(e)}"
                print(f"  [Attempt {attempt + 1}/{max_attempts}] {error_msg}")
                if attempt == max_attempts - 1:
                    import traceback
                    traceback.print_exc()
                    return {
                        "code": "",
                        "spec": None,
                        "is_valid": False,
                        "attempts": attempt + 1,
                        "errors": [error_msg],
                    }
        return {
            "code": "",
            "spec": None,
            "is_valid": False,
            "attempts": max_attempts,
            "errors": ["Max attempts reached"],
        }

    def _llm_detect_key_columns(
        self, field_name: str, field_description: str, subfields: list, semaphore=None
    ) -> set:
        """Ask the LLM which columns are row-identity ANCHORS vs measured VALUES.

        Judges each column by its meaning (description first, then name), so columns like
        ``mean_arm1``/``sd_arm2`` are correctly values rather than anchors. Falls back to the
        keyword heuristic (``_auto_detect_key_columns``) if the LLM is unavailable or returns an
        invalid/degenerate split (no anchors, no values, or every column an anchor).
        """
        import contextlib
        from .models import AnchorClassification

        col_names = [sf.get("field_name", "") for sf in subfields if sf.get("field_name")]
        columns_payload = {
            "table_field": field_name,
            "table_description": field_description or "",
            "columns": [
                {
                    "field_name": sf.get("field_name", ""),
                    "field_description": (sf.get("field_description") or sf.get("description") or ""),
                }
                for sf in subfields if sf.get("field_name")
            ],
        }
        try:
            template_path = Path(__file__).parent / "prompts" / "classify_anchors.md"
            prompt = template_path.read_text(encoding="utf-8").replace(
                "[[COLUMNS_JSON]]", json.dumps(columns_payload, indent=2)
            )
            structured_model = self.model.with_structured_output(
                AnchorClassification, method="json_schema"
            )
            from utils.langchain_cost_callback import make_callback_config
            with (semaphore if semaphore is not None else contextlib.nullcontext()):
                result = structured_model.invoke(
                    prompt,
                    config=make_callback_config("codegen:anchors", schema_name=field_name),
                )
            if result is None:
                raise ValueError("LLM returned None for key-column classification")

            valid = set(col_names)
            anchors = {c for c in (result.anchor_columns or []) if c in valid}

            # Measurement guard — the LLM classifier has put measured numbers in
            # the row key on live forms (see is_measured_result_column). Strip
            # them here rather than trusting the classifier's judgement, so the
            # LLM path is at least as safe as the keyword fallback.
            by_name = {
                sf.get("field_name"): sf
                for sf in subfields if isinstance(sf, dict) and sf.get("field_name")
            }
            _stripped = sorted(
                c for c in anchors if is_measured_result_column(by_name.get(c, {}))
            )
            if _stripped:
                anchors -= set(_stripped)
                logger.warning(
                    "Key guard for '%s': removed measured result column(s) %s "
                    "from the composite key (the classifier proposed them as key columns)",
                    field_name, _stripped,
                )
            # Numeric anchors that survive are legitimate-but-unusual identity
            # dimensions (dose, visit number, year). Surfaced, not removed —
            # stripping an anchor merges rows, which destroys data silently.
            _numeric_kept = sorted(
                c for c in anchors
                if (by_name.get(c, {}).get("field_type") or "").lower() == "number"
            )
            if _numeric_kept:
                logger.info(
                    "Key guard for '%s': numeric column(s) %s KEPT in the composite "
                    "key (no statistical token in the name) — review if wrong",
                    field_name, _numeric_kept,
                )

            # Guard against a degenerate split — need ≥1 anchor AND ≥1 value.
            if not anchors or len(anchors) >= len(valid):
                raise ValueError(
                    f"Degenerate split for '{field_name}': key={sorted(anchors)} of {sorted(valid)}"
                )
            logger.info(
                "Key classification for '%s': key=%s | attributes=%s | %s",
                field_name, sorted(anchors), sorted(valid - anchors), result.reasoning,
            )
            return anchors
        except Exception as exc:
            logger.warning(
                "Key classification failed for '%s' (%s) — falling back to the keyword heuristic",
                field_name, exc,
            )
            return _auto_detect_key_columns(subfields)

    def _enrich_subform_columns_independently(
        self,
        spec_dict: Dict[str, Any],
        enriched_sig: Dict[str, Any],
        semaphore=None,
    ) -> Dict[str, Any]:
        """Re-enrich each subform column via a dedicated single-field LLM call.

        All columns within a table are enriched IN PARALLEL via ThreadPoolExecutor
        so 8 columns take the same wall-clock time as 1.

        Each column call receives:
        - class_name + docstring: table-level context
        - existing_fields: ALL sibling column names/descriptions so the LLM can
          write disambiguation rules (e.g. "dose goes here, regimen goes in
          frequency_duration")
        - new_field: the single column being enriched

        Falls back to whatever the main call produced if a per-column call fails.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        enriched_fields = enriched_sig.get("fields", {})
        sig_class_name = spec_dict.get("class_name", "")
        sig_docstring = spec_dict.get("class_docstring", "")

        for out_field in spec_dict.get("output_fields", []):
            fname = out_field.get("field_name", "")
            user_field = enriched_fields.get(fname, {})
            user_subfields = list(user_field.get("subform_fields") or [])

            if not user_subfields:
                continue

            # Strategy is a user choice (form builder, per table field) carried
            # on the field from a previous save; it is NOT inferred from column
            # count. Unset/invalid values default to single_call.
            #
            # Anchors are detected independently of strategy: row_then_columns
            # needs them to split discovery from slot filling, and agentic needs them too
            # — for row identity, missing-row detection, and quote grounding —
            # even though it runs as a single call. Falling back to the keyword
            # heuristic for every wide table would quietly degrade agentic row
            # planning, so the LLM classifier still runs on the same >5-column
            # threshold regardless of which strategy ends up selected.
            user_strategy = user_field.get("extraction_strategy")
            if user_strategy not in _VALID_TABLE_STRATEGIES:
                user_strategy = "single_call"
            if len(user_subfields) > 5:
                field_desc = user_field.get("field_description") or out_field.get("description") or ""
                user_key_cols = self._llm_detect_key_columns(
                    fname, field_desc, user_subfields, semaphore=semaphore
                )
            else:
                user_key_cols = _auto_detect_key_columns(user_subfields)

            # Sibling context list shared across all column calls
            sibling_context = [
                {
                    "name": col.get("field_name", ""),
                    "description": (
                        col.get("field_description")
                        or col.get("description")
                        or ""
                    ),
                }
                for col in user_subfields
            ]

            def _enrich_one(col: Dict[str, Any]) -> tuple:
                """Returns (original_col, enriched_col_or_None)."""
                cname = col.get("field_name", "")
                role = "anchor" if cname in user_key_cols else "value"
                target_sig = {
                    "class_name": sig_class_name,
                    "docstring": sig_docstring,
                    "existing_fields": [
                        c for c in sibling_context if c["name"] != cname
                    ],
                }
                new_field: Dict[str, Any] = {
                    "field_name": cname,
                    "field_type": col.get("field_type", "text"),
                    "description": col.get("field_description") or col.get("description") or "",
                    "examples": col.get("examples") or [],
                    "options": col.get("options") or [],
                }
                if col.get("hints"):
                    new_field["hints"] = col["hints"]
                if col.get("rules"):
                    new_field["rules"] = col["rules"]

                result = self.enrich_new_field(target_sig, new_field, semaphore=semaphore, role=role)

                if result["is_valid"] and result["spec"]:
                    col_spec = result["spec"]
                    enriched_col: Dict[str, Any] = {
                        "field_name": cname,
                        "field_type": col.get("field_type", "text"),
                        "field_description": (
                            col_spec.get("description")
                            or col.get("field_description")
                            or ""
                        ),
                        "extraction_role": role,
                    }
                    for key in ("hints", "rules", "examples", "options"):
                        val = col_spec.get(key)
                        if val:
                            enriched_col[key] = _sanitize_generated_prose(
                                val, f"{fname}.{cname}", key
                            )
                    logger.info("  Per-column enrichment OK: %s.%s [%s]", fname, cname, role)
                    return col, enriched_col
                else:
                    logger.warning(
                        "  Per-column enrichment failed for '%s.%s' — using main-call result",
                        fname, cname,
                    )
                    return col, None

            # Run all columns in parallel — wall-clock time = one call, not N calls
            results_by_col: Dict[str, Dict] = {}
            with ThreadPoolExecutor(max_workers=len(user_subfields)) as pool:
                futures = {pool.submit(_enrich_one, col): col for col in user_subfields}
                for future in as_completed(futures):
                    original_col, enriched_col = future.result()
                    cname = original_col.get("field_name", "")
                    results_by_col[cname] = enriched_col if enriched_col is not None else original_col

            # Preserve original column ORDER (futures complete out of order)
            out_field["subform_fields"] = [
                results_by_col[col.get("field_name", "")] for col in user_subfields
            ]
            # Store extraction strategy metadata on the parent field for spec_to_sig_def
            out_field["extraction_strategy"] = user_strategy
            set_field_key_columns(out_field, sorted(user_key_cols))

            _warn_on_row_key_mismatch(
                fname,
                user_key_cols,
                [user_field.get("field_description") or "",
                 out_field.get("description") or ""]
                + [str(r) for r in (out_field.get("rules") or [])],
            )

        return spec_dict

    def enrich_new_field(
        self,
        target_sig: Dict[str, Any],
        new_field: Dict[str, Any],
        max_attempts: int = 3,
        semaphore=None,
        role: str = "value",
    ) -> Dict[str, Any]:
        """Enrich a single new field being added to an existing signature.

        Args:
            target_sig: {class_name, docstring, existing_fields: [{name, description}]}
            new_field:  {field_name, field_type, description, examples, options}
            max_attempts: validation retry limit
            semaphore: Optional threading.Semaphore to cap concurrent LLM calls.
            role: 'anchor' | 'value' | 'parent_discovery' — steers hint/rule generation.

        Returns:
            dict with 'spec' (AddFieldSpec.model_dump()), 'is_valid', 'errors'
        """
        import contextlib
        template_path = Path(__file__).parent / "prompts" / "add_field_prompt.md"
        base_prompt = template_path.read_text(encoding="utf-8")
        role_context = _ROLE_CONTEXT.get(role, "")
        prompt = (
            base_prompt
            .replace("[[TARGET_SIGNATURE_JSON]]", json.dumps(target_sig, indent=2))
            .replace("[[NEW_FIELD_JSON]]", json.dumps(new_field, indent=2))
            .replace("[[EXTRACTION_ROLE_CONTEXT]]", role_context)
        )

        expected_name = new_field.get("field_name", "")

        for attempt in range(max_attempts):
            try:
                structured_model = self.model.with_structured_output(
                    AddFieldSpec, method="json_schema"
                )
                from utils.langchain_cost_callback import make_callback_config
                with (semaphore if semaphore is not None else contextlib.nullcontext()):
                    spec = structured_model.invoke(
                        prompt,
                        config=make_callback_config(
                            "codegen:signatures:enrich",
                            schema_name=expected_name,
                        ),
                    )
                if spec is None:
                    raise ValueError("LLM returned None")
                if spec.field_name != expected_name:
                    raise ValueError(
                        f"LLM renamed field '{expected_name}' → '{spec.field_name}'"
                    )
                return {"spec": spec.model_dump(), "is_valid": True, "errors": []}
            except Exception as e:
                error_msg = f"enrich_new_field attempt {attempt + 1}: {e}"
                print(f"  [{attempt + 1}/{max_attempts}] {error_msg}")
                if attempt == max_attempts - 1:
                    return {"spec": None, "is_valid": False, "errors": [error_msg]}

        return {"spec": None, "is_valid": False, "errors": ["Max attempts reached"]}

    def spec_to_sig_def(
        self,
        spec_dict: Dict[str, Any],
        enriched_sig: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Convert a SignatureSpec dict + optional enriched_sig into schema_def format.

        Phase B: the LLM emits structured hints/rules/examples/options alongside a
        clean description. These are stored directly so the UI reads them without
        back-parsing. _compose_field_desc recomposes the full DSPy prompt from
        structured fields at runtime. Legacy Phase A forms (baked description, empty
        arrays) continue to work via _compose_field_desc's early-return path.
        """
        enriched_fields: Dict[str, Any] = {}
        if enriched_sig:
            enriched_fields = enriched_sig.get("fields", {})

        input_fields = [
            {
                "name": f["field_name"],
                "type": f["field_type"],
                "desc": f["description"],
            }
            for f in spec_dict.get("input_fields", [])
        ]

        # ── Move 1: Detect orphan subform columns promoted to top-level outputs ──
        # The LLM sometimes emits a subform column name as a sibling top-level
        # output_field (e.g. "antibiotic_dose" appears both inside
        # interventions.subform_fields[] AND as output_fields[1]).  The existing
        # seen_output_names dedup only catches same-name twins at the same level.
        # Here we detect column-name-at-wrong-level and build an orphan_map so
        # their hints/rules/examples can be re-attached to the correct column.

        # Collect every column name that legitimately lives inside a subform.
        all_subform_col_names: set = set()
        for f in spec_dict.get("output_fields", []):
            for sf in f.get("subform_fields") or []:
                cname = sf.get("field_name", "") if isinstance(sf, dict) else getattr(sf, "field_name", "")
                if cname:
                    all_subform_col_names.add(cname)
        # Also include user-owned structural columns from enriched_sig.
        for fdata in enriched_fields.values():
            for sf in fdata.get("subform_fields") or []:
                cname = sf.get("field_name", "") if isinstance(sf, dict) else ""
                if cname:
                    all_subform_col_names.add(cname)

        # Build orphan_map: column_name → its spec dict as emitted at the wrong level.
        orphan_map: Dict[str, Any] = {}
        _seen_pre: set = set()
        for f in spec_dict.get("output_fields", []):
            fn = f["field_name"]
            if fn in _seen_pre:
                continue
            _seen_pre.add(fn)
            if fn in all_subform_col_names:
                orphan_map[fn] = f

        if orphan_map:
            logger.warning(
                "LLM promoted subform column(s) %s to sibling top-level outputs — "
                "re-attaching hints/rules/examples to parent columns.",
                sorted(orphan_map.keys()),
            )

        # ── Build output_fields, skipping orphans ─────────────────────────────
        try:
            from dspy_components.runtime_builders import _parse_embedded_sections
        except ImportError:
            _parse_embedded_sections = None  # type: ignore

        output_fields = []
        seen_output_names: set = set()
        for f in spec_dict.get("output_fields", []):
            fname = f["field_name"]
            if fname in seen_output_names:
                continue  # LLM duplicated this field — keep first occurrence
            seen_output_names.add(fname)
            if fname in orphan_map:
                continue  # Orphan: drop here, data re-attached into its parent column below

            form_field = enriched_fields.get(fname, {})

            # User-authored values always take priority over LLM-generated ones.
            hints = form_field.get("hints") or f.get("hints") or []
            rules = form_field.get("rules") or f.get("rules") or []
            options = form_field.get("options") or f.get("options") or []

            # Merge user examples + LLM examples so both survive.
            # The simple "example" string from create form is folded in if no array exists.
            user_examples = list(form_field.get("examples") or [])
            single = form_field.get("example")
            if single and not user_examples:
                user_examples = [{"value": single, "source_text": ""}]
            llm_examples = list(f.get("examples") or [])
            examples = user_examples + llm_examples

            # Every output field uses the source-grounded {value, source_text}
            # envelope. The spec LLM is told to emit Dict[str, Any] for all
            # fields; coerce stragglers so a stray "str" can't silently drop
            # the grounding block or make a legitimate "NR" fail a typed
            # annotation's validation.
            ftype = f["field_type"]
            if ftype != "Dict[str, Any]":
                logger.warning(
                    "Output field '%s' emitted type %r — coerced to Dict[str, Any] "
                    "(envelope contract).", fname, ftype,
                )
                ftype = "Dict[str, Any]"

            out: Dict[str, Any] = {
                "name": fname,
                "type": ftype,
                "description": f["description"],
                "source_grounded": True,
            }
            if hints:
                out["hints"] = hints
            if rules:
                out["rules"] = rules
            if examples:
                out["examples"] = examples
            if options:
                out["options"] = options
            # Multi-select flag is user-owned form metadata — carry it into
            # schema_def so the runtime prompt and validators know the value
            # is a list of options, not exactly one.
            if form_field.get("multiple"):
                out["multiple"] = True

            # ── Subform columns: user structure is authoritative; LLM enriches prose ──
            user_subfields = list(form_field.get("subform_fields") or [])
            if user_subfields:
                llm_sf_list = f.get("subform_fields") or []
                # SubfieldEnrichment objects are dicts after model_dump(); handle both
                llm_sf_by_name: Dict[str, Any] = {}
                for sf in llm_sf_list:
                    if isinstance(sf, dict):
                        llm_sf_by_name[sf.get("field_name", "")] = sf
                    else:
                        # Pydantic model instance
                        llm_sf_by_name[sf.field_name] = sf if isinstance(sf, dict) else sf.model_dump()

                merged_cols = []
                user_col_names: set = set()
                for sf in user_subfields:
                    cname = sf.get("field_name", "")
                    user_col_names.add(cname)
                    llm_sf = llm_sf_by_name.get(cname) or {}
                    if not isinstance(llm_sf, dict):
                        llm_sf = llm_sf.model_dump() if hasattr(llm_sf, "model_dump") else {}

                    # ── Move 1: re-attach orphan hints/rules/examples ──────────
                    # If the LLM emitted this column's hints/rules at the top level
                    # (as an orphan output_field), pull them in here.
                    orphan = orphan_map.get(cname) or {}
                    orphan_hints = list(orphan.get("hints") or [])
                    orphan_rules = list(orphan.get("rules") or [])
                    orphan_examples = list(orphan.get("examples") or [])
                    orphan_options = list(orphan.get("options") or [])
                    orphan_desc = (orphan.get("description") or "").strip()

                    # description: user wins when non-empty, else LLM subform wins,
                    # else orphan top-level description as last resort.
                    user_desc = (sf.get("field_description") or sf.get("description") or "").strip()
                    llm_desc = (llm_sf.get("field_description") or "").strip()
                    final_desc = user_desc or llm_desc or orphan_desc

                    # hints / rules: user wins → LLM subform → re-attached orphan
                    user_hints = list(sf.get("hints") or [])
                    user_rules = list(sf.get("rules") or [])
                    final_hints = (
                        user_hints
                        or list(llm_sf.get("hints") or [])
                        or orphan_hints
                    )
                    final_rules = (
                        user_rules
                        or list(llm_sf.get("rules") or [])
                        or orphan_rules
                    )

                    # examples: merge user + LLM subform + orphan
                    user_col_examples = list(sf.get("examples") or [])
                    col_single = sf.get("example")
                    if col_single and not user_col_examples:
                        user_col_examples = [{"value": col_single, "source_text": ""}]
                    final_col_examples = (
                        user_col_examples
                        + list(llm_sf.get("examples") or [])
                        + orphan_examples
                    )

                    # options: prefer orphan (top-level often has these) when subform empty
                    final_options = list(llm_sf.get("options") or []) or orphan_options

                    # ── Move 4: validate-and-repair prose-baked description ────
                    # If the final description still has embedded Hints/Rules/Examples
                    # prose (because the LLM copied user's inline description verbatim),
                    # split them out now so schema_def is canonical at rest.
                    if _parse_embedded_sections and not (final_hints or final_rules or final_col_examples):
                        parsed = _parse_embedded_sections(final_desc)
                        if parsed["hints"] or parsed["rules"] or parsed["examples"]:
                            final_desc = parsed["description"]
                            if not final_hints:
                                final_hints = parsed["hints"]
                            if not final_rules:
                                final_rules = parsed["rules"]
                            if not final_col_examples:
                                final_col_examples = parsed["examples"]

                    col_out: Dict[str, Any] = {
                        "field_name": cname,
                        "field_type": sf.get("field_type", "string"),  # user-only
                        "field_description": final_desc,
                    }
                    if final_hints:
                        col_out["hints"] = final_hints
                    if final_rules:
                        col_out["rules"] = final_rules
                    if final_col_examples:
                        col_out["examples"] = final_col_examples
                    if final_options:
                        col_out["options"] = final_options
                    # Carry through extraction role set by per-column enrichment
                    role_val = llm_sf.get("extraction_role") if isinstance(llm_sf, dict) else None
                    if role_val:
                        col_out["extraction_role"] = role_val
                    merged_cols.append(col_out)

                out["subform_fields"] = merged_cols

                # Carry through keyed-extraction metadata
                if f.get("extraction_strategy"):
                    out["extraction_strategy"] = f["extraction_strategy"]
                _key = field_key_columns(f)
                if _key:
                    set_field_key_columns(out, _key)

                # Structural-lock: warn if LLM tried to invent / rename columns
                # (orphan names are excluded — they're legitimate columns re-attached above)
                extra_cols = set(llm_sf_by_name.keys()) - user_col_names - set(orphan_map.keys())
                if extra_cols:
                    logger.warning(
                        "LLM returned unknown subfields for '%s': %s — ignored.",
                        fname, sorted(extra_cols),
                    )

                # Prose-leakage check: warn if LLM baked column names into parent description
                parent_desc_lower = out.get("description", "").lower()
                leaked = [c for c in user_col_names if c.lower() in parent_desc_lower]
                if leaked:
                    logger.warning(
                        "LLM regressed: parent description for '%s' mentions column name(s) %s — "
                        "strip recommended.",
                        fname, leaked,
                    )

            output_fields.append(out)

        return {
            "class_name": spec_dict["class_name"],
            "docstring": spec_dict["class_docstring"],
            "input_fields": input_fields,
            "output_fields": output_fields,
        }

__all__ = ["SignatureGenerator"]
