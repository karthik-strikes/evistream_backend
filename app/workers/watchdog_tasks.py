"""
Watchdog tasks - detect and recover stuck jobs.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from supabase import create_client

from app.workers.celery_app import celery_app
from app.config import settings
from app.models.enums import JobStatus, JobType, FormStatus

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# A job that has STARTED is stuck once it outlives Celery's own hard time limit
# (the worker would already have killed the task) plus a buffer.
STUCK_THRESHOLD_SECONDS = settings.CELERY_TASK_TIME_LIMIT + 300  # task limit + 5 min buffer

# A job that has NOT started yet is only waiting its turn in the queue. Queue
# waits are legitimate under backlog (extraction runs 4 slots wide), so pending
# jobs get a much longer fuse than execution overrun. Measuring both from
# created_at — as this task used to — fails live jobs that are merely queued.
QUEUE_THRESHOLD_SECONDS = int(
    os.environ.get("WATCHDOG_QUEUE_THRESHOLD_SECONDS", str(6 * 3600))
)


def _parse_ts(value):
    """Parse a Supabase timestamp into an aware datetime, or None."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _stuck_reason(job: dict, now: datetime):
    """
    Decide whether a job is stuck, and why.

    Execution overrun is measured from started_at; queue wait from created_at.
    Rows written before started_at existed fall back to created_at.
    """
    if job.get("status") == JobStatus.PROCESSING.value:
        ref = _parse_ts(job.get("started_at")) or _parse_ts(job.get("created_at"))
        limit, label = STUCK_THRESHOLD_SECONDS, "running"
    else:
        ref = _parse_ts(job.get("created_at"))
        limit, label = QUEUE_THRESHOLD_SECONDS, "queued"

    if ref is None:
        return None
    age = (now - ref).total_seconds()
    if age <= limit:
        return None
    return f"{label} {age / 3600:.1f}h, limit {limit / 3600:.1f}h"


@celery_app.task(name="watchdog_cleanup_stuck_jobs")
def cleanup_stuck_jobs():
    """
    Find jobs stuck in 'pending' or 'processing' beyond the timeout
    threshold and mark them as failed. Also update the associated
    document/form/extraction records.
    """
    try:
        now = datetime.now(timezone.utc)

        # Open jobs are bounded (a handful in steady state), so fetch them and
        # apply the per-status staleness rule in Python — the two rules can't be
        # expressed as one PostgREST filter.
        open_jobs_result = supabase.table("jobs")\
            .select("*")\
            .in_("status", [JobStatus.PENDING.value, JobStatus.PROCESSING.value])\
            .execute()

        open_jobs = open_jobs_result.data or []
        stuck_jobs = []
        for job in open_jobs:
            reason = _stuck_reason(job, now)
            if reason:
                stuck_jobs.append((job, reason))

        if not stuck_jobs:
            return {"cleaned": 0, "open": len(open_jobs)}

        logger.warning(
            f"Watchdog found {len(stuck_jobs)} stuck job(s) of {len(open_jobs)} open"
        )

        cleaned = 0
        for job, reason in stuck_jobs:
            try:
                _fail_stuck_job(job, reason)
                cleaned += 1
            except Exception as e:
                logger.error(f"Watchdog failed to clean job {job['id']}: {e}")

        logger.info(f"Watchdog cleaned {cleaned}/{len(stuck_jobs)} stuck jobs")
        return {"cleaned": cleaned, "found": len(stuck_jobs), "open": len(open_jobs)}

    except Exception as e:
        logger.error(f"Watchdog task failed: {e}")
        return {"error": str(e)}


def _fail_stuck_job(job: dict, reason: str = ""):
    """Mark a single stuck job and its associated resource as failed."""
    job_id = job["id"]
    job_type = job.get("job_type")
    input_data = job.get("input_data") or {}
    error_msg = "Timed out: job exceeded maximum processing time"
    if reason:
        error_msg = f"{error_msg} ({reason})"

    logger.warning(
        f"Marking stuck job {job_id} (type={job_type}, status={job.get('status')}) "
        f"as failed: {reason or 'no reason recorded'}"
    )

    # Update the job itself
    supabase.table("jobs").update({
        "status": JobStatus.FAILED.value,
        "progress": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "error_message": error_msg,
    }).eq("id", job_id).execute()

    # Update the associated resource based on job type
    if job_type == JobType.PDF_PROCESSING.value:
        doc_id = input_data.get("document_id")
        if doc_id:
            supabase.table("documents").update({
                "processing_status": "failed",
                "processing_error": error_msg,
            }).eq("id", doc_id).execute()

    elif job_type == JobType.FORM_GENERATION.value:
        # Find form_id from input_data or from forms table
        form_id = input_data.get("form_id")
        if form_id:
            supabase.table("forms").update({
                "status": FormStatus.FAILED.value,
                "error": error_msg,
            }).eq("id", form_id).execute()

    elif job_type == JobType.EXTRACTION.value:
        extraction_id = input_data.get("extraction_id")
        if extraction_id:
            supabase.table("extractions").update({
                "status": "failed",
            }).eq("id", extraction_id).execute()


@celery_app.task(name="watchdog_cleanup_stuck_forms")
def cleanup_stuck_forms():
    """
    Find forms stuck in 'generating' or 'regenerating' that have no associated
    pending/processing job. This catches cases like the resume_after_rejection bug
    where the job completes but the form is left in a non-terminal status.
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=STUCK_THRESHOLD_SECONDS)
        cutoff_iso = cutoff.isoformat()

        # Find forms stuck in transient statuses beyond the timeout
        stuck_forms_result = supabase.table("forms")\
            .select("id, form_name, status")\
            .in_("status", [FormStatus.GENERATING.value, FormStatus.REGENERATING.value])\
            .lt("updated_at", cutoff_iso)\
            .execute()

        stuck_forms = stuck_forms_result.data or []
        if not stuck_forms:
            return {"cleaned": 0}

        logger.warning(f"Watchdog found {len(stuck_forms)} potentially stuck form(s)")

        cleaned = 0
        for form in stuck_forms:
            form_id = form["id"]
            try:
                # Check if there is an active job for this form
                active_job = supabase.table("jobs")\
                    .select("id")\
                    .eq("input_data->>form_id", form_id)\
                    .in_("status", [JobStatus.PENDING.value, JobStatus.PROCESSING.value])\
                    .limit(1)\
                    .execute()

                if active_job.data:
                    # A live job exists — the main watchdog will handle it if it times out
                    continue

                # No active job but form is stuck — mark it failed
                error_msg = "Timed out: form stuck in generating state with no active job"
                logger.warning(
                    f"Marking stuck form {form_id} ({form['form_name']}) as failed "
                    f"(status={form['status']}, no active job)"
                )
                supabase.table("forms").update({
                    "status": FormStatus.FAILED.value,
                    "error": error_msg,
                }).eq("id", form_id).execute()
                cleaned += 1
            except Exception as e:
                logger.error(f"Watchdog failed to clean stuck form {form_id}: {e}")

        logger.info(f"Watchdog cleaned {cleaned}/{len(stuck_forms)} stuck forms")
        return {"cleaned": cleaned, "found": len(stuck_forms)}

    except Exception as e:
        logger.error(f"Watchdog stuck-forms task failed: {e}")
        return {"error": str(e)}
