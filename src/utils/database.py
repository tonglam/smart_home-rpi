import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
SUPABASE_KEY: str = os.environ.get("SUPABASE_KEY")

# Initialize Supabase client
_supabase_client = None


def get_supabase_client() -> Client:
    """Get the Supabase client instance."""
    global _supabase_client
    if not _supabase_client:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must be set in environment variables"
            )
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


if not SUPABASE_URL or not SUPABASE_KEY:
    print("Critical: SUPABASE_URL or SUPABASE_KEY not found in environment variables.")


def get_latest_device_state(home_id: str, device_id: str) -> str | None:
    """Fetches the most recent 'new_state' for a given device_id under the current HOME_ID."""
    try:
        response = (
            get_supabase_client()
            .table("event_log")
            .select("new_state")
            .eq("home_id", home_id)
            .eq("device_id", device_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0].get("new_state")
        else:
            return None
    except Exception as e:
        print(f"DB query error (_get_latest_device_state for {device_id}): {e}")
        return None


def get_user_id_for_home(home_id: str) -> str | None:
    """Fetches the user_id associated with the HOME_ID from the user_homes table."""
    try:
        response = (
            get_supabase_client()
            .table("user_homes")
            .select("userId")
            .eq("homeId", home_id)
            .execute()
        )
        if response.data:
            user_id = response.data[0].get("userId")
            if user_id:
                print(f"Found userId: {user_id} for HOME_ID: {home_id}")
                return user_id
            else:
                print(
                    f"Error: 'userId' field not found in response for HOME_ID: {home_id}. Data: {response.data[0]}"
                )
                return None
        else:
            print(f"No user_id found for HOME_ID: {home_id} in user_homes table.")
            return None
    except Exception as e:
        print(f"DB query error (user_homes - get_user_id_for_home): {e}")
        return None


def insert_event(
    home_id: str,
    device_id: str,
    event_type: str,
    old_state: str | None,
    new_state: str | None,
    read: bool = False,
) -> dict:
    """Inserts an event into the event_log table.
    Automatically fetches the previous state for this device to set as old_state.
    """

    try:
        event_data = {
            "home_id": home_id,
            "device_id": device_id,
            "event_type": event_type,
            "old_state": old_state,
            "new_state": new_state,
            "read": read,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        response = get_supabase_client().table("event_log").insert(event_data).execute()
        print(f"Event inserted into event_log: {response.data}")
        return response.data[0] if response.data else {}
    except Exception as e:
        print(f"DB insert error (event_log): {e}")
        return {"error": str(e)}


def insert_alert(
    home_id: str,
    user_id: str,
    device_id: str,
    message: str,
    sent_status: bool = False,
    dismissed: bool = False,
) -> dict:
    """Inserts an alert into the alert_log table."""
    try:
        alert_data = {
            "home_id": home_id,
            "user_id": user_id,
            "device_id": device_id,
            "message": message,
            "sent_status": sent_status,
            "dismissed": dismissed,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        response = get_supabase_client().table("alert_log").insert(alert_data).execute()
        print(f"Alert inserted into alert_log: {response.data}")
        return response.data[0] if response.data else {}
    except Exception as e:
        print(f"DB insert error (alert_log): {e}")
        return {"error": str(e)}


def get_device_by_id(device_id: str) -> dict | None:
    """Fetches a device record by its device_id (primary key 'id')."""
    if not device_id:
        print("Error: device_id is required to fetch a device.")
        return None
    try:
        response = (
            get_supabase_client()
            .table("devices")
            .select("*")
            .eq("id", device_id)
            .limit(1)
            .execute()
        )
        if response.data:
            print(f"Device found with id '{device_id}': {response.data[0]}")
            return response.data[0]
        else:
            print(f"No device found with id '{device_id}'.")
            return None
    except Exception as e:
        print(f"DB query error (devices - get_device_by_id for {device_id}): {e}")
        return None


def insert_device(
    device_id: str,
    home_id: str,
    name: str,
    type: str,
    current_state: str,
    location: str | None = None,
    mode: str | None = None,
    brightness: int | None = None,
) -> dict:
    """Inserts a new device into the devices table."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        device_data = {
            "id": device_id,
            "homeId": home_id,
            "name": name,
            "type": type,
            "location": location,
            "mode": mode,
            "currentState": current_state,
            "brightness": brightness,
            "createdAt": now_iso,
            "lastUpdated": now_iso,
        }

        response = get_supabase_client().table("devices").insert(device_data).execute()
        print(f"Device inserted: {response.data}")
        return response.data[0] if response.data else {}
    except Exception as e:
        print(f"DB insert error (devices): {e}")
        return {"error": str(e)}
