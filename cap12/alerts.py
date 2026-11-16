# alerts.py
import json, time, uuid, threading
from datetime import datetime
from tzlocal import get_localzone

try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

try:
    import serial
except Exception:
    serial = None


class AlertSink:
    """
    Invio eventi verso MQTT e/o seriale Arduino.
    - MQTT: topic base (es. home/cam/alerts), QoS 0
    - Serial: invia righe testuali (es. "MOTION 1\n", "FACE 2\n")
    """
    def __init__(self,
                 mqtt_host="localhost",
                 mqtt_port=1883,
                 mqtt_topic="home/cam/alerts",
                 client_id="12345",
                 enable_mqtt=True,
                 serial_port="/dev/ttyACM0",       # es. "/dev/ttyACM0"
                 serial_baud=115200,
                 enable_serial=True):
        self.tz = get_localzone()
        self.mqtt_topic = mqtt_topic
        self.enable_mqtt = enable_mqtt and (mqtt is not None)
        self.enable_serial = enable_serial and (serial is not None) and (serial_port is not None)

        # MQTT
        self.mqtt = None
        if self.enable_mqtt:
            self.mqtt = mqtt.Client(client_id)# or f"cap12-{uuid.uuid4().hex[:8]}")
            # opzionale: credenziali
            # self.mqtt.username_pw_set("user","pass")
            self.mqtt.connect_async(mqtt_host, mqtt_port, keepalive=30)
            self.mqtt.loop_start()

        # Serial
        self.ser = None
        if self.enable_serial:
            try:
                self.ser = serial.Serial(serial_port, serial_baud, timeout=0.5)
                # piccola pausa per l'autoreset dell'Arduino
                time.sleep(2.0)
            except Exception as e:
                print(f"[AlertSink] Serial error: {e}")
                self.enable_serial = False

        # semplice rate-limit per non spammare
        self._last_sent = {}
        self._lock = threading.Lock()

    def _iso_now(self):
        return datetime.now(self.tz).isoformat(timespec="seconds")

    def _should_send(self, key, cooldown_s):
        now = time.time()
        with self._lock:
            last = self._last_sent.get(key, 0)
            if now - last >= cooldown_s:
                self._last_sent[key] = now
                return True
            return False

    def send_event(self, kind, data=None, cooldown_s=2.0):
        """
        kind: "motion_start" | "motion_end" | "face" | "motion_keepalive" | ...
        data: dict extra (es. {"faces": 2, "conf": 0.83, "boxes": [...]})
        cooldown_s: evita invii troppo ravvicinati per lo stesso tipo evento
        """
        if not self._should_send(kind, cooldown_s):
            return

        payload = {
            "id": uuid.uuid4().hex,
            "ts": self._iso_now(),
            "type": kind,
            "data": data or {}
        }

        # MQTT
        if self.enable_mqtt and self.mqtt:
            try:
                self.mqtt.publish(self.mqtt_topic, json.dumps(payload), qos=0, retain=False)
            except Exception as e:
                print(f"[AlertSink] MQTT publish error: {e}")

        # Serial (linee semplici e leggibili in Arduino)
        if self.enable_serial and self.ser:
            try:
                line = None
                if kind.startswith("motion"):
                    # motion_start / motion_end / motion_keepalive -> 1/0/keep
                    if kind == "motion_start":
                        line = "MOTION 1\n"
                    elif kind == "motion_end":
                        line = "MOTION 0\n"
                    else:
                        line = "MOTION KEEP\n"
                elif kind == "face":
                    n = int((data or {}).get("faces", 1))
                    line = f"FACE {n}\n"
                else:
                    # generico
                    line = f"EVT {kind}\n"

                if line:
                    self.ser.write(line.encode("utf-8"))
            except Exception as e:
                print(f"[AlertSink] Serial write error: {e}")

    def close(self):
        if self.mqtt:
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
        if self.ser:
            try:
                self.ser.close()
            except:
                pass
