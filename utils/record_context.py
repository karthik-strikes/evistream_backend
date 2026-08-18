"""
A short note prepended to imported documents telling the model what it's about
to read.

Why
---
Documents reach extraction as whatever sits at their `s3_markdown_path`. For a
PDF that's Datalab's markdown; for an import (ClinicalTrials.gov, PubMed,
EndNote, RIS) it's the normalized record as JSON. The signature's input field
describes itself as the document's markdown content, so on an import the model
is told it's reading a paper and handed a JSON object instead.

Two consequences, both observed on live data (Aug 5 2026):

1. `source_text` came back containing JSON syntax — 3 of 33 CT.gov quotes, e.g.
   `"lead": "Rutgers, The State University of New Jersey", "collaborators": [`.
   Not a mistake: the instruction is "quote the document exactly", and in a
   JSON document the exact quote is JSON. Extracted `value`s were clean (0 of
   33 affected), so this is a grounding-display problem, not an accuracy one.
2. Nothing tells the model its input is deliberately partial. A registration-
   only trial has no results and an abstract-only import has no methods, but
   the signature asks for those fields anyway, which invites inference.

The earlier attempt at this rewrote each record into markdown. That was the
wrong trade: ~200 lines in the extraction hot path, and the model would then
quote text that exists nowhere on disk, so no quote could be matched back to
the stored record. This only PREPENDS — the record body stays byte-identical,
so quotes remain literal substrings of what's in S3.

This steers rather than guarantees. It should reduce JSON-shaped quotes, not
eliminate them; the frontend's field-anchored highlighting (see
lib/quote-match.ts) is what makes a stray one harmless.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_QUOTE_RULE = (
    "When quoting evidence for source_text, quote the VALUE of a field only. "
    "Do not include JSON syntax — no key names, braces, brackets, or quotation marks."
)

# Keyed by the record shape, since that's all we have here: extraction reads
# files, not the `documents` row, so `source_type` isn't available.
_KINDS = [
    (
        lambda r: bool(r.get("nctId")),
        "a ClinicalTrials.gov trial registry record",
        "A registry record states what a trial planned, and — only if results were posted — what it "
        "measured. It is not a full paper. Anything it does not record is genuinely absent: return NR "
        "rather than inferring it from related fields.",
    ),
    (
        lambda r: bool(r.get("pmid")) and bool(r.get("fullText")),
        "a PubMed record that includes the full article text from PubMed Central",
        "The article body is under fullText, split into sections. Prefer it over the abstract when both "
        "state a fact.",
    ),
    (
        lambda r: bool(r.get("pmid")) or bool(r.get("abstractText")),
        "a PubMed citation record — metadata and abstract only",
        "There is no methods or results section here, only the abstract. Anything not stated in it is "
        "genuinely absent: return NR rather than inferring it.",
    ),
    (
        lambda r: bool(r.get("title")) or bool(r.get("authors")) or bool(r.get("doi")),
        "a bibliographic reference record",
        "This is a citation only — no article text. Anything beyond the citation details is genuinely "
        "absent: return NR.",
    ),
]


def _describe(record: dict) -> tuple:
    for predicate, kind, guidance in _KINDS:
        try:
            if predicate(record):
                return kind, guidance
        except Exception:  # a hostile/odd record shape must not break extraction
            continue
    return (
        "a structured record supplied as JSON rather than an article's text",
        "Anything the record does not contain is genuinely absent: return NR rather than inferring it.",
    )


def preamble_for(raw: str) -> str:
    """Note to prepend to `raw`, or "" when there's nothing to say.

    Returns "" for anything that isn't a JSON object — which is every
    PDF-derived document, so those pass through byte-for-byte. Never raises:
    a failure here must degrade to today's behaviour, not fail an extraction.
    """
    if not raw or raw.lstrip()[:1] != "{":
        return ""

    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return ""

    if not isinstance(record, dict) or not record:
        return ""

    try:
        kind, guidance = _describe(record)
    except Exception:
        logger.warning("[record_context] failed to describe record; no preamble added", exc_info=True)
        return ""

    # Fenced and explicitly disowned so the model doesn't mistake the note for
    # part of the record and quote it back as evidence.
    return (
        "<!-- DOCUMENT NOTE — context for the reader, not part of the record -->\n"
        f"This document is {kind}.\n"
        f"{_QUOTE_RULE}\n"
        f"{guidance}\n"
        "<!-- END DOCUMENT NOTE -->\n\n"
    )
