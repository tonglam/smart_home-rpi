import os

import pytest

from src.utils.database import get_device_by_id, get_supabase_client


@pytest.fixture(scope="module")
def check_env_vars():
    """Check if required environment variables are set."""
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
        pytest.skip("SUPABASE_URL or SUPABASE_KEY environment variables are not set")


def test_get_nonexistent_device():
    """Test fetching a non-existent device."""
    test_device_id_non_existent = "device_id_that_does_not_exist_12345"
    device_info = get_device_by_id(test_device_id_non_existent)
    assert device_info is None or (isinstance(device_info, dict) and not device_info)


def test_database_connection():
    """Test basic database connectivity."""
    response = (
        get_supabase_client()
        .table("devices")
        .select("id", count="exact")
        .limit(0)
        .execute()
    )
    assert hasattr(response, "data")
    assert isinstance(response.data, list)
    assert hasattr(response, "count")
    assert isinstance(response.count, int)


def run_db_test():
    """
    Runs a simple test to interact with the database using database utils.
    """
    print("--- Starting Database Test ---")

    # Test 1: Attempt to get a device by a non-existent ID
    test_device_id_non_existent = "device_id_that_does_not_exist_12345"
    print(
        f"\n[TestDB] Attempting to fetch a non-existent device with ID: '{test_device_id_non_existent}'..."
    )
    try:
        device_info = get_device_by_id(test_device_id_non_existent)
        if device_info is None:
            # This is the expected outcome for a non-existent device if the function handles it gracefully
            print(
                f"[TestDB] SUCCESS: Correctly received no data for non-existent device ID '{test_device_id_non_existent}'. (This tests the function's behavior for missing data)."
            )
        elif (
            isinstance(device_info, dict) and not device_info
        ):  # Empty dict might be returned on error by some utils
            print(
                f"[TestDB] SUCCESS: Received an empty response for non-existent device ID '{test_device_id_non_existent}', function may return empty dict on no find or error."
            )
        else:
            # This would be unexpected for a truly non-existent ID unless it was created elsewhere
            print(
                f"[TestDB] UNEXPECTED: Found device data for supposedly non-existent ID '{test_device_id_non_existent}': {device_info}"
            )
            print(
                "           This part of the test assumes the ID does not exist. If it does, this is not an error with the DB connection itself."
            )

    except Exception as e:
        print(
            f"[TestDB] FAILED: An error occurred while trying to fetch device '{test_device_id_non_existent}': {e}"
        )
        print(
            "          This could indicate a problem with the database connection, credentials, or the query itself."
        )
        return  # Stop further tests if this basic one fails catastrophically

    # Test 2: Try a generic SELECT 1 using supabase.rpc (if such an RPC exists or can be made)
    # Or, more simply, try to insert and then immediately retrieve a dummy device if permissions allow.
    # For now, let's rely on the fact that if get_device_by_id communicates without throwing an auth error,
    # the connection is likely fine.
    # A more robust direct test would be:
    print(
        f"\n[TestDB] Attempting a direct simple query to check connection (e.g., SELECT 1)..."
    )
    try:
        # Supabase doesn't have a direct `supabase.sql("SELECT 1").execute()` like some ORMs.
        # A common way to test a connection is to query a small, known table or a system catalog if accessible.
        # Let's try to count items in 'devices' table. This also tests if the table exists.
        # The 'devices' table is used by get_device_by_id, so if that worked (even if no device found),
        # the connection and table access is implicitly tested.
        # For a more direct test of SELECT ability without relying on specific data:
        response = (
            get_supabase_client()
            .table("devices")
            .select("id", count="exact")
            .limit(0)
            .execute()
        )

        # The response for a count query typically looks like: APIResponse(data=[], count=X, status_code=200)
        if hasattr(response, "count") and response.count is not None:
            print(
                f"[TestDB] SUCCESS: Able to execute a count query on 'devices' table. Number of devices: {response.count}."
            )
            print(
                f"           This indicates the database connection is working and the 'devices' table is accessible."
            )
        elif response.data is not None and response.status_code in [
            200,
            201,
            204,
        ]:  # Check for successful HTTP status too
            print(
                f"[TestDB] SUCCESS: Query to 'devices' table executed with status {response.status_code} and data: {response.data}. Connection appears to be working."
            )
        else:
            print(
                f"[TestDB] FAILED or UNEXPECTED RESPONSE: Direct query test to 'devices' table gave response: data={response.data}, count={getattr(response, 'count', 'N/A')}, status={response.status_code}"
            )
            print(f"           Full response object: {response}")

    except Exception as e:
        print(
            f"[TestDB] FAILED: An error occurred during direct simple query test: {e}"
        )
        print(
            "          This strongly indicates a problem with the database connection, credentials, or table access permissions."
        )

    print("\n--- Database Test Finished ---")


if __name__ == "__main__":
    # database.py already prints a warning if SUPABASE_URL or SUPABASE_KEY are missing
    # We add another check here for clarity before running the test.
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
        print("ERROR: SUPABASE_URL or SUPABASE_KEY environment variables are not set.")
        print(
            "Please set them (e.g., in your .env file if you use one and load_dotenv() is called)."
        )
        print("Database test cannot proceed without these.")
    else:
        run_db_test()
