"""Per-run context for exact LLM cost attribution.

Celery runs each extraction in its own prefork process, and the DSPy/LangChain
calls are flushed to `llm_history` deep inside the pipeline — far from where the
`job_id` is known. Threading job_id through every layer would be invasive, so we
stash it in a ContextVar at the top of the worker task instead.

`asyncio.run()` (extraction_service.run_files_extraction) copies the current
context into the extraction coroutine, so the flush in utils/logging.py runs in
a child context and can read the job_id back — letting every row be stamped with
the run it belongs to. This is what makes per-run / per-model cost splitting
exact instead of a schema_name + time-window guess.

Prefork means one task per process at a time, so there is no cross-run leakage;
the var is simply overwritten at the start of each task.
"""
from __future__ import annotations

import contextvars
from typing import Optional

_current_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "evistream_current_job_id", default=None
)

# The extraction this run belongs to. Separate from job_id because
# `llm_history.extraction_id` is the foreign key `usage.py:_assign_extraction`
# already prefers when grouping calls into runs — it was simply never written, so
# every run fell back to a `schema_name` + time-window guess that swallows any
# concurrent run of the same form. Codegen and pilot runs leave this None.
_current_extraction_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "evistream_current_extraction_id", default=None
)


def set_current_job_id(job_id: Optional[str]) -> None:
    """Record the job whose LLM calls we're about to make (call at task start)."""
    _current_job_id.set(str(job_id) if job_id else None)


def get_current_job_id() -> Optional[str]:
    """Return the current run's job_id, or None outside a stamped context."""
    try:
        return _current_job_id.get()
    except LookupError:
        return None


def set_current_extraction_id(extraction_id: Optional[str]) -> None:
    """Record the extraction whose LLM calls we're about to make."""
    _current_extraction_id.set(str(extraction_id) if extraction_id else None)


def get_current_extraction_id() -> Optional[str]:
    """Return the current run's extraction_id, or None outside a stamped context."""
    try:
        return _current_extraction_id.get()
    except LookupError:
        return None
