# Storage Architecture

## Overview

eviStream uses Amazon S3 for all file storage. This enables horizontal scaling across multiple application instances and Celery workers without requiring shared network file systems.

## Why S3

Local disk storage breaks in multi-instance deployments because:

- API servers on different machines cannot access files uploaded to another machine
- Celery workers on different servers cannot read PDFs uploaded via the API
- Shared NFS mounts are slow, operationally complex, and a single point of failure

S3 provides:

- Durable object storage accessible from any instance
- Native presigned URLs (files transfer directly between client and S3, zero API server bandwidth)
- Content-addressable storage via SHA256 hashes (built-in deduplication)
- Independent scaling of storage from compute

## Bucket Structure

**Bucket name:** `evistream-production`

**Prefixes:**

- `pdfs/` — uploaded PDF documents
- `markdown/` — processed markdown output

**Key patterns:**

```
pdfs/{project_id}/{sha256}.pdf
markdown/{project_id}/{sha256}.md
```

**Example:**

```
pdfs/3f2a1b4c-8d9e-4f0a-b1c2-d3e4f5a6b7c8/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855.pdf
markdown/3f2a1b4c-8d9e-4f0a-b1c2-d3e4f5a6b7c8/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855.md
```

## Content Hash Filenames

Files are named by their SHA256 content hash instead of UUIDs or original filenames.

Benefits:

- **Deduplication:** Uploading the same PDF twice results in the same S3 key — no duplicate storage
- **Integrity verification:** Hash can be recomputed to verify file was not corrupted
- **Deterministic keys:** Given a project_id and file content, the S3 key can always be computed

Original filenames are preserved in:

- S3 object metadata: `x-amz-meta-original-filename`
- The `documents` database table: `filename` column

## S3 Object Metadata

Every uploaded object carries metadata:

```
x-amz-meta-original-filename: research_paper.pdf
x-amz-meta-project-id: 3f2a1b4c-8d9e-4f0a-b1c2-d3e4f5a6b7c8
x-amz-meta-content-hash: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Upload Flow (Presigned URL)

Files are uploaded directly from the browser to S3. The API server never handles file bytes.

```
Browser                     API Server                  S3
  |                              |                        |
  |-- POST /upload (JSON) ------>|                        |
  |   {project_id, filename,     |                        |
  |    content_hash, file_size}  |                        |
  |                              |-- Check DB for dup --->|
  |                              |-- Create document rec  |
  |                              |-- Create job rec       |
  |                              |-- generate_presigned() |
  |<-- {presigned_url, fields, --|                        |
  |     s3_key, confirm_url}     |                        |
  |                              |                        |
  |-- PUT presigned_url (file bytes) ------------------>  |
  |<-- 200 OK ------------------------------------------ |
  |                              |                        |
  |-- POST /confirm-upload ------>|                        |
  |                              |-- object_exists() ---->|
  |                              |-- Update s3_pdf_path   |
  |                              |-- Queue Celery task    |
  |<-- {status: processing} -----|                        |
```

## Download Flow (Presigned URL)

```
Browser                     API Server                  S3
  |                              |                        |
  |-- GET /download ------------>|                        |
  |                              |-- generate_presigned() |
  |<-- {download_url, expires} --|                        |
  |                              |                        |
  |-- GET download_url --------------------------------->  |
  |<-- PDF bytes -----------------------------------------|
```

## Worker Temp File Flow

Celery workers use `/tmp` as scratch space. Files are never persisted to worker disk.

```
Celery Worker               S3
  |                          |
  |-- download_to_temp() --->|
  |   /tmp/{job_id}/source.pdf
  |                          |
  | [PDF processing]         |
  |                          |
  |-- upload_markdown() ---->|
  |   markdown/{pid}/{hash}.md
  |                          |
  | [Delete /tmp/{job_id}/]  |
```

Full flow:

1. Worker receives `document_id` and `job_id`
2. Downloads PDF from `pdfs/{project_id}/{hash}.pdf` to `/tmp/{job_id}/source.pdf`
3. Runs PDF to markdown conversion on the local temp file
4. Uploads resulting markdown string to `markdown/{project_id}/{hash}.md`
5. Updates DB `s3_markdown_path` with the S3 key
6. Deletes `/tmp/{job_id}/` directory

## Duplicate Detection

Before issuing a presigned upload URL, the API checks for an existing document with the same `(project_id, content_hash)` pair:

```sql
SELECT id FROM documents
WHERE project_id = $1 AND content_hash = $2
```

If found, the API returns HTTP 200 with `duplicate: true` and the existing document record. No new S3 upload is needed.

## Database Schema

The `documents` table includes:

```sql
content_hash TEXT,
-- Unique index prevents duplicate documents per project
UNIQUE INDEX idx_documents_project_hash ON documents(project_id, content_hash)
  WHERE content_hash IS NOT NULL
```

Other relevant columns:

- `s3_pdf_path TEXT` — S3 key for the PDF (e.g., `pdfs/{project_id}/{hash}.pdf`)
- `s3_markdown_path TEXT` — S3 key for the markdown (e.g., `markdown/{project_id}/{hash}.md`)
- `filename TEXT` — original filename as uploaded by the user
- `processing_status TEXT` — pending | processing | completed | failed

## Security

- Bucket is fully private (all public access blocked)
- All access is via presigned URLs with short expiry (uploads: 15 min, downloads: 1 hour)
- Server-side encryption enabled (AES-256)
- IAM policy follows least privilege: only `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the single bucket
- EC2 instance roles are preferred over long-lived IAM user keys
