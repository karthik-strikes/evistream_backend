"""
FastAPI application entry point.
"""

# Must be first — populates os.environ before any config classes initialize
from utils.secrets_loader import load_secrets
load_secrets()

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn

from app.config import settings
from app.context import configure_logging, request_id_var
configure_logging(level=settings.LOG_LEVEL, log_file="/home/ubuntu/evistream/logs/api.log")
from app.rate_limit import limiter
from app.api.v1.router import api_router

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        incoming = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore").strip()
        rid = incoming if incoming else str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    hdrs = list(message.get("headers", []))
                    hdrs.append((b"x-request-id", rid.encode("ascii")))
                    message = {**message, "headers": hdrs}
                await send(message)
            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


class RequestLoggingMiddleware:
    """Log every HTTP request with method, path, status code, and latency."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        start = time.monotonic()
        status_code = 0

        async def send_capturing(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_capturing)
        finally:
            ms = int((time.monotonic() - start) * 1000)
            level = logging.WARNING if status_code >= 400 else logging.INFO
            if status_code >= 500:
                level = logging.ERROR
            logger.log(level, f"{method} {path} {status_code} {ms}ms")


# ASGI middleware to strip trailing slashes so routes match without 307 redirects
class StripTrailingSlashMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            path = scope["path"]
            # Strip trailing slash from API paths (but not root "/")
            if path != "/" and path.endswith("/"):
                scope["path"] = path.rstrip("/")
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Safety net: dedicated thread pool for any remaining sync LLM calls
    executor = ThreadPoolExecutor(max_workers=200, thread_name_prefix="llm_thread")
    loop = asyncio.get_event_loop()
    loop.set_default_executor(executor)

    # Verify Redis is reachable — fail fast rather than silently broken workers
    try:
        import redis as _redis
        _r = _redis.from_url(settings.REDIS_URL)
        _r.ping()
        _r.close()
        logger.info("Redis connection verified")
    except Exception as e:
        logger.warning(f"Redis not reachable at startup: {e} — background tasks will fail")

    yield

    executor.shutdown(wait=False)
    from app.database import close_supabase_client
    close_supabase_client()
    # Flush all log handlers on shutdown to ensure CloudWatch and file handlers drain
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass


# Create FastAPI app — disable docs in production
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Production-ready medical data extraction platform",
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    openapi_url="/api/openapi.json" if settings.DEBUG else None,
    redirect_slashes=False,
    lifespan=lifespan,
)

# Add rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware — strict origin whitelist, no wildcard
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
    expose_headers=["Content-Disposition", "X-Request-Id"],
    max_age=3600,
)

# Strip trailing slashes middleware (outermost — runs first on every request)
# This normalizes /api/v1/projects/ → /api/v1/projects so routes match without 307 redirects
app.add_middleware(StripTrailingSlashMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RequestLoggingMiddleware)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# Health check endpoint — no internal details in production
@app.get("/health")
async def health_check():
    """Health check endpoint for load balancer."""
    # Check DB connectivity
    db_healthy = True
    try:
        from app.database import get_supabase_client
        db = get_supabase_client()
        db.table("users").select("id", count="exact").limit(0).execute()
    except Exception as e:
        logger.warning(f"Health check DB ping failed: {e}")
        db_healthy = False

    if not db_healthy:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "db": "unreachable"}
        )

    if settings.DEBUG:
        return JSONResponse(
            content={
                "status": "healthy",
                "app": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "environment": settings.ENVIRONMENT,
                "db": "connected"
            }
        )
    return JSONResponse(content={"status": "healthy"})


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "eviStreams API"}


@app.exception_handler(404)
async def not_found_handler(request, exc):
    """Handle 404 errors."""
    return JSONResponse(
        status_code=404,
        content={"detail": "Not found"}
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    """Handle 500 errors."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8001,
        reload=settings.DEBUG
    )
