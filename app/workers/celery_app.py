"""
Celery application configuration for background task processing.
"""

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

if __name__ == "__main__":
    celery_app.start()
