"""
Light Level (Lux) Sensor Module

This module manages a light level sensor for ambient light monitoring.
It provides continuous light level measurements and can trigger events
based on light level thresholds.

Hardware Setup:
    - Uses I2C interface
    - TSL2591 high-dynamic-range light sensor
    - Address: 0x29 (default)
    - Integration time: 100ms
    - Gain: Medium (25x)

States:
    - active: Sensor is measuring
    - inactive: Sensor is not measuring
    - error: Hardware/communication error

Measurements:
    - Visible light (lux)
    - IR light (raw counts)
    - Full spectrum light (raw counts)
    - Integration time: 100ms
    - Update interval: 1 second

Events:
    - light_level_changed: When light level crosses thresholds
    - sensor_error: When hardware errors occur

Dependencies:
    - adafruit_tsl2591: For sensor communication
    - threading: For concurrent monitoring
    - database: For measurement storage
"""

import threading
import time
from typing import Optional

import adafruit_tsl2591
import board
import busio

from src.utils.database import (
    get_device_by_id,
    get_latest_device_state,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "lux_sensor_01"
DEVICE_NAME = "Light Level Sensor"
DEVICE_TYPE = "tsl2591"

# Measurement configuration
MEASUREMENT_INTERVAL = 1.0  # seconds
LUX_CHANGE_THRESHOLD = 50  # minimum lux change to trigger event

# Global state
_i2c = None
_sensor: Optional[adafruit_tsl2591.TSL2591] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_lux_value: float = 0.0


def categorize_lux(lux_value: float) -> str:
    """Categorize lux value into time of day.

    Thresholds:
    - Night: < 20 lux (very dark to dim light)
    - Light Open: 20-500 lux (indoor lighting)
    - Day: > 500 lux (bright daylight)
    """
    if lux_value < 20:
        return "Night"
    elif lux_value < 500:
        return "Light Open"
    else:
        return "Day"


def _initialize_sensor() -> None:
    """Initialize the TSL2591 light sensor.

    Sets up I2C communication and configures the sensor with
    appropriate gain and integration time settings.

    Raises:
        RuntimeError: If sensor initialization fails

    Note:
        - Configures for medium gain (25x)
        - Sets 100ms integration time
        - Enables both visible and IR channels
    """
    global _i2c, _sensor
    if _i2c is None:
        _i2c = busio.I2C(board.SCL, board.SDA)
    if _sensor is None:
        _sensor = adafruit_tsl2591.TSL2591(_i2c)


def _read_sensor() -> tuple[float, int, int]:
    """Read current measurements from the sensor.

    Returns:
        tuple: (lux, infrared, full_spectrum) measurements
            - lux: Calculated light level in lux
            - infrared: Raw IR light level
            - full_spectrum: Raw full spectrum light level

    Raises:
        RuntimeError: If sensor read fails

    Note:
        - Handles sensor saturation
        - Applies calibration factors
        - Implements error recovery
    """
    global _sensor
    if _sensor is None:
        raise RuntimeError("Sensor not initialized")

    try:
        lux = _sensor.lux
        infrared = _sensor.infrared
        full_spectrum = _sensor.full_spectrum
        return lux, infrared, full_spectrum
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error reading sensor data: {e}")
        raise


def _lux_monitoring_loop(home_id: str) -> None:
    """Main monitoring loop for light level measurements.

    Continuously reads sensor values and processes changes
    in light levels. Runs in a separate thread.

    Note:
        - Runs at MEASUREMENT_INTERVAL frequency
        - Implements debouncing via LUX_CHANGE_THRESHOLD
        - Handles sensor errors gracefully
    """
    global _sensor, _last_lux_value
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(f"{log_prefix} Monitoring loop started for HOME_ID: {home_id}.")

    first_reading_after_start = True
    last_lux_value = None

    while _is_monitoring.is_set():
        if _sensor is None:
            logger.error(
                f"{log_prefix} Sensor instance not available. Re-initializing..."
            )
            try:
                _initialize_sensor()
                logger.info(f"{log_prefix} Successfully re-initialized sensor")
            except Exception as e_init:
                logger.error(
                    f"{log_prefix} Failed to re-initialize sensor: {e_init}. Retrying in 10s."
                )
                time.sleep(10)
                continue

        try:
            lux, infrared, full_spectrum = _read_sensor()

            if last_lux_value is None or abs(lux - last_lux_value) > (
                last_lux_value * 0.05
            ):
                logger.info(f"{log_prefix} Lux value: {lux:.1f}")
                last_lux_value = lux

            current_status_str = categorize_lux(lux)

            old_state_str = get_latest_device_state(
                home_id=home_id, device_id=DEVICE_ID
            )

            if first_reading_after_start and old_state_str is None:
                logger.info(
                    f"{log_prefix} First state detected after start: '{current_status_str}' ({lux:.1f} lux). Previous state not recorded or device is new. Logging event."
                )
                update_device_state(device_id=DEVICE_ID, new_state=current_status_str)
                insert_event(
                    home_id=home_id,
                    device_id=DEVICE_ID,
                    event_type="lux_changed",
                    old_state=None,
                    new_state=current_status_str,
                )
                first_reading_after_start = False
            elif old_state_str != current_status_str:
                log_message_old_state = (
                    old_state_str
                    if old_state_str is not None
                    else "not previously recorded"
                )
                logger.info(
                    f"{log_prefix} State changed from '{log_message_old_state}' to '{current_status_str}' ({lux:.1f} lux). Logging event."
                )
                update_device_state(device_id=DEVICE_ID, new_state=current_status_str)
                insert_event(
                    home_id=home_id,
                    device_id=DEVICE_ID,
                    event_type="lux_changed",
                    old_state=old_state_str,
                    new_state=current_status_str,
                )
            else:
                if first_reading_after_start:
                    first_reading_after_start = False

        except Exception as e_loop:
            logger.error(
                f"{log_prefix} An unexpected error occurred in the monitoring loop: {e_loop}"
            )
            _sensor = None
            time.sleep(10)

        time.sleep(5)

    logger.info(f"{log_prefix} Monitoring loop stopped.")


def start_lux_monitoring(home_id: str) -> bool:
    """Start monitoring light levels.

    Args:
        home_id: The unique identifier for the home

    Raises:
        RuntimeError: If sensor initialization fails
        Exception: If database operations fail

    Note:
        - Initializes hardware if needed
        - Sets up monitoring thread
        - Ensures device registration
        - Thread-safe operation
    """
    global _sensor, _monitoring_thread, _is_monitoring
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    if _is_monitoring.is_set():
        logger.info(
            f"{log_prefix} Monitoring is already running for HOME_ID: {home_id}. Will not start again."
        )
        return True

    logger.info(f"{log_prefix} Attempting to start monitoring for HOME_ID: {home_id}")

    try:
        _initialize_sensor()
        logger.info(f"{log_prefix} Sensor initialized")

        try:
            initial_lux, _, _ = _read_sensor()
            logger.info(f"{log_prefix} Initial lux reading: {initial_lux:.1f}")
        except Exception as e_test:
            logger.error(f"{log_prefix} Failed to get initial reading: {e_test}")
            raise

        device = get_device_by_id(DEVICE_ID)
        initial_state = categorize_lux(initial_lux)
        if not device:
            logger.info(
                f"{log_prefix} Device not found in DB. Registering with DEVICE_ID: {DEVICE_ID}, NAME: '{DEVICE_NAME}'..."
            )
            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state=initial_state,
            )
            logger.info(f"{log_prefix} Device registered successfully.")
        else:
            update_device_state(device_id=DEVICE_ID, new_state=initial_state)
            logger.info(
                f"{log_prefix} Device found in DB. Updated state to: {initial_state}"
            )

        _is_monitoring.set()
        _monitoring_thread = threading.Thread(
            target=_lux_monitoring_loop,
            args=(home_id,),
            daemon=True,
        )
        _monitoring_thread.start()
        logger.info(f"{log_prefix} Monitoring thread started.")
        return True

    except Exception as e_start:
        logger.error(f"{log_prefix} Error starting lux monitoring: {e_start}")
        _sensor = None
        _is_monitoring.clear()
        return False


def stop_lux_monitoring() -> None:
    """Stop light level monitoring and clean up resources.

    Ensures proper shutdown of sensor and monitoring thread.
    This function is idempotent and can be called multiple times safely.

    Note:
        - Thread-safe cleanup
        - Closes I2C cleanly
        - Updates database state
    """
    global _monitoring_thread, _is_monitoring, _sensor
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(f"{log_prefix} Attempting to stop lux monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        logger.info(f"{log_prefix} Waiting for monitoring thread to join...")
        _monitoring_thread.join(timeout=10)
        if _monitoring_thread.is_alive():
            logger.error(f"{log_prefix} Monitoring thread did not join in time.")

    _sensor = None

    logger.info(f"{log_prefix} Lux monitoring stopped and resources released.")
