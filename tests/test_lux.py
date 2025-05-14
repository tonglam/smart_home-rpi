import sys
from unittest.mock import MagicMock

import pytest

# Mock the PiicoDev_VEML6030 module before it's imported by src.sensors.lux
# This should ideally be in conftest.py if it affects multiple test files.
# Ensure this global mock is in place before `from src.sensors import lux`
if "PiicoDev_VEML6030" not in sys.modules:
    mock_veml6030_module_global = MagicMock(name="GlobalPiicoDevModuleMock")
    mock_veml6030_instance_global = MagicMock(name="GlobalPiicoDevInstanceMock")
    mock_veml6030_module_global.PiicoDev_VEML6030.return_value = (
        mock_veml6030_instance_global
    )
    sys.modules["PiicoDev_VEML6030"] = mock_veml6030_module_global

from src.sensors import lux

DEVICE_ID = "lux_sensor_01"
DEVICE_NAME = "Ambient Light Sensor"
DEVICE_TYPE = "lux_sensor"
EVENT_TYPE = "lux_level"
HOME_ID_TEST = "test_home_lux_01"


@pytest.fixture
def mock_piico_dev(mocker):
    mock_sensor_class = MagicMock(name="PiicoDev_VEML6030_Class_Fixture")
    mock_sensor_instance = MagicMock(name="PiicoDev_VEML6030_Instance_Fixture")
    mock_sensor_class.return_value = mock_sensor_instance
    # This patch will be active for tests using this fixture.
    # It overrides the global sys.modules mock for the scope of the test.
    mocker.patch("src.sensors.lux.PiicoDev_VEML6030", mock_sensor_class)
    return mock_sensor_instance


@pytest.fixture
def mock_db_lux_functions(mocker):
    db_mocks = {
        "get_device_by_id": mocker.patch("src.sensors.lux.get_device_by_id"),
        "insert_device": mocker.patch("src.sensors.lux.insert_device"),
        "get_latest_device_state": mocker.patch(
            "src.sensors.lux.get_latest_device_state"
        ),
        "update_device_state": mocker.patch("src.sensors.lux.update_device_state"),
        "insert_event": mocker.patch("src.sensors.lux.insert_event"),
    }
    db_mocks["get_device_by_id"].return_value = None
    db_mocks["get_latest_device_state"].return_value = None
    return db_mocks


@pytest.fixture
def mock_lux_logger(mocker):
    return mocker.patch("src.sensors.lux.logger")


@pytest.fixture
def mock_lux_time_sleep(mocker):
    # Important: Ensure this mocks time.sleep *within the lux module* specifically.
    return mocker.patch(
        "src.sensors.lux.time.sleep", MagicMock(name="lux_time_sleep_fixture")
    )


@pytest.fixture(autouse=True)
def reset_lux_module_state(
    mock_lux_time_sleep,
):  # mock_lux_time_sleep is auto-used via this fixture
    lux._is_monitoring.clear()
    if lux._monitoring_thread and lux._monitoring_thread.is_alive():
        lux._monitoring_thread.join(timeout=0.2)
    lux._monitoring_thread = None
    lux._sensor_instance = None
    yield
    if lux._is_monitoring.is_set() or (
        lux._monitoring_thread and lux._monitoring_thread.is_alive()
    ):
        lux._is_monitoring.clear()
        if lux._monitoring_thread and lux._monitoring_thread.is_alive():
            lux._monitoring_thread.join(timeout=0.6)
    lux._monitoring_thread = None
    lux._sensor_instance = None


# --- Pytest-style test functions START HERE ---


def test_categorize_lux_pytest():
    assert lux.categorize_lux(25) == "Night"
    assert lux.categorize_lux(49) == "Night"
    assert lux.categorize_lux(50) == "Light Open"
    assert lux.categorize_lux(150) == "Light Open"
    assert lux.categorize_lux(299) == "Light Open"
    assert lux.categorize_lux(300) == "Day"
    assert lux.categorize_lux(1000) == "Day"


def test_start_monitoring_registers_new_device_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_logger
):
    mock_db_lux_functions["get_device_by_id"].return_value = None
    assert lux.start_lux_monitoring(HOME_ID_TEST) is True
    mock_db_lux_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_lux_functions["insert_device"].assert_called_once_with(
        device_id=DEVICE_ID,
        home_id=HOME_ID_TEST,
        name=DEVICE_NAME,
        type=DEVICE_TYPE,
        current_state="unknown",
    )
    assert lux._monitoring_thread is not None
    assert lux._monitoring_thread.is_alive()
    assert lux._is_monitoring.is_set()
    assert lux._sensor_instance == mock_piico_dev


def test_start_monitoring_existing_device_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_logger
):
    mock_db_lux_functions["get_device_by_id"].return_value = {
        "id": DEVICE_ID,
        "currentState": "Day",
    }
    assert lux.start_lux_monitoring(HOME_ID_TEST) is True
    mock_db_lux_functions["get_device_by_id"].assert_called_once_with(DEVICE_ID)
    mock_db_lux_functions["insert_device"].assert_not_called()
    assert lux._monitoring_thread is not None
    assert lux._monitoring_thread.is_alive()


def test_start_monitoring_sensor_init_failure_pytest(
    mock_db_lux_functions, mock_lux_logger, mocker
):
    # This test specifically needs to mock PiicoDev_VEML6030 instantiation to fail.
    # mocker is used directly here, instead of relying on mock_piico_dev fixture which provides a *successful* instance.
    mock_failing_sensor_class = MagicMock(
        side_effect=Exception("Sensor HW Error From Pytest")
    )
    mocker.patch("src.sensors.lux.PiicoDev_VEML6030", mock_failing_sensor_class)
    assert lux.start_lux_monitoring(HOME_ID_TEST) is False
    mock_lux_logger.error.assert_any_call(
        f"[{DEVICE_ID} ({DEVICE_NAME})] Error starting lux monitoring: Sensor HW Error From Pytest"
    )
    assert lux._monitoring_thread is None
    assert not lux._is_monitoring.is_set()


def test_start_monitoring_already_running_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_logger
):
    assert lux.start_lux_monitoring(HOME_ID_TEST) is True
    thread1 = lux._monitoring_thread
    call_count_logger_info_before = mock_lux_logger.info.call_count
    assert lux.start_lux_monitoring(HOME_ID_TEST) is True  # Try starting again
    thread2 = lux._monitoring_thread
    mock_lux_logger.info.assert_any_call(
        f"[{DEVICE_ID} ({DEVICE_NAME})] Monitoring is already running for HOME_ID: {HOME_ID_TEST}. Will not start again."
    )
    # Ensure other init logs weren't repeated
    assert mock_lux_logger.info.call_count == call_count_logger_info_before + 1
    assert thread1 == thread2


def test_monitoring_loop_initial_read_and_state_change_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_time_sleep, mock_lux_logger
):
    mock_piico_dev.read.side_effect = [
        25.0,
        350.0,
        Exception("Read called too many times"),
    ]
    mock_db_lux_functions["get_latest_device_state"].side_effect = [None, "Night"]

    sleep_call_count = 0

    def sleep_controller(*args):
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count >= 2:  # After the second time.sleep(5) in the loop
            lux._is_monitoring.clear()  # Signal the loop to stop

    mock_lux_time_sleep.side_effect = sleep_controller

    lux.start_lux_monitoring(HOME_ID_TEST)
    current_thread = lux._monitoring_thread
    if current_thread and current_thread.is_alive():
        current_thread.join(timeout=2.5)

    assert not (
        current_thread and current_thread.is_alive()
    ), "Thread did not terminate as expected"

    mock_db_lux_functions["update_device_state"].assert_any_call(
        device_id=DEVICE_ID, new_state="Night"
    )
    mock_db_lux_functions["insert_event"].assert_any_call(
        home_id=HOME_ID_TEST,
        device_id=DEVICE_ID,
        event_type=EVENT_TYPE,
        old_state=None,
        new_state="Night",
    )
    mock_db_lux_functions["update_device_state"].assert_any_call(
        device_id=DEVICE_ID, new_state="Day"
    )
    mock_db_lux_functions["insert_event"].assert_any_call(
        home_id=HOME_ID_TEST,
        device_id=DEVICE_ID,
        event_type=EVENT_TYPE,
        old_state="Night",
        new_state="Day",
    )

    assert (
        mock_piico_dev.read.call_count == 2
    ), f"Expected sensor read 2 times, got {mock_piico_dev.read.call_count}"
    assert mock_db_lux_functions["update_device_state"].call_count == 2
    assert mock_db_lux_functions["insert_event"].call_count == 2
    assert (
        sleep_call_count == 2
    ), f"Expected sleep to be called 2 times from loop, got {sleep_call_count}"


def test_monitoring_loop_no_state_change_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_time_sleep, mock_lux_logger
):
    mock_db_lux_functions["get_device_by_id"].return_value = {
        "id": DEVICE_ID,
        "currentState": "Night",
    }
    mock_db_lux_functions["get_latest_device_state"].return_value = "Night"
    mock_piico_dev.read.side_effect = [
        25.0,
        25.0,
        Exception("Read called too many times"),
    ]

    sleep_call_count = 0

    def sleep_controller(*args):
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count >= 2:
            lux._is_monitoring.clear()

    mock_lux_time_sleep.side_effect = sleep_controller

    lux.start_lux_monitoring(HOME_ID_TEST)
    current_thread = lux._monitoring_thread
    if current_thread and current_thread.is_alive():
        current_thread.join(timeout=2.5)
    assert not (
        current_thread and current_thread.is_alive()
    ), "Thread did not terminate as expected"

    mock_db_lux_functions["update_device_state"].assert_not_called()
    mock_db_lux_functions["insert_event"].assert_not_called()
    assert mock_piico_dev.read.call_count == 2
    assert sleep_call_count == 2


def test_monitoring_loop_sensor_read_exception_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_time_sleep, mock_lux_logger
):
    mock_piico_dev.read.side_effect = Exception("Sensor Comm Error Pytest")
    mock_db_lux_functions["get_latest_device_state"].return_value = None

    # Loop: read fails -> except: logger.error, time.sleep(10) -> then loop tries time.sleep(5)
    # We want to stop it after the time.sleep(10) in the except block.
    sleep_calls_in_exception_test = {"count": 0}

    def sleep_controller_for_exception(duration):
        sleep_calls_in_exception_test["count"] += 1
        if (
            duration == 10 and sleep_calls_in_exception_test["count"] == 1
        ):  # First sleep is 10s in except block
            lux._is_monitoring.clear()  # Signal stop
        # The loop should not call the second sleep(5) if flag is cleared.

    mock_lux_time_sleep.side_effect = sleep_controller_for_exception

    lux.start_lux_monitoring(HOME_ID_TEST)
    current_thread = lux._monitoring_thread
    if current_thread and current_thread.is_alive():
        current_thread.join(timeout=2.5)
    assert not (
        current_thread and current_thread.is_alive()
    ), "Thread did not terminate as expected"

    mock_lux_logger.error.assert_any_call(
        f"[{DEVICE_ID} ({DEVICE_NAME})] An unexpected error occurred in the monitoring loop: Sensor Comm Error Pytest"
    )
    mock_db_lux_functions["update_device_state"].assert_not_called()
    mock_db_lux_functions["insert_event"].assert_not_called()
    assert mock_piico_dev.read.call_count == 1
    assert (
        sleep_calls_in_exception_test["count"] >= 1
    )  # Should call sleep at least once (the 10s one)


def test_stop_lux_monitoring_pytest(
    mock_piico_dev, mock_db_lux_functions, mock_lux_logger, mock_lux_time_sleep
):
    lux.start_lux_monitoring(HOME_ID_TEST)
    assert lux._is_monitoring.is_set()
    initial_thread = lux._monitoring_thread
    assert initial_thread is not None
    assert initial_thread.is_alive()

    # Reset side_effect on sleep to allow stop_lux_monitoring to function normally
    # as it might have its own sleeps or join timeouts that shouldn't be hyper-controlled by test side_effects.
    mock_lux_time_sleep.side_effect = None
    mock_lux_time_sleep.return_value = None  # Standard mock behavior

    lux.stop_lux_monitoring()

    assert not lux._is_monitoring.is_set()
    assert (
        not initial_thread.is_alive()
    ), "Thread should have been joined by stop_lux_monitoring"
    assert lux._sensor_instance is None
    mock_lux_logger.info.assert_any_call(
        f"[{DEVICE_ID} ({DEVICE_NAME})] Attempting to stop lux monitoring..."
    )
    mock_lux_logger.info.assert_any_call(
        f"[{DEVICE_ID} ({DEVICE_NAME})] Lux monitoring stopped and resources (if any) released."
    )


# === END OF PYTEST-STYLE TESTS ===
