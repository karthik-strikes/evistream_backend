# EviStreams — research backend

Human-in-the-loop extraction for systematic reviews: define structured fields,
approve the extraction plan, calibrate on a pilot, inspect supporting passages,
and resolve two reviewers' entries into an adjudicated export.

This is a curated public snapshot based on **11 July 2026**, source commit
`7530ee53fbf9e6d60c454744364b551c62607b50` (the latest available backend commit
before 25 July 2026). Portability fixes and release documentation were added on
19 October 2026. It is a historical research implementation, not the current
hosted deployment. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for its scope.

- [Paper](https://arxiv.org/abs/2609.27418)
- [Matching frontend](https://github.com/karthik-strikes/evistream_frontend)
- [Evaluation code and reference artifacts](eval/README.md)

## Run locally

Use Python 3.11 and your own Supabase project, S3 bucket, Redis, Datalab key,
and model-provider credentials. The API, workers, and frontend must use the
same database and configuration. These services are not bundled accounts.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt -r requirements.txt
cp .env.example .env
```

Fill in `.env`, including two generated signing secrets (`openssl rand -hex 32`),
your database credentials, bucket, and API keys. Keep the service-role key on the
backend. Follow [database setup](docs/DATABASE.md) for a new development database.
For S3 uploads, allow POST/GET from your frontend origin in the bucket CORS rules.

Start Redis and the API in separate terminals:

```bash
redis-server --port 6380
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Start the workers from this same directory with the same environment:

```bash
celery -A app.workers.celery_app:celery_app worker --loglevel=info --concurrency=2 -Q pdf_processing,code_generation,extraction,celery
```

Open the matching frontend, register accounts, create a project, and add the
reviewers through project membership. API documentation is at
`http://localhost:8001/api/docs` when `DEBUG=true`. The hosted shared-demo account
is disabled in this release; local users sign in with their own accounts.

Model defaults and provider routing are in `config/models.py`. This historical
configuration is retained; choose models available to your own provider account.

## Source map

| Directory | Responsibility |
| --- | --- |
| `app/` | FastAPI, permissions, forms, uploads, pilot, review, adjudication, jobs |
| `core/generators/` | Structured decomposition, approval, field specification |
| `dspy_components/`, `schemas/` | Runtime extraction and table decomposition |
| `pdf_processor/`, `utils/` | Ingestion, evidence linking, caching, provider adapters |
| `eval/` | Independent field scorer, four-corpus annotations/predictions, ablations |
| `database_schema.sql`, `migrations/` | Historical database definitions |

The Apache-2.0 license covers the project code. See [evaluation data notes](eval/DATA.md)
for the scope and provenance of research artifacts and third-party source material.
