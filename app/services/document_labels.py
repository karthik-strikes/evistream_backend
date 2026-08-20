"""Project-scoped study labels for API payloads.

`utils/study_label.py` is pure (and mirrored in TypeScript); this is the thin
database-aware layer on top of it.

Everything here fetches WHOLE PROJECTS, never just the documents a caller
happens to hold. The a/b/c suffix is a per-project computation: given only one
of the two "Polat 2005" documents you would emit a bare "Polat 2005", and the
same document would then appear as "Polat 2005" on one screen and "Polat 2005b"
on another. Fetching the project costs one query and removes that whole class
of bug.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from utils.study_label import build_label_map

logger = logging.getLogger(__name__)

# Every column derive_label() reads. Selecting less silently degrades a label
# rather than failing, which is the hardest kind of bug to notice.
LABEL_COLUMNS = "id,project_id,ref_id,filename,first_author,pub_year,study_label,pmid,nct_id"


def labels_for_projects(supabase, project_ids: Iterable[str]) -> Dict[str, str]:
    """{document_id: "Raslan 2021"} for every document in these projects."""
    ids = [str(p) for p in {p for p in project_ids if p}]
    if not ids:
        return {}
    try:
        rows = (
            supabase.table("documents")
            .select(LABEL_COLUMNS)
            .in_("project_id", ids)
            .execute()
            .data
            or []
        )
    except Exception:
        # A label is a nicety; never take an endpoint down for one. Callers all
        # fall back to the filename when a document is missing from the map.
        logger.warning("Could not load study labels for projects %s", ids, exc_info=True)
        return {}

    by_project: Dict[str, List[dict]] = {}
    for row in rows:
        by_project.setdefault(row["project_id"], []).append(row)

    out: Dict[str, str] = {}
    for project_rows in by_project.values():
        out.update(build_label_map(project_rows))
    return out


def labels_for_documents(supabase, document_ids: Iterable[str]) -> Dict[str, str]:
    """Same, for a set of documents that may span projects — resolves each
    document's project first so the suffixes match what those projects' own
    screens show."""
    ids = [str(d) for d in {d for d in document_ids if d}]
    if not ids:
        return {}
    try:
        owners = (
            supabase.table("documents")
            .select("project_id")
            .in_("id", ids)
            .execute()
            .data
            or []
        )
    except Exception:
        logger.warning("Could not resolve projects for documents", exc_info=True)
        return {}
    return labels_for_projects(supabase, [o["project_id"] for o in owners])
