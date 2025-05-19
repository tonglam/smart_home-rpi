import os
import threading
import time
from enum import Enum
from typing import Optional, Tuple

import serial
from serial.tools import list_ports

from src.utils.database import (
    get_device_by_id,
    get_device_state,
    get_home_mode,
    get_latest_device_state,
    get_user_id_for_home,
    insert_alert,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "motion_01"
DEVICE_NAME = "Room Motion Sensor"
DEVICE_TYPE = "motion_sensor"


# --- Serial port configuration for Linux ---
def get_default_serial_port() -> str:
    """Returns the default serial port for Linux systems."""
    # Raspberry Pi hardware serial port
    if os.path.exists("/dev/ttyS0"):
        return "/dev/ttyS0"
    # USB-to-Serial adapters usually appear as ttyUSB0
    elif os.path.exists("/dev/ttyUSB0"):
        return "/dev/ttyUSB0"
    # If neither exists, fall back to ttyAMA0 which is sometimes used on newer Pis
    elif os.path.exists("/dev/ttyAMA0"):
        return "/dev/ttyAMA0"
    # Default fallback
    return "/dev/ttyUSB0"


DEFAULT_SERIAL_PORT = get_default_serial_port()
DEFAULT_BAUD_RATE = 115200  # May need to be adjusted based on actual sensor

# --- Global state for the sensor module ---
_serial_connection: Optional[serial.Serial] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()
_last_state_change_time = 0
MOTION_COOLDOWN = 5.0  # Seconds between state changes to avoid excessive DB writes
MOTION_THRESHOLD = 3  # Minimum number of non-zero bytes to consider potential motion
READ_BUFFER_SIZE = 64  # Size of data to read from sensor


# --- Enum for Presence States ---
class PresenceState(Enum):
    NO_PRESENCE = "no_presence"
    MOVING_PRESENCE = "moving_presence"
    STILL_PRESENCE = "still_presence"
    UNKNOWN = "unknown"


def parse_binary_data(data: bytes) -> Tuple[bool, PresenceState]:
    """
    Parse binary data from the motion sensor.

    Args:
        data: Binary data from the sensor

    Returns:
        (success, presence_state) tuple
    """
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    # Log raw binary data at debug level
    if logger.isEnabledFor(10):  # DEBUG level
        logger.debug(
            f"{log_prefix} Raw data: [{','.join(hex(b) for b in data)}], Length={len(data)}"
        )

    # Check for meaningful data
    if not data or len(data) < 3:
        return False, PresenceState.UNKNOWN

    # Count significant bytes to determine motion
    non_zero_bytes = sum(1 for b in data if b != 0)
    special_bytes = sum(1 for b in data if b in (0xFE, 0xF0, 0xF8, 0xC0))

    # Log the counters at debug level
    logger.debug(
        f"{log_prefix} Non-zero bytes: {non_zero_bytes}, Special bytes: {special_bytes}"
    )

    # If we have many non-zero bytes or special bytes, likely motion
    if non_zero_bytes > MOTION_THRESHOLD + 3 or special_bytes > 2:
        return True, PresenceState.MOVING_PRESENCE
    elif non_zero_bytes > MOTION_THRESHOLD:
        return True, PresenceState.STILL_PRESENCE
    else:
        return True, PresenceState.NO_PRESENCE


def _motion_monitoring_loop(home_id: str) -> None:
    """Internal loop that reads sensor data, processes, and logs it."""
    global _serial_connection, _last_state_change_time
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(
        f"{log_prefix} Monitoring loop started for HOME_ID: {home_id} on port {DEFAULT_SERIAL_PORT}."
    )

    if not os.path.exists(DEFAULT_SERIAL_PORT):
        logger.error(
            f"{log_prefix} Serial port {DEFAULT_SERIAL_PORT} does not exist. "
            f"Available ports: {', '.join(port.device for port in list_ports.comports())}"
        )

    first_reading_after_start = True
    last_known_status = None
    last_data_buffer = bytearray()

    while _is_monitoring.is_set():
        if _serial_connection is None or not _serial_connection.isOpen():
            logger.error(
                f"{log_prefix} Serial connection lost or not open. Attempting to reconnect..."
            )
            try:
                # List available ports for debugging
                available_ports = list(list_ports.comports())
                logger.info(
                    f"{log_prefix} Available serial ports: {', '.join(port.device for port in available_ports)}"
                )

                if not os.path.exists(DEFAULT_SERIAL_PORT):
                    logger.error(
                        f"{log_prefix} Serial port {DEFAULT_SERIAL_PORT} does not exist. "
                        "Please check your hardware connection or update the port configuration."
                    )
                    time.sleep(5)
                    continue

                _serial_connection = serial.Serial(
                    DEFAULT_SERIAL_PORT, DEFAULT_BAUD_RATE, timeout=1
                )
                logger.info(
                    f"{log_prefix} Successfully re-opened serial port {DEFAULT_SERIAL_PORT} at {DEFAULT_BAUD_RATE} baud."
                )
            except serial.SerialException as e_serial:
                logger.error(
                    f"{log_prefix} Failed to re-open serial port {DEFAULT_SERIAL_PORT}: {e_serial}. "
                    "Please check your hardware connection and permissions. Retrying in 5s."
                )
                time.sleep(5)
                continue
            except Exception as e_other:
                logger.error(
                    f"{log_prefix} Unexpected error opening serial port {DEFAULT_SERIAL_PORT}: {e_other}. Retrying in 5s."
                )
                time.sleep(5)
                continue

        try:
            # Read binary data if available
            if _serial_connection.in_waiting > 0:
                # Read into buffer
                data = _serial_connection.read(_serial_connection.in_waiting)
                last_data_buffer.extend(data)

                # Keep buffer manageable
                if len(last_data_buffer) > READ_BUFFER_SIZE:
                    last_data_buffer = last_data_buffer[-READ_BUFFER_SIZE:]

                # Only process if we have enough data
                if len(last_data_buffer) >= 7:
                    # Parse the binary data
                    data_to_parse = bytes(last_data_buffer)
                    success, current_status_enum = parse_binary_data(data_to_parse)

                    if success:
                        current_status_str = current_status_enum.value
                        current_time = time.time()

                        # Log state changes or periodic updates
                        if (
                            last_known_status != current_status_enum
                            or current_time % 30 < 1
                        ):
                            logger.info(
                                f"{log_prefix} Motion state: '{current_status_str}'"
                            )

                        if current_status_enum == PresenceState.UNKNOWN:
                            continue

                        # Apply cooldown to reduce excessive state changes
                        should_update_state = (
                            first_reading_after_start
                            or last_known_status != current_status_enum
                            or (current_time - _last_state_change_time)
                            >= MOTION_COOLDOWN
                        )

                        if should_update_state:
                            old_state_str = get_latest_device_state(
                                home_id=home_id, device_id=DEVICE_ID
                            )

                            # Check for motion presence and home mode
                            if current_status_enum == PresenceState.MOVING_PRESENCE:
                                home_mode = get_home_mode(home_id)
                                if home_mode == "away":
                                    # Check sound sensor state
                                    sound_state = get_device_state("sound_sensor_01")
                                    if sound_state == "detected":
                                        # Both motion and sound detected while in away mode
                                        user_id = get_user_id_for_home(home_id)
                                        if user_id:
                                            alert_message = "Security Alert: Motion and sound detected while home is in away mode. There might be someone in your home."
                                            logger.warning(
                                                f"{log_prefix} {alert_message}"
                                            )
                                            insert_alert(
                                                home_id=home_id,
                                                user_id=user_id,
                                                device_id=DEVICE_ID,
                                                message=alert_message,
                                            )

                            if first_reading_after_start and old_state_str is None:
                                logger.info(
                                    f"{log_prefix} First state detected after start: '{current_status_str}'. Previous state was not recorded or device is new. Logging event."
                                )
                                update_device_state(
                                    device_id=DEVICE_ID, new_state=current_status_str
                                )
                                insert_event(
                                    home_id=home_id,
                                    device_id=DEVICE_ID,
                                    event_type="presence",
                                    old_state=None,
                                    new_state=current_status_str,
                                )
                                first_reading_after_start = False
                                _last_state_change_time = current_time
                            elif old_state_str != current_status_str:
                                log_message_old_state = (
                                    old_state_str
                                    if old_state_str is not None
                                    else "not previously recorded"
                                )
                                logger.info(
                                    f"{log_prefix} State changed from '{log_message_old_state}' to '{current_status_str}'. Logging event."
                                )
                                update_device_state(
                                    device_id=DEVICE_ID, new_state=current_status_str
                                )
                                insert_event(
                                    home_id=home_id,
                                    device_id=DEVICE_ID,
                                    event_type="presence",
                                    old_state=old_state_str,
                                    new_state=current_status_str,
                                )
                                first_reading_after_start = False
                                _last_state_change_time = current_time
                            else:
                                if first_reading_after_start:
                                    first_reading_after_start = False

                        last_known_status = current_status_enum
                        # Clear buffer after successful parse
                        last_data_buffer.clear()

            # If no data, sleep briefly
            else:
                time.sleep(0.1)

        except serial.SerialException as e_loop_serial:
            logger.error(
                f"{log_prefix} Serial port error in loop: {e_loop_serial}. Attempting to handle..."
            )
            if _serial_connection and _serial_connection.isOpen():
                _serial_connection.close()
            _serial_connection = None
            time.sleep(5)
            continue
        except Exception as e_loop_other:
            logger.error(
                f"{log_prefix} An unexpected error occurred in the monitoring loop: {e_loop_other}"
            )
            time.sleep(5)

        time.sleep(0.1)  # Reduced sleep time for more responsive readings

    logger.info(f"{log_prefix} Monitoring loop stopped.")


def start_motion_monitoring(home_id: str) -> bool:
    """Initializes and starts the motion sensor monitoring."""
    global _serial_connection, _monitoring_thread, _is_monitoring, _last_state_change_time
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    if _is_monitoring.is_set():
        logger.info(
            f"{log_prefix} Monitoring is already running for HOME_ID: {home_id}. Will not start again."
        )
        return True

    logger.info(
        f"{log_prefix} Attempting to start monitoring for HOME_ID: {home_id} on {DEFAULT_SERIAL_PORT}"
    )

    try:
        device = get_device_by_id(DEVICE_ID)
        if not device:
            logger.info(
                f"{log_prefix} Device not found in DB. Registering with DEVICE_ID: {DEVICE_ID}, NAME: '{DEVICE_NAME}'..."
            )
            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state=PresenceState.UNKNOWN.value,
            )
            logger.info(f"{log_prefix} Device registered successfully.")
        else:
            current_db_state = device.get("current_state", PresenceState.UNKNOWN.value)
            try:
                PresenceState(current_db_state)
            except ValueError:
                logger.error(
                    f"{log_prefix} Invalid state '{current_db_state}' in DB, defaulting to UNKNOWN."
                )
                current_db_state = PresenceState.UNKNOWN.value
            update_device_state(device_id=DEVICE_ID, new_state=current_db_state)

        _last_state_change_time = time.time()
        _is_monitoring.set()
        _monitoring_thread = threading.Thread(
            target=_motion_monitoring_loop,
            args=(home_id,),
        )
        _monitoring_thread.daemon = True
        _monitoring_thread.start()
        logger.info(f"{log_prefix} Monitoring thread started.")
        return True

    except Exception as e_start:
        logger.error(f"{log_prefix} Error starting motion monitoring: {e_start}")
        if _serial_connection and _serial_connection.isOpen():
            _serial_connection.close()
        _serial_connection = None
        _is_monitoring.clear()
        return False


def stop_motion_monitoring() -> None:
    """Stops the motion sensor monitoring and cleans up resources."""
    global _is_monitoring, _monitoring_thread, _serial_connection
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(f"{log_prefix} Attempting to stop monitoring...")
    _is_monitoring.clear()

    if _monitoring_thread and _monitoring_thread.is_alive():
        logger.info(f"{log_prefix} Waiting for monitoring thread to finish...")
        _monitoring_thread.join(timeout=3.0)
        if _monitoring_thread.is_alive():
            logger.error(f"{log_prefix} Monitoring thread did not finish in time.")

    if _serial_connection and _serial_connection.isOpen():
        port_info = (
            _serial_connection.port
            if hasattr(_serial_connection, "port") and _serial_connection.port
            else "the serial port"
        )
        logger.info(f"{log_prefix} Closing {port_info}.")
        _serial_connection.close()
        _serial_connection = None

    _monitoring_thread = None
    logger.info(f"{log_prefix} Monitoring stopped and resources cleaned up.")
