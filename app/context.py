"""
Core tracing infrastructure for end-to-end request tracing.
Three ContextVars (request_id, user_id, job_id) + logging filter + setup function.
"""

import logging
import socket
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
job_id_var: ContextVar[str] = ContextVar("job_id", default="")


class ContextFilter(logging.Filter):
    """Injects tracing IDs from ContextVars into every log record."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        record.job_id = job_id_var.get()
        return True


def configure_logging(
    level: str = "INFO",
    stream_name: str = "api/{hostname}",
    log_file: str | None = None,
) -> None:
    """
    Configure root logger with ContextFilter and tracing format.
    Attaches stdout handler always. Attaches CloudWatch handler when CLOUDWATCH_ENABLED=true.

    Args:
        stream_name: CloudWatch log stream name. "{hostname}" is replaced with socket.gethostname().
                     Workers pass "worker/{worker_name}".
    """
    fmt = (
        "%(asctime)s "
        "[%(request_id)s] "
        "[user=%(user_id)s] "
        "[job=%(job_id)s] "
        "%(name)s %(levelname)s %(message)s"
    )
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    # Handler 1: stdout (always)
    # ContextFilter on handler — runs before every emit, even for propagated records
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(ContextFilter())
    root.addHandler(stdout_handler)

    # Handler 2: file (workers only, when log_file is provided)
    if log_file:
        import os
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ContextFilter())
        root.addHandler(file_handler)

    # Suppress httpx request-level INFO logs (Supabase client spam)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Handler 2: CloudWatch (feature-gated)
    from app.config import settings  # deferred to avoid circular import at module load
    if not settings.CLOUDWATCH_ENABLED:
        return

    try:
        import watchtower
        import boto3

        hostname = socket.gethostname()
        resolved_stream = stream_name.replace("{hostname}", hostname)

        # Same credential pattern as storage_service.py
        kwargs: dict = {"region_name": settings.AWS_REGION}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        logs_client = boto3.client("logs", **kwargs)

        cw_handler = watchtower.CloudWatchLogHandler(
            boto3_client=logs_client,
            log_group_name=settings.CLOUDWATCH_LOG_GROUP,
            log_stream_name=resolved_stream,
            send_interval=settings.CLOUDWATCH_SEND_INTERVAL,
            create_log_group=True,
            create_log_stream=True,
        )
        cw_handler.setFormatter(formatter)
        cw_handler.addFilter(ContextFilter())
        root.addHandler(cw_handler)

        root.info(
            "CloudWatch logging enabled: group=%s stream=%s",
            settings.CLOUDWATCH_LOG_GROUP,
            resolved_stream,
        )

    except Exception as exc:
        # Never crash the app over logging setup
        root.warning("CloudWatch logging setup failed (stdout only): %s", exc)
