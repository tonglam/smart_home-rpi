"""
Reed Switch Door Sensor Module

This module manages a magnetic reed switch sensor for door state monitoring.
It handles door open/close detection, maintains state, and generates appropriate
alerts based on home security mode.

Hardware Setup:
    - Uses BCM GPIO pin 21
    - Normally closed configuration (LOW when door closed)
    - Pull-up resistor enabled
    - Magnetic sensor mounted on door frame
    - Magnet mounted on door

States:
    - open: Door is open (reed switch not in contact with magnet)
    - closed: Door is closed (reed switch in contact with magnet)
    - unknown: Initial state or error condition

Events:
    - door_changed: Generated when door state changes
    - alert: Generated when door opens in away mode

Dependencies:
    - RPi.GPIO: For GPIO pin control
    - threading: For concurrent monitoring
    - database: For state persistence and event logging
"""

import threading
import time
from typing import Optional

import RPi.GPIO as GPIO

from src.utils.database import (
    get_device_by_id,
    get_home_mode,
    get_latest_device_state,
    get_user_id_for_home,
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
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_state: Optional[str] = None
_last_event_time: float = 0.0
_gpio_initialized_by_this_module = False


def _on_door_opened_logic(
    home_id: str, user_id: Optional[str], old_state_from_loop: Optional[str]
):
    """Handles logic when the door transitions to an open state.

    This function updates the device state, logs the event, and generates
    security alerts if the home is in away mode.

    Args:
        home_id: The unique identifier for the home
        user_id: Optional user ID for alert association
        old_state_from_loop: Previous door state from monitoring loop

    Raises:
        Exception: If database operations fail
    """
    logger.info(f"[{DEVICE_NAME}] Door opened detected.")
    update_device_state(DEVICE_ID, "open")

    actual_old_state = (
        old_state_from_loop
        if old_state_from_loop is not None
        else get_latest_device_state(home_id, DEVICE_ID) or "unknown"
    )

    insert_event(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="door_changed",
        old_state=actual_old_state,
        new_state="open",
    )

    home_mode = get_home_mode(home_id)
    if home_mode == "away":
        alert_message = "Security Alert: Door opened while home is in away mode!"
        logger.warning(f"[{DEVICE_NAME}] {alert_message}")
        if not user_id:
            user_id = get_user_id_for_home(home_id)

        insert_alert(
            home_id=home_id,
            user_id=user_id,
            device_id=DEVICE_ID,
            message=alert_message,
        )


def _on_door_closed_logic(home_id: str, old_state_from_loop: Optional[str]):
    """Handles logic when the door transitions to a closed state.

    Updates device state and logs the state change event.

    Args:
        home_id: The unique identifier for the home
        old_state_from_loop: Previous door state from monitoring loop

    Raises:
        Exception: If database operations fail
    """
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
        event_type="door_changed",
        old_state=actual_old_state,
        new_state="closed",
    )


def _reed_monitoring_loop(home_id: str, user_id: Optional[str]):
    """Monitors the reed switch state by polling the GPIO pin using RPi.GPIO.

    This is the main monitoring loop that runs in a separate thread.
    It continuously polls the GPIO pin and processes state changes.

    Args:
        home_id: The unique identifier for the home
        user_id: Optional user ID for alert association

    Note:
        - Uses a polling interval of 0.1 seconds
        - Automatically attempts to recover from GPIO errors
        - Maintains state consistency with database
    """
    global _last_state, _last_event_time
    logger.info(
        f"[{DEVICE_NAME}] Reed switch monitoring loop started for HOME_ID: {home_id}."
    )

    try:
        pin_signal_init = GPIO.input(REED_PIN)
        pin_is_low_init = pin_signal_init == GPIO.LOW
        _last_state = "closed" if pin_is_low_init else "open"
        logger.info(f"[{DEVICE_NAME}] Initial polled state (RPi.GPIO): {_last_state}")
    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error reading initial pin state: {e}. Falling back to DB state.",
            exc_info=True,
        )
        _last_state = get_latest_device_state(home_id, DEVICE_ID) or "unknown"
        logger.warning(
            f"[{DEVICE_NAME}] Initial state from DB (fallback): {_last_state}"
        )

    try:
        while _is_monitoring.is_set():
            try:
                pin_signal = GPIO.input(REED_PIN)
                pin_is_low = pin_signal == GPIO.LOW
            except RuntimeError as e:
                logger.error(
                    f"[{DEVICE_NAME}] RuntimeError reading GPIO pin {REED_PIN}: {e}. Attempting to re-setup.",
                    exc_info=True,
                )
                try:
                    GPIO.setmode(GPIO.BCM)  # Ensure mode is set
                    GPIO.setup(REED_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                    logger.info(f"[{DEVICE_NAME}] Re-initialized GPIO pin {REED_PIN}.")
                    time.sleep(1)
                    continue
                except Exception as re_setup_e:
                    logger.critical(
                        f"[{DEVICE_NAME}] Failed to re-setup GPIO pin {REED_PIN}: {re_setup_e}. Stopping loop.",
                        exc_info=True,
                    )
                    break

            current_door_state = "closed" if pin_is_low else "open"

            if current_door_state != _last_state:
                logger.info(
                    f"[{DEVICE_NAME}] State change: {_last_state} -> {current_door_state}"
                )
                if current_door_state == "open":
                    _on_door_opened_logic(home_id, user_id, _last_state)
                else:
                    _on_door_closed_logic(home_id, _last_state)
                _last_state = current_door_state
            time.sleep(0.1)
    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Unhandled error in monitoring loop: {e}", exc_info=True
        )
    finally:
        logger.info(f"[{DEVICE_NAME}] Reed switch monitoring loop ended.")


def start_reed_monitoring(home_id: str, user_id: Optional[str]) -> None:
    """Start monitoring the reed switch for door state changes.

    Initializes GPIO, sets up the monitoring thread, and ensures proper
    device registration in the database.

    Args:
        home_id: The unique identifier for the home
        user_id: Optional user ID for alert association

    Raises:
        RuntimeError: If GPIO initialization fails
        Exception: If database operations fail

    Note:
        - Will not start if monitoring is already active
        - Handles GPIO mode conflicts with other modules
        - Ensures device state consistency on startup
    """
    global _monitoring_thread, _is_monitoring, _last_state, _last_event_time, _gpio_initialized_by_this_module

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
        current_gpio_mode = GPIO.getmode()
        if current_gpio_mode is None:
            GPIO.setmode(GPIO.BCM)
            logger.info(f"[{DEVICE_NAME}] RPi.GPIO mode set to BCM.")
            _gpio_initialized_by_this_module = True
        elif current_gpio_mode != GPIO.BCM:
            logger.warning(
                f"[{DEVICE_NAME}] RPi.GPIO mode was already set to {current_gpio_mode} (expected BCM). Proceeding with existing mode."
            )
        else:
            logger.info(f"[{DEVICE_NAME}] RPi.GPIO mode already BCM.")

        GPIO.setup(REED_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.info(f"[{DEVICE_NAME}] GPIO pin {REED_PIN} setup as IN with PUD_UP.")

        _last_event_time = 0

        device = get_device_by_id(DEVICE_ID)
        if not device:
            pin_signal_initial = GPIO.input(REED_PIN)
            initial_state_for_db = (
                "closed" if pin_signal_initial == GPIO.LOW else "open"
            )
            _last_state = initial_state_for_db

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
            db_state = device.get("current_state")
            hw_pin_signal = GPIO.input(REED_PIN)
            hw_state = "closed" if hw_pin_signal == GPIO.LOW else "open"

            if db_state and db_state in ["open", "closed"]:
                _last_state = db_state
                logger.info(
                    f"[{DEVICE_NAME}] Device exists. Initializing _last_state from DB: {db_state}"
                )
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
        _monitoring_thread = threading.Thread(
            target=_reed_monitoring_loop, args=(home_id, user_id)
        )
        _monitoring_thread.daemon = True
        _monitoring_thread.start()
        logger.info(f"[{DEVICE_NAME}] Monitoring thread started.")

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting monitoring: {e}", exc_info=True)
        _is_monitoring.clear()


def stop_reed_monitoring() -> None:
    """Stop reed switch monitoring and clean up resources.

    Stops the monitoring thread and ensures proper cleanup of resources.
    This function is idempotent and can be called multiple times safely.

    Note:
        - Waits up to 2 seconds for thread to finish
        - Does not cleanup GPIO pins if initialized by another module
        - Logs warning if thread doesn't finish in time
    """
    global _is_monitoring, _monitoring_thread, _last_state, _gpio_initialized_by_this_module
    logger.info(f"[{DEVICE_NAME}] Stopping monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        logger.info(f"[{DEVICE_NAME}] Waiting for monitoring thread to join...")
        _monitoring_thread.join(timeout=2.0)
        if _monitoring_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Monitoring thread did not finish in time.")
        _monitoring_thread = None

    _last_state = None
    logger.info(f"[{DEVICE_NAME}] Monitoring stopped.")
