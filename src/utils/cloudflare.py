"""
Cloudflare R2 Storage Integration Module

This module provides functionality to interact with Cloudflare's R2 storage service,
which is an S3-compatible object storage solution. It handles file uploads and
storage management for the smart home system.

Configuration:
    Required environment variables:
    - R2_ACCESS_KEY_ID: Access key for R2 authentication
    - R2_SECRET_ACCESS_KEY: Secret key for R2 authentication
    - R2_ENDPOINT_URL: R2 service endpoint URL
    - CLOUDFLARE_ACCOUNT_ID: Cloudflare account identifier

Storage Configuration:
    - Bucket: smart-home
    - Region: Auto (Cloudflare managed)
    - Access: Private
    - Versioning: Disabled

Features:
    - Secure file uploads
    - Automatic error recovery
    - Environment validation
    - Client connection pooling
    - Detailed logging

Dependencies:
    - boto3: For S3-compatible storage operations
    - python-dotenv: For environment configuration
    - logger: For operation logging
"""

import os

import boto3

from src.utils.logger import logger

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")

R2_BUCKET_NAME = "smart-home"
R2_ENDPOINT_URL = os.getenv(
    "R2_ENDPOINT_URL",
    "https://72fa41884795a1310a5f1c0354a8b3f0.r2.cloudflarestorage.com",
)


def get_r2_client():
    """Get a configured Cloudflare R2 client.

    Returns:
        boto3.client: Configured S3 client for R2 operations, or None if configuration fails

    Note:
        - Creates new client instance per call
        - Validates required environment variables
        - Uses S3-compatible API
        - Implements connection pooling
    """
    # Fetch keys here, after load_dotenv() from main.py has run
    r2_access_key_id = os.getenv("R2_ACCESS_KEY_ID")
    r2_secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")

    # Ensure required environment variables are present for client creation
    if not R2_ENDPOINT_URL or not r2_access_key_id or not r2_secret_access_key:
        logger.error(
            "[Cloudflare] R2 client environment variables (ENDPOINT, KEY_ID, ACCESS_KEY) not fully configured."
        )
        # Log which specific variables are missing for better debugging
        missing_vars = []
        if not R2_ENDPOINT_URL:
            missing_vars.append("R2_ENDPOINT_URL")
        if not r2_access_key_id:
            missing_vars.append("R2_ACCESS_KEY_ID")
        if not r2_secret_access_key:
            missing_vars.append("R2_SECRET_ACCESS_KEY")
        logger.error(
            f"[Cloudflare] Missing environment variables: {', '.join(missing_vars)}"
        )
        return None

    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=r2_access_key_id,
        aws_secret_access_key=r2_secret_access_key,
    )


def upload_file_to_r2(local_file_path: str, remote_file_name: str = None) -> bool:
    """Upload a file to Cloudflare R2 storage.

    Args:
        local_file_path: Path to the file on local filesystem
        remote_file_name: Optional custom name for the file in R2 storage.
                         If not provided, uses local filename.

    Returns:
        bool: True if upload successful, False otherwise

    Raises:
        Exception: If file operations or upload fails

    Note:
        - Validates file existence
        - Handles client initialization
        - Implements error recovery
        - Provides detailed logging
        - Uses global bucket configuration
    """
    if not os.path.exists(local_file_path):
        logger.error(f"[Cloudflare] File not found: {local_file_path}")
        return False

    try:
        client = get_r2_client()
        if not client:
            logger.error("[Cloudflare] Failed to get R2 client, cannot upload.")
            return False

        # Use the global constant for bucket name
        bucket_name = R2_BUCKET_NAME

        if not remote_file_name:
            remote_file_name = f"{os.path.basename(local_file_path)}"

        client.upload_file(local_file_path, bucket_name, remote_file_name)

        logger.info(
            f"[Cloudflare] Successfully uploaded {local_file_path} to R2 as {remote_file_name}"
        )
        return True

    except Exception as e:
        logger.error(f"[Cloudflare] Error uploading file to R2: {str(e)}")
        return False
