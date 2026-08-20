"""Last-resort study identity: read the paper's own first page.

The DOI cascade (`doi_service.extract_doi`) resolves most documents, but it
needs a DOI that is printed, embedded, or findable by title. Older trials and
scanned reprints have none of the three — and for those the identity is still
sitting in plain sight on page 1, under the title.

This module asks the model for exactly two facts and then REFUSES anything it
cannot find in the text it was given. That check is the point: a hallucinated
surname is worse than a filename, because a filename is visibly a filename
while "Smith 1994" looks like a citation and will be copied into one.

Only ever called when the cheaper paths have failed — see
`pdf_tasks.process_pdf` and `zscripts/backfill_study_labels.py --use-llm`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional, Tuple

import dspy

from utils.absence import is_absent
from utils.lm_config import get_dspy_model
from utils.study_label import surname_of, year_of

logger = logging.getLogger(__name__)

# The identity block (title, authors, affiliations, journal line) is always at
# the very top. Reading further costs tokens and adds the references section,
# which is full of OTHER papers' authors and years — the single most likely way
# to attach the wrong name to a document.
OPENING_CHARS = 2500

# Defaults to the extraction primary model, NOT to a cheaper one. Haiku 4.5 was
# the obvious pick for a two-field read off page 1 and it 400s on every call:
# config.models.reasoning_kwargs applies adaptive thinking to every
# `anthropic/` model, and Haiku does not support it ("adaptive thinking is not
# supported on this model"). Overriding the model here without also overriding
# that is a request the API rejects, so the model that the rest of the pipeline
# is already configured for is the safe default.
# Env-overridable like every other model choice in config/models.py.
import os  # noqa: E402  (kept next to the constant it serves)

from config.models import DEFAULT_MODEL  # noqa: E402

STUDY_IDENTITY_MODEL = os.environ.get("STUDY_IDENTITY_MODEL", DEFAULT_MODEL)

_EARLIEST_PLAUSIBLE_YEAR = 1600


class StudyIdentityFromText(dspy.Signature):
    """Read the opening of a research paper and report who wrote it and when.

    Report only what is printed in the text below. If the opening does not show
    an author list, or shows no publication year, answer NR for that field —
    NR is a correct answer here, a guess is not."""

    paper_opening: str = dspy.InputField(
        desc="The first page of a research paper: title, authors, affiliations, "
             "and journal line, as parsed text."
    )

    first_author_surname: str = dspy.OutputField(
        desc="FAMILY name of the FIRST listed author, spelled exactly as printed "
             "(keep accents and hyphens: Kyselovič, Al-Sukhun, van Dijk). Family "
             "name only — no given names, no initials, no title, no degrees. "
             "NR if no author list appears in this text."
    )

    publication_year: str = dspy.OutputField(
        desc="The 4-digit year this paper was PUBLISHED, as printed in the "
             "journal/copyright line. Not the year data were collected, and not "
             "a 'received'/'accepted' date if a publication year is also shown. "
             "NR if no year appears in this text."
    )


def _grounded(value: str, text: str) -> bool:
    """Is this string actually printed in the text we handed the model?

    Accent- and case-insensitive, because the parsed text and the model's copy
    of it disagree on both often enough to throw away good answers.
    """
    import unicodedata

    def fold(s: str) -> str:
        stripped = unicodedata.normalize("NFKD", s)
        return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()

    return fold(value) in fold(text)


def identify_study(
    markdown: Optional[str],
    *,
    model: str = STUDY_IDENTITY_MODEL,
) -> Tuple[Optional[str], Optional[str]]:
    """(first_author_surname, publication_year) from a paper's opening text.

    Returns (None, None) rather than raising — every caller is on a best-effort
    path, and a document with no study ID is a normal state, not a failure.
    Either half may come back None on its own: knowing the author but not the
    year still beats showing a 200-character title.
    """
    text = (markdown or "").strip()
    if not text:
        return None, None
    opening = text[:OPENING_CHARS]

    try:
        # Temperature is deliberately NOT overridden. The Anthropic models this
        # runs on have thinking enabled (config.models.reasoning_kwargs), and
        # thinking rejects any temperature but 1.0 with a hard 400 — a
        # temperature=0.0 here failed every call. Determinism is not what makes
        # this safe anyway; the grounding check below is.
        # max_tokens covers the thinking budget, not just the two short fields.
        lm = get_dspy_model(model_name=model, max_tokens=2000)
        with dspy.context(lm=lm):
            prediction = dspy.Predict(StudyIdentityFromText)(paper_opening=opening)
    except Exception as e:
        logger.warning("[study-identity] model call failed: %s", e)
        return None, None

    raw_author = (getattr(prediction, "first_author_surname", "") or "").strip()
    raw_year = (getattr(prediction, "publication_year", "") or "").strip()

    author: Optional[str] = None
    if raw_author and not is_absent(raw_author):
        # surname_of also recases block capitals, matching every other source.
        candidate = surname_of(raw_author)
        if candidate and _grounded(candidate, opening):
            author = candidate
        elif candidate:
            logger.info(
                "[study-identity] rejected ungrounded author %r — not printed in the opening",
                candidate,
            )

    year: Optional[str] = None
    if raw_year and not is_absent(raw_year):
        candidate_year = year_of(raw_year)
        # Grounding plus a sanity bound: a year the model invented is usually
        # plausible-looking, so "is it in the text" does the real work here.
        if (
            candidate_year
            and _grounded(candidate_year, opening)
            and _EARLIEST_PLAUSIBLE_YEAR <= int(candidate_year) <= datetime.now().year + 1
        ):
            year = candidate_year
        elif candidate_year:
            logger.info(
                "[study-identity] rejected ungrounded year %r — not printed in the opening",
                candidate_year,
            )

    return author, year
