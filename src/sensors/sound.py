import threading
import time
from enum import Enum
from typing import Optional

from gpiozero import Button  # Changed from InputDevice to Button for edge detection

from src.utils.database import (
    get_device_by_id,
    get_device_state,
    get_home_mode,
    insert_alert,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "sound_sensor_01"
DEVICE_NAME = "Sound Sensor"
DEVICE_TYPE = "sound_sensor"

GPIO_PIN_SOUND = 20

# Global state
_sound_sensor: Optional[Button] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_detection_time = 0
DETECTION_COOLDOWN = 3.0  # Reduced from 10s to 3s for better responsiveness


def _on_sound_detected():
    """Callback function for sound detection."""
    global _last_detection_time
    current_time = time.time()

    # Check cooldown
    if current_time - _last_detection_time < DETECTION_COOLDOWN:
        logger.debug(f"[{DEVICE_NAME}] Skipping detection due to cooldown")
        return

    _last_detection_time = current_time
    logger.info(f"[{DEVICE_NAME}] Sound event detected (Pin {GPIO_PIN_SOUND} active).")

    # Update device state
    update_device_state(DEVICE_ID, "detected")

    # Get the current device to check previous state
    device = get_device_by_id(DEVICE_ID)
    if device:
        old_state = device.get("current_state", "idle")
        # Log the event
        insert_event(
            home_id=device.get("home_id"),
            device_id=DEVICE_ID,
            event_type="sound_detected",
            old_state=old_state,
            new_state="detected",
        )


def _on_sound_ended():
    """Callback function for when sound detection ends."""
    logger.info(f"[{DEVICE_NAME}] Sound event ended (Pin {GPIO_PIN_SOUND} inactive).")
    update_device_state(DEVICE_ID, "idle")


def _sound_monitoring_loop():
    """Internal monitoring loop for sound sensor."""
    logger.info(f"[{DEVICE_NAME}] Sound sensor monitoring loop started.")

    try:
        while _is_monitoring.is_set():
            if not _sound_sensor:
                logger.error(f"[{DEVICE_NAME}] Sound sensor not initialized!")
                break

            # Check sensor health
            try:
                _sound_sensor.pin.state  # This will raise an exception if the pin is invalid
            except Exception as e:
                logger.error(f"[{DEVICE_NAME}] Error reading sensor state: {e}")
                update_device_state(DEVICE_ID, "error")
                time.sleep(1)  # Wait before retrying
                continue

            time.sleep(0.1)  # Reduced sleep time for more responsive detection

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error in monitoring loop: {e}")
    finally:
        logger.info(f"[{DEVICE_NAME}] Sound sensor monitoring loop ended.")


def start_sound_monitoring(home_id: str, user_id: str) -> None:
    """Start monitoring for sound events.

    Args:
        home_id: The ID of the home this sensor belongs to
        user_id: The ID of the user to notify
    """
    global _monitoring_thread, _is_monitoring, _sound_sensor

    logger.info(
        f"[{DEVICE_NAME}] Starting monitoring for HOME_ID: {home_id}, USER_ID: {user_id}"
    )

    try:
        # Initialize with pull-up resistor and edge detection
        _sound_sensor = Button(
            GPIO_PIN_SOUND,
            pull_up=True,
            bounce_time=0.1,  # Add debounce to prevent false triggers
        )

        # Set up callbacks for edge detection
        _sound_sensor.when_pressed = _on_sound_detected
        _sound_sensor.when_released = _on_sound_ended

        # Test if sensor is responding
        initial_state = "active" if _sound_sensor.is_pressed else "inactive"
        logger.info(
            f"[{DEVICE_NAME}] Initial sensor state on pin {GPIO_PIN_SOUND}: {initial_state}"
        )

        # Register device if not present
        device = get_device_by_id(DEVICE_ID)
        if not device:
            logger.info(f"[{DEVICE_NAME}] Device not found in DB. Registering...")
            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state="idle",
            )

        # Start monitoring thread
        _is_monitoring.set()
        _monitoring_thread = threading.Thread(target=_sound_monitoring_loop)
        _monitoring_thread.daemon = (
            True  # Make thread daemon so it exits with main program
        )
        _monitoring_thread.start()
        logger.info(f"[{DEVICE_NAME}] Monitoring started successfully.")

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting monitoring: {e}")
        if _sound_sensor:
            _sound_sensor.close()
            _sound_sensor = None
        _is_monitoring.clear()


def stop_sound_monitoring() -> None:
    """Stop sound monitoring and clean up resources."""
    global _is_monitoring, _monitoring_thread, _sound_sensor

    logger.info(f"[{DEVICE_NAME}] Stopping monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        _monitoring_thread.join(timeout=2.0)
        if _monitoring_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Monitoring thread did not finish in time.")

    if _sound_sensor:
        _sound_sensor.close()
        _sound_sensor = None

    logger.info(f"[{DEVICE_NAME}] Monitoring stopped and resources cleaned up.")
