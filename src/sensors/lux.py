import threading
import time
from typing import Optional

from gpiozero import MCP3008

from src.utils.database import (
    get_device_by_id,
    get_latest_device_state,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "lux_sensor_01"
DEVICE_NAME = "Ambient Light Sensor"
DEVICE_TYPE = "lux_sensor"

# ADC Configuration
ADC_CHANNEL = 0  # Channel 0 of MCP3008
REFERENCE_VOLTAGE = 3.3  # Reference voltage of Raspberry Pi
LUX_SCALE_FACTOR = 1000.0  # Scale factor for converting voltage to lux

# Global state
_sensor_instance: Optional[MCP3008] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()


def categorize_lux(lux_value: float) -> str:
    """Categorize lux value into time of day."""
    if lux_value < 50:
        return "Night"
    elif lux_value < 300:
        return "Light Open"
    else:
        return "Day"


def _read_lux_value() -> float:
    """Read the current lux value from the sensor.

    Returns:
        float: The current lux value in lux units
    """
    if not _sensor_instance:
        raise RuntimeError("Sensor not initialized")

    # Read raw value (0-1)
    raw_value = _sensor_instance.value

    # Convert to voltage (0-3.3V)
    voltage = raw_value * REFERENCE_VOLTAGE

    # Convert voltage to lux using calibration factor
    # The conversion depends on your specific light sensor
    # Here we use a simple linear conversion
    lux = voltage * LUX_SCALE_FACTOR

    return lux


def _lux_monitoring_loop(home_id: str) -> None:
    """Internal loop that reads lux sensor data, processes, and logs it."""
    global _sensor_instance
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(f"{log_prefix} Monitoring loop started for HOME_ID: {home_id}.")

    first_reading_after_start = True
    last_lux_value = None

    while _is_monitoring.is_set():
        if _sensor_instance is None:
            logger.error(
                f"{log_prefix} Sensor instance not available. Re-initializing..."
            )
            try:
                _sensor_instance = MCP3008(channel=ADC_CHANNEL)
                logger.info(
                    f"{log_prefix} Successfully re-initialized sensor on channel {ADC_CHANNEL}."
                )
            except Exception as e_init:
                logger.error(
                    f"{log_prefix} Failed to re-initialize sensor: {e_init}. Retrying in 10s."
                )
                time.sleep(10)
                continue

        try:
            # Read current lux value
            lux = _read_lux_value()
            logger.info(f"{log_prefix} Lux value: {lux:.1f}")

            # Only log if value has changed significantly (>5% change)
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
                    event_type="lux_level",
                    old_state=None,
                    new_state=current_status_str,
                    event_data={"lux": lux},
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
                    event_type="lux_level",
                    old_state=old_state_str,
                    new_state=current_status_str,
                    event_data={"lux": lux},
                )
            else:
                if first_reading_after_start:
                    first_reading_after_start = False

        except Exception as e_loop:
            logger.error(
                f"{log_prefix} An unexpected error occurred in the monitoring loop: {e_loop}"
            )
            time.sleep(10)

        time.sleep(5)

    logger.info(f"{log_prefix} Monitoring loop stopped.")


def start_lux_monitoring(home_id: str) -> bool:
    """Initializes and starts the lux sensor monitoring."""
    global _sensor_instance, _monitoring_thread, _is_monitoring
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    if _is_monitoring.is_set():
        logger.info(
            f"{log_prefix} Monitoring is already running for HOME_ID: {home_id}. Will not start again."
        )
        return True

    logger.info(f"{log_prefix} Attempting to start monitoring for HOME_ID: {home_id}")

    try:
        _sensor_instance = MCP3008(channel=ADC_CHANNEL)
        logger.info(
            f"{log_prefix} MCP3008 sensor initialized on channel {ADC_CHANNEL}."
        )

        # Test initial reading
        try:
            initial_lux = _read_lux_value()
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
        if _sensor_instance:
            _sensor_instance = None
        _is_monitoring.clear()
        return False


def stop_lux_monitoring() -> None:
    """Stops the lux sensor monitoring."""
    global _monitoring_thread, _is_monitoring, _sensor_instance
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(f"{log_prefix} Attempting to stop lux monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        logger.info(f"{log_prefix} Waiting for monitoring thread to join...")
        _monitoring_thread.join(timeout=10)
        if _monitoring_thread.is_alive():
            logger.error(f"{log_prefix} Monitoring thread did not join in time.")

    _sensor_instance = None
    logger.info(f"{log_prefix} Lux monitoring stopped and resources (if any) released.")
