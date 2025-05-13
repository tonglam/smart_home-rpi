import threading
import time

import pytest
from paho.mqtt.client import Client as MqttClient
from paho.mqtt.client import MQTTMessage

from src.utils.mqtt import (
    MQTT_BROKER_URL,
    MQTT_PASSWORD,
    MQTT_USERNAME,
    get_mqtt_client,
    publish_message,
    subscribe_to_topic,
)

# --- Configuration ---
TEST_TOPIC = "smart_home/test/mqtt_script_check"
TEST_PAYLOAD = f"MQTT_TEST_MESSAGE_PING_{int(time.time())}"  # Unique payload
WAIT_TIMEOUT_SECONDS = 5
# --- End Configuration ---


@pytest.fixture(scope="module")
def check_env_vars():
    """Check if required environment variables are set."""
    if not MQTT_BROKER_URL or not MQTT_USERNAME or not MQTT_PASSWORD:
        pytest.skip("MQTT environment variables are not set")


@pytest.fixture(scope="module")
def mqtt_client(check_env_vars):
    """Create and configure MQTT client for testing."""
    client = get_mqtt_client()
    # Wait for connection
    timeout = 5
    start_time = time.time()
    while not client.is_connected() and time.time() - start_time < timeout:
        time.sleep(0.1)
    if not client.is_connected():
        pytest.skip("Failed to connect to MQTT broker")
    yield client


@pytest.fixture(scope="module")
def message_received_event():
    """Create an event for message reception."""
    return threading.Event()


@pytest.fixture(scope="module")
def received_payload_holder():
    """Create a list to hold received payload."""
    return []


def test_mqtt_connection(mqtt_client):
    """Test MQTT client connection."""
    assert mqtt_client.is_connected(), "MQTT client is not connected"


def test_mqtt_publish_subscribe(
    mqtt_client, message_received_event, received_payload_holder
):
    """Test MQTT publish and subscribe functionality."""

    def on_message(client: MqttClient, userdata: any, msg: MQTTMessage):
        try:
            received_payload = msg.payload.decode()
            if msg.topic == TEST_TOPIC and received_payload == TEST_PAYLOAD:
                received_payload_holder.append(received_payload)
                message_received_event.set()
        except Exception as e:
            pytest.fail(f"Error in message handler: {e}")

    # Set up message handler
    mqtt_client.on_message = on_message

    # Subscribe to test topic
    subscribe_to_topic(mqtt_client, TEST_TOPIC, qos=1)
    time.sleep(2)  # Wait longer for subscription to complete

    # Publish test message
    publish_message(mqtt_client, TEST_TOPIC, TEST_PAYLOAD, qos=1)

    # Wait for message
    message_received = message_received_event.wait(timeout=WAIT_TIMEOUT_SECONDS)

    # Cleanup
    mqtt_client.unsubscribe(TEST_TOPIC)

    # Assertions
    assert message_received, "Message was not received within timeout"
    assert received_payload_holder, "No payload was received"
    assert (
        received_payload_holder[0] == TEST_PAYLOAD
    ), "Received payload does not match sent payload"
