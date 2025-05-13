import json
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from src.utils.logger import logger

load_dotenv()

MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_PORT = 8883

_mqtt_client_instance: mqtt.Client | None = None


def on_connect(
    client: mqtt.Client,
    userdata: any,
    flags: dict[str, int],
    rc: int,
    properties: mqtt.Properties | None = None,
) -> None:
    """Callback for when the client receives a CONNACK response from the server."""
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
    """Placeholder for handling light control messages."""
    logger.info(
        f"[_handle_light_control_message] Received light control payload: {payload}"
    )
    # TODO: Implement actual light control logic


def _handle_automation_control_message(payload: dict) -> None:
    """Placeholder for handling automation control messages."""
    logger.info(
        f"[_handle_automation_control_message] Received automation control payload: {payload}"
    )
    # TODO: Implement actual automation control logic


def on_message(client: mqtt.Client, userdata: any, msg: mqtt.MQTTMessage) -> None:
    """Callback for when a PUBLISH message is received from the server."""
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
            elif message_type == "automation":
                _handle_automation_control_message(parsed_payload)
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
    """Callback for when the client disconnects."""
    logger.info(f"Disconnected from MQTT Broker with result code {rc}")
    if rc != 0:
        logger.warning(
            "Unexpected MQTT disconnection. Client will attempt to reconnect automatically if loop is running."
        )


def get_mqtt_client() -> mqtt.Client:
    """Returns a singleton MQTT client instance.
    Initializes, connects, and starts the client's loop on the first call.
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
    """Publishes a pre-formatted string message to an MQTT topic.
    Useful for base64 strings or already JSON-stringified messages.
    All messages default to QoS 0.
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
    """Subscribes the client to a given topic. Defaults to QoS 0."""
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
    """Publish raw image bytes (e.g., JPEG) to an MQTT topic. Defaults to QoS 0."""
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
    """Publish a dictionary as a JSON message to an MQTT topic. Defaults to QoS 0."""
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
