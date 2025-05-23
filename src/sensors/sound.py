"""
Sound Detection Sensor Module

This module manages a sound detection sensor for monitoring audio events.
It includes debouncing logic, health monitoring, and state management.

Hardware Setup:
    - Uses BCM GPIO pin 20
    - Digital output (HIGH when sound detected)
    - Adjustable sensitivity via onboard potentiometer
    - 3.3V operating voltage
    - Built-in amplifier and comparator

States:
    - idle: No sound detected
    - detected: Sound event detected
    - disconnected: Sensor not responding

Events:
    - sound_changed: Generated when sound is detected in away mode
    - sensor_changed: Generated when sensor state changes (e.g., disconnection)

Configuration:
    - DETECTION_COOLDOWN: Time between detections (600s)
    - HEALTH_CHECK_INTERVAL: Sensor health check frequency (30s)

Dependencies:
    - gpiozero: For GPIO pin control
    - threading: For concurrent monitoring
    - database: For state persistence and event logging
"""

import threading
import time
from typing import Optional

from gpiozero import InputDevice

from src.utils.database import (
    get_device_by_id,
    get_home_mode,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

# Device configuration
DEVICE_ID = "sound_sensor_01"
DEVICE_NAME = "Sound Sensor"
DEVICE_TYPE = "sound_sensor"

# GPIO configuration
GPIO_PIN_SOUND = 20  # BCM pin number

# Global state management
_sound_sensor: Optional[InputDevice] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_detection_time = 0
_last_health_check_time = 0

# Configuration constants
DETECTION_COOLDOWN = 600.0  # 10 minutes cooldown between detections
HEALTH_CHECK_INTERVAL = 30.0  # Check sensor health every 30 seconds


def _handle_disconnection():
    """Handle sensor disconnection by updating state and logging."""
    logger.error(f"[{DEVICE_NAME}] Sensor appears to be disconnected")
    update_device_state(DEVICE_ID, "disconnected")

    device = get_device_by_id(DEVICE_ID)
    if device:
        old_state = device.get("current_state", "unknown")
        insert_event(
            home_id=device.get("home_id"),
            device_id=DEVICE_ID,
            event_type="sensor_changed",
            old_state=old_state,
            new_state="disconnected",
        )


def _check_sensor_health() -> bool:
    """Check if the sensor is still connected and functioning.

    Returns:
        bool: True if sensor is healthy, False otherwise
    """
    try:
        if not _sound_sensor:
            return False

        pin_state = _sound_sensor.value
        if pin_state is None:
            return False

        return True

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error checking sensor health: {e}")
        return False


def _process_sound_detection():
    """Process sound detection with cooldown period."""
    global _last_detection_time
    current_time = time.time()

    if current_time - _last_detection_time < DETECTION_COOLDOWN:
        logger.debug(
            f"[{DEVICE_NAME}] Skipping detection due to cooldown ({DETECTION_COOLDOWN}s)"
        )
        return False

    _last_detection_time = current_time
    logger.info(f"[{DEVICE_NAME}] Sound event detected (Pin {GPIO_PIN_SOUND} active).")

    try:
        if _sound_sensor and _sound_sensor.value is not None:
            logger.debug(
                f"[{DEVICE_NAME}] Pin state during detection: {_sound_sensor.value}"
            )

            update_device_state(DEVICE_ID, "detected")

            device = get_device_by_id(DEVICE_ID)
            if device:
                home_id = device.get("home_id")
                old_state = device.get("current_state", "idle")

                home_mode = get_home_mode(home_id)
                if home_mode == "away":
                    insert_event(
                        home_id=home_id,
                        device_id=DEVICE_ID,
                        event_type="sound_changed",
                        old_state=old_state,
                        new_state="detected",
                    )
                    logger.info(
                        f"[{DEVICE_NAME}] Sound event logged (home in away mode)"
                    )
                else:
                    logger.debug(
                        f"[{DEVICE_NAME}] Sound event detected but not logged (home mode: {home_mode})"
                    )
            return True
        else:
            _handle_disconnection()
            return False

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error during sound detection: {e}")
        _handle_disconnection()
        return False


def _sound_monitoring_loop():
    """Internal monitoring loop for sound sensor."""
    global _last_health_check_time

    logger.info(f"[{DEVICE_NAME}] Sound sensor monitoring loop started.")

    try:
        while _is_monitoring.is_set():
            current_time = time.time()

            if current_time - _last_health_check_time >= HEALTH_CHECK_INTERVAL:
                _last_health_check_time = current_time

                if not _check_sensor_health():
                    _handle_disconnection()
                    time.sleep(1)
                    continue

            if _sound_sensor and _sound_sensor.value:
                _process_sound_detection()

            time.sleep(0.1)

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
    global _monitoring_thread, _is_monitoring, _sound_sensor, _last_detection_time, _last_health_check_time

    logger.info(
        f"[{DEVICE_NAME}] Starting monitoring for HOME_ID: {home_id}, USER_ID: {user_id}"
    )

    try:
        _sound_sensor = InputDevice(GPIO_PIN_SOUND, pull_up=False)

        if _check_sensor_health():
            initial_state = "active" if _sound_sensor.value else "inactive"
            logger.info(
                f"[{DEVICE_NAME}] Initial sensor state on pin {GPIO_PIN_SOUND}: {initial_state}"
            )
        else:
            logger.error(f"[{DEVICE_NAME}] Failed initial sensor health check")
            raise RuntimeError("Sensor health check failed during initialization")

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

        _last_detection_time = 0
        _last_health_check_time = time.time()

        _is_monitoring.set()
        _monitoring_thread = threading.Thread(target=_sound_monitoring_loop)
        _monitoring_thread.daemon = True
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
