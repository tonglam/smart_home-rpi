"""
Smart Home Application Main Entry Point

This module serves as the main entry point for the Smart Home application.
It initializes and manages all sensor components, handles graceful shutdown,
and coordinates the communication between different parts of the system.

Components Initialized:
- MQTT Client: For message broker communication
- Reed Switch: Door state monitoring
- Sound Sensor: Audio event detection
- Camera: Video streaming
- Lux Sensor: Light level monitoring
- Light Control: Smart light management

Environment Variables:
    Required environment variables should be defined in .env file.
    See .env.example for required variables.

Signal Handling:
    - SIGINT (Ctrl+C): Graceful shutdown of all components
    - SIGTERM: Graceful shutdown of all components

Usage:
    python main.py
"""

import os
import signal
import sys

from dotenv import load_dotenv

from src.sensors import camera, light, lux, reed, sound
from utils.database import get_user_id_for_home
from utils.logger import logger
from utils.mqtt import _mqtt_client_instance, get_mqtt_client

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
loaded_dotenv = load_dotenv(dotenv_path=dotenv_path)

if loaded_dotenv:
    logger.info(f".env file loaded successfully from {dotenv_path}")
else:
    logger.warning(
        f"Failed to load .env file from {dotenv_path}, or it was empty. Environment variables might not be set."
    )

if __name__ == "__main__":
    logger.info("Starting Smart Home Application...")

    # Configuration
    app_home_id = (
        "00:1A:2B:3C:4D:5E"  # MAC address format for unique home identification
    )
    app_user_id = None

    logger.info(f"Application using HOME_ID: {app_home_id}")
    app_user_id = get_user_id_for_home(app_home_id)
    if not app_user_id:
        logger.error(
            f"Error: Could not fetch user_id for HOME_ID '{app_home_id}'. Alerts may lack user association."
        )
    else:
        logger.info(f"Application using USER_ID: {app_user_id}")

    try:
        # Initialize all components in sequence
        # Order matters due to dependencies between components
        logger.info("Initializing MQTT Client...")
        get_mqtt_client()

        logger.info("Initializing Reed Switch Monitoring...")
        reed.start_reed_monitoring(home_id=app_home_id, user_id=app_user_id)

        logger.info("Initializing Sound Sensor Monitoring...")
        sound.start_sound_monitoring(home_id=app_home_id, user_id=app_user_id)

        logger.info("Initializing Camera Streaming...")
        camera.start_camera_streaming(home_id=app_home_id)

        logger.info("Initializing Lux Sensor Monitoring...")
        lux.start_lux_monitoring(home_id=app_home_id)

        logger.info("Initializing Light Control...")
        light.initialize_light(home_id=app_home_id, user_id=app_user_id)

        logger.info(
            "Component initialization finished. GPIO event monitoring is active."
        )
        logger.info("Application running. Press Ctrl+C to exit.")

        # Main loop - wait for signals
        signal.pause()

    except KeyboardInterrupt:
        logger.info("[Main] KeyboardInterrupt received. Initiating shutdown...")
    except Exception as e:
        logger.error(f"[Main] An unexpected error occurred: {e}")
    finally:
        # Cleanup sequence - order matters
        # Stop monitoring components first, then close connections
        logger.info("[Main] Cleaning up resources...")
        reed.stop_reed_monitoring()
        sound.stop_sound_monitoring()
        camera.stop_camera_streaming(app_home_id)
        lux.stop_lux_monitoring()
        light.cleanup_light()

        if _mqtt_client_instance and _mqtt_client_instance.is_connected():
            logger.info("[Main] Disconnecting MQTT client...")
            _mqtt_client_instance.loop_stop(force=False)
            _mqtt_client_instance.disconnect()
            logger.info("[Main] MQTT client disconnected.")

        logger.info("[Main] Smart Home Application shut down.")
        sys.exit(0)
