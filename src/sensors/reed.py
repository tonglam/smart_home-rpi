import threading
import time
from typing import Optional

import RPi.GPIO as GPIO

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
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_state: Optional[str] = None
_last_event_time: float = 0.0
EVENT_COOLDOWN = 1.0  # Seconds
_gpio_initialized_by_this_module = False


def _on_door_opened_logic(
    home_id: str, user_id: Optional[str], old_state_from_loop: Optional[str]
):
    """Handles logic when the door transitions to an open state."""
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
    home_id: str, user_id: Optional[str], old_state_from_loop: Optional[str]
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


def _reed_monitoring_loop(home_id: str, user_id: Optional[str]):
    """Monitors the reed switch state by polling the GPIO pin using RPi.GPIO."""
    global _last_state, _last_event_time
    logger.info(
        f"[{DEVICE_NAME}] Reed switch monitoring loop started for HOME_ID: {home_id}."
    )

    # Initialize _last_state based on current sensor reading or database
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
                # This can happen if GPIOs are not set up or cleaned up unexpectedly
                logger.error(
                    f"[{DEVICE_NAME}] RuntimeError reading GPIO pin {REED_PIN}: {e}. Attempting to re-setup.",
                    exc_info=True,
                )
                try:
                    # Attempt to re-initialize GPIO settings for this pin
                    GPIO.setmode(GPIO.BCM)  # Ensure mode is set
                    GPIO.setup(REED_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                    logger.info(f"[{DEVICE_NAME}] Re-initialized GPIO pin {REED_PIN}.")
                    time.sleep(1)  # Wait a bit after re-setup
                    continue  # Retry reading in the next loop iteration
                except Exception as re_setup_e:
                    logger.critical(
                        f"[{DEVICE_NAME}] Failed to re-setup GPIO pin {REED_PIN}: {re_setup_e}. Stopping loop.",
                        exc_info=True,
                    )
                    break  # Exit loop if re-setup fails

            current_door_state = "closed" if pin_is_low else "open"

            if current_door_state != _last_state:
                current_time = time.time()
                if (current_time - _last_event_time) >= EVENT_COOLDOWN:
                    logger.info(
                        f"[{DEVICE_NAME}] State change: {_last_state} -> {current_door_state}"
                    )
                    if current_door_state == "open":
                        _on_door_opened_logic(home_id, user_id, _last_state)
                    else:  # current_door_state == "closed"
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
        logger.error(
            f"[{DEVICE_NAME}] Unhandled error in monitoring loop: {e}", exc_info=True
        )
    finally:
        logger.info(f"[{DEVICE_NAME}] Reed switch monitoring loop ended.")


def start_reed_monitoring(home_id: str, user_id: Optional[str]) -> None:
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
        # GPIO setup using RPi.GPIO
        # Check if another part of the application might have already set the mode.
        # This is a simple check; a more robust system might use a global GPIO manager.
        current_gpio_mode = GPIO.getmode()
        if current_gpio_mode is None:  # Mode not set
            GPIO.setmode(GPIO.BCM)
            logger.info(f"[{DEVICE_NAME}] RPi.GPIO mode set to BCM.")
            _gpio_initialized_by_this_module = (
                True  # Mark that this module set the mode
            )
        elif current_gpio_mode != GPIO.BCM:
            logger.warning(
                f"[{DEVICE_NAME}] RPi.GPIO mode was already set to {current_gpio_mode} (expected BCM). Proceeding with existing mode."
            )
            # If it was BOARD, our REED_PIN number might be wrong. This is a potential conflict.
        else:
            logger.info(f"[{DEVICE_NAME}] RPi.GPIO mode already BCM.")

        GPIO.setup(REED_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.info(f"[{DEVICE_NAME}] GPIO pin {REED_PIN} setup as IN with PUD_UP.")

        _last_event_time = 0  # Reset cooldown timer on start

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
        # No specific _reed_device to close, but RPi.GPIO cleanup is tricky here.
        # If this module set GPIO.setmode, it could clean up, but not if mode was pre-existing.


def stop_reed_monitoring() -> None:
    global _is_monitoring, _monitoring_thread, _last_state, _gpio_initialized_by_this_module
    logger.info(f"[{DEVICE_NAME}] Stopping monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        logger.info(f"[{DEVICE_NAME}] Waiting for monitoring thread to join...")
        _monitoring_thread.join(timeout=2.0)
        if _monitoring_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Monitoring thread did not finish in time.")
        _monitoring_thread = None

    # RPi.GPIO cleanup is complex for a module.
    # GPIO.cleanup() affects all channels. Calling it here might affect other modules.
    # If this module exclusively set the mode via GPIO.setmode(), it could call GPIO.cleanup().
    # For now, we are not calling GPIO.cleanup() here to avoid side effects.
    # The pin remains setup as IN. OS will reclaim on process exit.
    # if _gpio_initialized_by_this_module:
    #     try:
    #         GPIO.cleanup(REED_PIN) # Clean up only this channel if possible/desired
    #         logger.info(f"[{DEVICE_NAME}] GPIO pin {REED_PIN} cleaned up.")
    #     except Exception as e:
    #         logger.error(f"[{DEVICE_NAME}] Error during GPIO cleanup for pin {REED_PIN}: {e}")
    # _gpio_initialized_by_this_module = False

    _last_state = None  # Reset last_state
    logger.info(
        f"[{DEVICE_NAME}] Monitoring stopped."
    )  # Removed "and resources cleaned up" as GPIO isn't cleaned by this func
