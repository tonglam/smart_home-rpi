import os

import pytest

from src.utils.cloudflare import (
    R2_ACCESS_KEY_ID,
    R2_BUCKET_NAME,
    R2_ENDPOINT_URL,
    R2_SECRET_ACCESS_KEY,
    upload_file_to_r2,
)


@pytest.fixture(scope="module")
def check_env_vars():
    """Check if required environment variables are set."""
    if (
        not R2_BUCKET_NAME
        or R2_BUCKET_NAME == "YOUR_ACTUAL_R2_BUCKET_NAME_HERE"
        or not R2_ENDPOINT_URL
        or R2_ENDPOINT_URL == "YOUR_ACTUAL_R2_ENDPOINT_URL_HERE"
        or not R2_ACCESS_KEY_ID
        or not R2_SECRET_ACCESS_KEY
    ):
        pytest.skip("R2 configuration is not properly set")


@pytest.fixture
def temp_test_file():
    """Create a temporary test file and clean it up after the test."""
    filename = "temp_test_file.txt"
    with open(filename, "w") as f:
        f.write("This is a temporary file for testing Cloudflare R2 upload.\n")
        f.write("If you see this in R2, the upload was successful!\n")
    yield filename
    if os.path.exists(filename):
        os.remove(filename)


def test_r2_upload(temp_test_file):
    """Test uploading a file to R2."""
    # Set required environment variables for the test
    os.environ["R2_BUCKET_NAME"] = "smart-home"
    os.environ["R2_ENDPOINT_URL"] = (
        "https://72fa41884795a1310a5f1c0354a8b3f0.r2.cloudflarestorage.com"
    )
    os.environ["R2_ACCESS_KEY_ID"] = "d23857958743fdc462f6c988aa11e316"
    os.environ["R2_SECRET_ACCESS_KEY"] = (
        "ef128572fe1abbe775eb23298e2e0438b8396647fb1700c1799aae1041196fe4"
    )

    upload_successful = upload_file_to_r2(temp_test_file, temp_test_file)
    assert upload_successful, "Failed to upload file to R2"
