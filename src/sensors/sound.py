import sys
import time
from signal import pause

from gpiozero import InputDevice

from src.utils.database import (
    get_device_by_id,
    get_latest_device_state,
    insert_alert,
    insert_device,
    insert_event,
)

SOUND_DEVICE_ID = "sound_sensor_01"
SOUND_DEVICE_NAME = "Sound Sensor"
SOUND_DEVICE_TYPE = "sound_sensor"

GPIO_PIN_SOUND = 21

sound_sensor_device: InputDevice | None = None


def start_sound_monitoring(home_id: str, user_id: str | None):
    """Initializes sound sensor and sets up event handlers."""
    global sound_sensor_device

    try:
        print(
            f"[{SOUND_DEVICE_NAME}] Initializing GPIO {GPIO_PIN_SOUND} (Home: {home_id})..."
        )
        sound_sensor_device = InputDevice(GPIO_PIN_SOUND, pull_up=False)

        device = get_device_by_id(SOUND_DEVICE_ID)
        if not device:
            print(
                f"[{SOUND_DEVICE_NAME}] Device ID {SOUND_DEVICE_ID} not found in DB. Registering..."
            )

            insert_device(
                device_id=SOUND_DEVICE_ID,
                home_id=home_id,
                name=SOUND_DEVICE_NAME,
                type=SOUND_DEVICE_TYPE,
                current_state="quiet",
                location="General Area",
            )

        insert_event(
            home_id=home_id,
            device_id=SOUND_DEVICE_ID,
            event_type="status.monitoring",
            old_state=get_latest_device_state(home_id, SOUND_DEVICE_ID) or "unknown",
            new_state="active",
        )

        def on_sound_detected_callback():
            print(f"[Sound] Sound detected on GPIO {GPIO_PIN_SOUND}")

            insert_event(
                home_id=home_id,
                device_id=SOUND_DEVICE_ID,
                event_type="sound_detection",
                old_state="quiet",
                new_state="sound_detected",
            )

            insert_alert(
                home_id=home_id,
                user_id=user_id,
                device_id=SOUND_DEVICE_ID,
                message=f"{SOUND_DEVICE_NAME} detected a sound.",
            )

            if sound_sensor_device:
                original_callback = sound_sensor_device.when_activated
                sound_sensor_device.when_activated = None
                print("[Sound] Debouncing: Ignoring sound events for 2 seconds...")
                time.sleep(2)
                sound_sensor_device.when_activated = original_callback
                print("[Sound] Debouncing finished. Listening again.")

        sound_sensor_device.when_activated = on_sound_detected_callback
        print(
            f"[{SOUND_DEVICE_NAME}] Monitoring started. Event detection is active in the background."
        )

    except Exception as e:
        print(f"[Sound] Error during initialization: {e}")
        if sound_sensor_device:
            sound_sensor_device.close()
        sound_sensor_device = None
        raise


def stop_sound_monitoring():
    """Cleans up GPIO resources for the sound sensor."""
    global sound_sensor_device
    if sound_sensor_device:
        print(
            f"[Sound] Stopping monitoring and cleaning up {SOUND_DEVICE_NAME} GPIO resources..."
        )

        sound_sensor_device.close()
        sound_sensor_device = None
        print(f"[Sound] {SOUND_DEVICE_NAME} GPIO resources cleaned up.")
    else:
        print(
            f"[Sound] {SOUND_DEVICE_NAME} monitoring was not active or already cleaned up."
        )


if __name__ == "__main__":
    print("[Sound] Standalone Test Mode")

    test_home_id = "00:1A:22:33:44:55"
    test_user_id = "test_user"

    try:
        start_sound_monitoring(home_id=test_home_id, user_id=test_user_id)
        print("[Sound Standalone] Monitoring active. Press Ctrl+C to stop.")
        pause()
    except KeyboardInterrupt:
        print("\n[Sound Standalone] KeyboardInterrupt received.")
    except Exception as e:
        print(f"[Sound Standalone] An error occurred: {e}")
    finally:
        stop_sound_monitoring()
        print("[Sound] Standalone script finished.")

    sys.exit(0)
