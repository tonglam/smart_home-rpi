import sys
from signal import pause

from gpiozero import Button

from src.utils.database import (
    get_device_by_id,
    get_latest_device_state,
    insert_alert,
    insert_device,
    insert_event,
)

DEVICE_ID = "door_sensor_01"
DEVICE_NAME = "Main Entry Sensor"
DEVICE_TYPE = "reed_sensor"

GPIO_PIN = 17

reed_sensor_object: Button | None = None


def start_reed_monitoring(home_id: str, user_id: str | None):
    """Initializes reed switch and sets up event handlers."""
    global reed_sensor_object

    try:
        print(f"[{DEVICE_NAME}] Initializing GPIO {GPIO_PIN} (Home: {home_id})...")
        reed_sensor_object = Button(GPIO_PIN)

        device = get_device_by_id(DEVICE_ID)
        if not device:
            print(
                f"[{DEVICE_NAME}] Device ID {DEVICE_ID} not found in DB. Registering..."
            )

            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state="unknown",
                location="Main Door",
            )

        insert_event(
            home_id=home_id,
            device_id=DEVICE_ID,
            event_type="status.monitoring",
            old_state=get_latest_device_state(home_id, DEVICE_ID) or "unknown",
            new_state="active",
        )

        def on_door_closed_callback():
            print(f"[Reed] Door closed (GPIO {GPIO_PIN})")
            insert_event(
                home_id=home_id,
                device_id=DEVICE_ID,
                event_type="security",
                old_state="open",
                new_state="closed",
            )

            insert_alert(
                home_id=home_id,
                user_id=user_id,
                device_id=DEVICE_ID,
                message=f"{DEVICE_NAME} closed.",
            )

        def on_door_opened_callback():
            print(f"[Reed] Door opened (GPIO {GPIO_PIN})")

            insert_event(
                home_id=home_id,
                device_id=DEVICE_ID,
                event_type="security",
                old_state="closed",
                new_state="open",
            )

            insert_alert(
                home_id=home_id,
                user_id=user_id,
                device_id=DEVICE_ID,
                message=f"{DEVICE_NAME} open.",
            )

        reed_sensor_object.when_pressed = on_door_closed_callback
        reed_sensor_object.when_released = on_door_opened_callback
        print(
            f"[{DEVICE_NAME}] Monitoring started. Event detection is now active in the background."
        )

    except Exception as e:
        print(f"[Reed] Error during initialization: {e}")
        if reed_sensor_object:
            reed_sensor_object.close()
        reed_sensor_object = None
        raise


def stop_reed_monitoring():
    """Cleans up GPIO resources for the reed switch."""
    global reed_sensor_object
    if reed_sensor_object:
        print(
            f"[Reed] Stopping monitoring and cleaning up {DEVICE_NAME} GPIO resources..."
        )

        reed_sensor_object.close()
        reed_sensor_object = None
        print(f"[Reed] {DEVICE_NAME} GPIO resources cleaned up.")
    else:
        print(f"[Reed] {DEVICE_NAME} monitoring was not active or already cleaned up.")


if __name__ == "__main__":
    print("[Reed] Standalone Test Mode")

    test_home_id = "00:1A:22:33:44:55"
    test_user_id = "test_user"

    try:
        start_reed_monitoring(home_id=test_home_id, user_id=test_user_id)
        print("[Reed Standalone] Monitoring active. Press Ctrl+C to stop.")
        pause()
    except KeyboardInterrupt:
        print("\n[Reed Standalone] KeyboardInterrupt received.")
    except Exception as e:
        print(f"[Reed Standalone] An error occurred: {e}")
    finally:
        stop_reed_monitoring()
        print("[Reed] Standalone script finished.")

    sys.exit(0)
