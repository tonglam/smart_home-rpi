"""
Camera Module for Video Streaming

This module manages a Raspberry Pi camera for video streaming and motion detection.
It provides real-time video streaming over MQTT and can detect motion events
in the video stream.

Hardware Setup:
    - Raspberry Pi Camera Module v2
    - Resolution: 1280x720 (720p)
    - Framerate: 30 fps
    - Auto white balance
    - Auto exposure

Features:
    - Live video streaming over MQTT
    - Motion detection
    - Frame compression
    - Automatic exposure control
    - Error recovery
    - Resource management

States:
    - streaming: Camera is actively streaming
    - stopped: Camera is not streaming
    - error: Hardware/streaming error

Events:
    - motion_detected: When motion is detected
    - stream_started: When streaming begins
    - stream_stopped: When streaming ends
    - error: When camera errors occur

Dependencies:
    - picamera2: For camera control
    - opencv-python: For image processing
    - numpy: For frame manipulation
    - mqtt: For video streaming
"""

import base64
import io
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, JpegEncoder
from picamera2.outputs import FileOutput
from PIL import Image

from src.utils.cloudflare import upload_file_to_r2
from src.utils.database import (
    get_device_by_id,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger
from src.utils.mqtt import get_mqtt_client, publish_frame, publish_json

# Device configuration
DEVICE_ID = "camera_01"
DEVICE_NAME = "Security Camera"
DEVICE_TYPE = "pi_camera"

# Camera configuration
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_RATE = 30
RECORDING_DURATION_SECONDS = 300  # 5 minutes
VIDEO_FILE_PATH = "recording.h264"
MP4_FILE_PATH = "recording.mp4"

# MQTT topics
MQTT_CAMERA_LIVE_TOPIC = f"camera/{DEVICE_ID}/live"

# Global state
_picamera_object: Optional[Picamera2] = None
_camera_thread: Optional[threading.Thread] = None
_is_running = threading.Event()
_last_motion_time = 0.0
_last_frame = None


def _setup_camera() -> bool:
    """Set up camera hardware and configuration.

    Returns:
        bool: True if setup successful, False otherwise
    """
    global _picamera_object
    try:
        logger.info(f"[{DEVICE_NAME}] Initializing camera...")

        # First try to kill any existing camera processes
        try:
            logger.info(
                f"[{DEVICE_NAME}] Checking for and killing existing camera processes..."
            )
            # Check for processes using the camera
            subprocess.run(
                ["sudo", "fuser", "-k", "/dev/video0"],
                capture_output=True,
                text=True,
                check=False,
            )
            # Check specifically for libcamera processes
            subprocess.run(
                ["pkill", "-f", "libcamera"],
                capture_output=True,
                text=True,
                check=False,
            )
            # Add a delay to ensure processes are terminated
            time.sleep(3)
        except Exception as e_ps:
            logger.warning(
                f"[{DEVICE_NAME}] Unable to kill existing camera processes: {e_ps}"
            )

        # Add a delay before trying to initialize camera
        time.sleep(2)

        # Attempt to initialize the camera with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"[{DEVICE_NAME}] Attempting to initialize camera (attempt {attempt+1}/{max_retries})..."
                )
                _picamera_object = Picamera2()

                # Configure camera
                config = _picamera_object.create_video_configuration(
                    main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"}
                )
                _picamera_object.configure(config)

                # Add a delay after configure before starting
                time.sleep(1)

                _picamera_object.start()

                logger.info(
                    f"[{DEVICE_NAME}] Camera started successfully on attempt {attempt+1}."
                )
                return True
            except RuntimeError as e:
                if "Failed to acquire camera: Device or resource busy" in str(e):
                    logger.warning(
                        f"[{DEVICE_NAME}] Camera is busy on attempt {attempt+1}. Trying again after cleanup..."
                    )
                    # Try to kill processes again
                    try:
                        subprocess.run(
                            ["sudo", "fuser", "-k", "/dev/video0"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    except Exception:
                        pass
                    # Wait longer with each retry
                    time.sleep(3 + attempt * 2)
                    # Cleanup any partially initialized camera object
                    if _picamera_object:
                        try:
                            _picamera_object.close()
                        except Exception:
                            pass
                        _picamera_object = None
                elif "Permission denied" in str(e):
                    logger.error(
                        f"[{DEVICE_NAME}] Permission denied accessing camera. Ensure the user has video group permissions."
                    )
                    # Suggest a fix
                    logger.info(
                        f"[{DEVICE_NAME}] Try: sudo usermod -a -G video $USER && sudo reboot"
                    )
                    return False
                else:
                    logger.error(
                        f"[{DEVICE_NAME}] Error setting up camera: {e}", exc_info=True
                    )
                    return False

                # If this was the last attempt, log the failure
                if attempt == max_retries - 1:
                    logger.error(
                        f"[{DEVICE_NAME}] Failed to initialize camera after {max_retries} attempts."
                    )
                    return False
            except Exception as e:
                logger.error(
                    f"[{DEVICE_NAME}] Unexpected error setting up camera: {e}",
                    exc_info=True,
                )
                if _picamera_object:
                    try:
                        _picamera_object.close()
                    except Exception:
                        pass
                    _picamera_object = None
                return False

        return False  # This should not be reached, but added for clarity
    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error in overall camera setup: {e}", exc_info=True
        )
        if _picamera_object:
            try:
                _picamera_object.close()
            except Exception:
                pass
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

            frame_captured_this_iteration = False
            if _picamera_object:
                try:
                    frame = _picamera_object.capture_array()
                    _process_and_publish_frame(frame, home_id)
                    frame_captured_this_iteration = True
                except Exception as e_capture_publish:
                    logger.error(
                        f"[{DEVICE_NAME}] Error during frame capture or publish: {e_capture_publish}",
                        exc_info=True,
                    )
                    # If capture fails, perhaps the camera is in a bad state.
                    # Consider stopping the loop or re-initializing.
                    # For now, just sleep and continue, relying on external restart if persistent.
                    time.sleep(1)
                    # Continue to the next iteration without trying to handle recording for this failed attempt
                    continue
            else:
                logger.error(
                    f"PRINT_DEBUG: [{DEVICE_NAME}] _picamera_object is None in loop iteration {loop_iteration}. Skipping capture and recording handling."
                )
                time.sleep(1)  # Sleep if object is None
                # Continue to the next iteration if camera object is None
                continue

            # Only proceed to handle recording if a frame was successfully captured and picamera_object is valid
            # The check for _picamera_object here is somewhat redundant due to the continue above, but adds clarity.
            if frame_captured_this_iteration and _picamera_object:
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
                    # If recording handling fails, sleep and continue the loop.
                    time.sleep(1)
            elif not _picamera_object:
                # This case should ideally be caught by the `else` block above and `continue` executed.
                # Adding log here for safety in case logic flow changes.
                logger.warning(
                    f"[{DEVICE_NAME}] _picamera_object became None before recording handling in loop iteration {loop_iteration}. Skipping."
                )

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
        if new_state == "error":
            logger.error(f"[{DEVICE_NAME}] Camera error reported: {message}")
            # Do not update device state in DB or log an event for errors
            return

        device = get_device_by_id(DEVICE_ID)
        old_state = device.get("current_state") if device else None

        # Proceed with DB update only for non-error states
        update_device_state(DEVICE_ID, new_state)

        if old_state != new_state:
            # Event logging will only occur for non-error state changes here
            insert_event(
                home_id=home_id,
                device_id=DEVICE_ID,
                event_type="camera_changed",
                old_state=old_state,
                new_state=new_state,
            )
            logger.info(
                f"[{DEVICE_NAME}] State changed from {old_state} to {new_state}. Event logged. Message: {message}"
            )
        else:
            # This case handles when the state is confirmed but not changed (e.g. already online)
            logger.info(
                f"[{DEVICE_NAME}] State remained {new_state}. No event logged. Message: {message}"
            )

    except Exception as e:
        logger.error(
            f"[{DEVICE_NAME}] Error in _update_camera_state function itself: {e}",
            exc_info=True,
        )


def stop_camera_streaming(home_id: str) -> None:
    """Stops the camera streaming and recording service.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    global _picamera_object, _camera_thread, _is_running

    logger.info(f"[{DEVICE_NAME}] Attempting to stop streaming and recording...")

    _is_running.clear()  # Signal the loop to stop

    if _camera_thread and _camera_thread.is_alive():
        logger.info(f"[{DEVICE_NAME}] Waiting for camera thread to finish...")
        _camera_thread.join(timeout=5.0)
        if _camera_thread.is_alive():
            logger.warning(f"[{DEVICE_NAME}] Camera thread did not finish in time.")
        else:
            logger.info(f"[{DEVICE_NAME}] Camera thread finished.")
    _camera_thread = None  # Clear the thread reference

    if _picamera_object:
        camera_operations_successful = False
        try:
            logger.info(
                f"[{DEVICE_NAME}] Finalizing camera object: stopping recording (if active) and closing..."
            )
            if hasattr(_picamera_object, "recording") and _picamera_object.recording:
                logger.info(
                    f"[{DEVICE_NAME}] Active recording found in stop_camera_streaming. Stopping recording."
                )
                _picamera_object.stop_recording()
                logger.info(
                    f"[{DEVICE_NAME}] Recording stopped via stop_camera_streaming."
                )

            logger.info(
                f"[{DEVICE_NAME}] Closing Picamera2 object via stop_camera_streaming..."
            )
            _picamera_object.close()
            logger.info(
                f"[{DEVICE_NAME}] Picamera2 object closed via stop_camera_streaming."
            )
            camera_operations_successful = True

        except Exception as e_stop:
            logger.error(
                f"[{DEVICE_NAME}] Error during camera stop/close in stop_camera_streaming: {e_stop}",
                exc_info=True,
            )
            # Force release camera resources if normal close fails
            try:
                logger.info(
                    f"[{DEVICE_NAME}] Attempting alternative method to force close camera..."
                )
                # Try more aggressively to release the camera
                if hasattr(_picamera_object, "close"):
                    # Try multiple times
                    for i in range(3):
                        try:
                            _picamera_object.close()
                            logger.info(
                                f"[{DEVICE_NAME}] Forced camera close succeeded on attempt {i+1}"
                            )
                            camera_operations_successful = True
                            break
                        except Exception as e_retry:
                            logger.warning(
                                f"[{DEVICE_NAME}] Retry {i+1} to close camera failed: {e_retry}"
                            )
                            time.sleep(0.5)
            except Exception as e_force:
                logger.error(f"[{DEVICE_NAME}] Force close also failed: {e_force}")

            # If all else fails, try to kill processes using the camera
            if not camera_operations_successful:
                try:
                    logger.info(
                        f"[{DEVICE_NAME}] Attempting to identify and release camera resources..."
                    )
                    # Check for processes using video devices
                    subprocess.run(
                        ["sudo", "fuser", "-k", "/dev/video0"],
                        capture_output=True,
                        text=True,
                    )
                    logger.info(
                        f"[{DEVICE_NAME}] Sent kill signal to processes using camera"
                    )
                except Exception as e_kill:
                    logger.error(
                        f"[{DEVICE_NAME}] Failed to kill camera processes: {e_kill}"
                    )

            _update_camera_state(
                home_id, "error", f"Error stopping camera: {str(e_stop)}"
            )
        finally:
            _picamera_object = None
            logger.info(
                f"[{DEVICE_NAME}] _picamera_object set to None in stop_camera_streaming."
            )

        if camera_operations_successful:
            _update_camera_state(home_id, "offline", "Camera streaming stopped")
    else:
        logger.info(
            f"[{DEVICE_NAME}] _picamera_object was already None in stop_camera_streaming. No camera operations to perform."
        )
        # If it was supposed to be running, ensure state is offline if no error was previously set
        device_state = get_device_by_id(DEVICE_ID)
        if device_state and device_state.get("current_state") not in [
            "error",
            "offline",
        ]:
            _update_camera_state(
                home_id, "offline", "Camera confirmed offline (was already stopped)"
            )

    # Add a small delay before returning to allow any system resources to be fully released
    time.sleep(1)
    logger.info(f"[{DEVICE_NAME}] Stop_camera_streaming sequence completed.")


def _cleanup_camera() -> None:
    """Clean up camera resources."""
    global _picamera_object, _is_running

    logger.info(f"[{DEVICE_NAME}] Initiating _cleanup_camera sequence...")
    _is_running.clear()  # Ensure loop signal is off

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
            logger.info(f"[{DEVICE_NAME}] Picamera2 object closed during cleanup.")
        except Exception as e_cleanup_cam:
            logger.error(
                f"[{DEVICE_NAME}] Error during camera resource cleanup: {e_cleanup_cam}",
                exc_info=True,
            )
            # Force release camera resources if normal close fails
            try:
                logger.info(
                    f"[{DEVICE_NAME}] Attempting alternative cleanup method to force close camera..."
                )
                # Try more aggressively to release the camera
                if hasattr(_picamera_object, "close"):
                    # Try multiple times with increasing delays
                    for i in range(3):
                        try:
                            _picamera_object.close()
                            logger.info(
                                f"[{DEVICE_NAME}] Forced camera close succeeded on cleanup attempt {i+1}"
                            )
                            break
                        except Exception as e_retry:
                            logger.warning(
                                f"[{DEVICE_NAME}] Cleanup retry {i+1} to close camera failed: {e_retry}"
                            )
                            time.sleep(1)  # Longer delay with each retry
            except Exception as e_force:
                logger.error(
                    f"[{DEVICE_NAME}] Force close during cleanup also failed: {e_force}"
                )

            # If everything else fails, try to kill processes
            try:
                logger.info(
                    f"[{DEVICE_NAME}] Last resort: attempting to release camera system resources..."
                )
                # Try to identify and kill processes using the camera
                subprocess.run(
                    ["sudo", "fuser", "-k", "/dev/video0"],
                    capture_output=True,
                    text=True,
                )
                # Add a delay after the kill command
                time.sleep(2)
                logger.info(
                    f"[{DEVICE_NAME}] Kill signal sent to processes using camera"
                )
            except Exception as e_kill:
                logger.error(
                    f"[{DEVICE_NAME}] Failed to kill camera processes: {e_kill}"
                )
        finally:
            _picamera_object = None
            logger.info(
                f"[{DEVICE_NAME}] _picamera_object set to None in _cleanup_camera."
            )
    else:
        logger.info(
            f"[{DEVICE_NAME}] _picamera_object was already None in _cleanup_camera."
        )

    # Add a delay to allow system resources to be fully released
    time.sleep(1)
    logger.info(f"[{DEVICE_NAME}] _cleanup_camera sequence completed.")


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
