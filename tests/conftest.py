"""
Shared pytest setup. Loads secrets (AWS Secrets Manager in production, or
backend/.env as a local fallback) before any test module imports
app.config-dependent modules — mirrors the load_secrets() call already at the
top of app/main.py, app/workers/celery_app.py, and repair_decomposition.py.
Without this, importing anything under app.* (e.g. app.dependencies,
app.services.cache_service) raises a pydantic Settings ValidationError,
since Settings() reads os.environ directly and nothing else populates it
before test collection.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.secrets_loader import load_secrets  # noqa: E402

load_secrets()
