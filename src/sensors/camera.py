import base64
import io
import os
import subprocess
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
RECORDING_DURATION_SECONDS = 30  # 5 minutes
VIDEO_FILE_PATH = "recording.h264"
MP4_FILE_PATH = "recording.mp4"

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
        if not mqtt_client:
            logger.error(
                f"[{DEVICE_NAME}] MQTT client not available (get_mqtt_client failed)."
            )
            return False

        for attempt in range(3):
            if mqtt_client.is_connected():
                logger.info(
                    f"[{DEVICE_NAME}] MQTT client connected successfully in _setup_mqtt."
                )
                return True
            logger.info(
                f"[{DEVICE_NAME}] MQTT client not connected on attempt {attempt + 1}, waiting..."
            )
            time.sleep(1)

        logger.error(f"[{DEVICE_NAME}] MQTT client is not connected after retries.")
        return False

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
            "image_b64": base64.b64encode(img_byte_arr).decode("utf-8"),
        }

        # Publish frame
        publish_json(MQTT_CAMERA_LIVE_TOPIC, message)

    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error processing/publishing frame: {e}", exc_info=True
        )


def _convert_h264_to_mp4(input_path: str, output_path: str) -> bool:
    """Convert H.264 video file to MP4 format using ffmpeg.

    Args:
        input_path: Path to the input H.264 file.
        output_path: Path for the output MP4 file.

    Returns:
        bool: True if conversion was successful, False otherwise.
    """
    if not os.path.exists(input_path):
        logger.error(
            f"[{DEVICE_NAME}] Input H.264 file not found for conversion: {input_path}"
        )
        return False

    logger.info(f"[{DEVICE_NAME}] Converting {input_path} to {output_path}...")
    conversion_command = [
        "ffmpeg",
        "-y",  # Overwrite output file if it exists
        "-i",
        input_path,
        "-c:v",
        "copy",  # No re-encoding, just copy video stream
        output_path,
    ]
    try:
        process = subprocess.run(
            conversion_command, capture_output=True, text=True, check=False
        )

        if process.returncode != 0:
            logger.error(
                f"[{DEVICE_NAME}] ffmpeg conversion failed. Return code: {process.returncode}"
            )
            logger.error(f"[{DEVICE_NAME}] ffmpeg stdout: {process.stdout}")
            logger.error(f"[{DEVICE_NAME}] ffmpeg stderr: {process.stderr}")
            return False
        else:
            logger.info(
                f"[{DEVICE_NAME}] Successfully converted {input_path} to {output_path}"
            )
            return True
    except FileNotFoundError:
        logger.error(
            f"[{DEVICE_NAME}] ffmpeg command not found. Please ensure ffmpeg is installed and in PATH."
        )
        return False
    except subprocess.SubprocessError as e_ffmpeg_run:
        logger.error(
            f"[{DEVICE_NAME}] Error during ffmpeg command execution: {e_ffmpeg_run}",
            exc_info=True,
        )
        if hasattr(e_ffmpeg_run, "stdout") and e_ffmpeg_run.stdout:
            logger.error(f"[{DEVICE_NAME}] ffmpeg stdout: {e_ffmpeg_run.stdout}")
        if hasattr(e_ffmpeg_run, "stderr") and e_ffmpeg_run.stderr:
            logger.error(f"[{DEVICE_NAME}] ffmpeg stderr: {e_ffmpeg_run.stderr}")
        return False


def _process_segment_after_recording_stops() -> None:
    """
    Processes the most recently recorded H264 segment (VIDEO_FILE_PATH):
    Converts to MP4, uploads to R2, and cleans up local files.
    Assumes recording was just stopped and VIDEO_FILE_PATH is the target.
    """
    if not os.path.exists(VIDEO_FILE_PATH):
        logger.warning(
            f"[{DEVICE_NAME}] H264 file {VIDEO_FILE_PATH} not found for post-stop processing. Cannot convert or upload."
        )
        return

    logger.info(f"[{DEVICE_NAME}] Processing segment: {VIDEO_FILE_PATH}")
    conversion_successful = _convert_h264_to_mp4(VIDEO_FILE_PATH, MP4_FILE_PATH)

    if conversion_successful:
        logger.info(
            f"[{DEVICE_NAME}] Conversion to MP4 successful for segment: {MP4_FILE_PATH}"
        )
        _upload_recording_to_r2()
    else:
        logger.error(
            f"[{DEVICE_NAME}] Failed to convert segment {VIDEO_FILE_PATH} to MP4. It will not be uploaded."
        )
        # If conversion fails, MP4_FILE_PATH might be a leftover from ffmpeg; clean it up
        # as _upload_recording_to_r2 won't be called to clean it.
        if os.path.exists(MP4_FILE_PATH):
            try:
                os.remove(MP4_FILE_PATH)
                logger.info(
                    f"[{DEVICE_NAME}] Cleaned up unconverted/failed MP4: {MP4_FILE_PATH}"
                )
            except Exception as e_remove_failed_mp4:
                logger.error(
                    f"[{DEVICE_NAME}] Error removing failed/orphaned MP4 {MP4_FILE_PATH}: {e_remove_failed_mp4}"
                )

    # Clean up the H264 file in all cases after processing attempt
    # Re-check existence as a safeguard, though it should normally exist if we got this far.
    if os.path.exists(VIDEO_FILE_PATH):
        try:
            os.remove(VIDEO_FILE_PATH)
            logger.info(
                f"[{DEVICE_NAME}] Removed local H264 file after processing attempt: {VIDEO_FILE_PATH}"
            )
        except Exception as e_remove_h264:
            logger.error(
                f"[{DEVICE_NAME}] Error removing local H264 file {VIDEO_FILE_PATH}: {e_remove_h264}"
            )


def _handle_recording(
    current_time: float, recording_start_time: float, is_recording: bool
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
        if os.path.exists(VIDEO_FILE_PATH):
            logger.warning(
                f"[{DEVICE_NAME}] Existing H264 file found at start of new segment: {VIDEO_FILE_PATH}. Will be overwritten."
            )
        if os.path.exists(MP4_FILE_PATH):
            logger.warning(
                f"[{DEVICE_NAME}] Existing MP4 file found at start of new segment: {MP4_FILE_PATH}. This might be an orphaned file."
            )
            try:
                os.remove(MP4_FILE_PATH)
                logger.info(
                    f"[{DEVICE_NAME}] Removed potentially orphaned MP4 file: {MP4_FILE_PATH}"
                )
            except Exception as e_remove_orphan:
                logger.error(
                    f"[{DEVICE_NAME}] Error removing orphaned MP4 file {MP4_FILE_PATH}: {e_remove_orphan}"
                )

        _picamera_object.start_recording(H264Encoder(), VIDEO_FILE_PATH)
        return current_time, True

    if current_time - recording_start_time >= RECORDING_DURATION_SECONDS:
        logger.info(
            f"[{DEVICE_NAME}] Segment duration reached. Stopping current recording..."
        )
        _picamera_object.stop_recording()
        logger.info(f"[{DEVICE_NAME}] Current recording stopped: {VIDEO_FILE_PATH}")

        _process_segment_after_recording_stops()  # Process the segment that just stopped

        logger.info(f"[{DEVICE_NAME}] Starting new recording segment...")
        # Safeguard: Clean up any potentially orphaned MP4 file from a previous cycle
        # before starting a new H264 recording. _process_segment_after_recording_stops
        # and _upload_recording_to_r2 should handle their own MP4s, but this is an extra check.
        if os.path.exists(MP4_FILE_PATH):
            logger.warning(
                f"[{DEVICE_NAME}] Cleaning up potentially orphaned MP4 {MP4_FILE_PATH} before new H264 recording start."
            )
            try:
                os.remove(MP4_FILE_PATH)
            except Exception as e_mp4_pre_clean:
                logger.error(
                    f"[{DEVICE_NAME}] Error pre-cleaning orphaned MP4 file {MP4_FILE_PATH}: {e_mp4_pre_clean}"
                )

        _picamera_object.start_recording(H264Encoder(), VIDEO_FILE_PATH)
        logger.info(f"[{DEVICE_NAME}] New recording segment started.")
        return current_time, True

    return recording_start_time, is_recording


def _camera_loop(home_id: str) -> None:
    """Main camera loop for capturing and publishing frames.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    try:
        logger.info(
            f"[{DEVICE_NAME}] _camera_loop function entered for HOME_ID: {home_id}."
        )
    except Exception as e_log_init:
        logger.error(
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

            if _picamera_object:
                try:
                    frame = _picamera_object.capture_array()

                    _process_and_publish_frame(frame, home_id)

                except Exception as e_capture_publish:
                    logger.error(
                        f"[{DEVICE_NAME}] Error during frame capture or publish: {e_capture_publish}",
                        exc_info=True,
                    )
                    time.sleep(1)
                    continue

            else:
                logger.error(
                    f"PRINT_DEBUG: [{DEVICE_NAME}] _picamera_object is None in loop iteration {loop_iteration}. Skipping capture."
                )
                time.sleep(1)

            current_time = time.time()
            try:
                recording_start_time, is_recording = _handle_recording(
                    current_time, recording_start_time, is_recording
                )
            except Exception as e_recording:
                logger.error(
                    f"[{DEVICE_NAME}] Error during _handle_recording: {e_recording}",
                    exc_info=True,
                )
                time.sleep(1)

            time.sleep(1.0 / FRAME_RATE)

    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Unhandled error in camera_loop: {e}", exc_info=True
        )

    finally:
        # Check if the camera object exists and is currently recording
        if (
            _picamera_object
            and hasattr(_picamera_object, "recording")
            and _picamera_object.recording
        ):
            logger.info(
                f"[{DEVICE_NAME}] Loop ending. Stopping and processing final recording segment..."
            )
            try:
                _picamera_object.stop_recording()
                logger.info(
                    f"[{DEVICE_NAME}] Final recording segment stopped: {VIDEO_FILE_PATH}"
                )
                _process_segment_after_recording_stops()  # Process the final segment
            except Exception as e_stop_final:
                logger.error(
                    f"[{DEVICE_NAME}] Error stopping/processing final recording segment: {e_stop_final}",
                    exc_info=True,
                )
        elif (
            is_recording and _picamera_object
        ):  # is_recording was true, but camera says it's not recording
            # This case could occur if the loop exited while is_recording was true,
            # but _picamera_object.recording became false due to an error or other reason
            # before this finally block could stop it. The H264 file might still exist.
            logger.warning(
                f"[{DEVICE_NAME}] Loop ending. 'is_recording' was true, but Picamera2 object shows not currently recording. "
                f"Checking for potentially unprocessed segment {VIDEO_FILE_PATH}."
            )
            if os.path.exists(VIDEO_FILE_PATH):
                logger.info(
                    f"[{DEVICE_NAME}] Found unprocessed H264 segment {VIDEO_FILE_PATH} from 'is_recording' state. Attempting to process."
                )
                _process_segment_after_recording_stops()
            else:
                logger.info(
                    f"[{DEVICE_NAME}] No H264 segment file found at {VIDEO_FILE_PATH} to process based on 'is_recording' state."
                )
        elif not _picamera_object and is_recording:
            logger.warning(
                f"[{DEVICE_NAME}] Loop ending. 'is_recording' was true, but _picamera_object is None. "
                f"Cannot process final segment for {VIDEO_FILE_PATH}."
            )

        logger.info(f"[{DEVICE_NAME}] Camera loop ended (iteration {loop_iteration}).")


def start_camera_streaming(home_id: str) -> None:
    """Start the camera streaming and recording service.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    logger.info(
        f"[{DEVICE_NAME}] start_camera_streaming entered for HOME_ID: {home_id}"
    )
    global _picamera_object, _camera_thread, _is_running

    if _camera_thread and _camera_thread.is_alive() and _is_running.is_set():
        device_db_info = get_device_by_id(DEVICE_ID)
        if device_db_info:
            if device_db_info.get("current_state") == "online":
                logger.info(
                    f"[{DEVICE_NAME}] Camera is already running and DB state is 'online'. No action needed."
                )
                return
            else:
                logger.warning(
                    f"[{DEVICE_NAME}] Camera is running, but DB state is '{device_db_info.get('current_state')}'. Updating DB to 'online'."
                )
                _update_camera_state(
                    home_id, "online", "Reconciled running state to DB"
                )
                return
        else:
            logger.warning(
                f"[{DEVICE_NAME}] Camera process is running, but device not found in DB. Proceeding with stop and full re-initialization."
            )

    logger.info(
        f"[{DEVICE_NAME}] Proceeding with camera start/restart sequence for HOME_ID: {home_id}..."
    )

    logger.info(
        f"[{DEVICE_NAME}] Ensuring previous camera instance is stopped before starting..."
    )
    stop_camera_streaming(home_id)
    logger.info(f"[{DEVICE_NAME}] Previous camera instance stop sequence completed.")

    if not _setup_camera():
        logger.error(f"[{DEVICE_NAME}] Failed to setup camera hardware.")
        _update_camera_state(home_id, "error", "Failed to initialize camera hardware")
        logger.info(
            f"[{DEVICE_NAME}] Exiting start_camera_streaming due to _setup_camera() failure."
        )
        return
    logger.info(f"[{DEVICE_NAME}] _setup_camera() successful.")

    if not _setup_mqtt():
        logger.error(f"[{DEVICE_NAME}] Failed to setup MQTT.")
        _cleanup_camera()
        _update_camera_state(home_id, "error", "Failed to initialize MQTT")
        logger.info(
            f"[{DEVICE_NAME}] Exiting start_camera_streaming due to _setup_mqtt() failure."
        )
        return
    logger.info(f"[{DEVICE_NAME}] _setup_mqtt() successful.")

    logger.info(
        f"[{DEVICE_NAME}] Entering main try block for device registration and thread start..."
    )
    try:
        current_device_in_db = get_device_by_id(DEVICE_ID)
        logger.info(
            f"[{DEVICE_NAME}] Fetched current_device_in_db status: {'Exists' if current_device_in_db else 'Not Found'}"
            f" (State: {current_device_in_db.get('current_state') if current_device_in_db else 'N/A'})"
        )

        if not current_device_in_db:
            logger.info(f"[{DEVICE_NAME}] Device not in DB. Registering device...")
            inserted_device = insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state="initializing",
            )
            if not inserted_device:
                logger.error(
                    f"[{DEVICE_NAME}] Failed to register/insert device into DB."
                )
                _cleanup_camera()  # Clean up hardware
                logger.info(
                    f"[{DEVICE_NAME}] Exiting start_camera_streaming due to DB insert failure."
                )
                return
            logger.info(
                f"[{DEVICE_NAME}] Device inserted successfully with 'initializing' state."
            )
        else:
            logger.info(
                f"[{DEVICE_NAME}] Device already exists in DB (current state: {current_device_in_db.get('current_state')}). Will be updated to 'online'."
            )

        logger.info(f"[{DEVICE_NAME}] Calling _update_camera_state to set 'online'...")
        _update_camera_state(home_id, "online", "Camera streaming started")

        logger.info(f"[{DEVICE_NAME}] Setting _is_running event.")
        _is_running.set()

        if _camera_thread and _camera_thread.is_alive():
            logger.warning(
                f"[{DEVICE_NAME}] _camera_thread unexpectedly alive before new thread creation. This implies an issue in stop logic."
            )
            _camera_thread.join(timeout=2.0)
            if _camera_thread.is_alive():
                logger.error(
                    f"[{DEVICE_NAME}] CRITICAL: Previous camera thread could not be stopped. Aborting start."
                )
                _is_running.clear()
                _cleanup_camera()
                _update_camera_state(
                    home_id, "error", "Failed to stop prior camera thread"
                )
                return

        _camera_thread = threading.Thread(target=_camera_loop, args=(home_id,))
        _camera_thread.daemon = True
        logger.info(f"[{DEVICE_NAME}] Attempting to start _camera_thread...")
        _camera_thread.start()

        if _camera_thread.is_alive():
            logger.info(
                f"[{DEVICE_NAME}] _camera_thread.start() called and thread is alive."
            )
        else:
            logger.error(
                f"[{DEVICE_NAME}] _camera_thread.start() was called BUT THREAD IS NOT ALIVE. Potential issue starting thread."
            )
            _is_running.clear()
            _cleanup_camera()
            _update_camera_state(home_id, "error", "Failed to start camera_loop thread")
            return

        logger.info(
            f"[{DEVICE_NAME}] Main try block for device registration and thread start completed."
        )

    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error during camera start sequence: {e}", exc_info=True
        )
        _cleanup_camera()
        _update_camera_state(home_id, "error", f"Error starting camera: {str(e)}")

    logger.info(f"[{DEVICE_NAME}] Exiting start_camera_streaming function.")


def _update_camera_state(home_id: str, new_state: str, message: str) -> None:
    """Update camera state in database and log the event.

    Args:
        home_id: The ID of the home this camera belongs to
        new_state: New state of the camera ('online', 'offline', 'error', 'initializing')
        message: Message describing the state change
    """
    try:
        device = get_device_by_id(DEVICE_ID)
        old_state = device.get("current_state") if device else None

        update_device_state(DEVICE_ID, new_state)

        if old_state != new_state:
            if new_state == "error":
                logger.warning(
                    f"[{DEVICE_NAME}] State changed from {old_state} to error. Error event not logged: {message}"
                )
            else:
                insert_event(
                    home_id=home_id,
                    device_id=DEVICE_ID,
                    event_type="camera_changed",
                    old_state=old_state,
                    new_state=new_state,
                )
                logger.info(
                    f"[{DEVICE_NAME}] State changed from {old_state} to {new_state}. Event logged: {message}"
                )
        else:
            logger.info(
                f"[{DEVICE_NAME}] State remained {new_state}. No event logged for message: {message}"
            )

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
            logger.info(
                f"[{DEVICE_NAME}] Stopping recording (if active) and closing camera..."
            )
            if hasattr(_picamera_object, "recording") and _picamera_object.recording:
                logger.info(
                    f"[{DEVICE_NAME}] Active recording found. Stopping recording."
                )
                _picamera_object.stop_recording()
                logger.info(f"[{DEVICE_NAME}] Active recording stopped.")

            logger.info(f"[{DEVICE_NAME}] Closing Picamera2 object...")
            _picamera_object.close()
            _picamera_object = None
            logger.info(f"[{DEVICE_NAME}] Picamera2 object closed.")

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
            logger.info(
                f"[{DEVICE_NAME}] Cleaning up camera: stopping recording (if active) and closing..."
            )
            if hasattr(_picamera_object, "recording") and _picamera_object.recording:
                logger.info(
                    f"[{DEVICE_NAME}] Active recording found during cleanup. Stopping recording."
                )
                _picamera_object.stop_recording()
                logger.info(f"[{DEVICE_NAME}] Recording stopped during cleanup.")

            logger.info(f"[{DEVICE_NAME}] Closing Picamera2 object during cleanup...")
            _picamera_object.close()
            _picamera_object = None
            logger.info(f"[{DEVICE_NAME}] Picamera2 object closed during cleanup.")
        except Exception as e:
            logger.error(
                f"[{DEVICE_NAME}] Error cleaning up camera: {e}", exc_info=True
            )


def _upload_recording_to_r2() -> bool:
    """Upload the recorded MP4 video file to R2 storage and clean up the local MP4 file.

    Returns:
        bool: True if upload was successful, False otherwise
    """
    if not os.path.exists(MP4_FILE_PATH):
        logger.error(f"[{DEVICE_NAME}] MP4 file {MP4_FILE_PATH} not found for upload.")
        return False

    logger.info(f"[{DEVICE_NAME}] Uploading {MP4_FILE_PATH} to R2...")
    try:
        upload_successful = upload_file_to_r2(MP4_FILE_PATH)
        if upload_successful:
            logger.info(
                f"[{DEVICE_NAME}] MP4 file {MP4_FILE_PATH} uploaded successfully."
            )
            if os.path.exists(MP4_FILE_PATH):
                try:
                    os.remove(MP4_FILE_PATH)
                    logger.info(
                        f"[{DEVICE_NAME}] Local MP4 file {MP4_FILE_PATH} removed after upload."
                    )
                except Exception as e_remove_mp4:
                    logger.error(
                        f"[{DEVICE_NAME}] Error removing local MP4 file {MP4_FILE_PATH}: {e_remove_mp4}"
                    )
            return True
        else:
            logger.error(f"[{DEVICE_NAME}] MP4 file {MP4_FILE_PATH} upload failed.")
            return False
    except Exception as e_upload:
        logger.error(
            f"[{DEVICE_NAME}] Error uploading MP4 file to R2: {e_upload}", exc_info=True
        )
        return False
