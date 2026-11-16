import os, json, time, threading
from typing import Optional, Tuple
import paho.mqtt.client as mqtt
import serial

# ====== ENV ======
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

TOPIC_SENSORS = os.getenv("TOPIC_SENSORS", "sensors/dht11")
TOPIC_CMD     = os.getenv("TOPIC_CMD", "assistant/cmd")

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))

# ====== CACHE ======
class SensorCache:
    def __init__(self):
        self.temp: Optional[float] = None
        self.hum: Optional[float]  = None
        self.ts: float = 0.0
        self._lock = threading.Lock()

    def update(self, t: Optional[float], h: Optional[float]):
        with self._lock:
            if t is not None:
                self.temp = t
            if h is not None:
                self.hum  = h
            self.ts = time.time()

    def get(self) -> Tuple[Optional[float], Optional[float]]:
        with self._lock:
            return self.temp, self.hum

sensor_cache = SensorCache()

# ====== BRIDGE ======
_serial_bridge_singleton = None  # per request_one_shot()

class SerialBridge:
    def __init__(self, mqtt_client: mqtt.Client):
        self.mqtt = mqtt_client
        self.ser: Optional[serial.Serial] = None
        self.stop_flag = False
        self.serial_lock = threading.Lock()
        self.last_line = ""

    def _open_serial(self):
        while not self.stop_flag:
            try:
                self.ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1)
                print(f"[SER] Aperta {SERIAL_PORT} @ {SERIAL_BAUD}")
                return
            except Exception as e:
                print(f"[SER] Non apro {SERIAL_PORT}: {e}. Riprovo in 2s…")
                time.sleep(20)

    def _publish_sensor(self, data: dict):
        t = data.get("temp")
        h = data.get("hum")
        sensor_cache.update(t, h)
        print(f"[SER] Cache aggiornata: temp={t} hum={h}")
        # pub MQTT (opzionale)
        payload = json.dumps({k: data[k] for k in ("temp","hum") if k in data})
        if payload != "{}":
            self.mqtt.publish(TOPIC_SENSORS, payload, qos=0, retain=False)

    def _read_loop(self):
        while not self.stop_flag:
            if self.ser is None or not self.ser.is_open:
                self._open_serial(); continue
            try:
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                self.last_line = line
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        self._publish_sensor(data)
                    except Exception as e:
                        print(f"[SER] JSON non valido: {line} ({e})")
                else:
                    print(f"[SER] {line}")
            except Exception as e:
                print(f"[SER] Errore lettura: {e}")
                try: self.ser.close()
                except: pass
                self.ser = None
                time.sleep(20)

    def _on_mqtt_cmd(self, client, userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        led = (d.get("led") or "").lower()
        if led == "on":
            self.send_serial("LED_ON\n")
        elif led == "off":
            self.send_serial("LED_OFF\n")

    def send_serial(self, text: str):
        with self.serial_lock:
            try:
                if self.ser and self.ser.is_open:
                    self.ser.write(text.encode("utf-8"))
                    self.ser.flush()
                    print(f"[SER→ARD] {text.strip()}")
                else:
                    print("[SER] Porta seriale non aperta")
            except Exception as e:
                print(f"[SER] Errore invio: {e}")

    def start(self):
        self.mqtt.subscribe(TOPIC_CMD)
        self.mqtt.message_callback_add(TOPIC_CMD, self._on_mqtt_cmd)
        threading.Thread(target=self._read_loop, daemon=True).start()

def _on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connesso (rc={rc})")

def start_mqtt_listener() -> mqtt.Client:
    client = mqtt.Client()
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = _on_connect
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()
    return client

def start_serial_and_mqtt_bridge():
    global _serial_bridge_singleton
    client = start_mqtt_listener()
    bridge = SerialBridge(client)
    bridge.start()
    _serial_bridge_singleton = bridge
    return bridge

# ====== UTIL ======
def read_dht_local() -> Tuple[Optional[float], Optional[float]]:
    # ora i dati arrivano da Arduino → restituiamo la cache
    return sensor_cache.get()

def request_one_shot(timeout_sec: float = 2.0) -> Tuple[Optional[float], Optional[float]]:
    
    """
    Invia 'READ' ad Arduino e attende fino a timeout un aggiornamento di cache.
    Ritorna (temp, hum) anche se già presenti (non resetta).
    """
    start_ts = sensor_cache.ts
    if _serial_bridge_singleton:
        _serial_bridge_singleton.send_serial("READ\n")
    else:
        print("[SER] Bridge non avviato, impossibile READ")

    t0 = time.time()
##    while time.time() - t0 < timeout_sec:
##        if sensor_cache.ts > start_ts:
##            break
##        time.sleep(0.05)
    return sensor_cache.get()
