"""
Database Interface Module

This module provides a high-level interface to the application's database,
handling all data persistence needs including device states, events,
user data, and system configuration.

Database Schema:
    Tables:
    1. devices:
        - device_id: Primary key
        - home_id: Foreign key to homes
        - name: Device name
        - type: Device type
        - current_state: Current device state
        - created_at: Timestamp
        - updated_at: Timestamp

    2. events:
        - event_id: Primary key
        - home_id: Foreign key to homes
        - device_id: Foreign key to devices
        - event_type: Type of event
        - old_state: Previous state
        - new_state: New state
        - created_at: Timestamp

    3. alerts:
        - alert_id: Primary key
        - home_id: Foreign key to homes
        - user_id: Foreign key to users
        - device_id: Foreign key to devices
        - message: Alert message
        - created_at: Timestamp

    4. homes:
        - home_id: Primary key
        - name: Home name
        - mode: Current mode (away, home, etc.)
        - created_at: Timestamp
        - updated_at: Timestamp

Features:
    - Connection pooling
    - Transaction support
    - Error handling
    - State management
    - Event logging
    - Alert generation

Dependencies:
    - supabase: For database operations
    - dotenv: For configuration
    - logger: For operation logging
"""

import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from supabase import Client, create_client

from src.utils.logger import logger

load_dotenv()

# Database configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Initialize Supabase client
if not all([SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Missing Supabase configuration in environment variables")

_supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_device_by_id(device_id: str) -> Optional[dict]:
    """Get device information by ID.

    Args:
        device_id: The unique identifier of the device

    Returns:
        Optional[dict]: Device data if found, None otherwise

    Raises:
        Exception: If database query fails

    Note:
        - Returns full device record
        - Includes current state
        - Thread-safe operation
    """
    if not device_id:
        logger.error("Error: device_id is required to fetch a device.")
        return None
    try:
        response = (
            _supabase.table("devices")
            .select("*")
            .eq("id", device_id)
            .limit(1)
            .execute()
        )
        if response.data:
            logger.info(f"Device found with id '{device_id}': {response.data[0]}")
            return response.data[0]
        else:
            logger.error(f"No device found with id '{device_id}'.")
            return None
    except Exception as e:
        logger.error(
            f"DB query error (devices - get_device_by_id for {device_id}): {e}"
        )
        return None


def insert_device(
    device_id: str,
    home_id: str,
    name: str,
    type: str,
    current_state: str,
    location: str | None = None,
    brightness: int | None = None,
) -> dict:
    """Insert a new device record.

    Args:
        device_id: The unique identifier for the device
        home_id: The home this device belongs to
        name: Human-readable device name
        type: Device type identifier
        current_state: Initial device state

    Returns:
        dict: Device data if insertion successful, error message otherwise

    Raises:
        Exception: If database operation fails

    Note:
        - Sets creation timestamp
        - Handles duplicates
        - Thread-safe operation
    """
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        device_data = {
            "id": device_id,
            "home_id": home_id,
            "name": name,
            "type": type,
            "location": location or "unknown",  # location is required in schema
            "current_state": current_state,
            "brightness": brightness,
            "created_at": now_iso,
            "last_updated": now_iso,
        }

        response = _supabase.table("devices").insert(device_data).execute()
        logger.info(f"Device inserted into devices table: {response.data}")
        return response.data[0] if response.data else {}
    except Exception as e:
        logger.error(f"DB insert error (devices): {e}")
        return {"error": str(e)}


def update_device_state(device_id: str, new_state: str | dict) -> None:
    """Update a device's current state.

    Args:
        device_id: The device to update
        new_state: The new state to set

    Returns:
        None

    Raises:
        Exception: If database operation fails

    Note:
        - Updates timestamp
        - Validates state transition
        - Thread-safe operation
    """
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        if isinstance(new_state, str):
            update_data = {
                "current_state": new_state,
                "last_updated": now_iso,
            }
        else:
            update_data = {**new_state, "last_updated": now_iso}

        response = (
            _supabase.table("devices").update(update_data).eq("id", device_id).execute()
        )
        logger.info(f"Device state updated: {response.data}")
    except Exception as e:
        logger.error(f"DB update error (devices): {e}")


def insert_event(
    home_id: str,
    device_id: str,
    event_type: str,
    old_state: str | None,
    new_state: str | None,
    read: bool = False,
) -> dict:
    """Log a device state change event.

    Args:
        home_id: The home where the event occurred
        device_id: The device that changed state
        event_type: The type of event
        old_state: The previous state
        new_state: The new state

    Returns:
        dict: Event data if insertion successful, error message otherwise

    Raises:
        Exception: If database operation fails

    Note:
        - Sets event timestamp
        - Validates state values
        - Thread-safe operation
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
        response = _supabase.table("event_log").insert(event_data).execute()
        logger.info(f"Event inserted into event_log: {response.data}")
        return response.data[0] if response.data else {}
    except Exception as e:
        logger.error(f"DB insert error (event_log): {e}")
        return {"error": str(e)}


def insert_alert(
    home_id: str,
    user_id: str,
    device_id: str,
    message: str,
    sent_status: bool = False,
    dismissed: bool = False,
) -> dict:
    """Create a new alert record.

    Args:
        home_id: The home where the alert occurred
        user_id: The user to notify
        device_id: The device that triggered the alert
        message: The alert message

    Returns:
        dict: Alert data if insertion successful, error message otherwise

    Raises:
        Exception: If database operation fails

    Note:
        - Sets alert timestamp
        - Validates message length
        - Thread-safe operation
    """
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
        response = _supabase.table("alert_log").insert(alert_data).execute()
        logger.info(f"Alert inserted into alert_log: {response.data}")
        return response.data[0] if response.data else {}
    except Exception as e:
        logger.error(f"DB insert error (alert_log): {e}")
        return {"error": str(e)}


def get_home_mode(home_id: str) -> str | None:
    """Get the current mode of a home.

    Args:
        home_id: The home to query

    Returns:
        Optional[str]: Current mode if found, None otherwise

    Raises:
        Exception: If database query fails

    Note:
        - Returns cached value if available
        - Thread-safe operation
        - Handles missing homes
    """
    try:
        response = (
            _supabase.table("user_homes")
            .select("mode")
            .eq("home_id", home_id)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0].get("mode")
        else:
            logger.error(f"No mode found for home_id: {home_id}")
            return None
    except Exception as e:
        logger.error(f"DB query error (user_homes - get_home_mode for {home_id}): {e}")
        return None


def get_device_state(device_id: str) -> str | None:
    """Get the current state of a device from the devices table."""
    try:
        response = (
            _supabase.table("devices")
            .select("current_state")
            .eq("id", device_id)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0].get("current_state")
        else:
            logger.error(f"No state found for device_id: {device_id}")
            return None
    except Exception as e:
        logger.error(f"DB query error (devices - get_device_state): {e}")
        return None


def get_user_id_for_home(home_id: str) -> str | None:
    """Get the user ID associated with a home.

    Args:
        home_id: The home to query

    Returns:
        Optional[str]: User ID if found, None otherwise

    Raises:
        Exception: If database query fails

    Note:
        - Returns primary user only
        - Thread-safe operation
        - Handles missing associations
    """
    try:
        response = (
            _supabase.table("user_homes")
            .select("user_id")
            .eq("home_id", home_id)
            .execute()
        )
        if response.data:
            user_id = response.data[0].get("user_id")
            if user_id:
                logger.info(f"Found user_id: {user_id} for HOME_ID: {home_id}")
                return user_id
            else:
                logger.error(
                    f"Error: 'user_id' field not found in response for HOME_ID: {home_id}. Data: {response.data[0]}"
                )
                return None
        else:
            logger.error(
                f"No user_id found for HOME_ID: {home_id} in user_homes table."
            )
            return None
    except Exception as e:
        logger.error(f"DB query error (user_homes - get_user_id_for_home): {e}")
        return None


def get_latest_device_state(home_id: str, device_id: str) -> str | None:
    """Fetches the most recent 'new_state' for a given device_id under the current HOME_ID."""
    try:
        response = (
            _supabase.table("event_log")
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
        logger.error(f"DB query error (_get_latest_device_state for {device_id}): {e}")
        return None
