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


# Global storage service instance
storage_service = S3StorageService()
