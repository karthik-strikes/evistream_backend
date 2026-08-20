"""Suggest a project's review scope from its planning documents.

The reviewer uploads the protocol / PROSPERO record / eligibility table, a model
reads it, and the guided scope builder opens pre-filled with chips they can
keep, edit or throw away.

**Nothing is saved here.** ``PATCH /projects/{id}/review-scope`` is still the
only writer, because ``frontend/lib/reviewScope.ts:composeScope`` is the only
thing allowed to compose the prose that reaches an extraction prompt — and
``projects.py:_normalize_review_scope`` rejects chips that arrive without it.

Shaped like ``synthesis.py:suggest_mapping``: a markdown template with a
placeholder, ``with_structured_output(..., method="json_schema")``, everything
the model returned validated before the frontend sees it, its reasoning logged,
and the answer cached by file content.

It differs on one point, on purpose. Synthesis falls back to a column-name
heuristic when the model is unreachable, so the screen stays usable. There is no
honest equivalent here: regex-scraping PICO out of prose would produce
plausible-looking chips a reviewer then has to audit one by one, and the scope
goes verbatim into every signature docstring in the project. So an unavailable
model is a 503 that tells them to type it by hand.
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.dependencies import get_current_user
from app.rate_limit import limiter
from app.services.cache_service import cache_service
from app.services.project_access import check_project_access
from config.models import CODEGEN_SIGNATURE_MODEL
from utils.scope_document import (
    MAX_TOTAL_CHARS,
    UnsupportedDocument,
    assemble_document,
)
from utils.scope_suggestion import validate_suggestion

logger = logging.getLogger(__name__)
router = APIRouter()

# Reading a protocol is a small, cheap comprehension job — the signature model is
# already sized for it.
SCOPE_SUGGEST_MODEL = os.environ.get("SCOPE_SUGGEST_MODEL", CODEGEN_SIGNATURE_MODEL)

# Bump when suggest_review_scope.md changes, so cached answers from the old
# wording are not served against the new one.
SCOPE_PROMPT_VERSION = 1

MAX_FILES = 3
MAX_FILE_BYTES = 20 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60


# ── Response ─────────────────────────────────────────────────────────────────


class SuggestedChip(BaseModel):
    family: str
    value: str
    evidence: str
    confidence: str
    # True when the quote could not be found in the uploaded text. The chip is
    # still returned — see utils/scope_suggestion.py for why it is not dropped —
    # and the dialog leaves it unticked.
    unverified: bool


class FileReadReport(BaseModel):
    filename: str
    chars_total: int
    chars_read: int
    truncated: bool
    empty: bool


class SuggestScopeResponse(BaseModel):
    chips: List[SuggestedChip]
    not_used: List[str]
    needs_review: List[str]
    notes: str
    dropped: List[str]
    files: List[FileReadReport]
    cached: bool = False


# ── LLM call ─────────────────────────────────────────────────────────────────


def _ask_model(document_text: str) -> Any:
    """Blocking — callers must push this off the event loop."""
    from core.generators.models import ScopeSuggestion
    from utils.langchain_cost_callback import make_callback_config
    from utils.lm_config import get_langchain_model

    template = (
        Path(__file__).resolve().parents[3]
        / "core" / "generators" / "prompts" / "suggest_review_scope.md"
    )
    prompt = template.read_text(encoding="utf-8").replace("[[DOCUMENT_TEXT]]", document_text)

    model = get_langchain_model(SCOPE_SUGGEST_MODEL, temperature=0.0, max_tokens=8000)
    structured = model.with_structured_output(ScopeSuggestion, method="json_schema")
    return structured.invoke(prompt, config=make_callback_config("review_scope:suggest"))


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/suggest", response_model=SuggestScopeResponse)
@limiter.limit("10/minute")
async def suggest_review_scope(
    request: Request,
    project_id: UUID = Form(...),
    files: List[UploadFile] = File(...),
    user_id: UUID = Depends(get_current_user),
):
    """Read review-planning documents and propose scope entries for the builder.

    Requires ``can_create_forms`` — the same permission as saving the scope,
    since this is the same extraction-design authority. That permission is in
    WRITE_PERMISSIONS, so an archived project is refused by
    ``check_project_access`` before any LLM spend rather than after it.
    """
    await check_project_access(project_id, user_id, "can_create_forms")

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload at least one document.",
        )
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Upload at most {MAX_FILES} documents at a time.",
        )

    payloads: List[tuple] = []
    for upload in files:
        data = await upload.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{upload.filename}: larger than "
                       f"{MAX_FILE_BYTES // (1024 * 1024)} MB.",
            )
        payloads.append((upload.filename or "document", data))

    try:
        document_text, file_report = assemble_document(payloads)
    except UnsupportedDocument as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        )

    if not document_text.strip():
        # A scanned protocol has no text layer. Returning zero chips here would
        # read as "this document has no scope in it", which is a different and
        # much more misleading claim.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No readable text in these files — a scanned PDF has no text layer. "
                   "Paste the criteria into Free text mode instead.",
        )

    # Keyed on the file bytes, so re-opening the dialog with the same protocol
    # costs nothing, while a corrected protocol is a different key.
    digest = hashlib.sha256()
    for name, data in payloads:
        digest.update(name.encode("utf-8", errors="replace"))
        digest.update(data)
    cache_key = f"scope_suggest:{digest.hexdigest()[:16]}:v{SCOPE_PROMPT_VERSION}"

    cached: Dict[str, Any] = cache_service.get(cache_key)
    if cached:
        return SuggestScopeResponse(**{**cached, "cached": True})

    try:
        suggestion = await asyncio.to_thread(_ask_model, document_text)
        if suggestion is None:
            raise ValueError("scope suggestion model returned None")
    except Exception as exc:
        logger.warning(
            "Review-scope suggestion failed for project=%s (%s chars, %s files): %s",
            project_id, len(document_text), len(payloads), exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The suggestion model is unavailable right now — add the scope by hand, "
                   "or try again in a minute.",
        )

    body = validate_suggestion(suggestion, document_text)
    logger.info(
        "Review-scope suggestion for project=%s: %s chips (%s unverified) from %s chars "
        "across %s file(s); dropped=%s needs_review=%s | %s",
        project_id,
        len(body["chips"]),
        sum(1 for c in body["chips"] if c["unverified"]),
        len(document_text),
        len(payloads),
        body["dropped"],
        body["needs_review"],
        body["notes"],
    )

    payload = {**body, "files": file_report}
    cache_service.set(cache_key, payload, ttl=CACHE_TTL_SECONDS)

    return SuggestScopeResponse(**payload, cached=False)
