from unittest.mock import MagicMock

import pytest

from src.sensors.sound import (
    GPIO_PIN_SOUND,
    SOUND_DEVICE_ID,
    SOUND_DEVICE_NAME,
    SOUND_DEVICE_TYPE,
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
    mock_instance.when_activated = None
    mock_instance.close = MagicMock()

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
        "get_latest_device_state": mocker.patch(
            "src.sensors.sound.get_latest_device_state"
        ),
    }
    # Set up default return values
    mocks["get_device_by_id"].return_value = None
    mocks["get_latest_device_state"].return_value = "quiet"
    return mocks


@pytest.fixture
def mock_time(mocker):
    """Mock time.sleep function."""
    return mocker.patch("src.sensors.sound.time.sleep")


def test_start_sound_monitoring_new_device(mock_input_device, mock_db_functions):
    """Test starting sound monitoring for a new device."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Start monitoring
    start_sound_monitoring(home_id=home_id, user_id=user_id)

    # Verify device registration
    mock_db_functions["get_device_by_id"].assert_called_once_with(SOUND_DEVICE_ID)
    mock_db_functions["insert_device"].assert_called_once_with(
        device_id=SOUND_DEVICE_ID,
        home_id=home_id,
        name=SOUND_DEVICE_NAME,
        type=SOUND_DEVICE_TYPE,
        current_state="quiet",
        location="General Area",
    )

    # Verify monitoring started
    mock_db_functions["insert_event"].assert_called_once_with(
        home_id=home_id,
        device_id=SOUND_DEVICE_ID,
        event_type="status.monitoring",
        old_state="quiet",
        new_state="active",
    )

    # Verify GPIO setup
    mock_input_device.assert_called_once()
    assert mock_input_device.call_args[0][0] == GPIO_PIN_SOUND
    assert mock_input_device.call_args[1]["pull_up"] is False


def test_start_sound_monitoring_existing_device(mock_input_device, mock_db_functions):
    """Test starting sound monitoring for an existing device."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Mock existing device
    mock_db_functions["get_device_by_id"].return_value = {
        "device_id": SOUND_DEVICE_ID,
        "home_id": home_id,
        "name": SOUND_DEVICE_NAME,
        "type": SOUND_DEVICE_TYPE,
    }

    # Start monitoring
    start_sound_monitoring(home_id=home_id, user_id=user_id)

    # Verify no device registration occurred
    mock_db_functions["get_device_by_id"].assert_called_once_with(SOUND_DEVICE_ID)
    mock_db_functions["insert_device"].assert_not_called()

    # Verify monitoring started
    mock_db_functions["insert_event"].assert_called_once()


def test_sound_detection_callback(mock_input_device, mock_db_functions, mock_time):
    """Test sound detection callback with debouncing."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Start monitoring to set up callbacks
    start_sound_monitoring(home_id=home_id, user_id=user_id)

    # Get the mock instance that was created
    mock_instance = mock_input_device.side_effect(GPIO_PIN_SOUND, pull_up=False)

    # Reset mock call counts after initialization
    mock_db_functions["insert_event"].reset_mock()
    mock_db_functions["insert_alert"].reset_mock()

    # Store original callback for later comparison
    original_callback = mock_instance.when_activated

    # Trigger the callback
    original_callback()

    # Verify event and alert were logged
    mock_db_functions["insert_event"].assert_called_with(
        home_id=home_id,
        device_id=SOUND_DEVICE_ID,
        event_type="sound_detection",
        old_state="quiet",
        new_state="sound_detected",
    )
    mock_db_functions["insert_alert"].assert_called_with(
        home_id=home_id,
        user_id=user_id,
        device_id=SOUND_DEVICE_ID,
        message=f"{SOUND_DEVICE_NAME} detected a sound.",
    )

    # Verify debouncing behavior
    mock_time.assert_called_once_with(2)  # 2-second debounce delay
    assert mock_instance.when_activated == original_callback  # Callback restored


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


def test_error_handling(mock_input_device, mock_db_functions):
    """Test error handling during initialization."""
    # Configure mock to raise exception on instantiation
    mock_input_device.side_effect = Exception("GPIO Error")

    with pytest.raises(Exception) as exc_info:
        start_sound_monitoring(home_id="test_home_123", user_id="test_user_123")

    assert str(exc_info.value) == "GPIO Error"

    # Verify no device registration occurred after error
    mock_db_functions["insert_device"].assert_not_called()
    mock_db_functions["insert_event"].assert_not_called()
