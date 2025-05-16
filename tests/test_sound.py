from unittest.mock import MagicMock

import pytest

from src.sensors.sound import (
    DEVICE_ID,
    DEVICE_NAME,
    DEVICE_TYPE,
    GPIO_PIN_SOUND,
    start_sound_monitoring,
    stop_sound_monitoring,
)


@pytest.fixture
def mock_input_device(mocker):
    """Mock gpiozero InputDevice class."""
    # Create a mock class
    mock_class = MagicMock()

    # Create a mock instance that will be returned when InputDevice is instantiated
    mock_instance = MagicMock()
    mock_instance.close = MagicMock()
    mock_instance.is_active = False

    # Configure the class mock to return our instance
    mock_class.side_effect = lambda pin, pull_up: mock_instance

    # Patch the InputDevice class
    mocker.patch("src.sensors.sound.InputDevice", mock_class)

    return mock_class


@pytest.fixture
def mock_db_functions(mocker):
    """Mock database utility functions."""
    mocks = {
        "get_device_by_id": mocker.patch("src.sensors.sound.get_device_by_id"),
        "insert_device": mocker.patch("src.sensors.sound.insert_device"),
        "insert_event": mocker.patch("src.sensors.sound.insert_event"),
        "insert_alert": mocker.patch("src.sensors.sound.insert_alert"),
    }
    # Set up default return values
    mocks["get_device_by_id"].return_value = None
    return mocks


@pytest.fixture
def mock_time(mocker):
    """Mock time.sleep function."""
    return mocker.patch("src.sensors.sound.time.sleep")


@pytest.fixture(autouse=True)
def cleanup_sound():
    yield
    stop_sound_monitoring()


def test_start_sound_monitoring_new_device(mock_input_device, mock_db_functions):
    """Test starting sound monitoring for a new device."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Start monitoring
    start_sound_monitoring(home_id=home_id, user_id=user_id)

    # Verify device registration
    mock_db_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions["insert_device"].assert_called_once_with(
        device_id=DEVICE_ID,
        home_id=home_id,
        name=DEVICE_NAME,
        type=DEVICE_TYPE,
        current_state="idle",
    )

    # Verify monitoring started (event and alert are called in loop, not on start)
    mock_input_device.assert_called_once()
    assert mock_input_device.call_args[0][0] == GPIO_PIN_SOUND
    assert mock_input_device.call_args[1]["pull_up"] is False


def test_start_sound_monitoring_existing_device(mock_input_device, mock_db_functions):
    """Test starting sound monitoring for an existing device."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Mock existing device
    mock_db_functions["get_device_by_id"].return_value = {
        "device_id": DEVICE_ID,
        "home_id": home_id,
        "name": DEVICE_NAME,
        "type": DEVICE_TYPE,
    }

    # Start monitoring
    start_sound_monitoring(home_id=home_id, user_id=user_id)

    # Verify no device registration occurred
    mock_db_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions["insert_device"].assert_not_called()

    # Verify monitoring started (event and alert are called in loop, not on start)
    mock_input_device.assert_called_once()
    assert mock_input_device.call_args[0][0] == GPIO_PIN_SOUND
    assert mock_input_device.call_args[1]["pull_up"] is False


def test_sound_detection_callback(mock_input_device, mock_db_functions, mocker, caplog):
    """Test sound detection callback with debouncing."""
    import threading
    import time

    home_id = "test_home_123"
    user_id = "test_user_123"

    # Set up the mock to simulate is_active True once, then False
    mock_instance = mock_input_device.side_effect(GPIO_PIN_SOUND, pull_up=False)
    is_active_sequence = [True, False]

    def is_active_side_effect():
        return is_active_sequence.pop(0) if is_active_sequence else False

    type(mock_instance).is_active = property(lambda self: is_active_side_effect())

    # Patch time.sleep to fast-forward
    mocker.patch("src.sensors.sound.time.sleep", return_value=None)

    # Start monitoring to set up callbacks
    start_sound_monitoring(home_id=home_id, user_id=user_id)

    # Run the monitoring loop in a thread
    from src.sensors.sound import _is_monitoring, _sound_monitoring_loop

    monitor_thread = threading.Thread(
        target=_sound_monitoring_loop, args=(home_id, user_id)
    )
    _is_monitoring.set()
    monitor_thread.start()

    # Allow the thread to run briefly
    time.sleep(0.05)
    _is_monitoring.clear()
    monitor_thread.join(timeout=1.0)

    # Verify event and alert were logged
    mock_db_functions["insert_event"].assert_called_with(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="sound_detected",
        old_state=None,
        new_state="detected",
    )
    mock_db_functions["insert_alert"].assert_called_with(
        home_id=home_id,
        user_id=user_id,
        device_id=DEVICE_ID,
        message="Sound detected.",
    )


def test_stop_sound_monitoring(mock_input_device):
    """Test stopping sound monitoring."""
    # Get the mock instance that will be created
    mock_instance = mock_input_device.side_effect(GPIO_PIN_SOUND, pull_up=False)

    # Start monitoring first
    start_sound_monitoring(home_id="test_home_123", user_id="test_user_123")

    # Stop monitoring
    stop_sound_monitoring()

    # Verify cleanup
    mock_instance.close.assert_called_once()


def test_error_handling(mock_input_device, mock_db_functions, caplog):
    """Test error handling during initialization."""
    # Configure mock to raise exception on instantiation
    mock_input_device.side_effect = Exception("GPIO Error")

    # Should not raise, but should log an error
    start_sound_monitoring(home_id="test_home_123", user_id="test_user_123")
    assert any(
        "Error starting monitoring: GPIO Error" in m for m in caplog.text.splitlines()
    )

    # Verify no device registration occurred after error
    mock_db_functions["insert_device"].assert_not_called()
    mock_db_functions["insert_event"].assert_not_called()
