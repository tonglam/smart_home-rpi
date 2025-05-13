import io
import json
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

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
        print("Connected to MQTT Broker!")
    else:
        print(f"Failed to connect to MQTT Broker, return code {rc}")


def on_message(client: mqtt.Client, userdata: any, msg: mqtt.MQTTMessage) -> None:
    """Callback for when a PUBLISH message is received from the server."""
    print(f"Received message on topic {msg.topic}: {msg.payload.decode()}")


def on_publish(
    client: mqtt.Client,
    userdata: any,
    mid: int,
    rc: int,
    properties: mqtt.Properties | None = None,
) -> None:
    """Callback for when a message is published (QoS > 0)."""
    pass


def on_subscribe(
    client: mqtt.Client,
    userdata: any,
    mid: int,
    granted_qos: tuple[int, ...],
    properties: mqtt.Properties | None = None,
) -> None:
    """Callback for when the broker responds to a subscription request."""
    pass


def on_disconnect(
    client: mqtt.Client,
    userdata: any,
    flags: dict[str, int],
    rc: int,
    properties: mqtt.Properties | None = None,
) -> None:
    """Callback for when the client disconnects."""
    print(f"Disconnected from MQTT Broker with result code {rc}")
    if rc != 0:
        print(
            "Unexpected MQTT disconnection. Client will attempt to reconnect automatically if loop is running."
        )


def get_mqtt_client() -> mqtt.Client:
    """Returns a singleton MQTT client instance.
    Initializes, connects, and starts the client's loop on the first call.
    """
    global _mqtt_client_instance

    if _mqtt_client_instance is None:
        if not all([MQTT_BROKER_URL, MQTT_USERNAME, MQTT_PASSWORD]):
            print(
                "Critical: MQTT credentials (URL, USERNAME, PASSWORD) not found. Check .env file."
            )
            raise ValueError("Missing MQTT configuration in environment variables.")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_publish = on_publish
        client.on_subscribe = on_subscribe
        client.on_disconnect = on_disconnect

        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        client.tls_set()

        try:
            print(
                f"Initializing and connecting MQTT client to {MQTT_BROKER_URL}:{MQTT_PORT}..."
            )
            client.connect(MQTT_BROKER_URL, MQTT_PORT, 60)
            client.loop_start()
            _mqtt_client_instance = client
            print("MQTT client connected and loop started.")
        except ConnectionRefusedError as e:
            print(
                f"Connection to {MQTT_BROKER_URL}:{MQTT_PORT} refused. Check broker, port, firewall, TLS."
            )
            raise e
        except mqtt.WebsocketConnectionError as e:
            print(f"MQTT WebSocket connection error: {e}")
            raise e
        except TimeoutError as e:
            print(f"Timeout during MQTT connection to {MQTT_BROKER_URL}:{MQTT_PORT}.")
            raise e
        except Exception as e:
            print(
                f"An unexpected error occurred during MQTT client initialization: {e}"
            )
            raise e

    return _mqtt_client_instance


def publish_message(
    client: mqtt.Client, topic: str, payload: str, qos: int = 0, retain: bool = False
) -> None:
    if not client.is_connected():
        print(
            "MQTT client is not connected. Cannot publish. Reconnection is usually automatic."
        )
        return

    msg_info = client.publish(topic, payload, qos=qos, retain=retain)

    if msg_info.rc != mqtt.MQTT_ERR_SUCCESS:
        print(
            f"Failed to queue message for topic {topic}. Error: {mqtt.error_string(msg_info.rc)} (Code: {msg_info.rc})"
        )


def subscribe_to_topic(client: mqtt.Client, topic: str, qos: int = 1) -> None:
    """Subscribes the client to a given topic."""
    if not client.is_connected():
        print(
            "MQTT client is not connected. Cannot subscribe now. Will attempt on (re)connect if configured in on_connect."
        )
        return

    result, mid = client.subscribe(topic, qos)
    if result == mqtt.MQTT_ERR_SUCCESS:
        pass
    else:
        print(
            f"Failed to subscribe to '{topic}'. Error: {mqtt.error_string(result)} (Code: {result})"
        )


def publish_frame(image, topic):
    """Publish a frame to MQTT topic."""
    client = get_mqtt_client()
    # Convert PIL Image to bytes
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format="JPEG")
    img_byte_arr = img_byte_arr.getvalue()
    # Publish
    client.publish(topic, img_byte_arr)


def publish_message(topic, message):
    """Publish a message to MQTT topic."""
    client = get_mqtt_client()
    client.publish(topic, json.dumps(message))
