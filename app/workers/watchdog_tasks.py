"""
Watchdog tasks - detect and recover stuck jobs.
"""

import logging
from datetime import datetime, timedelta, timezone

from supabase import create_client

from app.workers.celery_app import celery_app
from app.config import settings
from app.models.enums import JobStatus, JobType, FormStatus

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

# Jobs stuck longer than this are considered timed out
STUCK_THRESHOLD_SECONDS = settings.CELERY_TASK_TIME_LIMIT + 300  # task limit + 5 min buffer


@celery_app.task(name="watchdog_cleanup_stuck_jobs")
def cleanup_stuck_jobs():
    """
    Find jobs stuck in 'pending' or 'processing' beyond the timeout
    threshold and mark them as failed. Also update the associated
    document/form/extraction records.
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=STUCK_THRESHOLD_SECONDS)
        cutoff_iso = cutoff.isoformat()

        # Find stuck jobs: status is pending/processing AND created before cutoff
        stuck_jobs_result = supabase.table("jobs")\
            .select("*")\
            .in_("status", [JobStatus.PENDING.value, JobStatus.PROCESSING.value])\
            .lt("created_at", cutoff_iso)\
            .execute()

        stuck_jobs = stuck_jobs_result.data or []

        if not stuck_jobs:
            return {"cleaned": 0}

        logger.warning(f"Watchdog found {len(stuck_jobs)} stuck job(s)")

        cleaned = 0
        for job in stuck_jobs:
            try:
                _fail_stuck_job(job)
                cleaned += 1
            except Exception as e:
                logger.error(f"Watchdog failed to clean job {job['id']}: {e}")

        logger.info(f"Watchdog cleaned {cleaned}/{len(stuck_jobs)} stuck jobs")
        return {"cleaned": cleaned, "found": len(stuck_jobs)}

    except Exception as e:
        logger.error(f"Watchdog task failed: {e}")
        return {"error": str(e)}


def _fail_stuck_job(job: dict):
    """Mark a single stuck job and its associated resource as failed."""
    job_id = job["id"]
    job_type = job.get("job_type")
    input_data = job.get("input_data") or {}
    error_msg = "Timed out: job exceeded maximum processing time"

    logger.warning(f"Marking stuck job {job_id} (type={job_type}) as failed")

    # Update the job itself
    supabase.table("jobs").update({
        "status": JobStatus.FAILED.value,
        "progress": 0,
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
