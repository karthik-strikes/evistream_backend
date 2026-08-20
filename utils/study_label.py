"""Cochrane-style study identity: "Raslan 2021", "Polat 2005b".

Reviewers name a study by first author + year. Every screen used to show
`documents.filename`, which for an EndNote/RIS/PubMed import is the full
article title ("A single-tablet fixed-dose combination of racemic
ibuprofen/paracetamol in the management of..."). This module turns whatever
identity a document happens to carry into that short label.

Mirrored in `frontend/lib/documentLabel.ts` — the two must stay in sync, or an
exported CSV disagrees with the screen it was exported from. Same arrangement
as utils/absence.py <-> lib/absence.ts.

Precedence (first hit wins), and the order is not arbitrary:
  1. `study_label`      — a human typed it. Never overridden, never suffixed.
  2. filename pattern   — "Raslan 2021.pdf" is *already* a curated study ID
                          (230 of 472 live documents are named this way), and
                          it carries hand-assigned a/b suffixes that no
                          metadata source can reproduce. It beats Crossref for
                          exactly that reason.
  3. first_author+year  — derived from Crossref / PubMed / EndNote / RIS.
  4. registry id        — NCT number, then PMID: a trial record has no author
                          in the citing sense, and Cochrane cites it by ID.
  5. None               — caller falls back to the filename stem. ~80% of the
                          existing corpus has no bibliographic metadata, so
                          this branch is normal, not an error.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# A publication year we would actually believe. Bounded on purpose: it stops
# "Receipt-2456-8583-5984.pdf" and "2026.acl-demo.7.pdf" from being read as a
# year, which a bare \d{4} would do.
_YEAR = r"(?:1[6-9]\d{2}|20\d{2})"

# A surname as it appears in a curated filename. Allows the multi-word and
# hyphenated forms that are real names ("Steen Law 2000", "van Dijk 2005",
# "Al-Waili 2011") and the disambiguating initial some libraries keep
# ("Wang Y 2017" — deliberately preserved, see _label_from_filename).
_NAME = r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’\-\u2010\u2011\u2013]*(?:\s+[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’\-\u2010\u2011\u2013]*){0,2}"

_FILENAME_LABEL_RE = re.compile(
    r"^(?P<name>" + _NAME + r")[\s_,\-]*(?P<year>" + _YEAR + r")(?P<suffix>[a-z])?(?![0-9])"
)

# "Smith AB" (PubMed esummary) / "Smith A. B." — trailing initials are not part
# of the surname when they follow it in the *author* field. Filenames are left
# alone; see _label_from_filename.
_TRAILING_INITIALS_RE = re.compile(r"\s+(?:[A-Z]\.?\s*){1,3}$")

# Name particles that belong to the surname: "Jan van Dijk" -> "van Dijk".
_PARTICLES = {
    "van", "von", "de", "der", "den", "del", "della", "di", "da", "dos",
    "du", "la", "le", "el", "al", "ter", "ten", "bin", "ibn",
}

_SUFFIXES = "abcdefghijklmnopqrstuvwxyz"


def _normalize_case(name: str) -> str:
    """Crossref stores some author records in block capitals ("SEYMOUR",
    "MORRISON"). A study ID reading "SEYMOUR 1996" in a list of "Dionne 1994"s
    looks like a different kind of thing, so all-caps names are recased.

    Only fully-uppercase input is touched — "McDonald" and "van Dijk" already
    carry deliberate casing that re-titling would destroy.
    """
    if not name or not name.isupper():
        return name
    out = []
    for word in name.split(" "):
        # Capitalise after internal punctuation too: "O'BRIEN" -> "O'Brien",
        # "AL-SUKHUN" -> "Al-Sukhun".
        piece = re.sub(r"[A-Za-zÀ-ɏ]+", lambda m: m.group(0).capitalize(), word)
        out.append(piece.lower() if piece.lower() in _PARTICLES else piece)
    return " ".join(out)


def surname_of(author: Any) -> Optional[str]:
    """Surname of one author, from any of the shapes our four import paths use:
    Crossref's {"family": "Raslan"}, EndNote's "Raslan, Nada", PubMed's
    "Raslan N", or a plain "Nada Raslan"."""
    if isinstance(author, dict):
        family = (author.get("family") or "").strip()
        if family:
            # Falls through to the shared cleanup below rather than returning
            # here — Crossref's `family` is the most authoritative shape we get
            # and also the one most often stored in block capitals.
            return _normalize_case(family)
        author = (author.get("name") or author.get("literal") or "").strip()

    raw = (author or "").strip() if isinstance(author, str) else ""
    if not raw:
        return None

    # "Last, First" — the comma form is unambiguous, take everything before it.
    if "," in raw:
        candidate = raw.split(",", 1)[0].strip()
    else:
        candidate = _TRAILING_INITIALS_RE.sub("", raw).strip()
        if " " in candidate:
            parts = candidate.split()
            i = len(parts) - 1
            while i > 0 and parts[i - 1].lower().strip(".") in _PARTICLES:
                i -= 1
            candidate = " ".join(parts[i:])

    candidate = _normalize_case(candidate.strip(" .,;"))
    return candidate or None


def first_surname(authors: Any) -> Optional[str]:
    """Surname of the FIRST author of a list (or of a single author value)."""
    if isinstance(authors, (list, tuple)):
        for a in authors:
            found = surname_of(a)
            if found:
                return found
        return None
    return surname_of(authors)


def year_of(raw: Any) -> Optional[str]:
    """First believable 4-digit year in a date-ish value. Handles Crossref's
    {"date-parts": [[2021, 3]]}, PubMed's "2021 Mar", and a bare "2021"."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        parts = raw.get("date-parts") or []
        if parts and parts[0]:
            return year_of(parts[0][0])
        raw = raw.get("date-time") or raw.get("raw") or ""
    if isinstance(raw, (list, tuple)):
        return year_of(raw[0]) if raw else None
    if isinstance(raw, int):
        raw = str(raw)
    match = re.search(_YEAR, str(raw))
    return match.group(0) if match else None


def label_from_filename(filename: Optional[str]) -> Optional[str]:
    """"Raslan 2021.pdf" / "Aggarwal_2022.pdf" / "Kujan2020 REF 34.pdf" ->
    "Raslan 2021" / "Aggarwal 2022" / "Kujan 2020".

    The name portion is kept VERBATIM apart from separator normalisation.
    "Wang Y 2017" and "Wang S 2017" are two different studies in the same
    project and the initial is the only thing telling them apart — collapsing
    it to "Wang 2017" would merge two studies into one visible identity.
    """
    if not filename:
        return None
    stem = re.sub(r"\.(pdf|txt|md|json|xml)$", "", filename.strip(), flags=re.IGNORECASE)
    stem = stem.replace("_", " ").strip()
    match = _FILENAME_LABEL_RE.match(stem)
    if not match:
        return None
    name = re.sub(r"\s+", " ", match.group("name")).strip(" -,")
    if len(name) < 2:
        return None
    return f"{name} {match.group('year')}{match.group('suffix') or ''}"


def derive_label(doc: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """(label, is_fixed) for one document row.

    `is_fixed` is true ONLY for a label a human typed into `study_label`. That
    one is never suffixed, never renamed — it is the curated study ID and the
    whole point of the column.

    A label parsed out of a filename is NOT fixed. "Polat 2005b.pdf" carries a
    reviewer's own suffix and normally survives untouched, because a unique
    label is never suffixed at all — but if two documents in the project parse
    to the same string, the human did not in fact distinguish them, and two
    rows reading identically is the exact problem this feature exists to fix.
    """
    manual = (doc.get("study_label") or "").strip()
    if manual:
        return manual, True

    from_filename = label_from_filename(doc.get("filename"))
    if from_filename:
        return from_filename, False

    author = (doc.get("first_author") or "").strip()
    year = (doc.get("pub_year") or "").strip()
    if author and year:
        return f"{author} {year}", False
    if author:
        return author, False

    nct = (doc.get("nct_id") or "").strip()
    if nct:
        return nct, True
    pmid = (doc.get("pmid") or "").strip()
    if pmid:
        return f"PMID {pmid}", True

    return None, True


def filename_stem(filename: Optional[str]) -> str:
    return re.sub(r"\.pdf$", "", (filename or "").strip(), flags=re.IGNORECASE) or "Untitled"


def _suffixed(base: str, index: int) -> str:
    """`index`-th disambiguated form of `base`.

    Letters when the base ends in the year ("Polat 2005" -> "Polat 2005a"),
    because that is the notation reviewers read. When the base ALREADY ends in
    a letter suffix — two documents whose filenames were both "Polat 2005b" —
    stacking another letter would read as a different, plausible-looking study
    ID ("Polat 2005ba"), so those get an unmistakable numeric marker instead.
    """
    if base and base[-1].isdigit():
        return f"{base}{_SUFFIXES[index]}" if index < len(_SUFFIXES) else f"{base}-{index + 1}"
    return base if index == 0 else f"{base} ({index + 1})"


def build_label_map(documents: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Project-scoped labels, with Cochrane a/b/c suffixes for collisions.

    Pass the documents of ONE project: "Polat 2005a"/"Polat 2005b" only means
    anything within the review that cites them. Ordering is by `ref_id` so the
    suffix a document gets is stable across reloads and across exports — an ID
    that reshuffles when a new document is uploaded is worse than no ID.

    A label held by exactly one document is emitted bare: a lone "Raslan 2021"
    never becomes "Raslan 2021a".
    """
    docs: List[Dict[str, Any]] = list(documents)
    docs.sort(key=lambda d: (d.get("ref_id") is None, d.get("ref_id") or 0, str(d.get("id"))))

    derived: List[Tuple[str, Optional[str], bool, str]] = []
    counts: Dict[str, int] = {}
    manual_labels = set()
    for d in docs:
        label, fixed = derive_label(d)
        derived.append((str(d.get("id")), label, fixed, filename_stem(d.get("filename"))))
        if label:
            # Manual labels count toward the tally — a derived "Raslan 2021"
            # sitting next to a hand-typed "Raslan 2021" still needs to move —
            # but they are never themselves the one that moves.
            counts[label] = counts.get(label, 0) + 1
            if fixed:
                manual_labels.add(label)

    seen: Dict[str, int] = {}
    out: Dict[str, str] = {}
    for doc_id, label, fixed, stem in derived:
        if not label:
            out[doc_id] = stem
            continue
        if fixed or counts.get(label, 0) < 2:
            out[doc_id] = label
            continue
        index = seen.get(label, 0)
        candidate = _suffixed(label, index)
        while candidate in manual_labels:  # never shadow a curated ID
            index += 1
            candidate = _suffixed(label, index)
        seen[label] = index + 1
        out[doc_id] = candidate
    return out


def label_for(doc: Dict[str, Any]) -> str:
    """Single-document label with no collision context — for the rare caller
    that has one row and no project list. Prefer build_label_map."""
    label, _ = derive_label(doc)
    return label or filename_stem(doc.get("filename"))
