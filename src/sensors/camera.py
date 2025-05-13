import io
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import MP4Encoder
from PIL import Image

from src.utils.cloudflare import upload_file_to_r2
from src.utils.database import get_device_by_id, insert_device, insert_event
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
VIDEO_FILE_PATH = "recording.mp4"

# MQTT topics
MQTT_CAMERA_LIVE_TOPIC = "/live"

# Global state
_picamera_object: Optional[Picamera2] = None
_camera_thread: Optional[threading.Thread] = None
_is_running = threading.Event()
_current_home_id: Optional[str] = None


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
        logger.error(f"[{DEVICE_NAME}] Error setting up camera: {e}")
        if _picamera_object:
            _picamera_object.close()
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
        logger.error(f"[{DEVICE_NAME}] Error setting up MQTT: {e}")
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
        logger.error(f"[{DEVICE_NAME}] Error processing/publishing frame: {e}")


def _camera_loop(home_id: str) -> None:
    """Main camera loop for capturing and publishing frames.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    global _picamera_object

    logger.info(f"[{DEVICE_NAME}] Camera loop started for HOME_ID: {home_id}.")

    recording_start_time = time.time()
    is_recording = False

    try:
        while _is_running.is_set():
            # Capture and process frame
            if _picamera_object:
                frame = _picamera_object.capture_array()
                _process_and_publish_frame(frame, home_id)

            # Handle video recording
            current_time = time.time()
            if not is_recording:
                logger.info(f"[{DEVICE_NAME}] Starting new recording segment...")
                _picamera_object.start_recording(MP4Encoder(), VIDEO_FILE_PATH)
                recording_start_time = current_time
                is_recording = True
            elif current_time - recording_start_time >= RECORDING_DURATION_SECONDS:
                logger.info(
                    f"[{DEVICE_NAME}] Segment duration reached. Stopping current recording..."
                )
                _picamera_object.stop_recording()
                logger.info(f"[{DEVICE_NAME}] Current recording stopped.")

                # Upload to R2 if file exists
                if os.path.exists(VIDEO_FILE_PATH):
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    r2_path = f"{home_id}/{DEVICE_ID}/{timestamp}.mp4"
                    if upload_file_to_r2(VIDEO_FILE_PATH, r2_path):
                        logger.info(f"[{DEVICE_NAME}] Video uploaded to R2: {r2_path}")
                        try:
                            os.remove(VIDEO_FILE_PATH)
                        except Exception as e:
                            logger.error(
                                f"[{DEVICE_NAME}] Error removing local video file: {e}"
                            )
                    else:
                        logger.error(f"[{DEVICE_NAME}] Failed to upload video to R2")

                # Start new recording segment
                logger.info(f"[{DEVICE_NAME}] Starting new recording segment...")
                _picamera_object.start_recording(MP4Encoder(), VIDEO_FILE_PATH)
                recording_start_time = current_time
                logger.info(f"[{DEVICE_NAME}] New recording segment started.")

            time.sleep(1.0 / FRAME_RATE)

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error in camera loop: {e}")

    finally:
        if is_recording and _picamera_object:
            logger.info(f"[{DEVICE_NAME}] Stopping final recording...")
            _picamera_object.stop_recording()
            logger.info(f"[{DEVICE_NAME}] Final recording stopped.")
        logger.info(f"[{DEVICE_NAME}] Camera loop ended.")


def start_camera_streaming(home_id: str) -> None:
    """Start the camera streaming and recording service.

    Args:
        home_id: The ID of the home this camera belongs to
    """
    global _picamera_object, _camera_thread, _current_home_id, _is_running

    logger.info(
        f"[{DEVICE_NAME}] Attempting to start streaming and recording for HOME_ID: {home_id}..."
    )

    _current_home_id = home_id

    if not _setup_camera():
        return

    if not _setup_mqtt():
        _cleanup_camera()
        return

    # Get or register device
    try:
        device = get_device_by_id(DEVICE_ID)
        if not device:
            logger.info(
                f"[{DEVICE_NAME}] Device ID {DEVICE_ID} not found in DB. Registering..."
            )
            device = insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state="online",
            )
            if not device:
                logger.error(f"[{DEVICE_NAME}] Failed to register device.")
                _cleanup_camera()
                return

        # Log camera start event
        event = insert_event(
            home_id=home_id,
            device_id=DEVICE_ID,
            event_type="camera_started",
            old_state="offline",
            new_state="online",
        )
        if not event:
            logger.error(f"[{DEVICE_NAME}] Failed to log camera start event.")
            _cleanup_camera()
            return

        # Start camera thread
        _is_running.set()
        _camera_thread = threading.Thread(target=_camera_loop, args=(home_id,))
        _camera_thread.start()

    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error starting camera: {e}")
        _cleanup_camera()


def stop_camera_streaming() -> None:
    """Stops the camera streaming and recording service."""
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

            # Log camera stop event
            if _current_home_id:
                event = insert_event(
                    home_id=_current_home_id,
                    device_id=DEVICE_ID,
                    event_type="camera_stopped",
                    old_state="online",
                    new_state="offline",
                )
                if not event:
                    logger.error(f"[{DEVICE_NAME}] Failed to log camera stop event.")

        except Exception as e:
            logger.error(f"[{DEVICE_NAME}] Error stopping camera: {e}")

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
            logger.error(f"[{DEVICE_NAME}] Error cleaning up camera: {e}")


if __name__ == "__main__":
    logger.info(f"[{DEVICE_NAME}] Standalone Test Mode")

    test_home_id = "00:1A:22:33:44:55"
    test_user_id = "test_user"

    def signal_handler(sig):
        logger.info(
            f"\n[{DEVICE_NAME} Standalone] Signal {sig} received. Initiating shutdown..."
        )
        stop_camera_streaming()
        logger.info(f"[{DEVICE_NAME} Standalone] Shutdown complete.")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        start_camera_streaming(home_id=test_home_id)

        if _is_running.is_set():
            logger.info(
                f"[{DEVICE_NAME} Standalone] Camera streaming started. Press Ctrl+C to stop."
            )
            while _is_running.is_set():
                time.sleep(1)
        else:
            logger.error(
                f"[{DEVICE_NAME} Standalone] Failed to start camera streaming. Check logs."
            )
            stop_camera_streaming()
            sys.exit(1)

    except Exception as e:
        logger.error(f"[{DEVICE_NAME} Standalone] An unexpected error occurred: {e}")
        stop_camera_streaming()
    finally:
        logger.info(f"[{DEVICE_NAME} Standalone] Exiting standalone mode.")
        if _is_running.is_set() or _picamera_object is not None:
            stop_camera_streaming()
        sys.exit(0)
