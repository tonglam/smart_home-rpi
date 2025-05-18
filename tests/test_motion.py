import logging
import time
from unittest import mock

import pytest
import serial

from src.sensors import motion
from src.sensors.motion import PresenceState

TEST_HOME_ID = "test_home_motion_001"
TEST_DEVICE_ID = motion.DEVICE_ID


MMWAVE_NO_PRESENCE_PACKET = (
    b"\xa5\x5a\x00\x00\x00\x00\x00"  # State 0x00 (NO_PRESENCE) at index 4
)
MMWAVE_MOVING_PRESENCE_PACKET = (
    b"\xa5\x5a\x00\x00\x01\x00\x00"  # State 0x01 (MOVING_PRESENCE) at index 4
)
MMWAVE_STILL_PRESENCE_PACKET = (
    b"\xa5\x5a\x00\x00\x02\x00\x00"  # State 0x02 (STILL_PRESENCE) at index 4
)
MMWAVE_UNKNOWN_PACKET = (
    b"\xa5\x5a\x00\x00\x03\x00\x00"  # State 0x03 (UNKNOWN) at index 4
)


@pytest.fixture(autouse=True)
def reset_motion_module_state_fixture():
    """Fixture to reset the global state of the motion module before each test."""
    # Stop monitoring if it's running from a previous test
    if motion._is_monitoring.is_set():
        motion.stop_motion_monitoring()

    # Clear event and thread explicitly
    motion._is_monitoring.clear()
    if motion._monitoring_thread and motion._monitoring_thread.is_alive():
        motion._monitoring_thread.join(timeout=0.1)  # Short timeout for cleanup
    motion._monitoring_thread = None

    # Close serial connection if it exists and is open
    if motion._serial_connection and motion._serial_connection.isOpen():
        try:
            motion._serial_connection.close()
        except Exception:
            pass  # Ignore errors during test cleanup of mock
    motion._serial_connection = None

    motion._current_home_id = None
    yield  # Test runs here
    # Teardown: repeat stop to ensure cleanup if test started monitoring
    if motion._is_monitoring.is_set():
        motion.stop_motion_monitoring()
    motion._is_monitoring.clear()
    if motion._monitoring_thread and motion._monitoring_thread.is_alive():
        motion._monitoring_thread.join(timeout=0.1)
    motion._monitoring_thread = None
    if motion._serial_connection and motion._serial_connection.isOpen():
        try:
            motion._serial_connection.close()
        except Exception:
            pass
    motion._serial_connection = None


@pytest.fixture
def mock_serial_fixture():
    """Mocks the serial.Serial object and its methods using create_autospec for robustness."""

    mock_ser_instance = mock.create_autospec(serial.Serial, instance=True)

    # serial.Serial.isOpen() is a method. create_autospec makes this a MagicMock.
    # Configure its return_value.
    mock_ser_instance.isOpen.return_value = True

    # Other default behaviors
    mock_ser_instance.in_waiting = 0
    mock_ser_instance.read.return_value = (
        b""  # Method: read() returns empty bytes by default
    )
    mock_ser_instance.write.return_value = None  # Method: write() returns None

    # Configure the close() method to affect isOpen's return value
    def custom_close_side_effect():
        mock_ser_instance.isOpen.return_value = (
            False  # Change the return_value of the isOpen mock
        )
        return None  # close() usually returns None

    mock_ser_instance.close.side_effect = custom_close_side_effect
    # .port should be handled by create_autospec if it's a standard attribute/property.
    # The serial.Serial constructor typically sets self.port. The autospecced mock constructor
    # should mimic this if 'port' is part of the spec.

    mock_serial_constructor = mock.create_autospec(
        serial.Serial, return_value=mock_ser_instance
    )

    # Patch serial.Serial in the module where it's imported and used (e.g., motion.py)
    # For these tests, serial is imported directly, so patching "serial.Serial" is global if not careful.
    # Assuming motion.py does "import serial" then "serial.Serial()"
    with mock.patch(
        "serial.Serial", new=mock_serial_constructor
    ) as patched_constructor_mock:
        # Yield the constructor mock (which is 'serial.Serial' itself during the patch)
        # and the default instance it will return. Tests can then further configure this instance.
        yield patched_constructor_mock, mock_ser_instance


@pytest.fixture
def mock_db_functions_fixture():
    """Mocks all relevant database functions with smarter state handling."""

    # This dictionary simulates a tiny in-memory store for the device's state.
    # It allows get_latest_device_state to reflect changes made by update_device_state.
    mock_device_db_store = {"latest_state": None}

    def custom_get_latest_device_state(
        home_id: str, device_id: str
    ):  # Added type hints
        # In a more complex scenario, device_id could be used as a key if multiple devices were tested.
        return mock_device_db_store["latest_state"]

    def custom_update_device_state(device_id: str, new_state: str):  # Added type hints
        mock_device_db_store["latest_state"] = new_state
        return True  # Simulate successful DB update

    # Default behavior for get_device_by_id (can be overridden by tests)
    # Store it to allow tests to modify its return_value/side_effect if needed for specific scenarios
    # without re-patching.
    _mock_get_device_obj = mock.Mock(return_value=None)

    with mock.patch(
        "src.sensors.motion.get_device_by_id", new=_mock_get_device_obj
    ) as mock_get_device, mock.patch(
        "src.sensors.motion.insert_device"
    ) as mock_insert_device, mock.patch(
        "src.sensors.motion.update_device_state", side_effect=custom_update_device_state
    ) as mock_update_state, mock.patch(
        "src.sensors.motion.get_latest_device_state",
        side_effect=custom_get_latest_device_state,
    ) as mock_get_latest, mock.patch(
        "src.sensors.motion.insert_event"
    ) as mock_insert_event:

        # Ensure insert_device mock is also sensible, e.g., returns a device_id or True
        mock_insert_device.return_value = {
            "id": TEST_DEVICE_ID
        }  # Or simply True if it indicates success

        yield {
            "get_device_by_id": mock_get_device,  # This is actually _mock_get_device_obj due to `new=`
            "insert_device": mock_insert_device,
            "update_device_state": mock_update_state,
            "get_latest_device_state": mock_get_latest,
            "insert_event": mock_insert_event,
            "mock_device_db_store": mock_device_db_store,  # Expose for tests to set initial state
        }


@pytest.fixture
def mock_time_sleep_fixture():
    """Mocks time.sleep to prevent tests from actually sleeping, but allows thread to yield."""
    # Keep a reference to the original time.sleep
    original_sleep = time.sleep

    def custom_sleep(seconds):
        # Perform a very short, real sleep to allow other threads to run.
        # This helps prevent the monitoring thread from spinning too tightly
        # and allows it to recognize _is_monitoring.clear() more reliably.
        original_sleep(0.0001)  # Tiny actual sleep
        # The mock object itself can still be used for assertions if needed,
        # but its primary role here is to control/observe calls to time.sleep.
        # We don't need to call mock_sleep(seconds) because the mock.patch
        # replaces time.sleep with mock_sleep object itself.
        # If we wanted to track calls on it, we would call mock_sleep_instance(seconds)
        # but here the goal is to modify behavior.

    with mock.patch("time.sleep", side_effect=custom_sleep) as mock_sleep_object:
        yield mock_sleep_object


def test_start_motion_monitoring_new_device(
    mock_serial_fixture, mock_db_functions_fixture, caplog, mock_time_sleep_fixture
):
    """Test starting monitoring for a new device."""
    caplog.set_level(logging.INFO)
    mock_serial_constructor, _ = mock_serial_fixture

    # Initial condition: device not found
    mock_db_functions_fixture["get_device_by_id"].return_value = None
    # Initial condition: no prior state in our mock DB store
    mock_db_functions_fixture["mock_device_db_store"]["latest_state"] = None

    success = motion.start_motion_monitoring(home_id=TEST_HOME_ID)
    assert success is True
    assert motion._is_monitoring.is_set()
    assert motion._monitoring_thread is not None
    assert motion._monitoring_thread.is_alive()

    mock_serial_constructor.assert_called_once_with(
        motion.DEFAULT_SERIAL_PORT, motion.DEFAULT_BAUD_RATE, timeout=1
    )
    mock_db_functions_fixture["get_device_by_id"].assert_called_once_with(
        TEST_DEVICE_ID
    )
    mock_db_functions_fixture["insert_device"].assert_called_once_with(
        device_id=TEST_DEVICE_ID,
        home_id=TEST_HOME_ID,
        name=motion.DEVICE_NAME,
        type=motion.DEVICE_TYPE,
        current_state=PresenceState.UNKNOWN.value,
    )
    assert f"Attempting to start monitoring for HOME_ID: {TEST_HOME_ID}" in caplog.text
    assert (
        f"Device not found in DB. Registering with DEVICE_ID: {TEST_DEVICE_ID}"
        in caplog.text
    )
    assert "Monitoring thread started" in caplog.text

    motion.stop_motion_monitoring()  # Cleanup


def test_start_motion_monitoring_existing_device(
    mock_serial_fixture, mock_db_functions_fixture, caplog, mock_time_sleep_fixture
):
    """Test starting monitoring for an existing device."""
    caplog.set_level(logging.INFO)
    # Initial condition: device exists
    existing_device_state = PresenceState.NO_PRESENCE.value
    mock_db_functions_fixture["get_device_by_id"].return_value = {
        "id": TEST_DEVICE_ID,
        "name": motion.DEVICE_NAME,
        "current_state": existing_device_state,  # This is from the main DB representation
    }
    # Initial condition: set our mock DB store to reflect this existing state
    mock_db_functions_fixture["mock_device_db_store"][
        "latest_state"
    ] = existing_device_state

    success = motion.start_motion_monitoring(home_id=TEST_HOME_ID)
    assert success is True
    mock_db_functions_fixture["insert_device"].assert_not_called()
    mock_db_functions_fixture["update_device_state"].assert_called_once_with(
        device_id=TEST_DEVICE_ID, new_state=PresenceState.NO_PRESENCE.value
    )
    motion.stop_motion_monitoring()  # Cleanup


def test_start_already_running(mock_serial_fixture, caplog, mock_time_sleep_fixture):
    """Test attempting to start monitoring when it's already running."""
    caplog.set_level(logging.INFO)
    motion.start_motion_monitoring(home_id=TEST_HOME_ID)  # Start it once
    first_thread = motion._monitoring_thread

    success = motion.start_motion_monitoring(home_id=TEST_HOME_ID)  # Try to start again
    assert success is True
    assert motion._monitoring_thread == first_thread  # Should be the same thread
    # assert "Monitoring is already running" in caplog.text
    log_found = any(
        "Monitoring is already running" in record.message and record.levelname == "INFO"
        for record in caplog.records
    )
    assert log_found, "Expected 'Monitoring is already running' INFO log not found."
    motion.stop_motion_monitoring()  # Cleanup


def test_stop_motion_monitoring(mock_serial_fixture, caplog, mock_time_sleep_fixture):
    """Test stopping the monitoring."""
    caplog.set_level(logging.INFO)
    mock_serial_constructor, mock_ser_instance = mock_serial_fixture

    motion.start_motion_monitoring(home_id=TEST_HOME_ID)
    assert motion._is_monitoring.is_set()
    thread_before_stop = motion._monitoring_thread

    motion.stop_motion_monitoring()

    assert not motion._is_monitoring.is_set()
    assert motion._serial_connection is None
    if mock_ser_instance:
        mock_ser_instance.close.assert_called_once()
    assert "Monitoring stopped and resources cleaned up." in caplog.text
    if thread_before_stop:
        assert not thread_before_stop.is_alive()  # Thread should have joined


@mock.patch("time.sleep")  # Mock sleep globally for this test
def test_monitoring_loop_state_change(
    mock_time_sleep_fixture, mock_serial_fixture, mock_db_functions_fixture, caplog
):
    """Test the monitoring loop detecting a state change."""
    caplog.set_level(logging.INFO)
    _, mock_ser_instance = mock_serial_fixture

    # Initial state in our mock DB store before monitoring loop acts
    initial_db_state = PresenceState.NO_PRESENCE.value
    mock_db_functions_fixture["mock_device_db_store"]["latest_state"] = initial_db_state

    # Simulate sensor sending moving presence data indefinitely
    mock_ser_instance.read.return_value = MMWAVE_MOVING_PRESENCE_PACKET

    motion.start_motion_monitoring(home_id=TEST_HOME_ID)
    time.sleep(
        0.1
    )  # Allow a moment for the thread to process first read if not mocked properly

    # Check database interactions for state change
    # Need to wait a bit for thread, or use a more robust sync mechanism if test is flaky
    # For now, relying on multiple reads and then stopping

    # Let the loop run a few times to process
    for _ in range(3):
        if mock_db_functions_fixture["update_device_state"].called:
            break
        time.sleep(0.05)  # short sleep

    mock_db_functions_fixture["update_device_state"].assert_called_with(
        device_id=TEST_DEVICE_ID, new_state=PresenceState.MOVING_PRESENCE.value
    )
    mock_db_functions_fixture["insert_event"].assert_called_with(
        home_id=TEST_HOME_ID,
        device_id=TEST_DEVICE_ID,
        event_type="presence",  # Changed from presence_update based on actual call if old_state_str is None
        old_state=initial_db_state,  # This was PresenceState.NO_PRESENCE.value
        new_state=PresenceState.MOVING_PRESENCE.value,
    )
    assert (
        f"State changed from '{PresenceState.NO_PRESENCE.value}' to '{PresenceState.MOVING_PRESENCE.value}'"
        in caplog.text
    )

    motion.stop_motion_monitoring()


@mock.patch("time.sleep")
def test_monitoring_loop_initial_state_none(
    mock_time_sleep_fixture, mock_serial_fixture, mock_db_functions_fixture, caplog
):
    """Test monitoring loop with no previous state (device new or first event)."""
    caplog.set_level(logging.INFO)
    _, mock_ser_instance = mock_serial_fixture

    # Initial state in our mock DB store is None (no previous state)
    mock_db_functions_fixture["mock_device_db_store"]["latest_state"] = None

    # Simulate sensor sending no presence data indefinitely
    mock_ser_instance.read.return_value = (
        MMWAVE_NO_PRESENCE_PACKET  # Revert to return_value
    )

    motion.start_motion_monitoring(home_id=TEST_HOME_ID)
    time.sleep(0.1)  # Allow thread to process

    for _ in range(3):
        if mock_db_functions_fixture["insert_event"].called:
            break
        time.sleep(0.05)

    # First, assert update_device_state was called correctly
    mock_db_functions_fixture["update_device_state"].assert_called_with(
        device_id=TEST_DEVICE_ID, new_state=PresenceState.NO_PRESENCE.value
    )
    # Then, assert insert_event was called correctly
    mock_db_functions_fixture["insert_event"].assert_called_with(
        home_id=TEST_HOME_ID,
        device_id=TEST_DEVICE_ID,
        event_type="presence",  # Changed from presence_detected_initial based on motion.py
        old_state=None,
        new_state=PresenceState.NO_PRESENCE.value,
    )
    assert (
        f"First state detected after start: '{PresenceState.NO_PRESENCE.value}'"
        in caplog.text
    )

    motion.stop_motion_monitoring()


@mock.patch("time.sleep")
def test_monitoring_loop_serial_exception_reconnect(
    mock_time_sleep_fixture, mock_serial_fixture, mock_db_functions_fixture, caplog
):
    """Test handling of serial.SerialException during reads."""
    caplog.set_level(logging.INFO)
    mock_serial_constructor, mock_ser_instance_orig = mock_serial_fixture

    # Mock for the new serial instance after reconnect attempt
    mock_ser_instance_new = mock.MagicMock()  # Use MagicMock for simplicity here
    mock_ser_instance_new.isOpen = mock.Mock(
        return_value=True
    )  # isOpen is a mock method returning True
    mock_ser_instance_new.read.side_effect = [
        MMWAVE_MOVING_PRESENCE_PACKET,  # Now uses new packet def
        MMWAVE_MOVING_PRESENCE_PACKET,
    ]

    # Configure the close() method to affect the isOpen mock method for the new instance
    def custom_close_new_instance():
        mock_ser_instance_new.isOpen.return_value = (
            False  # Change return value of isOpen mock
        )
        return None

    mock_ser_instance_new.close.side_effect = custom_close_new_instance
    mock_ser_instance_new.port = motion.DEFAULT_SERIAL_PORT

    mock_ser_instance_orig.read.side_effect = [
        MMWAVE_NO_PRESENCE_PACKET,  # Initial successful read (new packet def)
        serial.SerialException("Test serial error"),  # Error
        # Loop should attempt reconnect, new instance will be used then
    ]
    # Constructor returns original, then new instance upon "reconnect"
    mock_serial_constructor.side_effect = [
        mock_ser_instance_orig,
        mock_ser_instance_new,
        mock_ser_instance_new,
    ]  # Allow multiple calls

    # Set initial state in our mock DB store for state change detection after reconnect
    initial_state_before_error = PresenceState.NO_PRESENCE.value
    mock_db_functions_fixture["mock_device_db_store"][
        "latest_state"
    ] = initial_state_before_error

    # Ensure get_device_by_id returns a device so start_monitoring doesn't try to insert one,
    # and uses the existing state if logic relies on it.
    # The start_motion_monitoring will call update_device_state with current_db_state.
    # We want the loop's get_latest_device_state to pick up from initial_state_before_error.
    mock_db_functions_fixture["get_device_by_id"].return_value = {
        "id": TEST_DEVICE_ID,
        "name": motion.DEVICE_NAME,
        "current_state": initial_state_before_error,
    }

    motion.start_motion_monitoring(home_id=TEST_HOME_ID)

    # Allow time for multiple loop iterations: initial read, error, reconnect, new read
    for _ in range(15):  # Increased iterations for more processing time
        if (
            mock_db_functions_fixture["update_device_state"].call_count >= 2
        ):  # Initial state + state after reconnect
            break
        time.sleep(0.01)

    # Check that the error was logged and reconnect was successful
    log_text_found = any(
        "Serial port error in loop: Test serial error. Attempting to handle..."
        in record.message
        for record in caplog.records
        if record.levelname == "ERROR"
    )
    assert log_text_found, "Expected serial error log not found in ERROR records"

    assert (
        f"Successfully re-opened serial port {motion.DEFAULT_SERIAL_PORT}"
        in caplog.text
    )
    # Ensure the "failed to re-open" message is NOT present
    assert (
        f"Failed to re-open serial port {motion.DEFAULT_SERIAL_PORT}" not in caplog.text
    )

    # Check that after reconnect, the new state (MOVING_PRESENCE) was processed
    # The first call to update_device_state is during start_motion_monitoring (e.g. to UNKNOWN or initial NO_PRESENCE)
    # or from the first successful read before the error.
    # The key is to find the update to MOVING_PRESENCE after the simulated error and reconnect.

    # Allow more time for the processing after reconnect, as the loop in test was short
    # and might not have caught the update_device_state call after successful processing by mock_ser_instance_new
    final_update_call_count = mock_db_functions_fixture[
        "update_device_state"
    ].call_count
    for _ in range(20):  # Extra time for post-reconnect processing
        if (
            mock_db_functions_fixture["update_device_state"].call_count
            > final_update_call_count
        ):
            # A new update has occurred, presumably the one we are looking for
            if any(
                call
                == mock.call(
                    device_id=TEST_DEVICE_ID,
                    new_state=PresenceState.MOVING_PRESENCE.value,
                )
                for call in mock_db_functions_fixture[
                    "update_device_state"
                ].call_args_list
            ):
                break
        time.sleep(0.01)  # Uses custom_sleep with original_sleep(0.0001)

    calls = mock_db_functions_fixture["update_device_state"].call_args_list
    found_moving_presence_update = any(
        call
        == mock.call(
            device_id=TEST_DEVICE_ID, new_state=PresenceState.MOVING_PRESENCE.value
        )
        for call in calls
    )
    assert (
        found_moving_presence_update
    ), "update_device_state to MOVING_PRESENCE after reconnect not found"

    # Ensure the log for state change after reconnect occurred
    assert (
        f"State changed from '{PresenceState.NO_PRESENCE.value}' to '{PresenceState.MOVING_PRESENCE.value}'"
        in caplog.text
    )


@mock.patch("time.sleep")
def test_parse_mmwave_state(mock_time_sleep_fixture):
    """Test the mmWave state parsing function."""
    assert motion.parse_mmwave_state(0x00) == PresenceState.NO_PRESENCE
    assert motion.parse_mmwave_state(0x01) == PresenceState.MOVING_PRESENCE
    assert motion.parse_mmwave_state(0x02) == PresenceState.STILL_PRESENCE
    assert motion.parse_mmwave_state(0x03) == PresenceState.UNKNOWN
    assert motion.parse_mmwave_state(0xFF) == PresenceState.UNKNOWN


def test_motion_monitoring_existing_device(mock_serial, mock_db_functions_motion):
    """Test motion monitoring with an existing device."""
    # Arrange
    existing_device_state = PresenceState.NO_PRESENCE.value
    mock_db_functions_motion["get_device_by_id"].return_value = {
        "id": TEST_DEVICE_ID,
        "home_id": TEST_HOME_ID,
        "name": motion.DEVICE_NAME,
        "type": motion.DEVICE_TYPE,
        "current_state": existing_device_state,  # This is from the main DB representation
    }


def test_motion_monitoring_error_handling(mock_serial, mock_db_functions_motion):
    """Test motion monitoring error handling."""
    # Arrange
    initial_state_before_error = PresenceState.NO_PRESENCE.value
    mock_db_functions_motion["get_device_by_id"].return_value = {
        "id": TEST_DEVICE_ID,
        "home_id": TEST_HOME_ID,
        "name": motion.DEVICE_NAME,
        "type": motion.DEVICE_TYPE,
        "current_state": initial_state_before_error,
    }
