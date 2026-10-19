"""Import and exercise the API offline, without accessing a real account."""
import os
import asyncio
import socket
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update({
    "PYTHON_DOTENV_DISABLED": "1",
    "AWS_EC2_METADATA_DISABLED": "true",
    "SECRET_KEY": "offline-release-verification-" + "x" * 48,
    "SUPABASE_URL": "http://127.0.0.1:1",
    "SUPABASE_KEY": "offline-test",
    "SUPABASE_SERVICE_KEY": "offline-test",
    "AWS_ACCESS_KEY_ID": "offline-test",
    "AWS_SECRET_ACCESS_KEY": "offline-test",
    "DATALAB_API_KEY": "offline-test",
    "S3_BUCKET": "offline-test",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "REDIS_PORT": "1",
    "DEBUG": "true",
    "DEMO_MODE_ENABLED": "false",
    "DSPY_CACHEDIR": str(ROOT / "cache" / "smoke-dspy"),
})


def deny_network(*args, **kwargs):
    raise AssertionError("Network access is forbidden in the offline smoke test")


def fake_aws(client, operation, params):
    if operation == "GetCallerIdentity":
        return {"Account": "000000000000", "UserId": "offline-test"}
    raise AssertionError(f"Unexpected AWS call: {operation}")


with patch.object(socket.socket, "connect", deny_network), patch(
    "botocore.client.BaseClient._make_api_call", fake_aws
):
    import httpx
    from app.main import app
    # Workers have additional lazy imports that API routing alone does not cover.
    from app.workers import pdf_tasks, generation_tasks, extraction_tasks, watchdog_tasks

    # No lifespan: Redis is deliberately unavailable. Test HTTP routing/auth only.
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/health")).status_code == 200
            assert (await client.get("/api/v1/projects")).status_code in {401, 403}
            assert (await client.post("/api/v1/auth/demo")).status_code in {403, 404}
            spec = await client.get("/api/openapi.json")
            assert spec.status_code == 200
            paths = spec.json()["paths"]
            for area in ["forms", "documents", "extractions", "assignments", "adjudication", "results"]:
                assert any(p.startswith(f"/api/v1/{area}") for p in paths), area
            assert not any(p.startswith("/api/v1/admin") for p in paths)
            print(f"PASS: API import, {len(paths)} documented paths, auth required, shared demo disabled", flush=True)
    asyncio.run(check())
