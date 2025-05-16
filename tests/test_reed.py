from unittest.mock import MagicMock

import pytest

from src.sensors.reed import (
    DEVICE_ID,
    DEVICE_NAME,
    DEVICE_TYPE,
    REED_PIN,
    start_reed_monitoring,
)


@pytest.fixture
def mock_button(mocker):
    """Mock gpiozero Button class."""
    # Create a mock class for Button
    mock_button_class = MagicMock()

    # Create a mock instance that will be returned when Button is instantiated
    mock_button_instance = MagicMock()
    mock_button_instance.when_pressed = None
    mock_button_instance.when_released = None
    mock_button_instance.close = MagicMock()
    mock_button_instance.is_pressed = False  # Default state

    # Configure the class mock to return our specific instance upon instantiation
    mock_button_class.return_value = mock_button_instance

    # Patch the Button class in the reed module
    mocker.patch("src.sensors.reed.Button", mock_button_class)

    # Return the mock CLASS and the INSTANCE it produces, for different assertion needs
    return mock_button_class, mock_button_instance


@pytest.fixture
def mock_db_functions(mocker):
    """Mock database utility functions."""
    mocks = {
        "get_device_by_id": mocker.patch("src.sensors.reed.get_device_by_id"),
        "insert_device": mocker.patch("src.sensors.reed.insert_device"),
        "insert_event": mocker.patch("src.sensors.reed.insert_event"),
        "insert_alert": mocker.patch("src.sensors.reed.insert_alert"),
    }
    # Set up default return values
    mocks["get_device_by_id"].return_value = None
    return mocks


def test_start_reed_monitoring_new_device(mock_button, mock_db_functions):
    """Test starting reed monitoring for a new device."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    mock_button_class, _ = mock_button  # We need the class to assert it was called

    # Start monitoring
    start_reed_monitoring(home_id=home_id, user_id=user_id)

    # Verify device registration (current_state is 'open' by default in mock)
    mock_db_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions["insert_device"].assert_called_once_with(
        device_id=DEVICE_ID,
        home_id=home_id,
        name=DEVICE_NAME,
        type=DEVICE_TYPE,
        current_state="open",  # Default based on mock_button_instance.is_pressed = False
    )
    mock_button_class.assert_called_once_with(REED_PIN)


def test_start_reed_monitoring_existing_device(mock_button, mock_db_functions):
    """Test starting reed monitoring for an existing device."""
    home_id = "test_home_123"
    user_id = "test_user_123"
    mock_button_class, _ = mock_button

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
    mock_button_class.assert_called_once_with(REED_PIN)


def test_door_callbacks(mock_button, mock_db_functions):
    """Test door open/close callbacks."""
    home_id = "test_home_123"
    user_id = "test_user_123"

    _, mock_button_instance = mock_button  # We need the instance to trigger callbacks

    # Start monitoring to set up callbacks on mock_button_instance
    start_reed_monitoring(home_id=home_id, user_id=user_id)

    # Reset mock call counts for DB functions after initialization effects
    mock_db_functions["insert_event"].reset_mock()
    mock_db_functions["insert_alert"].reset_mock()
    # Also reset insert_device and get_device_by_id if they were called during init
    # and we only want to test callback effects.
    mock_db_functions["insert_device"].reset_mock()
    mock_db_functions["get_device_by_id"].reset_mock()

    # Test door opened callback (when_released)
    # The when_released attribute on mock_button_instance now holds the functools.partial object
    assert mock_button_instance.when_released is not None
    mock_button_instance.when_released()  # Execute the callback

    mock_db_functions["insert_event"].assert_called_with(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="door_opened",
        old_state="closed",
        new_state="open",
    )
    mock_db_functions["insert_alert"].assert_called_with(
        home_id=home_id,
        user_id=user_id,
        device_id=DEVICE_ID,
        message="Door opened.",
    )

    # Reset mock call counts
    mock_db_functions["insert_event"].reset_mock()
    mock_db_functions["insert_alert"].reset_mock()

    # Test door closed callback (when_pressed)
    assert mock_button_instance.when_pressed is not None
    mock_button_instance.when_pressed()  # Execute the callback

    mock_db_functions["insert_event"].assert_called_with(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="door_closed",
        old_state="open",
        new_state="closed",
    )
    mock_db_functions["insert_alert"].assert_called_with(
        home_id=home_id,
        user_id=user_id,
        device_id=DEVICE_ID,
        message="Door closed.",
    )


def test_stop_reed_monitoring(mocker):
    """Test stopping reed monitoring."""
    # Create a single mock instance for Button
    mock_button_instance = MagicMock()
    mock_button_instance.when_pressed = None
    mock_button_instance.when_released = None
    mock_button_instance.close = MagicMock()
    mock_button_instance.is_pressed = False

    # Patch Button class to always return this specific instance
    mocker.patch("src.sensors.reed.Button", return_value=mock_button_instance)

    # Start monitoring first
    start_reed_monitoring(home_id="test_home_123", user_id="test_user_123")

    # Stop monitoring
    from src.sensors.reed import stop_reed_monitoring as local_stop

    local_stop()

    # Verify cleanup (close should be called on the instance)
    mock_button_instance.close.assert_called_once()


def test_error_handling(mock_button, mock_db_functions, caplog):
    """Test error handling during initialization."""
    mock_button_class, _ = mock_button
    # Configure mock to raise exception on instantiation
    mock_button_class.side_effect = Exception("GPIO Error")

    # Should not raise, but should log an error
    start_reed_monitoring(home_id="test_home_123", user_id="test_user_123")
    assert any(
        "Error starting monitoring: GPIO Error" in m for m in caplog.text.splitlines()
    )

    # Verify no device registration occurred after error
    mock_db_functions["insert_device"].assert_not_called()
    mock_db_functions["insert_event"].assert_not_called()
