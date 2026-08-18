"""
PMID/PMCID extraction from arbitrary URLs pasted into a reference's URL
field(s) — shared by endnote_service.py and ris_service.py, both of which
parse a source format that carries free-text URLs but no field as reliable
as PubMed's own structured PMID.

Deliberately narrow: only matches URLs on the specific NCBI/Europe PMC paths
that actually carry these IDs, never a bare number anywhere in a URL. A loose
"any digits" match would misfire on page counts, years, or tracking
parameters that happen to sit in a citation URL.
"""

from __future__ import annotations

import re
from typing import List, Optional

_PMID_URL_RE = re.compile(
    r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d{1,9})",
    re.IGNORECASE,
)
_PMCID_URL_RE = re.compile(
    r"(?:ncbi\.nlm\.nih\.gov/pmc/articles/|pmc\.ncbi\.nlm\.nih\.gov/articles/|europepmc\.org/articles/)"
    r"(PMC\d+)",
    re.IGNORECASE,
)


def extract_pmid_from_urls(urls: Optional[List[str]]) -> Optional[str]:
    """The first PMID found in a PubMed article URL, or None. Ignores every
    other kind of URL (publisher pages, DOI resolvers, Google Scholar, ...)."""
    for url in urls or []:
        m = _PMID_URL_RE.search(url or "")
        if m:
            return m.group(1)
    return None


def extract_pmcid_from_urls(urls: Optional[List[str]]) -> Optional[str]:
    """The first PMCID found in an NCBI PMC / Europe PMC article URL, or
    None. Normalized to upper-case "PMC" (matches Unpaywall/PMC's own
    casing)."""
    for url in urls or []:
        m = _PMCID_URL_RE.search(url or "")
        if m:
            return m.group(1).upper()
    return None
