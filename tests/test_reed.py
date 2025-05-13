from unittest.mock import MagicMock, patch

import pytest

from src.sensors.reed import (
    DEVICE_ID,
    DEVICE_NAME,
    DEVICE_TYPE,
    GPIO_PIN,
    start_reed_monitoring,
    stop_reed_monitoring,
)


@pytest.fixture
def mock_button(mocker):
    """Mock gpiozero Button class."""
    # Create a mock class
    mock_class = MagicMock()

    # Create a mock instance that will be returned when Button is instantiated
    mock_instance = MagicMock()
    mock_instance.when_pressed = None
    mock_instance.when_released = None
    mock_instance.close = MagicMock()

    # Configure the class mock to return our instance
    mock_class.side_effect = lambda pin: mock_instance

    # Patch the Button class
    mocker.patch("src.sensors.reed.Button", mock_class)

    return mock_class


@pytest.fixture
def mock_db_functions(mocker):
    """Mock database utility functions."""
    mocks = {
        "get_device_by_id": mocker.patch("src.sensors.reed.get_device_by_id"),
        "insert_device": mocker.patch("src.sensors.reed.insert_device"),
        "insert_event": mocker.patch("src.sensors.reed.insert_event"),
        "insert_alert": mocker.patch("src.sensors.reed.insert_alert"),
        "get_latest_device_state": mocker.patch(
            "src.sensors.reed.get_latest_device_state"
        ),
    }
    # Set up default return values
    mocks["get_device_by_id"].return_value = None
    mocks["get_latest_device_state"].return_value = "unknown"
    return mocks


def test_start_reed_monitoring_new_device(mock_button, mock_db_functions):
    """Test starting reed monitoring for a new device."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Start monitoring
    start_reed_monitoring(home_id=home_id, user_id=user_id)

    # Verify device registration
    mock_db_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions["insert_device"].assert_called_once_with(
        device_id=DEVICE_ID,
        home_id=home_id,
        name=DEVICE_NAME,
        type=DEVICE_TYPE,
        current_state="unknown",
        location="Main Door",
    )

    # Verify monitoring started
    mock_db_functions["insert_event"].assert_called_once_with(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="status.monitoring",
        old_state="unknown",
        new_state="active",
    )

    # Verify GPIO setup
    mock_button.assert_called_once()
    assert mock_button.call_args[0][0] == GPIO_PIN


def test_start_reed_monitoring_existing_device(mock_button, mock_db_functions):
    """Test starting reed monitoring for an existing device."""
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
    start_reed_monitoring(home_id=home_id, user_id=user_id)

    # Verify no device registration occurred
    mock_db_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions["insert_device"].assert_not_called()

    # Verify monitoring started
    mock_db_functions["insert_event"].assert_called_once()


def test_door_callbacks(mock_button, mock_db_functions):
    """Test door open/close callbacks."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    # Mock initial state
    mock_db_functions["get_latest_device_state"].return_value = "closed"

    # Start monitoring to set up callbacks
    start_reed_monitoring(home_id=home_id, user_id=user_id)

    # Get the mock instance that was created
    mock_instance = mock_button.side_effect(GPIO_PIN)

    # Reset mock call counts after initialization
    mock_db_functions["insert_event"].reset_mock()
    mock_db_functions["insert_alert"].reset_mock()

    # Test door opened callback
    mock_instance.when_released()
    mock_db_functions["insert_event"].assert_called_with(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="security",
        old_state="closed",
        new_state="open",
    )
    mock_db_functions["insert_alert"].assert_called_with(
        home_id=home_id,
        user_id=user_id,
        device_id=DEVICE_ID,
        message=f"{DEVICE_NAME} open.",
    )

    # Reset mock call counts
    mock_db_functions["insert_event"].reset_mock()
    mock_db_functions["insert_alert"].reset_mock()
    mock_db_functions["get_latest_device_state"].return_value = "open"

    # Test door closed callback
    mock_instance.when_pressed()
    mock_db_functions["insert_event"].assert_called_with(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="security",
        old_state="open",
        new_state="closed",
    )
    mock_db_functions["insert_alert"].assert_called_with(
        home_id=home_id,
        user_id=user_id,
        device_id=DEVICE_ID,
        message=f"{DEVICE_NAME} closed.",
    )


def test_stop_reed_monitoring(mock_button):
    """Test stopping reed monitoring."""
    # Get the mock instance that will be created
    mock_instance = mock_button.side_effect(GPIO_PIN)

    # Start monitoring first
    start_reed_monitoring(home_id="test_home_123", user_id="test_user_123")

    # Stop monitoring
    stop_reed_monitoring()

    # Verify cleanup
    mock_instance.close.assert_called_once()


def test_error_handling(mock_button, mock_db_functions):
    """Test error handling during initialization."""
    # Configure mock to raise exception on instantiation
    mock_button.side_effect = Exception("GPIO Error")

    with pytest.raises(Exception) as exc_info:
        start_reed_monitoring(home_id="test_home_123", user_id="test_user_123")

    assert str(exc_info.value) == "GPIO Error"

    # Verify no device registration occurred after error
    mock_db_functions["insert_device"].assert_not_called()
    mock_db_functions["insert_event"].assert_not_called()
