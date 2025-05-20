import threading
from typing import Optional

from gpiozero import PWMLED

from src.utils.database import (
    get_device_by_id,
    get_home_mode,
    insert_alert,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "light_01"
DEVICE_NAME = "Room Light"
DEVICE_TYPE = "led_light"
LED_PIN = 16  # GPIO pin for LED

# Global state for LED instance
_led: Optional[PWMLED] = None
_led_lock = threading.Lock()  # Lock for thread-safe LED operations


def initialize_light(home_id: str, user_id: Optional[str] = None) -> None:
    """Initialize the light device, ensure hardware is ready, and synchronize its state with the database."""
    global _led
    logger.info(
        f"[{DEVICE_NAME}] Attempting to initialize/synchronize light for HOME_ID: {home_id}"
    )

    with _led_lock:
        if _led is None:
            try:
                _led = PWMLED(LED_PIN)
                logger.info(
                    f"[{DEVICE_NAME}] PWMLED hardware initialized on pin {LED_PIN}."
                )
            except Exception as e_init_hw:
                logger.error(
                    f"[{DEVICE_NAME}] Critical error initializing PWMLED on pin {LED_PIN}: {e_init_hw}",
                    exc_info=True,
                )
                return

        current_intensity_float = _led.value

        current_intensity_float = _led.value
        current_state_str = "on" if current_intensity_float > 0.0 else "off"
        brightness_int = int(current_intensity_float * 100)

        device_in_db = get_device_by_id(DEVICE_ID)

        if not device_in_db:
            logger.info(
                f"[{DEVICE_NAME}] Device not found in database. Registering with current hardware state: {current_state_str}, brightness: {brightness_int}%"
            )
            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state=current_state_str,
                brightness=brightness_int,
            )
            insert_event(
                home_id=home_id,
                device_id=DEVICE_ID,
                event_type="light_registered",
                old_state="not_registered",
                new_state=f"state:{current_state_str},brightness:{brightness_int}",
                user_id=user_id,
            )
            logger.info(f"[{DEVICE_NAME}] Device registered successfully.")
        else:
            db_state = device_in_db.get("current_state")
            db_brightness = device_in_db.get("brightness")

            if not isinstance(db_brightness, int):
                logger.warning(
                    f"[{DEVICE_NAME}] DB brightness was not an int ('{db_brightness}'), defaulting to 0 for comparison."
                )
                db_brightness = 0

            if db_state != current_state_str or db_brightness != brightness_int:
                logger.info(
                    f"[{DEVICE_NAME}] Hardware state ({current_state_str}, {brightness_int}%) differs from DB ({db_state}, {db_brightness}%). Synchronizing DB."
                )
                update_payload = {
                    "current_state": current_state_str,
                    "brightness": brightness_int,
                }
                update_device_state(DEVICE_ID, update_payload)
                insert_event(
                    home_id=home_id,
                    device_id=DEVICE_ID,
                    event_type="light_state_sync",
                    old_state=f"state:{db_state},brightness:{db_brightness}",
                    new_state=f"state:{current_state_str},brightness:{brightness_int}",
                    user_id=user_id,
                )
                logger.info(f"[{DEVICE_NAME}] Database synchronized to hardware state.")
            else:
                logger.info(
                    f"[{DEVICE_NAME}] Light already initialized and database state is consistent ({current_state_str}, {brightness_int}%). No changes made."
                )

    logger.info(f"[{DEVICE_NAME}] Initialization/synchronization complete.")


def set_light_intensity(home_id: str, level: float, user_id: Optional[str] = None):
    """Set the light intensity to a specific level."""
    with _led_lock:
        if _led is None:
            logger.error(
                f"[{DEVICE_NAME}] Light not initialized. Call initialize_light() first."
            )
            raise RuntimeError(f"[{DEVICE_NAME}] Light not initialized.")

        old_level_float = _led.value
        _led.value = level

        current_state_str = "on" if level > 0.0 else "off"
        brightness_int = int(level * 100)

        logger.info(
            f"[{DEVICE_NAME}] Light intensity set to {level*100}%. State: {current_state_str}"
        )

        update_payload = {
            "current_state": current_state_str,
            "brightness": brightness_int,
        }
        update_device_state(DEVICE_ID, update_payload)

        if old_level_float != level:
            insert_event(
                home_id=home_id,
                device_id=DEVICE_ID,
                event_type="light_changed",
                old_state=str(old_level_float),
                new_state=str(level),
                user_id=user_id,
            )

            if level > 0.0 and old_level_float == 0.0:
                home_mode = get_home_mode(home_id)
                if home_mode == "away" and user_id:
                    alert_message = (
                        "Security Alert: Light turned on while home is in away mode!"
                    )
                    logger.warning(f"[{DEVICE_NAME}] {alert_message}")
                    insert_alert(
                        home_id=home_id,
                        user_id=user_id,
                        device_id=DEVICE_ID,
                        message=alert_message,
                    )
        else:
            logger.info(
                f"[{DEVICE_NAME}] Light intensity was already {level*100}%. No event logged."
            )


def get_light_intensity() -> float:
    """Get the current light intensity."""
    with _led_lock:
        if _led is None:
            logger.error(
                f"[{DEVICE_NAME}] Light not initialized. Call initialize_light() first."
            )
            raise RuntimeError(f"[{DEVICE_NAME}] Light not initialized.")
        return _led.value


def turn_light_on(home_id: str, user_id: Optional[str] = None):
    """Turn the light on (set to maximum intensity)."""
    logger.info(f"[{DEVICE_NAME}] Turning light on.")
    set_light_intensity(home_id, 1.0, user_id)


def turn_light_off(home_id: str, user_id: Optional[str] = None):
    """Turn the light off."""
    logger.info(f"[{DEVICE_NAME}] Turning light off.")
    set_light_intensity(home_id, 0.0, user_id)


def cleanup_light() -> None:
    """Clean up light resources."""
    global _led
    with _led_lock:
        if _led is not None:
            try:
                _led.close()
                _led = None
                logger.info(f"[{DEVICE_NAME}] Light resources cleaned up.")
            except Exception as e:
                logger.error(f"[{DEVICE_NAME}] Error cleaning up light resources: {e}")
