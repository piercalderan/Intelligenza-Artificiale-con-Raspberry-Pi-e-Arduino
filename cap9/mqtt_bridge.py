import os, json, time
import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

TOPIC_COMMANDS = "assistant/cmd"
TOPIC_SAY = "assistant/say"

class MqttBridge:
    def __init__(self):
        self.client = mqtt.Client()
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.connect(MQTT_HOST, MQTT_PORT, 60)
        self.client.loop_start()

    def send_led(self, on: bool):
        payload = {"led": "on" if on else "off", "ts": int(time.time())}
        self.client.publish(TOPIC_COMMANDS, json.dumps(payload), qos=0, retain=False)

    def send_say(self, text: str):
        payload = {"say": text, "ts": int(time.time())}
        self.client.publish(TOPIC_SAY, json.dumps(payload), qos=0, retain=False)
