import io
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from PIL import Image

from src.utils.cloudflare import upload_file_to_r2
from src.utils.database import (
    get_device_by_id,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger
from src.utils.mqtt import get_mqtt_client, publish_json

# Device configuration
DEVICE_ID = "camera_01"
DEVICE_NAME = "Security Camera"
DEVICE_TYPE = "camera"

# Camera configuration
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_RATE = 30
RECORDING_DURATION_SECONDS = 300  # 5 minutes
VIDEO_FILE_PATH = "recording.h264"

# MQTT topics
MQTT_CAMERA_LIVE_TOPIC = "live"

# Global state
_picamera_object: Optional[Picamera2] = None
_camera_thread: Optional[threading.Thread] = None
_is_running = threading.Event()


def _setup_camera() -> bool:
    """Set up camera hardware and configuration.

    Returns:
        bool: True if setup successful, False otherwise
    """
    global _picamera_object
    try:
        logger.info(f"[{DEVICE_NAME}] Initializing camera...")
        _picamera_object = Picamera2()

        # Configure camera
        config = _picamera_object.create_video_configuration(
            main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"}
        )
        _picamera_object.configure(config)
        _picamera_object.start()

        logger.info(f"[{DEVICE_NAME}] Camera started successfully.")
        return True
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error setting up camera: {e}", exc_info=True)
        if _picamera_object:
            _picamera_object.close()
            _picamera_object = None
        return False


def _setup_mqtt() -> bool:
    """Set up MQTT client connection.

    Returns:
        bool: True if setup successful, False otherwise
    """
    try:
        mqtt_client = get_mqtt_client()
        if not mqtt_client or not mqtt_client.is_connected():
            logger.error(f"[{DEVICE_NAME}] MQTT client not available or not connected.")
            return False
        logger.info(f"[{DEVICE_NAME}] MQTT client connected.")
        return True
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error setting up MQTT: {e}", exc_info=True)
        return False


def _process_and_publish_frame(frame: np.ndarray, home_id: str) -> None:
    """Process and publish a frame via MQTT.

    Args:
        frame: The frame to process and publish
        home_id: The ID of the home this camera belongs to
    """
    try:
        # Convert frame to JPEG
        img = Image.fromarray(frame)
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="JPEG")
        img_byte_arr = img_byte_arr.getvalue()

        # Create message
        message = {
            "home_id": home_id,
            "device_id": DEVICE_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "format": "jpeg",
            "resolution": f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
            "data": img_byte_arr.hex(),
        }

        # Publish frame
        publish_json(MQTT_CAMERA_LIVE_TOPIC, message)

    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error processing/publishing frame: {e}", exc_info=True
        )


def _upload_recording_to_r2(home_id: str) -> bool:
    """Upload the recorded video file to R2 storage.

    Args:
        home_id: The ID of the home this camera belongs to

    Returns:
        bool: True if upload was successful, False otherwise
    """
    if not os.path.exists(VIDEO_FILE_PATH):
        logger.error(
            f"[{DEVICE_NAME}] Video file not found for upload: {VIDEO_FILE_PATH}"
        )
        return False

    try:
        # Generate timestamped filename for R2 upload
        r2_path = f"recording.h264"

        if upload_file_to_r2(VIDEO_FILE_PATH, r2_path):
            logger.info(f"[{DEVICE_NAME}] Video uploaded to R2: {r2_path}")
            try:
                os.remove(VIDEO_FILE_PATH)
            except Exception as e:
                logger.error(
                    f"[{DEVICE_NAME}] Error removing local video file: {e}",
                    exc_info=True,
                )
            return True
        else:
            logger.error(f"[{DEVICE_NAME}] Failed to upload video to R2")
            return False
    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error during R2 upload process: {e}", exc_info=True
        )
        return False


def _handle_recording(
    home_id: str, current_time: float, recording_start_time: float, is_recording: bool
) -> tuple[float, bool]:
    """Handle video recording operations.

    Args:
        home_id: The ID of the home this camera belongs to
        current_time: Current timestamp
        recording_start_time: Start time of current recording
        is_recording: Current recording state

    Returns:
        tuple[float, bool]: Updated recording_start_time and is_recording state
    """
    global _picamera_object

    if not _picamera_object:
        logger.error(
            f"[{DEVICE_NAME}] _picamera_object is None in _handle_recording. Cannot control recording."
        )
        return recording_start_time, is_recording

    if not is_recording:
        logger.info(f"[{DEVICE_NAME}] Starting new recording segment...")
        _picamera_object.start_recording(H264Encoder(), VIDEO_FILE_PATH)
        return current_time, True

    if current_time - recording_start_time >= RECORDING_DURATION_SECONDS:
        logger.info(
            f"[{DEVICE_NAME}] Segment duration reached. Stopping current recording..."
        )
        _picamera_object.stop_recording()
        logger.info(f"[{DEVICE_NAME}] Current recording stopped.")

        # Upload current recording to R2
        _upload_recording_to_r2(home_id)

        # Start new recording segment
        logger.info(f"[{DEVICE_NAME}] Starting new recording segment...")
        _picamera_object.start_recording(H264Encoder(), VIDEO_FILE_PATH)
        logger.info(f"[{DEVICE_NAME}] New recording segment started.")
        return current_time, True

    return recording_start_time, is_recording


def _camera_loop(home_id: str) -> None:
    """Main camera loop for capturing and publishing frames.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    # Log entry to the function immediately
    try:
        logger.info(
            f"[{DEVICE_NAME}] THREAD_DEBUG: _camera_loop function entered for HOME_ID: {home_id}."
        )
    except Exception as e_log_init:
        # Fallback print if logger itself has issues in thread context initially
        print(
            f"PRINT_DEBUG: [{DEVICE_ID}] _camera_loop entered, logger exception: {e_log_init}"
        )

    global _picamera_object

    logger.info(f"[{DEVICE_NAME}] Camera loop thread started for HOME_ID: {home_id}.")

    recording_start_time = time.time()
    is_recording = False
    loop_iteration = 0

    try:
        if not _is_running.is_set():
            logger.warning(
                f"[{DEVICE_NAME}] _is_running is not set at the start of _camera_loop. Exiting loop."
            )
            return

        while _is_running.is_set():
            loop_iteration += 1
            logger.debug(f"[{DEVICE_NAME}] Camera loop iteration: {loop_iteration}")

            # Capture and process frame
            if _picamera_object:
                logger.debug(f"[{DEVICE_NAME}] Attempting to capture frame...")
                try:
                    frame = _picamera_object.capture_array()
                    logger.debug(
                        f"[{DEVICE_NAME}] Frame captured successfully. Shape: {frame.shape if frame is not None else 'None'}"
                    )

                    logger.debug(
                        f"[{DEVICE_NAME}] Attempting to process and publish frame..."
                    )
                    _process_and_publish_frame(frame, home_id)
                    logger.debug(
                        f"[{DEVICE_NAME}] Frame processing and publishing step completed."
                    )

                except Exception as e_capture_publish:
                    logger.error(
                        f"[{DEVICE_NAME}] Error during frame capture or publish: {e_capture_publish}",
                        exc_info=True,
                    )
                    # Optional: short sleep to prevent rapid error logging if in a tight loop of failures
                    time.sleep(1)
                    continue  # Continue to next iteration or exit if _is_running is cleared

            else:
                logger.warning(
                    f"[{DEVICE_NAME}] _picamera_object is None in loop iteration {loop_iteration}. Skipping capture."
                )
                time.sleep(1)  # Wait a bit if camera object is missing

            # Handle video recording
            logger.debug(f"[{DEVICE_NAME}] Handling recording...")
            current_time = time.time()
            try:
                recording_start_time, is_recording = _handle_recording(
                    home_id, current_time, recording_start_time, is_recording
                )
                logger.debug(
                    f"[{DEVICE_NAME}] Recording handled. Is recording: {is_recording}"
                )
            except Exception as e_recording:
                logger.error(
                    f"[{DEVICE_NAME}] Error during _handle_recording: {e_recording}",
                    exc_info=True,
                )
                # Optional: short sleep
                time.sleep(1)
                # Decide if to continue or break, for now, it continues

            logger.debug(
                f"[{DEVICE_NAME}] Sleeping for {1.0 / FRAME_RATE:.3f} seconds..."
            )
            time.sleep(1.0 / FRAME_RATE)

    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Unhandled error in camera_loop: {e}", exc_info=True
        )

    finally:
        if is_recording and _picamera_object:
            logger.info(
                f"[{DEVICE_NAME}] Stopping final recording (from camera_loop finally block)..."
            )
            try:
                _picamera_object.stop_recording()
                logger.info(
                    f"[{DEVICE_NAME}] Final recording stopped (from camera_loop finally block)."
                )
            except Exception as e_stop_final:
                logger.error(
                    f"[{DEVICE_NAME}] Error stopping final recording in finally block: {e_stop_final}",
                    exc_info=True,
                )
        logger.info(f"[{DEVICE_NAME}] Camera loop ended (iteration {loop_iteration}).")


def start_camera_streaming(home_id: str) -> None:
    """Start the camera streaming and recording service.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    # Failsafe print to check if the function is entered AT ALL
    try:
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] Entered start_camera_streaming for HOME_ID: {home_id}"
        )
    except Exception as e_print:
        print(f"PRINT_DEBUG: [{DEVICE_NAME}] Exception in initial print: {e_print}")

    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: start_camera_streaming entered for HOME_ID: {home_id}")
    # Using print for now due to logger issues
    print(
        f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: start_camera_streaming entered for HOME_ID: {home_id}"
    )
    global _picamera_object, _camera_thread

    # logger.info(
    #     f"[{DEVICE_NAME}] Attempting to start streaming and recording for HOME_ID: {home_id}..."
    # )
    print(
        f"PRINT_DEBUG: [{DEVICE_NAME}] Attempting to start streaming and recording for HOME_ID: {home_id}..."
    )

    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Checking existing device state...")
    print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Checking existing device state...")
    # First check if camera is already running
    device = get_device_by_id(DEVICE_ID)  # Initial fetch of device state
    if device and device.get("current_state") == "online":
        # logger.warning(
        #     f"[{DEVICE_NAME}] Camera is already marked as online in database. Will attempt to restart by cleaning up."
        # )
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] Camera is already marked as online in database. Will attempt to restart by cleaning up."
        )
        # Force cleanup of any existing resources
        _cleanup_camera()
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: _cleanup_camera called.")
        print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: _cleanup_camera called.")
        device = None  # Reset device variable so it's re-evaluated later if needed

    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Attempting _setup_camera()...")
    print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Attempting _setup_camera()...")
    if not _setup_camera():
        logger.error(
            f"[{DEVICE_NAME}] Failed to setup camera hardware."
        )  # Keep logger.error for actual errors
        _update_camera_state(home_id, "error", "Failed to initialize camera hardware")
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming due to _setup_camera() failure.")
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming due to _setup_camera() failure."
        )
        return
    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: _setup_camera() successful.")
    print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: _setup_camera() successful.")

    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Attempting _setup_mqtt()...")
    print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Attempting _setup_mqtt()...")
    if not _setup_mqtt():
        logger.error(f"[{DEVICE_NAME}] Failed to setup MQTT.")  # Keep logger.error
        _cleanup_camera()
        _update_camera_state(home_id, "error", "Failed to initialize MQTT")
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming due to _setup_mqtt() failure.")
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming due to _setup_mqtt() failure."
        )
        return
    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: _setup_mqtt() successful.")
    print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: _setup_mqtt() successful.")

    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Entering main try block for device registration and thread start...")
    print(
        f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Entering main try block for device registration and thread start..."
    )
    try:
        # Get or register device. Re-fetch device status here to be certain after potential cleanup.
        current_device_in_db = get_device_by_id(DEVICE_ID)
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Fetched current_device_in_db: {current_device_in_db is not None}")
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Fetched current_device_in_db: {current_device_in_db is not None}"
        )

        if not current_device_in_db:
            # logger.info(
            #     f"[{DEVICE_NAME}] Device not in DB. Registering device..."
            # )
            print(
                f"PRINT_DEBUG: [{DEVICE_NAME}] Device not in DB. Registering device..."
            )
            inserted_device = insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state="initializing",  # Set to initializing, will be updated by _update_camera_state
            )
            if not inserted_device:
                logger.error(
                    f"[{DEVICE_NAME}] Failed to register/insert device into DB."
                )  # Keep logger.error
                _cleanup_camera()
                # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming due to DB insert failure.")
                print(
                    f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming due to DB insert failure."
                )
                return
            # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Device inserted successfully.")
            print(
                f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Device inserted successfully."
            )
        else:
            # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Device already exists in DB.")
            print(
                f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Device already exists in DB."
            )

        # Update state to online and log the event
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Calling _update_camera_state to set 'online'...")
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Calling _update_camera_state to set 'online'..."
        )
        _update_camera_state(home_id, "online", "Camera streaming started")

        # Start camera thread
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Setting _is_running event.")
        print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Setting _is_running event.")
        _is_running.set()
        _camera_thread = threading.Thread(target=_camera_loop, args=(home_id,))
        _camera_thread.daemon = (
            True  # Make thread daemon so it exits when main program exits
        )
        # logger.info(f"[{DEVICE_NAME}] THREAD_DEBUG: Attempting to start _camera_thread...")
        print(
            f"PRINT_DEBUG: [{DEVICE_NAME}] THREAD_DEBUG: Attempting to start _camera_thread..."
        )
        _camera_thread.start()
        # Check if thread is alive immediately after starting
        if _camera_thread.is_alive():
            # logger.info(f"[{DEVICE_NAME}] THREAD_DEBUG: _camera_thread.start() called and thread is alive.")
            print(
                f"PRINT_DEBUG: [{DEVICE_NAME}] THREAD_DEBUG: _camera_thread.start() called and thread is alive."
            )
        else:
            # logger.error(f"[{DEVICE_NAME}] THREAD_DEBUG: _camera_thread.start() was called BUT THREAD IS NOT ALIVE.") # Keep logger.error
            print(
                f"PRINT_DEBUG: [{DEVICE_NAME}] THREAD_DEBUG: _camera_thread.start() was called BUT THREAD IS NOT ALIVE. ERROR WILL BE LOGGED."
            )
            logger.error(
                f"[{DEVICE_NAME}] THREAD_DEBUG: _camera_thread.start() was called BUT THREAD IS NOT ALIVE."
            )
        # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Main try block completed.")
        print(f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Main try block completed.")

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting camera: {e}", exc_info=True)
        _cleanup_camera()
        _update_camera_state(home_id, "error", f"Error starting camera: {str(e)}")
    # logger.info(f"[{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming function.")
    print(
        f"PRINT_DEBUG: [{DEVICE_NAME}] FUNC_DEBUG: Exiting start_camera_streaming function."
    )


def _update_camera_state(home_id: str, new_state: str, message: str) -> None:
    """Update camera state in database and log the event.

    Args:
        home_id: The ID of the home this camera belongs to
        new_state: New state of the camera ('online', 'offline', 'error', 'initializing')
        message: Message describing the state change
    """
    try:
        # Get current state for event logging
        device = get_device_by_id(DEVICE_ID)
        old_state = device.get("current_state") if device else None

        # Update device state
        update_device_state(DEVICE_ID, new_state)

        # Log the event
        insert_event(
            home_id=home_id,
            device_id=DEVICE_ID,
            event_type="camera_state_changed",
            old_state=old_state,
            new_state=new_state,
            event_data={"message": message},
        )

        logger.info(f"[{DEVICE_NAME}] State updated to {new_state}: {message}")
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error updating camera state: {e}", exc_info=True)


def stop_camera_streaming(home_id: str) -> None:
    """Stops the camera streaming and recording service.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    global _picamera_object, _camera_thread, _is_running

    logger.info(f"[{DEVICE_NAME}] Attempting to stop streaming and recording...")

    _is_running.clear()

    if _camera_thread and _camera_thread.is_alive():
        logger.info(f"[{DEVICE_NAME}] Waiting for camera thread to finish...")
        _camera_thread.join(timeout=5.0)
        if _camera_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Camera thread did not finish in time.")

    if _picamera_object:
        try:
            logger.info(f"[{DEVICE_NAME}] Stopping and closing camera...")
            if hasattr(_picamera_object, "recording") and _picamera_object.recording:
                logger.info(
                    f"[{DEVICE_NAME}] Final check: stopping active recording before camera close."
                )
                _picamera_object.stop_recording()
            _picamera_object.close()
            _picamera_object = None
            logger.info(f"[{DEVICE_NAME}] Camera stopped and closed.")

            # Update state to offline
            _update_camera_state(home_id, "offline", "Camera streaming stopped")

        except Exception as e:
            logger.error(f"[{DEVICE_NAME}] Error stopping camera: {e}", exc_info=True)
            _update_camera_state(home_id, "error", f"Error stopping camera: {str(e)}")

    logger.info(
        f"[{DEVICE_NAME}] Streaming and recording stopped, resources cleaned up."
    )


def _cleanup_camera() -> None:
    """Clean up camera resources."""
    global _picamera_object, _is_running

    _is_running.clear()

    if _picamera_object:
        try:
            logger.info(f"[{DEVICE_NAME}] Stopping and closing camera...")
            if hasattr(_picamera_object, "recording") and _picamera_object.recording:
                logger.info(
                    f"[{DEVICE_NAME}] Final check: stopping active recording before camera close."
                )
                _picamera_object.stop_recording()
            _picamera_object.close()
            _picamera_object = None
            logger.info(f"[{DEVICE_NAME}] Camera stopped and closed.")
        except Exception as e:
            logger.error(
                f"[{DEVICE_NAME}] Error cleaning up camera: {e}", exc_info=True
            )
