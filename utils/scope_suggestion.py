"""Validate a model-proposed review scope before a human ever sees it.

Shaped like ``app/api/v1/synthesis.py:_validate``: the model is not trusted to
have respected the five families, the length limits, or the document it was
given. Everything it returned is checked here rather than in the browser.

One deliberate difference from synthesis. There, a slot naming a column that
does not exist is DROPPED, because a wrong column name is unusable. Here a chip
is just text a reviewer is about to read, and PDF table extraction mangles
whitespace often enough that dropping every chip whose quote fails to match
would lose correct ones. So an unquotable chip is KEPT, demoted to low
confidence and flagged ``unverified`` — the dialog leaves it unticked, which
costs a click instead of costing a criterion.

The caps mirror ``app/models/schemas.py:ReviewScopeStructured`` exactly. Chips
that would be rejected on save are dropped here instead, so the builder can
never be handed something the save endpoint will refuse. That agreement is
pinned in ``tests/test_review_scope_payload.py``.

``pairs_off`` is never accepted from a model: muting a comparison is a judgment
about what this review is for, and the suggester is not asked for one.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from utils.scope_document import normalize_for_match

logger = logging.getLogger(__name__)

FAMILIES = ("population", "intervention", "comparator", "outcome", "timepoint")

# Mirrors ReviewScopeStructured._clean / max_length.
MAX_ENTRY_CHARS = 300
MAX_PER_FAMILY = 40

# Below this length a "quote" matches almost any document — "NA", "6 months",
# "adults" — so it proves nothing and the chip is treated as unverified.
MIN_EVIDENCE_CHARS = 12

_WS = re.compile(r"\s+")
# Bullet or numbering the model copied along with the criterion.
_LEADING_MARKER = re.compile(r"^(?:[-*•·–—]|\(?\d+[.)]|[a-z][.)])\s+", re.I)


def _clean_value(value: str) -> str:
    """Chip text as the builder would want it typed.

    Collapses whitespace (PDF line breaks land mid-phrase), strips a copied
    bullet or "1." prefix, and drops surrounding quotes and one trailing period
    so the composed prose does not read "...at 3 months..".
    """
    v = _WS.sub(" ", value or "").strip()
    v = _LEADING_MARKER.sub("", v).strip()
    if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'“‘":
        v = v[1:-1].strip()
    v = v.strip("“”‘’\"'").strip()
    if v.endswith(".") and not v.endswith(".."):
        v = v[:-1].rstrip()
    return v


def _clean_list(items: Optional[List[str]], limit: int = 30) -> List[str]:
    out: List[str] = []
    for item in items or []:
        t = _WS.sub(" ", str(item or "")).strip()
        if t and t not in out:
            out.append(t[:400])
        if len(out) >= limit:
            break
    return out


def quote_is_in_document(evidence: str, normalized_document: str) -> bool:
    """Is this quote actually in the document?

    Both sides are normalised hard (see ``normalize_for_match``). A quote that
    still fails whole may legitimately span a page break or a table cell whose
    columns were interleaved by extraction, so a long contiguous head of it
    counts too: enough to show the model was reading rather than inventing,
    while still rejecting a paraphrase built out of thin air.
    """
    needle = normalize_for_match(evidence)
    if len(needle) < MIN_EVIDENCE_CHARS:
        return False
    if needle in normalized_document:
        return True
    for size in (60, 40, 24):
        if len(needle) >= size and needle[:size] in normalized_document:
            return True
    return False


def validate_suggestion(suggestion: Any, document_text: str) -> Dict[str, Any]:
    """Turn a raw ``ScopeSuggestion`` into what the frontend may see."""
    haystack = normalize_for_match(document_text)

    chips: List[Dict[str, Any]] = []
    dropped: List[str] = []
    seen: Dict[str, Set[str]] = {f: set() for f in FAMILIES}

    for chip in (getattr(suggestion, "chips", None) or []):
        family = getattr(chip, "family", None)
        if family not in FAMILIES:
            dropped.append(f"{family!r} is not a scope family")
            continue

        value = _clean_value(getattr(chip, "value", "") or "")
        if not value:
            continue
        if len(value) > MAX_ENTRY_CHARS:
            dropped.append(
                f"{family}: '{value[:60]}...' is longer than {MAX_ENTRY_CHARS} characters"
            )
            continue

        fold = value.casefold()
        if fold in seen[family]:
            continue
        if len(seen[family]) >= MAX_PER_FAMILY:
            dropped.append(f"{family}: '{value}' is past the {MAX_PER_FAMILY}-entry limit")
            continue

        evidence = _WS.sub(" ", getattr(chip, "evidence", "") or "").strip()
        verified = quote_is_in_document(evidence, haystack)

        confidence = getattr(chip, "confidence", None)
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"

        chips.append(
            {
                "family": family,
                "value": value,
                "evidence": evidence,
                "confidence": "low" if not verified else confidence,
                "unverified": not verified,
            }
        )
        seen[family].add(fold)

    return {
        "chips": chips,
        "not_used": _clean_list(getattr(suggestion, "not_used", None)),
        "needs_review": _clean_list(getattr(suggestion, "needs_review", None)),
        "notes": _WS.sub(" ", getattr(suggestion, "notes", "") or "").strip(),
        "dropped": dropped,
    }
