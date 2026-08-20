"""
Storage service for handling file uploads via Amazon S3.
Uses presigned URLs for direct browser-to-S3 transfers.
"""

import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError

from app.config import settings

logger = logging.getLogger(__name__)


class S3StorageService:
    """Service for S3 file storage operations."""

    def __init__(self):
        self.bucket = settings.S3_BUCKET
        self.region = settings.AWS_REGION

        # boto3 picks up credentials from env vars or instance role automatically
        kwargs = {"region_name": self.region}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

        self.s3_client = boto3.client("s3", **kwargs)

        # Validate credentials at init to fail fast with a clear message
        try:
            sts = boto3.client("sts", **kwargs)
            sts.get_caller_identity()
            logger.info("AWS credentials validated successfully")
        except (NoCredentialsError, PartialCredentialsError) as e:
            raise RuntimeError(
                f"AWS credentials are missing or incomplete: {e}. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables "
                "or configure an IAM instance role."
            )
        except ClientError as e:
            # Credentials exist but may be invalid (e.g., expired token)
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("InvalidClientTokenId", "SignatureDoesNotMatch", "ExpiredToken"):
                raise RuntimeError(
                    f"AWS credentials are invalid: {e}. "
                    "Check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
                )
            # Other ClientErrors (e.g., network issues) — log warning but don't block startup
            logger.warning(f"Could not verify AWS credentials at startup: {e}")

    def generate_presigned_upload_url(
        self,
        project_id: str,
        content_hash: str,
        original_filename: str,
        content_type: str = "application/pdf",
        expires: int = 900,
    ) -> dict:
        """
        Generate a presigned POST URL for direct browser-to-S3 upload.

        Returns:
            dict with keys: url, fields, s3_key
        """
        s3_key = f"pdfs/{project_id}/{content_hash}.pdf"
        try:
            response = self.s3_client.generate_presigned_post(
                Bucket=self.bucket,
                Key=s3_key,
                Fields={
                    "Content-Type": content_type,
                },
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", 1, settings.MAX_UPLOAD_SIZE],
                ],
                ExpiresIn=expires,
            )
            return {
                "url": response["url"],
                "fields": response["fields"],
                "s3_key": s3_key,
            }
        except ClientError as e:
            logger.error(f"Failed to generate presigned upload URL: {e}")
            raise

    def generate_presigned_download_url(
        self,
        s3_key: str,
        original_filename: str,
        expires: int = 3600,
    ) -> str:
        """
        Generate a presigned GET URL for direct browser-to-S3 download.

        Returns:
            Presigned URL string
        """
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": s3_key,
                    "ResponseContentDisposition": f'inline; filename="{original_filename}"',
                },
                ExpiresIn=expires,
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate presigned download URL: {e}")
            raise

    def upload_markdown(self, content: str, project_id: str, content_hash: str) -> str:
        """
        Upload markdown content string to S3.

        Returns:
            S3 key of the uploaded object
        """
        s3_key = f"markdown/{project_id}/{content_hash}.md"
        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=content.encode("utf-8"),
                ContentType="text/markdown",
                Metadata={
                    "project-id": project_id,
                    "content-hash": content_hash,
                },
            )
            logger.info(f"Uploaded markdown to s3://{self.bucket}/{s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"Failed to upload markdown: {e}")
            raise

    def upload_pdf(self, pdf_bytes: bytes, project_id: str, content_hash: str) -> str:
        """
        Upload raw PDF bytes directly to the SAME key a browser presigned-POST
        upload lands on (pdfs/{project_id}/{content_hash}.pdf — see
        generate_presigned_upload_url above). Used for server-side PDF
        acquisition where we already have the bytes in memory and skip the
        presigned-POST dance entirely: PubMed imports that found a free copy
        via Unpaywall, and the manual "attach PDF" fallback. Once this is
        called, process_pdf_document can run on the document exactly as it
        would for a normal upload.
        """
        s3_key = f"pdfs/{project_id}/{content_hash}.pdf"
        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=pdf_bytes,
                ContentType="application/pdf",
                Metadata={
                    "project-id": project_id,
                    "content-hash": content_hash,
                    "variant": "original",
                },
            )
            logger.info(f"Uploaded PDF to s3://{self.bucket}/{s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"Failed to upload PDF: {e}")
            raise

    def upload_clean_pdf(self, pdf_bytes: bytes, project_id: str, content_hash: str) -> str:
        """
        Upload the annotation-stripped PDF (output of pdf_cleaner.clean_pdf_bytes)
        to S3 under a separate prefix so the original is still recoverable.
        Returns the S3 key.
        """
        s3_key = f"clean-pdfs/{project_id}/{content_hash}.pdf"
        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=pdf_bytes,
                ContentType="application/pdf",
                Metadata={
                    "project-id": project_id,
                    "content-hash": content_hash,
                    "variant": "clean",
                },
            )
            logger.info(f"Uploaded clean PDF to s3://{self.bucket}/{s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"Failed to upload clean PDF: {e}")
            raise

    def upload_blocks(self, blocks_json: dict, project_id: str, content_hash: str) -> str:
        """
        Upload Datalab block-level JSON (per-block bbox, page_id, type, text) to S3.
        Sidecar to the markdown file — same content_hash, different prefix.
        """
        import json as _json
        s3_key = f"blocks/{project_id}/{content_hash}.json"
        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=_json.dumps(blocks_json).encode("utf-8"),
                ContentType="application/json",
                Metadata={
                    "project-id": project_id,
                    "content-hash": content_hash,
                },
            )
            logger.info(f"Uploaded blocks JSON to s3://{self.bucket}/{s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"Failed to upload blocks JSON: {e}")
            raise

    def upload_import_file(self, file_bytes: bytes, project_id: str, token: str, ext: str = "enlx") -> str:
        """Stash a raw uploaded import archive (e.g. an EndNote .enlx) in S3 so a
        Celery worker on another host can pull it down and parse it. The object
        is temporary — the import task deletes it once parsing is done."""
        s3_key = f"imports/{project_id}/{token}.{ext}"
        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=file_bytes,
                ContentType="application/zip",
                Metadata={"project-id": project_id},
            )
            logger.info(f"Uploaded import file to s3://{self.bucket}/{s3_key}")
            return s3_key
        except ClientError as e:
            logger.error(f"Failed to upload import file: {e}")
            raise

    def download_to_temp(self, s3_key: str, local_path: str) -> str:
        """
        Download an S3 object to a local file path.

        Returns:
            The local_path it was written to
        """
        try:
            self.s3_client.download_file(self.bucket, s3_key, local_path)
            logger.info(f"Downloaded s3://{self.bucket}/{s3_key} to {local_path}")
            return local_path
        except ClientError as e:
            logger.error(f"Failed to download {s3_key}: {e}")
            raise

    def delete_object(self, s3_key: str) -> bool:
        """
        Delete an object from S3.

        Returns:
            True on success, False on error
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
            logger.info(f"Deleted s3://{self.bucket}/{s3_key}")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete {s3_key}: {e}")
            return False

    def object_exists(self, s3_key: str) -> bool:
        """
        Check whether an S3 object exists via HEAD request.

        Returns:
            True if object exists, False otherwise
        """
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            logger.error(f"Error checking existence of {s3_key}: {e}")
            raise


    # ------------------------------------------------------------------ images
    # Datalab returns the figures it extracted from the PDF as a
    # {filename: base64} map alongside the markdown, and the markdown itself
    # references them by bare filename (`![](<hash>_img.jpg)`). Store one
    # object per image under a per-document prefix so those bare names resolve
    # 1:1 against the prefix and the bytes are addressable on their own.
    # Before this, the bytes survived only as base64 buried inside the blocks
    # sidecar, which nothing could render and every markdown image link dangled.
    _IMAGE_CONTENT_TYPES = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    def image_prefix(self, project_id: str, content_hash: str) -> str:
        """S3 prefix holding every extracted image for one document."""
        return f"images/{project_id}/{content_hash}/"

    def upload_images(
        self,
        images: dict,
        project_id: str,
        content_hash: str,
        overwrite: bool = False,
    ) -> list:
        """Upload Datalab-extracted images to S3, one object per image.

        `images` is Datalab's {filename: base64-encoded-bytes} map, taken
        straight off the /convert response. Each entry lands at
        images/{project_id}/{content_hash}/{filename}.

        Best-effort per image: a malformed, unsafe or undecodable entry is
        logged and skipped rather than failing the document, because extraction
        is text-only — a missing figure must never invalidate a parse whose
        markdown is already good.

        Idempotent: an image already present is counted, not re-uploaded,
        unless `overwrite=True`.

        Returns the list of S3 keys now holding this document's images.
        """
        import base64
        import os as _os

        if not images:
            return []

        prefix = self.image_prefix(project_id, content_hash)
        written = []
        skipped = 0
        for filename, b64 in images.items():
            name = str(filename)
            # Datalab names images by content hash, but the value ends up in an
            # S3 key — refuse anything with a path component rather than let it
            # write outside this document's prefix.
            safe_name = _os.path.basename(name)
            if not safe_name or safe_name != name or safe_name.startswith("."):
                logger.warning(f"Skipping image with unsafe name {name!r} for {content_hash}")
                skipped += 1
                continue
            if not isinstance(b64, str):
                logger.warning(f"Skipping non-string image payload {safe_name} for {content_hash}")
                skipped += 1
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except Exception as decode_err:
                logger.warning(
                    f"Skipping undecodable image {safe_name} for {content_hash}: {decode_err}"
                )
                skipped += 1
                continue
            if not raw:
                skipped += 1
                continue

            s3_key = f"{prefix}{safe_name}"
            ext = _os.path.splitext(safe_name)[1].lower()
            try:
                if not overwrite and self.object_exists(s3_key):
                    written.append(s3_key)
                    continue
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=s3_key,
                    Body=raw,
                    ContentType=self._IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream"),
                    Metadata={
                        "project-id": project_id,
                        "content-hash": content_hash,
                    },
                )
                written.append(s3_key)
            except ClientError as e:
                logger.error(f"Failed to upload image {s3_key}: {e}")
                skipped += 1

        logger.info(
            f"Stored {len(written)} image(s) under s3://{self.bucket}/{prefix}"
            + (f" ({skipped} skipped)" if skipped else "")
        )
        return written

    def list_images(self, project_id: str, content_hash: str) -> list:
        """List the S3 keys of every stored image for one document."""
        prefix = self.image_prefix(project_id, content_hash)
        keys = []
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
        except ClientError as e:
            logger.error(f"Failed to list images under {prefix}: {e}")
        return keys

    def delete_images(self, project_id: str, content_hash: str) -> int:
        """Delete every stored image for one document. Returns the count deleted."""
        deleted = 0
        for key in self.list_images(project_id, content_hash):
            if self.delete_object(key):
                deleted += 1
        return deleted


# Global storage service instance
storage_service = S3StorageService()
