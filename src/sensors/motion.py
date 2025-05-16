import os
import threading
import time
from enum import Enum
from typing import Optional

import serial

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

# --- Default serial port configuration ---
DEFAULT_SERIAL_PORT = "/dev/ttyS0"
DEFAULT_BAUD_RATE = 115200

# --- Global state for the sensor module ---
_serial_connection: Optional[serial.Serial] = None
_monitoring_thread: Optional[threading.Thread] = None
_is_monitoring = threading.Event()


# --- Enum for Presence States ---
class PresenceState(Enum):
    NO_PRESENCE = "no_presence"
    MOVING_PRESENCE = "moving_presence"
    STILL_PRESENCE = "still_presence"
    UNKNOWN = "unknown"


def parse_mmwave_state(state_byte: int) -> PresenceState:
    """Parses the state byte from the mmWave sensor into a PresenceState enum member."""
    if state_byte == 0x00:
        return PresenceState.NO_PRESENCE
    elif state_byte == 0x01:
        return PresenceState.MOVING_PRESENCE
    elif state_byte == 0x02:
        return PresenceState.STILL_PRESENCE
    else:
        logger.warning(
            f"[{DEVICE_ID}] Unknown mmWave sensor state byte: {hex(state_byte)}"
        )
        return PresenceState.UNKNOWN


def _motion_monitoring_loop(home_id: str) -> None:
    """Internal loop that reads sensor data, processes, and logs it."""
    global _serial_connection
    log_prefix = f"[{DEVICE_ID} ({DEVICE_NAME})]"

    logger.info(
        f"{log_prefix} Monitoring loop started for HOME_ID: {home_id} on port {DEFAULT_SERIAL_PORT}."
    )

    first_reading_after_start = True

    while _is_monitoring.is_set():
        if _serial_connection is None or not _serial_connection.isOpen():
            logger.error(
                f"{log_prefix} Serial connection lost or not open. Attempting to reconnect..."
            )
            try:
                _serial_connection = serial.Serial(
                    DEFAULT_SERIAL_PORT, DEFAULT_BAUD_RATE, timeout=1
                )
                logger.info(
                    f"{log_prefix} Successfully re-opened serial port {DEFAULT_SERIAL_PORT}."
                )
            except serial.SerialException as e_serial:
                logger.error(
                    f"{log_prefix} Failed to re-open serial port {DEFAULT_SERIAL_PORT}: {e_serial}. Retrying in 5s."
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
            data = _serial_connection.read(7)
            if len(data) == 7 and data[0] == 0xA5 and data[1] == 0x5A:
                state_byte = data[4]
                current_status_enum = parse_mmwave_state(state_byte)
                current_status_str = current_status_enum.value

                if current_status_enum == PresenceState.UNKNOWN:
                    time.sleep(1)
                    continue

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
                                logger.warning(f"{log_prefix} {alert_message}")
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
                else:
                    if first_reading_after_start:
                        first_reading_after_start = False

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
            print(
                f"DEBUG: Unexpected error in loop: {type(e_loop_other).__name__}, {e_loop_other}"
            )  # DEBUG PRINT
            time.sleep(5)

        time.sleep(1)

    logger.info(f"{log_prefix} Monitoring loop stopped.")


def start_motion_monitoring(home_id: str) -> bool:
    """Initializes and starts the motion sensor monitoring."""
    global _serial_connection, _monitoring_thread, _is_monitoring
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
            current_db_state = device.get("currentState", PresenceState.UNKNOWN.value)
            try:
                PresenceState(current_db_state)
            except ValueError:
                logger.error(
                    f"{log_prefix} Invalid state '{current_db_state}' in DB, defaulting to UNKNOWN."
                )
                current_db_state = PresenceState.UNKNOWN.value
            update_device_state(device_id=DEVICE_ID, new_state=current_db_state)

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
