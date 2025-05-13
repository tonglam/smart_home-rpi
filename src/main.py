import os
import signal
import sys

from dotenv import load_dotenv

from sensors import camera, reed, sound
from utils.database import get_user_id_for_home

load_dotenv()

if __name__ == "__main__":
    print("Starting Smart Home Application...")

    app_home_id = os.getenv("HOME_ID")
    app_user_id = None

    if not app_home_id:
        print("Critical Error: HOME_ID not set in .env file. Application cannot start.")
        sys.exit(1)

    print(f"Application using HOME_ID: {app_home_id}")
    app_user_id = get_user_id_for_home(app_home_id)
    if not app_user_id:
        print(
            f"Warning: Could not fetch user_id for HOME_ID '{app_home_id}'. Alerts may lack user association."
        )
    else:
        print(f"Application using USER_ID: {app_user_id}")

    try:
        print("Initializing Reed Switch Monitoring...")
        reed.start_reed_monitoring(home_id=app_home_id, user_id=app_user_id)

        print("Initializing Sound Sensor Monitoring...")
        sound.start_sound_monitoring(home_id=app_home_id, user_id=app_user_id)

        print("Initializing Camera Streaming...")
        camera.start_camera_streaming(home_id=app_home_id)

        print("Component initialization finished. GPIO event monitoring is active.")
        print("Application running. Press Ctrl+C to exit.")
        signal.pause()

    except KeyboardInterrupt:
        print("\n[Main] KeyboardInterrupt received. Initiating shutdown...")
    except Exception as e:
        print(f"[Main] An unexpected error occurred: {e}")
    finally:
        print("[Main] Cleaning up resources...")
        reed.stop_reed_monitoring()
        sound.stop_sound_monitoring()
        camera.stop_camera_streaming()
        print("[Main] Smart Home Application shut down.")
        sys.exit(0)
