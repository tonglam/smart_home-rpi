import threading
import time
from typing import Optional

from gpiozero import Button

from src.utils.database import (
    get_device_by_id,
    insert_alert,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "door_sensor_01"
DEVICE_NAME = "Door Reed Switch"
DEVICE_TYPE = "reed_switch"

# GPIO pin configuration
REED_PIN = 17

# Global state
_reed_button: Optional[Button] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_current_home_id: Optional[str] = None
_current_user_id: Optional[str] = None


def _on_door_opened_callback():
    logger.info(f"[{DEVICE_NAME}] Door opened detected.")
    update_device_state(DEVICE_ID, "open")
    insert_event(
        home_id=_current_home_id,
        device_id=DEVICE_ID,
        event_type="door_opened",
        old_state="closed",
        new_state="open",
    )
    insert_alert(
        home_id=_current_home_id,
        user_id=_current_user_id,
        device_id=DEVICE_ID,
        message="Door opened.",
    )


def _on_door_closed_callback():
    logger.info(f"[{DEVICE_NAME}] Door closed detected.")
    update_device_state(DEVICE_ID, "closed")
    insert_event(
        home_id=_current_home_id,
        device_id=DEVICE_ID,
        event_type="door_closed",
        old_state="open",
        new_state="closed",
    )
    insert_alert(
        home_id=_current_home_id,
        user_id=_current_user_id,
        device_id=DEVICE_ID,
        message="Door closed.",
    )


def _reed_monitoring_loop():
    logger.info(f"[{DEVICE_NAME}] Reed switch monitoring loop started.")
    try:
        while _is_monitoring.is_set():
            time.sleep(0.1)
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error in monitoring loop: {e}")
    finally:
        logger.info(f"[{DEVICE_NAME}] Reed switch monitoring loop ended.")


def start_reed_monitoring(home_id: str, user_id: str) -> None:
    global _reed_button, _monitoring_thread, _is_monitoring, _current_home_id, _current_user_id

    logger.info(
        f"[{DEVICE_NAME}] Starting monitoring for HOME_ID: {home_id}, USER_ID: {user_id}"
    )
    _current_home_id = home_id
    _current_user_id = user_id

    try:
        _reed_button = Button(REED_PIN)
        _reed_button.when_pressed = _on_door_closed_callback
        _reed_button.when_released = _on_door_opened_callback

        # Register device if not present
        device = get_device_by_id(DEVICE_ID)
        if not device:
            logger.info(f"[{DEVICE_NAME}] Device not found in DB. Registering...")
            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state="closed" if _reed_button.is_pressed else "open",
            )

        # Start monitoring thread
        _is_monitoring.set()
        _monitoring_thread = threading.Thread(target=_reed_monitoring_loop)
        _monitoring_thread.start()
        logger.info(f"[{DEVICE_NAME}] Monitoring started.")
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting monitoring: {e}")


def stop_reed_monitoring() -> None:
    global _is_monitoring, _monitoring_thread, _reed_button
    logger.info(f"[{DEVICE_NAME}] Stopping monitoring...")
    _is_monitoring.clear()
    if _monitoring_thread and _monitoring_thread.is_alive():
        _monitoring_thread.join(timeout=2.0)
        if _monitoring_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Monitoring thread did not finish in time.")
    # Clean up GPIO resource
    if _reed_button is not None:
        _reed_button.close()
        _reed_button = None
    logger.info(f"[{DEVICE_NAME}] Monitoring stopped.")
