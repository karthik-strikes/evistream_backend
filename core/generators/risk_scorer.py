"""
Risk Scorer for Decomposition Review (Phase 2 B7)

Lightweight heuristic-based risk signals attached to each signature, plus an
overall review tier:
  - high:   any high-severity signal present
  - normal: any warn-severity signal present (no highs)
  - auto:   only info-severity signals (or none)

No embeddings, no LLM calls — pure Python checks on the structured decomposition.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SEVERITY_RANK = {"info": 0, "warn": 1, "high": 2}
ENUM_TYPES = {"enum", "select", "multiselect", "multi_select", "categorical"}


def _enum_without_options(field_def: Dict[str, Any]) -> bool:
    ftype = (field_def.get("field_type") or "").lower()
    if ftype not in ENUM_TYPES:
        return False
    return not (field_def.get("options") or [])


def _missing_example(field_def: Dict[str, Any]) -> bool:
    return not bool(field_def.get("example"))


def _score_signature(
    sig: Dict[str, Any],
    all_sigs: List[Dict[str, Any]],
    fields_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    sig_name = sig.get("name", "")
    sig_fields = sig.get("fields") or {}
    depends_on = sig.get("depends_on") or []
    my_field_names = set(sig_fields.keys())

    if len(depends_on) >= 3:
        signals.append({
            "id": f"heavy_dependency:{sig_name}",
            "type": "heavy_dependency",
            "severity": "warn",
            "signature": sig_name,
            "message": f"{sig_name} depends on {len(depends_on)} upstream fields — consider splitting",
        })

    has_dependents = False
    for s in all_sigs:
        if s.get("name") == sig_name:
            continue
        if set(s.get("depends_on") or []) & my_field_names:
            has_dependents = True
            break
    if not depends_on and not has_dependents and len(sig_fields) <= 1:
        signals.append({
            "id": f"isolated_signature:{sig_name}",
            "type": "isolated_signature",
            "severity": "info",
            "signature": sig_name,
            "message": f"{sig_name} has no dependencies and isn't used downstream",
        })

    for fname, fdef in sig_fields.items():
        original = fields_index.get(fname, {})
        merged = {**(original or {}), **(fdef if isinstance(fdef, dict) else {})}
        if _enum_without_options(merged):
            signals.append({
                "id": f"enum_without_options:{fname}",
                "type": "enum_without_options",
                "severity": "high",
                "signature": sig_name,
                "field": fname,
                "message": f"Field '{fname}' is enum-like but has no options listed",
            })
        if _missing_example(merged):
            signals.append({
                "id": f"missing_example:{fname}",
                "type": "missing_example",
                "severity": "info",
                "signature": sig_name,
                "field": fname,
                "message": f"Field '{fname}' has no example value",
            })

    return signals


def _detect_subform_split(
    signatures: List[Dict[str, Any]],
    fields: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    parent_to_children: Dict[str, List[str]] = {}
    for f in fields:
        parent = f.get("parent_field") or f.get("subform_parent")
        if parent:
            parent_to_children.setdefault(parent, []).append(f.get("field_name"))

    field_to_sig: Dict[str, str] = {}
    for sig in signatures:
        for fname in (sig.get("fields") or {}).keys():
            field_to_sig[fname] = sig.get("name", "")

    out: List[Dict[str, Any]] = []
    for parent, children in parent_to_children.items():
        sigs_for = {field_to_sig.get(c) for c in children if field_to_sig.get(c)}
        if len(sigs_for) > 1:
            out.append({
                "id": f"subform_split:{parent}",
                "type": "subform_split",
                "severity": "high",
                "field": parent,
                "message": f"Subform '{parent}' children split across {len(sigs_for)} signatures: {sorted(s for s in sigs_for if s)}",
            })
    return out


def _compute_tier(all_signals: List[Dict[str, Any]]) -> str:
    if not all_signals:
        return "auto"
    max_rank = max(SEVERITY_RANK.get(s.get("severity", "info"), 0) for s in all_signals)
    if max_rank >= SEVERITY_RANK["high"]:
        return "high"
    if max_rank >= SEVERITY_RANK["warn"]:
        return "normal"
    return "auto"


def score_decomposition(
    decomposition: Dict[str, Any],
    form_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Score a decomposition; return per-signature signals + overall tier."""
    sigs = decomposition.get("signatures") or []
    fields = form_data.get("fields") or []
    fields_index = {
        f.get("field_name"): f
        for f in fields
        if f.get("field_name")
    }

    by_signature: Dict[str, List[Dict[str, Any]]] = {}
    aggregated: List[Dict[str, Any]] = []

    for sig in sigs:
        sig_signals = _score_signature(sig, sigs, fields_index)
        if sig_signals:
            by_signature[sig.get("name", "")] = sig_signals
            aggregated.extend(sig_signals)

    subform_signals = _detect_subform_split(sigs, fields)
    if subform_signals:
        aggregated.extend(subform_signals)
        by_signature.setdefault("__global__", []).extend(subform_signals)

    tier = _compute_tier(aggregated)

    return {
        "by_signature": by_signature,
        "aggregated": aggregated,
        "tier": tier,
        "counts": {
            "high": sum(1 for s in aggregated if s.get("severity") == "high"),
            "warn": sum(1 for s in aggregated if s.get("severity") == "warn"),
            "info": sum(1 for s in aggregated if s.get("severity") == "info"),
        },
    }


__all__ = ["score_decomposition"]
