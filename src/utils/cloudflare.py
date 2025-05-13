import os

import boto3

from src.utils.logger import logger

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")


R2_BUCKET_NAME = "smart-home"
R2_ENDPOINT_URL = "https://72fa41884795a1310a5f1c0354a8b3f0.r2.cloudflarestorage.com"


def get_r2_client():
    """Get the Cloudflare R2 client."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("R2_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
    )


def upload_file_to_r2(local_file_path: str, remote_file_name: str = None) -> bool:
    """Upload a file to Cloudflare R2 storage."""
    if not os.path.exists(local_file_path):
        logger.error(f"[Cloudflare] File not found: {local_file_path}")
        return False

    try:
        client = get_r2_client()
        bucket_name = os.environ.get("R2_BUCKET_NAME")

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
