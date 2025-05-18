import threading
import time
from enum import Enum
from typing import Optional

from gpiozero import InputDevice

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
_sound_sensor_device: Optional[InputDevice] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()


def _sound_monitoring_loop(home_id: str, user_id: str):
    """Internal monitoring loop for sound sensor.

    Args:
        home_id: The ID of the home this sensor belongs to
        user_id: The ID of the user to notify
    """
    global _sound_sensor_device
    logger.info(f"[{DEVICE_NAME}] Sound sensor monitoring loop started.")

    last_detection_time = 0
    DETECTION_COOLDOWN = 10.0  # 10 second cooldown between detections

    try:
        while _is_monitoring.is_set():
            current_time = time.time()

            if (
                _sound_sensor_device
                and _sound_sensor_device.is_active
                and current_time - last_detection_time >= DETECTION_COOLDOWN
            ):

                logger.info(
                    f"[{DEVICE_NAME}] Sound event detected (Pin {GPIO_PIN_SOUND} active)."
                )
                last_detection_time = current_time

                # Update device state
                update_device_state(DEVICE_ID, "detected")

                # Check home mode
                home_mode = get_home_mode(home_id)
                if home_mode == "away":
                    # Check motion sensor state
                    motion_state = get_device_state("motion_01")
                    if motion_state == "moving_presence":
                        # Both sound and motion detected while in away mode - potential security issue
                        alert_message = "Security Alert: Motion and sound detected while home is in away mode. There might be someone in your home."
                        logger.warning(f"[{DEVICE_NAME}] {alert_message}")
                        insert_alert(
                            home_id=home_id,
                            user_id=user_id,
                            device_id=DEVICE_ID,
                            message=alert_message,
                        )

                # Log the event regardless of mode
                insert_event(
                    home_id=home_id,
                    device_id=DEVICE_ID,
                    event_type="sound_detected",
                    old_state="idle",
                    new_state="detected",
                )

            elif _sound_sensor_device and not _sound_sensor_device.is_active:
                # Update state to idle when no sound is detected
                update_device_state(DEVICE_ID, "idle")

            time.sleep(0.01)  # Small sleep to prevent CPU hogging

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
    global _monitoring_thread, _is_monitoring, _sound_sensor_device

    logger.info(
        f"[{DEVICE_NAME}] Starting monitoring for HOME_ID: {home_id}, USER_ID: {user_id}"
    )

    try:
        # Initialize with active_state=True to detect HIGH as active
        _sound_sensor_device = InputDevice(GPIO_PIN_SOUND, active_state=True)

        # Test if sensor is responding
        initial_state = "active" if _sound_sensor_device.is_active else "inactive"
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
        _monitoring_thread = threading.Thread(
            target=_sound_monitoring_loop,
            args=(home_id, user_id),
        )
        _monitoring_thread.start()
        logger.info(f"[{DEVICE_NAME}] Monitoring started.")
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting monitoring: {e}")


def stop_sound_monitoring() -> None:
    global _is_monitoring, _monitoring_thread, _sound_sensor_device
    logger.info(f"[{DEVICE_NAME}] Stopping monitoring...")
    _is_monitoring.clear()
    if _monitoring_thread and _monitoring_thread.is_alive():
        _monitoring_thread.join(timeout=2.0)
        if _monitoring_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Monitoring thread did not finish in time.")
    if _sound_sensor_device:
        _sound_sensor_device.close()
        _sound_sensor_device = None
    logger.info(f"[{DEVICE_NAME}] Monitoring stopped.")
