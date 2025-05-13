"""Test suite for the camera module."""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import paho.mqtt.client as mqtt
import pytest
from PIL import Image

# Mock picamera2 module and its submodules before importing camera
picamera2_mock = MagicMock()
picamera2_mock.Picamera2 = MagicMock()
picamera2_mock.encoders = MagicMock()
picamera2_mock.encoders.MP4Encoder = MagicMock()

# Mock MQTT client before importing camera
mqtt_client_mock = MagicMock()
mqtt_utils_mock = MagicMock()
mqtt_utils_mock.get_mqtt_client = MagicMock(return_value=mqtt_client_mock)
mqtt_utils_mock.publish_message = MagicMock()

# Mock database functions before importing camera
db_mock = MagicMock()
db_mock.get_device_by_id = MagicMock(return_value=None)
db_mock.insert_device = MagicMock()
db_mock.insert_event = MagicMock()

# Add picamera2 mock to sys.modules
sys.modules["picamera2"] = picamera2_mock
sys.modules["picamera2.encoders"] = picamera2_mock.encoders

# Import after mocks
from src.sensors.camera import (
    DEVICE_ID,
    DEVICE_NAME,
    DEVICE_TYPE,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    MQTT_CAMERA_LIVE_TOPIC,
    RECORDING_DURATION_SECONDS,
    VIDEO_FILE_PATH,
    _camera_loop,
    _camera_thread,
    _is_running,
    _picamera_object,
    _process_and_publish_frame,
    _setup_camera,
    start_camera_streaming,
    stop_camera_streaming,
)

# Test constants
WAIT_TIMEOUT = 5  # seconds
TEST_HOME_ID = "test_home_123"
MQTT_CAMERA_LIVE_TOPIC = "/live"  # Match the actual topic in camera.py


@pytest.fixture(autouse=True)
def check_env_vars():
    """Check if required environment variables are set."""
    required_vars = [
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "MQTT_BROKER_URL",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        pytest.skip(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )


@pytest.fixture(autouse=True)
def reset_camera_state():
    """Reset camera state before each test."""
    global _picamera_object, _is_running
    _is_running.clear()
    _picamera_object = None
    yield
    stop_camera_streaming()


@pytest.fixture(autouse=True)
def mock_mqtt_client(mocker):
    """Mock MQTT client with proper connection state tracking."""
    mock_client_instance = MagicMock(spec=mqtt.Client)
    mock_client_instance.is_connected.return_value = True
    mock_client_instance.published_messages = []

    def mock_publish_general(topic, payload, qos=0, retain=False):
        msg_info = MagicMock()
        msg_info.rc = mqtt.MQTT_ERR_SUCCESS
        return msg_info

    mock_client_instance.publish = MagicMock(side_effect=mock_publish_general)

    mocker.patch("src.utils.mqtt.get_mqtt_client", return_value=mock_client_instance)
    mocker.patch(
        "src.sensors.camera.get_mqtt_client", return_value=mock_client_instance
    )
    mocker.patch(
        "src.utils.mqtt._mqtt_client_instance", new=mock_client_instance, create=True
    )

    return mock_client_instance


@pytest.fixture(autouse=True)
def mock_db(mocker):
    """Mock database with proper state tracking."""
    db_state = {
        "devices": {},
        "events": [],
    }

    def mock_get_device(device_id):
        return db_state["devices"].get(device_id)

    def mock_insert_device(**kwargs):
        now_iso = datetime.now(timezone.utc).isoformat()
        device = {
            "id": kwargs["device_id"],
            "home_id": kwargs["home_id"],
            "name": kwargs["name"],
            "type": kwargs["type"],
            "current_state": kwargs["current_state"],
            "createdAt": now_iso,
            "lastUpdated": now_iso,
        }
        db_state["devices"][kwargs["device_id"]] = device
        return device

    def mock_insert_event(**kwargs):
        now_iso = datetime.now(timezone.utc).isoformat()
        event = {
            "id": len(db_state["events"]) + 1,
            "home_id": kwargs["home_id"],
            "device_id": kwargs["device_id"],
            "event_type": kwargs["event_type"],
            "old_state": kwargs.get("old_state"),
            "new_state": kwargs.get("new_state"),
            "read": False,
            "created_at": now_iso,
        }
        db_state["events"].append(event)
        return event

    # Patch database functions where they are used in camera.py
    mocker.patch("src.sensors.camera.get_device_by_id", side_effect=mock_get_device)
    mocker.patch("src.sensors.camera.insert_device", side_effect=mock_insert_device)
    mocker.patch("src.sensors.camera.insert_event", side_effect=mock_insert_event)

    return db_state


@pytest.fixture(autouse=True)
def mock_picamera2(mocker):
    """Mock Picamera2 module and class."""
    # Create mock camera instance
    mock_camera = MagicMock()

    # Configure video configuration
    mock_config = {"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"}
    mock_camera.create_video_configuration.return_value = mock_config
    mock_camera.configure.return_value = None
    mock_camera.start.return_value = None

    # Configure frame capture
    frame_data = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    mock_camera.capture_array.return_value = frame_data

    # Configure recording state
    mock_camera.recording = False

    # Create a mock Picamera2 class that returns our configured instance
    mock_picamera2_class = MagicMock(return_value=mock_camera)

    # Patch the Picamera2 class
    mocker.patch("src.sensors.camera.Picamera2", mock_picamera2_class)

    return mock_camera


@pytest.fixture
def mock_image_processing(mocker):
    """Mock PIL Image processing."""
    mock_image = MagicMock()

    # Mock image.save to write some test data
    def mock_save(buffer, format):
        buffer.write(b"test_image_data")

    mock_image.save.side_effect = mock_save
    mocker.patch("PIL.Image.fromarray", return_value=mock_image)
    return mock_image


@pytest.fixture(autouse=True)
def mock_cloudflare(mocker):
    """Mock Cloudflare R2 utilities."""
    mock_upload = mocker.patch("src.sensors.camera.upload_file_to_r2")
    mock_upload.return_value = True  # Successful upload by default
    return mock_upload


@pytest.fixture
def mock_mqtt_publishing(mocker, mock_mqtt_client):
    """Mocks src.sensors.camera.publish_json for testing frame publishing logic."""

    def side_effect_publish_json(topic, message_dict):  # Expects dictionary
        # For the test, we can simulate the json.dumps to store what would be sent
        try:
            payload_str = json.dumps(message_dict)
            mock_mqtt_client.published_messages.append(
                {"topic": topic, "payload": payload_str}  # Store the string form
            )
        except Exception as e:
            pytest.fail(f"Mock publish_json failed to dump or append: {e}")
        # The actual publish_json function in src.utils.mqtt is void (returns None).
        # MagicMock default return is fine.

    # Patch where it's used by the code under test (_process_and_publish_frame in src.sensors.camera)
    return mocker.patch(
        "src.sensors.camera.publish_json",
        side_effect=side_effect_publish_json,  # Patch new name
    )


class TestCameraSetup:
    """Test cases for camera setup and initialization."""

    def test_setup_camera_success(self, mock_picamera2):
        """Test successful camera setup."""
        assert _setup_camera() is True
        mock_picamera2.create_video_configuration.assert_called_once()
        mock_picamera2.configure.assert_called_once()
        mock_picamera2.start.assert_called_once()

    def test_setup_camera_failure(self, mock_picamera2):
        """Test camera setup failure handling."""
        mock_picamera2.configure.side_effect = Exception("Camera init failed")
        assert _setup_camera() is False
        mock_picamera2.close.assert_called_once()

    def test_setup_camera_configuration(self, mock_picamera2):
        """Test camera configuration parameters."""
        _setup_camera()
        mock_picamera2.create_video_configuration.assert_called_once_with(
            main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"}
        )


class TestCameraStreaming:
    """Test cases for camera streaming functionality."""

    def test_process_and_publish_frame(
        self, mock_mqtt_client, mock_image_processing, mock_mqtt_publishing
    ):
        """Test frame processing and MQTT publishing."""
        frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
        _process_and_publish_frame(frame, TEST_HOME_ID)

        # Verify published message
        assert len(mock_mqtt_client.published_messages) == 1
        published = mock_mqtt_client.published_messages[0]
        assert published["topic"] == MQTT_CAMERA_LIVE_TOPIC

        message = json.loads(published["payload"])  # Should be the JSON string
        assert message["home_id"] == TEST_HOME_ID
        assert message["device_id"] == DEVICE_ID
        assert message["format"] == "jpeg"
        assert message["resolution"] == f"{FRAME_WIDTH}x{FRAME_HEIGHT}"
        assert "data" in message  # Base64 encoded image data
        assert isinstance(message["data"], str)  # Should be hex string

    def test_start_camera_streaming(self, mock_db, mock_mqtt_client):
        """Test starting camera streaming service."""
        # Start streaming
        start_camera_streaming(TEST_HOME_ID)

        # Verify device registration
        device = mock_db["devices"].get(DEVICE_ID)
        assert device is not None
        assert device["id"] == DEVICE_ID
        assert device["home_id"] == TEST_HOME_ID
        assert device["name"] == DEVICE_NAME
        assert device["type"] == DEVICE_TYPE
        assert device["current_state"] == "online"

        # Verify event logging
        events = mock_db["events"]
        assert len(events) == 1
        event = events[0]
        assert event["home_id"] == TEST_HOME_ID
        assert event["device_id"] == DEVICE_ID
        assert event["event_type"] == "camera_started"
        assert event["old_state"] == "offline"
        assert event["new_state"] == "online"
        assert event["read"] is False

    def test_stop_camera_streaming(
        self, mock_mqtt_client, mock_db, mock_picamera2, mock_cloudflare
    ):
        """Test stopping camera streaming service."""
        # Start first
        start_camera_streaming(TEST_HOME_ID)
        assert _is_running.is_set()

        # Then stop
        stop_camera_streaming()
        assert not _is_running.is_set()

        # Verify stop event was logged
        events = [e for e in mock_db["events"] if e["device_id"] == DEVICE_ID]
        stop_events = [e for e in events if e["event_type"] == "camera_stopped"]
        assert len(stop_events) > 0


class TestVideoRecording:
    """Test cases for video recording functionality."""

    def test_recording_segment_switch(
        self, mock_picamera2, mock_cloudflare, mock_time, test_env, mocker
    ):
        """Test video recording segment switching."""
        # Mock os.path.exists to return True for video file
        mocker.patch("os.path.exists", return_value=True)

        # We need to re-assign these to ensure they are fresh for this test's .called check
        # mock_start_method = MagicMock()
        # mock_stop_method = MagicMock()
        # mock_picamera2.start_recording = mock_start_method
        # mock_picamera2.stop_recording = mock_stop_method
        # Instead, reset the mocks on the fixture's object
        mock_picamera2.start_recording.reset_mock()
        mock_picamera2.stop_recording.reset_mock()

        # Start camera streaming - this will do the *initial* start_recording
        start_camera_streaming(test_env["HOME_ID"])
        time.sleep(0.1)  # Allow camera thread to attempt start_recording

        # Now clear mocks again to only count calls during the segment switch
        mock_picamera2.start_recording.reset_mock()
        mock_picamera2.stop_recording.reset_mock()

        # Advance time to trigger segment switch
        mock_time.set_time(RECORDING_DURATION_SECONDS + 1)
        # mock_time.advance(0.5) # Advancing time here might be less critical than ensuring the loop runs with the new base time
        # The crucial part is that the camera loop's time.time() will now see the advanced time.
        # The time.sleep within the camera loop is also mocked and will advance the controller's time.
        time.sleep(
            1.0
        )  # Increased real sleep to allow camera thread to process with advanced mock time

    def test_recording_failure_handling(
        self, mock_picamera2, mock_cloudflare, test_env
    ):
        """Test handling of recording failures."""
        mock_picamera2.start_recording.side_effect = Exception("Recording failed")

        # Start camera streaming
        start_camera_streaming(test_env["HOME_ID"])
        time.sleep(
            0.1
        )  # Allow camera thread to attempt start_recording and handle error

        # Verify error handling
        assert _is_running.is_set()  # Should keep running despite error
        mock_picamera2.stop_recording.assert_not_called()


class TestErrorHandling:
    """Test cases for error handling scenarios."""

    def test_mqtt_connection_failure(self, mock_mqtt_client, mock_db):
        """Test handling of MQTT connection failure."""
        # Configure MQTT client to be disconnected
        mock_mqtt_client.is_connected.return_value = False

        # Start camera streaming
        start_camera_streaming(TEST_HOME_ID)

        # Verify camera is not running
        assert not _is_running.is_set()

        # Verify no device registration
        device = mock_db["devices"].get(DEVICE_ID)
        assert device is None

        # Verify no events logged
        events = mock_db["events"]
        assert len(events) == 0

    def test_camera_initialization_failure(self, mock_picamera2, mock_db):
        """Test handling of camera initialization failure."""
        mock_picamera2.configure.side_effect = Exception("Camera init failed")
        start_camera_streaming(TEST_HOME_ID)
        assert not _is_running.is_set()

    def test_r2_upload_failure(self, mock_picamera2, mock_cloudflare, mock_db, mocker):
        """Test handling of R2 upload failure."""
        # Mock os.path.exists to return True for video file
        mocker.patch("os.path.exists", return_value=True)
        mock_cloudflare.return_value = False

        # Configure recording methods
        mock_picamera2.start_recording = MagicMock()
        mock_picamera2.stop_recording = MagicMock()

        # Start camera streaming
        start_camera_streaming(TEST_HOME_ID)
        assert _is_running.is_set()
        time.sleep(
            0.1
        )  # Allow camera thread to process and attempt initial start recording
