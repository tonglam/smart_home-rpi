from typing import Optional

from gpiozero import PWMLED

from src.utils.database import (
    get_device_by_id,
    insert_device,
    insert_event,
    update_device_state,
)
from src.utils.logger import logger

DEVICE_ID = "light_01"
DEVICE_NAME = "PWM LED Light"
DEVICE_TYPE = "led_light"
LED_PIN = 2  # GPIO pin for LED

# Allowed intensity levels
levels = [0.0, 0.3, 0.5, 1.0]

# Global state for LED instance
_led: Optional[PWMLED] = None


def initialize_light(home_id: str, user_id: str) -> None:
    """Initialize the light device and register it in the database if not present."""
    global _led
    logger.info(
        f"[{DEVICE_NAME}] Initializing light for HOME_ID: {home_id}, USER_ID: {user_id}"
    )

    try:
        # Initialize GPIO
        _led = PWMLED(LED_PIN)

        device = get_device_by_id(DEVICE_ID)
        if not device:
            logger.info(f"[{DEVICE_NAME}] Device not found in DB. Registering...")
            initial_intensity_float = _led.value

            current_state_str = "on" if initial_intensity_float > 0.0 else "off"
            brightness_int = int(initial_intensity_float * 100)

            insert_device(
                device_id=DEVICE_ID,
                home_id=home_id,
                name=DEVICE_NAME,
                type=DEVICE_TYPE,
                current_state=current_state_str,
                brightness=brightness_int,
            )
            logger.info(
                f"[{DEVICE_NAME}] Device registered with state: {current_state_str}, brightness: {brightness_int}%"
            )
        else:
            db_state = device.get("current_state")
            db_brightness = device.get("brightness")
            logger.info(
                f"[{DEVICE_NAME}] Device already registered. DB state: {db_state}, DB brightness: {db_brightness}%. Hardware state: {_led.value*100.0}%"
            )

        logger.info(f"[{DEVICE_NAME}] Initialization complete.")
    except Exception as e:
        logger.error(f"[{DEVICE_NAME}] Error during initialization: {e}")


def set_light_intensity(home_id: str, level: float):
    """Set the light intensity to a specific level."""
    if _led is None:
        logger.error(
            f"[{DEVICE_NAME}] Light not initialized. Call initialize_light() first."
        )
        raise RuntimeError(f"[{DEVICE_NAME}] Light not initialized.")

    if level not in levels:
        logger.error(
            f"[{DEVICE_NAME}] Invalid light intensity level: {level}. Must be one of: {levels}"
        )
        raise ValueError(
            f"Invalid light intensity level: {level}. Must be one of: {levels}"
        )

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

    insert_event(
        home_id=home_id,
        device_id=DEVICE_ID,
        event_type="light_intensity_changed",
        old_state=str(old_level_float),
        new_state=str(level),
        event_data={"brightness": brightness_int},
    )


def get_light_intensity() -> float:
    """Get the current light intensity."""
    if _led is None:
        logger.error(
            f"[{DEVICE_NAME}] Light not initialized. Call initialize_light() first."
        )
        raise RuntimeError(f"[{DEVICE_NAME}] Light not initialized.")
    return _led.value


def turn_light_on(home_id: str):
    """Turn the light on (set to maximum intensity)."""
    logger.info(f"[{DEVICE_NAME}] Turning light on.")
    set_light_intensity(home_id, 1.0)


def turn_light_off(home_id: str):
    """Turn the light off."""
    logger.info(f"[{DEVICE_NAME}] Turning light off.")
    set_light_intensity(home_id, 0.0)
