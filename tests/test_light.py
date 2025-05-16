from unittest.mock import MagicMock, patch

import pytest

from src.sensors.light import (
    DEVICE_ID,
    DEVICE_NAME,
    DEVICE_TYPE,
    LED_PIN,
    get_light_intensity,
    initialize_light,
    levels,
    set_light_intensity,
    turn_light_off,
    turn_light_on,
)

# Assuming the PWMLED is on pin 2 as in light.py
LED_PIN = 2


@pytest.fixture(autouse=True)
def reset_light_module():
    """Reset the light module's global state before each test."""
    # Import the module to access its globals
    import src.sensors.light as light_module

    # Reset _led to None
    light_module._led = None
    yield
    # Clean up after test (if needed)
    light_module._led = None


@pytest.fixture
def mock_pwmled(mocker):
    """Mock gpiozero.PWMLED class."""
    mock_led_instance = MagicMock()
    mock_led_instance.value = 0.0  # Default initial value

    # mock_pwmled_class will be the mock for the PWMLED class itself
    mock_pwmled_class = mocker.patch(
        "src.sensors.light.PWMLED", return_value=mock_led_instance
    )
    # mock_pwmled_class.return_value = mock_led_instance # Ensure constructor returns our specific instance

    return (
        mock_led_instance,
        mock_pwmled_class,
    )  # Return both for different assertion needs


@pytest.fixture
def mock_db_functions_light(mocker):
    """Mock database utility functions for light module."""
    mocks = {
        "get_device_by_id": mocker.patch("src.sensors.light.get_device_by_id"),
        "insert_device": mocker.patch("src.sensors.light.insert_device"),
        "update_device_state": mocker.patch("src.sensors.light.update_device_state"),
        "insert_event": mocker.patch("src.sensors.light.insert_event"),
    }
    # Set up default return values
    mocks["get_device_by_id"].return_value = None  # Default to device not found
    return mocks


HOME_ID_TEST = "test_home_light_123"
USER_ID_TEST = "test_user_light_123"


def test_initialize_light_new_device(mock_pwmled, mock_db_functions_light):
    """Test initializing light for a new device."""
    # Arrange
    _, mock_pwmled_class = mock_pwmled  # Unpack the instance and the class mock
    expected_initial_brightness = 0
    expected_initial_state = "off"

    # Act
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)

    # Assert
    mock_pwmled_class.assert_called_once_with(LED_PIN)

    mock_db_functions_light["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions_light["insert_device"].assert_called_once_with(
        device_id=DEVICE_ID,
        home_id=HOME_ID_TEST,
        name=DEVICE_NAME,
        type=DEVICE_TYPE,
        current_state=expected_initial_state,
        brightness=expected_initial_brightness,
    )


def test_initialize_light_new_device_initially_on(mock_pwmled, mock_db_functions_light):
    """Test initializing light for a new device when hardware is initially non-zero."""
    # Arrange
    mock_led_instance, mock_pwmled_class = mock_pwmled
    mock_led_instance.value = 0.5  # Simulate LED being at 0.5 intensity initially
    expected_initial_brightness = 50
    expected_initial_state = "on"

    # Act
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)

    # Assert
    mock_pwmled_class.assert_called_once_with(LED_PIN)
    mock_db_functions_light["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions_light["insert_device"].assert_called_once_with(
        device_id=DEVICE_ID,
        home_id=HOME_ID_TEST,
        name=DEVICE_NAME,
        type=DEVICE_TYPE,
        current_state=expected_initial_state,
        brightness=expected_initial_brightness,
    )


def test_initialize_light_existing_device(mock_pwmled, mock_db_functions_light):
    """Test initializing light for an existing device."""
    # Arrange
    _, mock_pwmled_class = mock_pwmled
    mock_db_functions_light["get_device_by_id"].return_value = {
        "id": DEVICE_ID,
        "home_id": HOME_ID_TEST,
        "name": DEVICE_NAME,
        "type": DEVICE_TYPE,
        "current_state": "on",
        "brightness": 50,
    }

    # Act
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)

    # Assert
    mock_pwmled_class.assert_called_once_with(LED_PIN)
    mock_db_functions_light["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_functions_light["insert_device"].assert_not_called()


def test_set_light_intensity_valid_level(mock_pwmled, mock_db_functions_light):
    """Test setting light intensity to a valid level."""
    # Arrange
    mock_led_instance, _ = mock_pwmled
    initialize_light(
        home_id=HOME_ID_TEST, user_id=USER_ID_TEST
    )  # Ensure initialized state for context

    mock_led_instance.value = 0.0  # Start from off state
    target_level_float = 0.5
    expected_brightness_int = 50
    expected_state_str = "on"

    # Act
    set_light_intensity(home_id=HOME_ID_TEST, level=target_level_float)

    # Assert
    assert mock_led_instance.value == target_level_float
    mock_db_functions_light["update_device_state"].assert_called_once_with(
        DEVICE_ID,
        {"current_state": expected_state_str, "brightness": expected_brightness_int},
    )
    mock_db_functions_light["insert_event"].assert_called_once_with(
        home_id=HOME_ID_TEST,
        device_id=DEVICE_ID,
        event_type="light_intensity_changed",
        old_state="0.0",
        new_state=str(target_level_float),
        event_data={"brightness": expected_brightness_int},
    )


def test_set_light_intensity_turn_off(mock_pwmled, mock_db_functions_light):
    """Test setting light intensity to 0.0 (off)."""
    # Arrange
    mock_led_instance, _ = mock_pwmled
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)

    mock_led_instance.value = 1.0  # Start from on state
    target_level_float = 0.0
    expected_brightness_int = 0
    expected_state_str = "off"

    # Act
    set_light_intensity(home_id=HOME_ID_TEST, level=target_level_float)

    # Assert
    assert mock_led_instance.value == target_level_float
    mock_db_functions_light["update_device_state"].assert_called_once_with(
        DEVICE_ID,
        {"current_state": expected_state_str, "brightness": expected_brightness_int},
    )
    mock_db_functions_light["insert_event"].assert_called_once_with(
        home_id=HOME_ID_TEST,
        device_id=DEVICE_ID,
        event_type="light_intensity_changed",
        old_state="1.0",
        new_state=str(target_level_float),
        event_data={"brightness": expected_brightness_int},
    )


def test_set_light_intensity_invalid_level(mock_pwmled, mock_db_functions_light):
    """Test setting light intensity to an invalid level."""
    # Arrange
    mock_led_instance, _ = mock_pwmled
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)
    invalid_level = 0.7  # Not in defined levels

    # Act & Assert
    with pytest.raises(ValueError) as excinfo:
        set_light_intensity(home_id=HOME_ID_TEST, level=invalid_level)

    assert f"Invalid light intensity level: {invalid_level}" in str(excinfo.value)
    mock_db_functions_light["update_device_state"].assert_not_called()
    mock_db_functions_light["insert_event"].assert_not_called()


def test_turn_light_on(mock_pwmled, mock_db_functions_light):
    """Test turning the light on."""
    # Arrange
    mock_led_instance, _ = mock_pwmled
    # Reset mocks for this specific test if initialize_light was called in a previous one within the same session for other reasons.
    # However, pytest fixtures are typically function-scoped, so they reset. Explicit init is better.
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)
    mock_led_instance.value = 0.0  # Ensure light is off initially
    # Clear mocks from initialize_light call to focus on turn_light_on's effects
    mock_db_functions_light["update_device_state"].reset_mock()
    mock_db_functions_light["insert_event"].reset_mock()

    # Act
    turn_light_on(home_id=HOME_ID_TEST)

    # Assert
    assert mock_led_instance.value == 1.0
    mock_db_functions_light["update_device_state"].assert_called_once_with(
        DEVICE_ID, {"current_state": "on", "brightness": 100}
    )
    mock_db_functions_light["insert_event"].assert_called_once_with(
        home_id=HOME_ID_TEST,
        device_id=DEVICE_ID,
        event_type="light_intensity_changed",
        old_state="0.0",
        new_state="1.0",
        event_data={"brightness": 100},
    )


def test_turn_light_off(mock_pwmled, mock_db_functions_light):
    """Test turning the light off."""
    # Arrange
    mock_led_instance, _ = mock_pwmled
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)
    mock_led_instance.value = 1.0  # Ensure light is on initially
    # Clear mocks from initialize_light call
    mock_db_functions_light["update_device_state"].reset_mock()
    mock_db_functions_light["insert_event"].reset_mock()
    mock_db_functions_light[
        "insert_device"
    ].reset_mock()  # If it was a new device test before
    mock_db_functions_light["get_device_by_id"].reset_mock()

    # Act
    turn_light_off(home_id=HOME_ID_TEST)

    # Assert
    assert mock_led_instance.value == 0.0
    mock_db_functions_light["update_device_state"].assert_called_once_with(
        DEVICE_ID, {"current_state": "off", "brightness": 0}
    )
    mock_db_functions_light["insert_event"].assert_called_once_with(
        home_id=HOME_ID_TEST,
        device_id=DEVICE_ID,
        event_type="light_intensity_changed",
        old_state="1.0",
        new_state="0.0",
        event_data={"brightness": 0},
    )


def test_get_light_intensity(mock_pwmled):
    """Test getting the current light intensity."""
    # Arrange
    mock_led_instance, _ = mock_pwmled
    # Initialize first to set up the _led instance
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)

    expected_intensity = 0.3
    mock_led_instance.value = expected_intensity

    # Act
    actual_intensity = get_light_intensity()

    # Assert
    assert actual_intensity == expected_intensity


def test_get_light_intensity_not_initialized(mock_pwmled):
    """Test getting light intensity when light is not initialized."""
    # Act & Assert
    with pytest.raises(RuntimeError) as excinfo:
        get_light_intensity()
    assert "Light not initialized" in str(excinfo.value)


def test_initialize_light_gpio_error(mock_pwmled, mock_db_functions_light, caplog):
    """Test error handling during light initialization if PWMLED fails."""
    # Arrange
    _, mock_pwmled_class = (
        mock_pwmled  # We need the class mock to change its side_effect
    )
    mock_pwmled_class.side_effect = Exception("GPIO Failure")

    # Act: Call initialize_light, expecting it to catch the error and log it.
    initialize_light(home_id=HOME_ID_TEST, user_id=USER_ID_TEST)

    # Assert
    # Check that an error was logged
    assert any(
        f"[{DEVICE_NAME}] Error during initialization: GPIO Failure" in message
        for message in caplog.text.splitlines()
    )
    # Ensure that database operations like insert_device were not called after the error
    mock_db_functions_light["insert_device"].assert_not_called()


# Placeholder for error handling tests
