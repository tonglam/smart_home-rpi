import threading
import time
from typing import Optional

from gpiozero import InputDevice

from src.utils.database import (
    get_device_by_id,
    get_home_mode,
    get_latest_device_state,
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
REED_PIN = 21

# Global state
_reed_device: Optional[InputDevice] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_state: Optional[str] = None
_last_event_time: float = 0.0
EVENT_COOLDOWN = 1.0  # Seconds


def _on_door_opened_logic(
    home_id: str, user_id: str, old_state_from_loop: Optional[str]
):
    """Handles logic when the door transitions to an open state."""
    logger.info(f"[{DEVICE_NAME}] Door opened detected.")
    update_device_state(DEVICE_ID, "open")

    # Use the state passed from the loop as the definitive old_state
    actual_old_state = (
        old_state_from_loop
        if old_state_from_loop is not None
        else get_latest_device_state(home_id, DEVICE_ID) or "unknown"
    )

    insert_event(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="door_opened",
        old_state=actual_old_state,
        new_state="open",
    )

    home_mode = get_home_mode(home_id)
    if home_mode == "away":
        alert_message = "Security Alert: Door opened while home is in away mode!"
        logger.warning(f"[{DEVICE_NAME}] {alert_message}")
        if user_id:  # Ensure user_id is present before inserting alert
            insert_alert(
                home_id=home_id,
                user_id=user_id,
                device_id=DEVICE_ID,
                message=alert_message,
            )
        else:
            logger.error(
                f"[{DEVICE_NAME}] Cannot send alert, user_id is None for home_id {home_id}"
            )


def _on_door_closed_logic(
    home_id: str, user_id: str, old_state_from_loop: Optional[str]
):
    """Handles logic when the door transitions to a closed state."""
    logger.info(f"[{DEVICE_NAME}] Door closed detected.")
    update_device_state(DEVICE_ID, "closed")

    actual_old_state = (
        old_state_from_loop
        if old_state_from_loop is not None
        else get_latest_device_state(home_id, DEVICE_ID) or "unknown"
    )

    insert_event(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="door_closed",
        old_state=actual_old_state,
        new_state="closed",
    )


def _reed_monitoring_loop(home_id: str, user_id: Optional[str]):  # user_id can be None
    """Monitors the reed switch state by polling the GPIO pin."""
    global _last_state, _last_event_time
    logger.info(
        f"[{DEVICE_NAME}] Reed switch monitoring loop started for HOME_ID: {home_id}."
    )

    # Initialize _last_state based on current sensor reading or database
    if _reed_device:
        pin_is_low_init = not _reed_device.value  # active_low with pull_up=True
        _last_state = "closed" if pin_is_low_init else "open"
        logger.info(f"[{DEVICE_NAME}] Initial polled state: {_last_state}")
    else:  # Fallback if _reed_device is somehow not set, though start_reed_monitoring should ensure it
        _last_state = get_latest_device_state(home_id, DEVICE_ID) or "unknown"
        logger.warning(
            f"[{DEVICE_NAME}] Initial state from DB (fallback): {_last_state}"
        )

    try:
        while _is_monitoring.is_set():
            if _reed_device is None:
                logger.error(
                    f"[{DEVICE_NAME}] Reed device not initialized. Stopping loop."
                )
                break

            pin_is_low = not _reed_device.value
            current_door_state = "closed" if pin_is_low else "open"

            if current_door_state != _last_state:
                current_time = time.time()
                if (current_time - _last_event_time) >= EVENT_COOLDOWN:
                    logger.info(
                        f"[{DEVICE_NAME}] State change: {_last_state} -> {current_door_state}"
                    )
                    if current_door_state == "open":
                        _on_door_opened_logic(home_id, user_id, _last_state)
                    else:
                        _on_door_closed_logic(home_id, user_id, _last_state)
                    _last_event_time = current_time
                else:
                    logger.debug(
                        f"[{DEVICE_NAME}] State change from {_last_state} to {current_door_state} "
                        f"suppressed due to cooldown. Last event: {_last_event_time:.2f}, Current: {current_time:.2f}"
                    )
                _last_state = current_door_state
            time.sleep(0.1)  # Polling interval
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error in monitoring loop: {e}", exc_info=True)
    finally:
        logger.info(f"[{DEVICE_NAME}] Reed switch monitoring loop ended.")


def start_reed_monitoring(
    home_id: str, user_id: Optional[str]
) -> None:  # user_id can be None
    global _reed_device, _monitoring_thread, _is_monitoring, _last_state, _last_event_time

    if _is_monitoring.is_set():
        logger.info(
            f"[{DEVICE_NAME}] Monitoring is already running. Will not start again."
        )
        return

    logger.info(
        f"[{DEVICE_NAME}] Starting monitoring for HOME_ID: {home_id}"
        f"{f', USER_ID: {user_id}' if user_id else ''}"
    )

    try:
        # pull_up=True means pin is HIGH when switch is open, LOW when closed.
        _reed_device = InputDevice(REED_PIN, pull_up=True)
        _last_event_time = 0  # Reset cooldown timer on start

        # Determine initial state for registration
        # pin_is_low_initial = not _reed_device.value
        # initial_state_for_db = "closed" if pin_is_low_initial else "open"
        # _last_state = initial_state_for_db # Set initial _last_state here

        device = get_device_by_id(DEVICE_ID)
        if not device:
            # Determine initial state for registration more carefully
            pin_is_low_initial = not _reed_device.value  # Read current hardware state
            initial_state_for_db = "closed" if pin_is_low_initial else "open"
            _last_state = initial_state_for_db  # Set initial _last_state for the loop

            logger.info(
                f"[{DEVICE_NAME}] Device not found in DB. Registering with initial state: {initial_state_for_db}..."
            )
            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state=initial_state_for_db,
            )
            logger.info(f"[{DEVICE_NAME}] Device registered.")
        else:
            # If device exists, set _last_state from DB or current hardware state
            # to prevent an immediate false event if states differ.
            db_state = device.get("current_state")
            hw_pin_is_low = not _reed_device.value
            hw_state = "closed" if hw_pin_is_low else "open"

            if db_state and db_state in ["open", "closed"]:
                _last_state = db_state
                logger.info(
                    f"[{DEVICE_NAME}] Device exists. Initializing _last_state from DB: {db_state}"
                )
                # Optionally, sync DB if hardware is different (or assume loop will handle it)
                if db_state != hw_state:
                    logger.warning(
                        f"[{DEVICE_NAME}] DB state ({db_state}) differs from HW state ({hw_state}). Loop will sync."
                    )
            else:
                _last_state = hw_state
                logger.info(
                    f"[{DEVICE_NAME}] Device exists but DB state invalid. Initializing _last_state from HW: {hw_state}"
                )

        _is_monitoring.set()
        # Pass home_id and user_id to the loop
        _monitoring_thread = threading.Thread(
            target=_reed_monitoring_loop, args=(home_id, user_id)
        )
        _monitoring_thread.daemon = True
        _monitoring_thread.start()
        logger.info(f"[{DEVICE_NAME}] Monitoring thread started.")

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting monitoring: {e}", exc_info=True)
        if _reed_device:
            _reed_device.close()
            _reed_device = None
        _is_monitoring.clear()


def stop_reed_monitoring() -> None:
    global _is_monitoring, _monitoring_thread, _reed_device
    logger.info(f"[{DEVICE_NAME}] Stopping monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        logger.info(f"[{DEVICE_NAME}] Waiting for monitoring thread to join...")
        _monitoring_thread.join(timeout=2.0)
        if _monitoring_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Monitoring thread did not finish in time.")
        _monitoring_thread = None

    if _reed_device is not None:
        try:
            _reed_device.close()
            logger.info(f"[{DEVICE_NAME}] Reed device closed.")
        except Exception as e:
            logger.error(
                f"[{DEVICE_NAME}] Error closing reed device: {e}", exc_info=True
            )
        _reed_device = None

    _last_state = None  # Reset last_state
    logger.info(f"[{DEVICE_NAME}] Monitoring stopped and resources cleaned up.")
