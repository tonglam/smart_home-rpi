import os
import sys
import threading
import time
from typing import Dict
from unittest.mock import MagicMock

import pytest


# Mock modules before importing camera
def pytest_configure():
    """Configure test environment before running tests."""
    # Mock picamera2 module
    picamera2_mock = MagicMock()
    picamera2_mock.Picamera2 = MagicMock()
    picamera2_mock.encoders = MagicMock()
    picamera2_mock.encoders.MP4Encoder = MagicMock()
    sys.modules["picamera2"] = picamera2_mock
    sys.modules["picamera2.encoders"] = picamera2_mock.encoders


class TimeController:
    """Mock time controller for testing."""

    def __init__(self):
        """Initialize the time controller."""
        self._current_time = 0.0

    def set_time(self, new_time: float) -> None:
        """Set the current time.

        Args:
            new_time: The new time value
        """
        self._current_time = new_time

    def advance(self, delta: float) -> None:
        """Advance time by delta seconds.

        Args:
            delta: The time to advance in seconds
        """
        self._current_time += delta

    def time(self) -> float:
        """Get the current time.

        Returns:
            float: The current time
        """
        return self._current_time


@pytest.fixture
def mock_time(monkeypatch):
    """Mock time functions for testing."""
    controller = TimeController()
    monkeypatch.setattr(time, "time", controller.time)
    monkeypatch.setattr(time, "sleep", lambda x: controller.advance(x))
    return controller


@pytest.fixture
def test_env() -> Dict[str, str]:
    """Test environment configuration.

    Returns:
        Dict[str, str]: Test environment variables
    """
    return {
        "HOME_ID": "test_home_123",
        "FRAME_WIDTH": 640,
        "FRAME_HEIGHT": 480,
        "TEST_TIMEOUT": 2.0,
    }


@pytest.fixture(scope="function")
def mock_threading(mocker):
    """Mock threading components for better test control."""
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    mock_thread.join.return_value = None

    def mock_thread_init(*args, **kwargs):
        thread = mock_thread
        if "target" in kwargs:
            thread.target = kwargs["target"]
        return thread

    mocker.patch("threading.Thread", side_effect=mock_thread_init)
    mocker.patch("threading.Event", return_value=threading.Event())
    return mock_thread


@pytest.fixture(scope="function")
def mock_io(mocker):
    """Mock I/O operations."""
    mock_bytesio = MagicMock()
    mock_bytesio.getvalue.return_value = b"test_image_data"
    mocker.patch("io.BytesIO", return_value=mock_bytesio)
    return mock_bytesio


@pytest.fixture(scope="function")
def mock_base64(mocker):
    """Mock base64 encoding."""
    mock_b64 = MagicMock()
    mock_b64.encode.return_value = b"test_base64_data"
    mock_b64.decode.return_value = "test_base64_string"
    mocker.patch("base64.b64encode", side_effect=mock_b64.encode)
    mocker.patch("base64.b64decode", side_effect=mock_b64.decode)
    return mock_b64


@pytest.fixture(scope="function")
def cleanup_camera():
    """Cleanup camera resources after each test."""
    yield
    from src.sensors.camera import stop_camera_streaming, _camera_thread

    # Stop any active streaming
    stop_camera_streaming()

    # Ensure thread is cleaned up
    if _camera_thread and _camera_thread.is_alive():
        _camera_thread.join(timeout=1.0)
