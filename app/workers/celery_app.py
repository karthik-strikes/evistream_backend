"""
Celery application configuration for background task processing.
"""

# Must be first — populates os.environ before any config classes initialize
from utils.secrets_loader import load_secrets
load_secrets()

from celery import Celery
from celery.schedules import crontab
from app.config import settings

# Create Celery app
celery_app = Celery(
    "evistream_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.pdf_tasks",
        "app.workers.extraction_tasks",
        "app.workers.generation_tasks",
        "app.workers.watchdog_tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    # Let our configure_logging() own the root logger in worker processes
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=False,
    # Reliability: only ack task after it completes; re-queue if worker dies mid-task
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Result management: expire results after 24h and compress to save Redis memory
    result_expires=86400,
    result_compression="gzip",
)

# Task routing - routes tasks to specific queues by their registered names
celery_app.conf.task_routes = {
    # PDF processing tasks
    "process_pdf_document": {"queue": "pdf_processing"},
    "check_pdf_processor_health": {"queue": "pdf_processing"},

    # Code generation tasks
    "generate_form_code": {"queue": "code_generation"},
    "resume_after_approval": {"queue": "code_generation"},
    "resume_after_rejection": {"queue": "code_generation"},
    "check_code_generator_health": {"queue": "code_generation"},

    # Extraction tasks
    "run_extraction": {"queue": "extraction"},
    "check_extraction_service_health": {"queue": "extraction"},

    # Watchdog tasks (use default queue or specify one)
    "watchdog_cleanup_stuck_jobs": {"queue": "celery"},
}

# Beat schedule: periodic tasks
celery_app.conf.beat_schedule = {
    "cleanup-stuck-jobs-every-5-min": {
        "task": "watchdog_cleanup_stuck_jobs",
        "schedule": 300.0,  # every 5 minutes
    },
}

from celery.signals import before_task_publish, task_prerun, celeryd_after_setup
from app.context import request_id_var, user_id_var, job_id_var


@before_task_publish.connect
def inject_trace_context(headers: dict, **kwargs):
    """Fired in API process before task is sent to broker. Injects context into headers."""
    headers["x_request_id"] = request_id_var.get()
    headers["x_user_id"] = user_id_var.get()


@task_prerun.connect
def restore_trace_context(task, kwargs: dict, **kw):
    """Fired in worker process before task runs. Restores context from headers."""
    req = task.request
    request_id_var.set(getattr(req, "x_request_id", "") or "")
    user_id_var.set(getattr(req, "x_user_id", "") or "")
    jid = kwargs.get("job_id") or kwargs.get("document_id") or ""
    job_id_var.set(str(jid))


@celeryd_after_setup.connect
def setup_worker_logging(sender, instance, **kwargs):
    """
    Fired once per worker process after Celery sets up its own logging.
    Installs CloudWatch handler with a worker-specific stream name.

    sender: worker node name string, e.g. "pdf_worker@ip-10-0-1-44"
    """
    from app.context import configure_logging
    from app.config import settings

    # "pdf_worker@ip-10-0-1-44" → "worker/pdf_worker/ip-10-0-1-44"
    safe_name = sender.replace("@", "/")
    stream_name = f"worker/{safe_name}"

    # Derive log file from worker name: "extraction_worker@host" → "logs/extraction_worker.log"
    worker_name = sender.split("@")[0]
    log_file = f"/home/ubuntu/evistream/logs/{worker_name}.log"

    configure_logging(level=settings.LOG_LEVEL, stream_name=stream_name, log_file=log_file)


if __name__ == "__main__":
    celery_app.start()
