"""
MQTT Communication Module

This module handles all MQTT communication for the Smart Home system.
It manages the connection to the MQTT broker, handles message publishing
and subscription, and processes various control messages.

Connection Strategy:
    - Uses TLS for secure communication (port 8883)
    - Implements automatic reconnection
    - Maintains QoS levels for different message types
    - Handles connection failures gracefully

Message Types:
    1. Light Control:
        - Topic: control/light
        - States: on/off
        - Optional: brightness (0-100%)

    2. Device Control:
        - Topic: control/device
        - States: varies by device
        - Includes device-specific parameters

    3. Automation Control:
        - Topic: control/automation
        - Modes: movie, away, home
        - States: active/inactive

    4. Camera Control:
        - Topic: control/camera
        - States: online/offline
        - Includes streaming parameters

Dependencies:
    - paho-mqtt: For MQTT protocol implementation
    - dotenv: For configuration management
    - database: For user and device state management

Environment Variables:
    - MQTT_BROKER_URL: MQTT broker address
    - MQTT_USERNAME: Authentication username
    - MQTT_PASSWORD: Authentication password
"""

import json
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from src.utils.database import get_user_id_for_home
from src.utils.logger import logger

load_dotenv()

# MQTT Configuration
MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_PORT = 8883  # TLS port

# Global client instance
_mqtt_client_instance: mqtt.Client | None = None


def on_connect(
    client: mqtt.Client,
    userdata: any,
    flags: dict[str, int],
    rc: int,
    properties: mqtt.Properties | None = None,
) -> None:
    """Callback for when the client receives a CONNACK response from the server.

    Args:
        client: The client instance for this callback
        userdata: The private user data as set in Client() or userdata_set()
        flags: Response flags sent by the broker
        rc: The connection result (0 = success)
        properties: Properties for MQTT v5.0 (optional)

    Note:
        - Automatically subscribes to 'control' topic on successful connection
        - Logs connection status and any subscription errors
        - Called by paho-mqtt client internally
    """
    if rc == 0:
        logger.info("Connected to MQTT Broker!")
        logger.info("Subscribing to 'control' topic with QoS 0...")
        subscribe_result, mid = client.subscribe("control", qos=0)
        if subscribe_result == mqtt.MQTT_ERR_SUCCESS:
            logger.info("Successfully subscribed to 'control' topic.")
        else:
            logger.error(
                f"Failed to subscribe to 'control' topic. Error: {mqtt.error_string(subscribe_result)}"
            )
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {rc}")


def _handle_light_control_message(payload: dict) -> None:
    """Handle light control messages from MQTT.

    Processes messages for controlling smart lights, including:
    - On/Off control
    - Brightness adjustment
    - State validation

    Args:
        payload: Dictionary containing the message payload

    Expected payload formats:
        1. On/Off control:
        {
            "homeId": "00:1A:22:33:44:55",
            "type": "light",
            "deviceId": "light_e5f6g7h8",
            "state": "on"|"off",
            "createdAt": "2025-05-16T18:34:28.140Z"
        }

        2. Brightness control:
        {
            "homeId": "00:1A:22:33:44:55",
            "type": "light",
            "deviceId": "light_e5f6g7h8",
            "state": "on",
            "brightness": 0|25|100,
            "createdAt": "2025-05-16T18:35:17.251Z"
        }

    Note:
        - Validates all required fields
        - Maps brightness percentages to PWM values
        - Handles errors gracefully
    """
    from src.sensors.light import set_light_intensity, turn_light_off, turn_light_on

    try:
        logger.info(f"[MQTT] Received light control payload: {payload}")

        # Validate required fields
        required_fields = ["homeId", "type", "deviceId", "state"]
        if not all(field in payload for field in required_fields):
            logger.error(
                f"[MQTT] Missing required fields in light control payload: {payload}"
            )
            return

        # Only process light type messages
        if payload["type"] != "light":
            logger.error(
                f"[MQTT] Received non-light type in light control handler: {payload['type']}"
            )
            return

        home_id = payload["homeId"]
        state = payload["state"].lower()

        # Handle brightness control
        if "brightness" in payload:
            brightness = int(payload["brightness"])
            # Map brightness percentages from MQTT to PWMLED intensity levels (0.0-1.0)
            intensity_map = {
                0: 0.0,  # Off
                10: 0.1,  # Low
                100: 1.0,  # Full
            }
            if brightness in intensity_map:
                logger.info(
                    f"[MQTT] Setting light brightness to {brightness}% (Intensity: {intensity_map[brightness]})"
                )
                set_light_intensity(home_id, intensity_map[brightness])
            else:
                logger.error(
                    f"[MQTT] Invalid brightness value: {brightness}. Must be one of: {list(intensity_map.keys())}"
                )
            return

        # Handle on/off control
        if state == "on":
            logger.info("[MQTT] Turning light on")
            turn_light_on(home_id)
        elif state == "off":
            logger.info("[MQTT] Turning light off")
            turn_light_off(home_id)
        else:
            logger.error(f"[MQTT] Invalid light state: {state}. Must be 'on' or 'off'")

    except Exception as e:
        logger.error(f"[MQTT] Error handling light control message: {e}")


def _handle_device_control_message(payload: dict) -> None:
    """Handle device control messages from MQTT.

    Processes general device control messages and routes them
    to appropriate device handlers.

    Args:
        payload: Dictionary containing the message payload

    Expected payload format:
        {
            "home_id": "00:1A:22:33:44:55",
            "type": "device",
            "device_id": "light_01",
            "state": "on",
            "brightness": 50,
            "created_at": "2025-05-16T18:39:59.196Z"
        }

    Note:
        - Currently only handles light devices
        - Validates required fields
        - Extensible for other device types
    """
    from src.sensors.light import turn_light_off, turn_light_on

    try:
        # Validate required fields
        required_fields = ["home_id", "type", "device_id", "state"]
        for field in required_fields:
            if field not in payload:
                logger.error(
                    f"[MQTT] Missing required field '{field}' in device control payload"
                )
                return

        home_id = payload["home_id"]
        device_id = payload["device_id"]
        state = payload["state"]
        brightness = payload.get("brightness")

        # Only handle light device for now
        if device_id == "light_01":
            if state == "on":
                turn_light_on(home_id, brightness)
            elif state == "off":
                turn_light_off(home_id)

    except Exception as e:
        logger.error(f"[MQTT] Error handling device control message: {e}")


def _handle_automation_control_message(payload: dict) -> None:
    """Handle automation control messages from MQTT.

    Processes home automation mode changes and triggers
    appropriate device actions.

    Args:
        payload: Dictionary containing the message payload

    Expected payload format:
        {
            "home_id": "00:1A:22:33:44:55",
            "type": "automation",
            "mode_id": "movie",
            "active": true|false,
            "created_at": "2025-05-16T18:39:59.196Z"
        }

    Note:
        - Currently supports movie mode
        - Validates required fields
        - Handles user association for alerts
    """
    from src.sensors.light import set_light_intensity, turn_light_off

    try:
        # Validate required fields
        required_fields = ["home_id", "type", "mode_id", "active"]
        for field in required_fields:
            if field not in payload:
                logger.error(
                    f"[MQTT] Missing required field '{field}' in automation control payload"
                )
                return

        home_id = payload["home_id"]
        mode_id = payload["mode_id"]
        is_active = payload["active"]

        # Get user_id for security alerts
        user_id = get_user_id_for_home(home_id)
        if not user_id:
            logger.warning(
                f"[MQTT] Could not fetch user_id for HOME_ID '{home_id}'. Alerts may not be sent."
            )

        # Only handle movie mode for now
        if mode_id == "movie":
            logger.info(
                f"[MQTT] Movie mode {'activated' if is_active else 'deactivated'} for home {home_id}"
            )
            if is_active:
                set_light_intensity(home_id, 0.2)
                logger.info("[MQTT] Turned on lights for movie mode to 20%")

    except Exception as e:
        logger.error(f"[MQTT] Error handling automation control message: {e}")


def _handle_camera_control_message(payload: dict) -> None:
    """Handle camera control messages from MQTT.

    Processes camera state changes and manages video streaming.

    Args:
        payload: Dictionary containing the message payload

    Expected payload format:
        {
            "homeId": "00:1A:2B:3C:4D:5E",
            "type": "camera",
            "deviceId": "camera_01",
            "state": "online" | "offline",
            "createdAt": "2025-05-19T16:48:47.667Z"
        }

    Note:
        - Only handles camera_01 device
        - Manages streaming start/stop
        - Validates message format
    """
    from src.sensors.camera import start_camera_streaming, stop_camera_streaming

    try:
        logger.info(f"[MQTT] Received camera control payload: {payload}")

        # Validate required fields
        required_fields = ["homeId", "type", "deviceId", "state"]
        if not all(field in payload for field in required_fields):
            logger.error(
                f"[MQTT] Missing required fields in camera control payload: {payload}"
            )
            return

        # Only process camera type messages for the specific camera_01
        if payload["type"] != "camera" or payload["deviceId"] != "camera_01":
            logger.warning(
                f"[MQTT] Received non-camera type or wrong deviceId in camera control handler: {payload}"
            )
            return

        home_id = payload["homeId"]
        state = payload["state"].lower()

        if state == "online":
            logger.info(f"[MQTT] Starting camera streaming for home_id: {home_id}")
            start_camera_streaming(home_id)
        elif state == "offline":
            logger.info(f"[MQTT] Stopping camera streaming for home_id: {home_id}")
            stop_camera_streaming(home_id)
        else:
            logger.error(
                f"[MQTT] Invalid camera state: {state}. Must be 'online' or 'offline'"
            )

    except Exception as e:
        logger.error(f"[MQTT] Error handling camera control message: {e}")


def on_message(client: mqtt.Client, userdata: any, msg: mqtt.MQTTMessage) -> None:
    """Callback for when a PUBLISH message is received from the server.

    Routes incoming messages to appropriate handlers based on payload type.

    Args:
        client: The client instance for this callback
        userdata: The private user data as set in Client() or userdata_set()
        msg: An instance of MQTTMessage containing topic and payload

    Note:
        - Decodes JSON payloads
        - Routes to specific handlers based on type field
        - Handles malformed messages gracefully
    """
    logger.info(f"Received raw message on topic {msg.topic}: {msg.payload[:200]}...")
    try:
        payload_str = msg.payload.decode("utf-8")
    except UnicodeDecodeError:
        logger.error(
            f"Error decoding message payload on topic {msg.topic} as UTF-8. Payload (hex): {msg.payload.hex()}"
        )
        return

    if msg.topic == "control":
        try:
            parsed_payload = json.loads(payload_str)
            message_type = parsed_payload.get("type")

            if message_type == "light":
                _handle_light_control_message(parsed_payload)
            elif message_type == "device":
                _handle_device_control_message(parsed_payload)
            elif message_type == "automation":
                _handle_automation_control_message(parsed_payload)
            elif message_type == "camera":
                _handle_camera_control_message(parsed_payload)
            else:
                logger.warning(
                    f"Received message on 'control' topic with unknown type: '{message_type}'. Payload: {parsed_payload}"
                )
        except json.JSONDecodeError:
            logger.error(
                f"Error decoding JSON from message on 'control' topic. Payload: {payload_str}"
            )
        except Exception as e:
            logger.error(
                f"Error processing message from 'control' topic: {e}. Payload: {payload_str}"
            )


def on_disconnect(
    client: mqtt.Client,
    userdata: any,
    flags: dict[str, int],
    rc: int,
    properties: mqtt.Properties | None = None,
) -> None:
    """Callback for when the client disconnects from the broker.

    Args:
        client: The client instance for this callback
        userdata: The private user data as set in Client() or userdata_set()
        flags: Response flags sent by the broker
        rc: The disconnection result (0 = expected)
        properties: Properties for MQTT v5.0 (optional)

    Note:
        - Logs unexpected disconnections
        - Client will automatically attempt to reconnect
    """
    logger.info(f"Disconnected from MQTT Broker with result code {rc}")
    if rc != 0:
        logger.warning(
            "Unexpected MQTT disconnection. Client will attempt to reconnect automatically if loop is running."
        )


def get_mqtt_client() -> mqtt.Client:
    """Get or create an MQTT client instance.

    Creates a new MQTT client if none exists, or returns the existing one.
    Handles all client setup including:
    - TLS configuration
    - Authentication
    - Callback registration
    - Connection establishment

    Returns:
        mqtt.Client: Configured MQTT client instance

    Raises:
        RuntimeError: If connection fails or configuration is invalid

    Note:
        - Uses singleton pattern
        - Configures TLS for secure communication
        - Sets up automatic reconnection
    """
    global _mqtt_client_instance

    if _mqtt_client_instance is None:
        if not all([MQTT_BROKER_URL, MQTT_USERNAME, MQTT_PASSWORD]):
            logger.error(
                "Critical: MQTT credentials (URL, USERNAME, PASSWORD) not found. Check .env file."
            )
            raise ValueError("Missing MQTT configuration in environment variables.")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect

        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        client.tls_set()

        try:
            logger.info(
                f"Initializing and connecting MQTT client to {MQTT_BROKER_URL}:{MQTT_PORT}..."
            )
            client.connect(MQTT_BROKER_URL, MQTT_PORT, 60)
            client.loop_start()
            _mqtt_client_instance = client
            logger.info("MQTT client connected and loop started.")
        except ConnectionRefusedError as e:
            logger.error(
                f"Connection to {MQTT_BROKER_URL}:{MQTT_PORT} refused. Check broker, port, firewall, TLS."
            )
            raise e
        except mqtt.WebsocketConnectionError as e:
            logger.error(f"MQTT WebSocket connection error: {e}")
            raise e
        except TimeoutError as e:
            logger.error(
                f"Timeout during MQTT connection to {MQTT_BROKER_URL}:{MQTT_PORT}."
            )
            raise e
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during MQTT client initialization: {e}"
            )
            raise e

    return _mqtt_client_instance


def publish_string(
    topic: str, payload: str, qos: int = 0, retain: bool = False
) -> None:
    """Publish a string message to an MQTT topic.

    Args:
        topic: The topic to publish to
        payload: The string message to publish
        qos: Quality of Service level (0-2)
        retain: Whether to retain the message

    Raises:
        RuntimeError: If client is not connected
    """
    client = get_mqtt_client()
    if not client.is_connected():
        logger.warning(
            "MQTT client is not connected. Cannot publish string. Reconnection is usually automatic."
        )
        return

    msg_info = client.publish(topic, payload, qos=qos, retain=retain)

    if msg_info.rc != mqtt.MQTT_ERR_SUCCESS:
        logger.error(
            f"Failed to queue string message for topic {topic}. Error: {mqtt.error_string(msg_info.rc)} (Code: {msg_info.rc})"
        )


def subscribe_to_topic(client: mqtt.Client, topic: str, qos: int = 0) -> None:
    """Subscribe to an MQTT topic.

    Args:
        client: MQTT client instance
        topic: Topic to subscribe to
        qos: Quality of Service level (0-2)

    Raises:
        RuntimeError: If subscription fails
    """
    if not client.is_connected():
        logger.warning(
            "MQTT client is not connected. Cannot subscribe now. Will attempt on (re)connect if configured in on_connect."
        )
        return

    result, mid = client.subscribe(topic, qos)
    if result != mqtt.MQTT_ERR_SUCCESS:
        logger.error(
            f"Failed to subscribe to '{topic}'. Error: {mqtt.error_string(result)} (Code: {result})"
        )
        return


def publish_frame(
    topic: str, image_bytes: bytes, qos: int = 0, retain: bool = False
) -> None:
    """Publish a video frame as bytes to an MQTT topic.

    Args:
        topic: The topic to publish to
        image_bytes: The frame data as bytes
        qos: Quality of Service level (0-2)
        retain: Whether to retain the message

    Note:
        - Optimized for video streaming
        - Does not encode/decode frames
    """
    client = get_mqtt_client()
    if not client.is_connected():
        logger.warning("MQTT client is not connected. Cannot publish frame.")
        return

    msg_info = client.publish(topic, image_bytes, qos=qos, retain=retain)
    if msg_info.rc != mqtt.MQTT_ERR_SUCCESS:
        logger.error(
            f"Failed to queue frame for topic {topic}. Error: {mqtt.error_string(msg_info.rc)}"
        )


def publish_json(
    topic: str, message_dict: dict, qos: int = 0, retain: bool = False
) -> None:
    """Publish a dictionary as JSON to an MQTT topic.

    Args:
        topic: The topic to publish to
        message_dict: The dictionary to publish as JSON
        qos: Quality of Service level (0-2)
        retain: Whether to retain the message

    Raises:
        RuntimeError: If client is not connected
        JSONEncodeError: If dictionary cannot be serialized
    """
    client = get_mqtt_client()
    try:
        json_payload = json.dumps(message_dict)
    except TypeError as e:
        logger.error(f"Error serializing message_dict to JSON for topic {topic}: {e}")
        return

    if not client.is_connected():
        logger.warning(
            "MQTT client is not connected. Cannot publish JSON. Reconnection is usually automatic."
        )
        return

    msg_info = client.publish(topic, json_payload, qos=qos, retain=retain)

    if msg_info.rc != mqtt.MQTT_ERR_SUCCESS:
        logger.error(
            f"Failed to queue JSON message for topic {topic}. Error: {mqtt.error_string(msg_info.rc)} (Code: {msg_info.rc})"
        )
