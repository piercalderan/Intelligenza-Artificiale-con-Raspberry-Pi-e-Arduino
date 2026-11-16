# assistente_bmp180.py â€” CircuitPython BMP180 â†’ MQTT (+ HA Discovery)
import os, json, time, sys, signal
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DISC_PREFIX = "homeassistant"
BASE = "home/assistant"
TOPIC_STATE = f"{BASE}/bmp180"
TOPIC_AVAIL = f"{BASE}/status"
PUBLISH_EVERY = int(os.getenv("PUBLISH_EVERY", "10"))
I2C_ADDR = int(os.getenv("I2C_ADDR", "0x77"), 16)

import board, busio
from circuitpython-bmp180 import BMP180

i2c = busio.I2C(board.SCL, board.SDA)
bmp = BMP180(i2c, address=I2C_ADDR)  # default 0x77
# opzionale: bmp.oversample = 2

client = mqtt.Client("assistant-bmp180")
client.will_set(TOPIC_AVAIL, "offline", retain=True)

def ha_discovery():
    dev = {"identifiers":["assistant_bmp180"],"name":"Assistente BMP180","manufacturer":"Bosch","model":"BMP180"}
    client.publish(f"{DISC_PREFIX}/sensor/assistant_temperature/config", json.dumps({
        "name":"Assistente Temperatura","uniq_id":"assistant_bmp180_temp",
        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.temp_c }}",
        "unit_of_meas":"Â°C","dev_cla":"temperature","state_class":"measurement","avty_t":TOPIC_AVAIL,"device":dev
    }), retain=True)
    client.publish(f"{DISC_PREFIX}/sensor/assistant_pressure/config", json.dumps({
        "name":"Assistente Pressione","uniq_id":"assistant_bmp180_press",
        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.press_hpa }}",
        "unit_of_meas":"hPa","dev_cla":"pressure","state_class":"measurement","avty_t":TOPIC_AVAIL,"device":dev
    }), retain=True)
    client.publish(TOPIC_AVAIL, "online", retain=True)

def on_connect(c,u,f,rc):
    print("[MQTT] Connected:", rc)
    ha_discovery()

client.on_connect = on_connect
client.connect(MQTT_HOST, MQTT_PORT, 30)
client.loop_start()

def sigterm(*_):
    try: client.publish(TOPIC_AVAIL, "offline", retain=True)
    except: pass
    sys.exit(0)
signal.signal(signal.SIGINT, sigterm)
signal.signal(signal.SIGTERM, sigterm)

while True:
    temp_c = round(float(bmp.temperature), 1)
    press_hpa = round(float(bmp.pressure), 1)   # giÃ  in hPa
    alt_m = getattr(bmp, "altitude", None)
    alt_m = None if alt_m is None else round(float(alt_m), 1)
    payload = {"temp_c": temp_c, "press_hpa": press_hpa, "alt_m": alt_m, "ts": datetime.now(timezone.utc).isoformat()}
    client.publish(TOPIC_STATE, json.dumps(payload))
    time.sleep(PUBLISH_EVERY)


### assistente_bmp180.py — CircuitPython BMP180 → MQTT (+ HA Discovery)
##import os, json, time, sys, signal
##from datetime import datetime, timezone
##import paho.mqtt.client as mqtt
##
##MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
##MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
##DISC_PREFIX = "homeassistant"
##BASE = "home/assistant"
##TOPIC_STATE = f"{BASE}/bmp180"
##TOPIC_AVAIL = f"{BASE}/status"
##PUBLISH_EVERY = int(os.getenv("PUBLISH_EVERY", "10"))
##I2C_ADDR = int(os.getenv("I2C_ADDR", "0x77"), 16)
##
##import board, busio
##from circuitpython_bmp180 import BMP180
##
##i2c = busio.I2C(board.SCL, board.SDA)
##bmp = BMP180(i2c, address=I2C_ADDR)  # default 0x77
### opzionale: bmp.oversample = 2
##
##client = mqtt.Client("assistant-bmp180")
##client.will_set(TOPIC_AVAIL, "offline", retain=True)
##
##def ha_discovery():
##    dev = {"identifiers":["assistant_bmp180"],"name":"Assistente BMP180","manufacturer":"Bosch","model":"BMP180"}
##    client.publish(f"{DISC_PREFIX}/sensor/assistant_temperature/config", json.dumps({
##        "name":"Assistente Temperatura","uniq_id":"assistant_bmp180_temp",
##        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.temp_c }}",
##        "unit_of_meas":"°C","dev_cla":"temperature","state_class":"measurement","avty_t":TOPIC_AVAIL,"device":dev
##    }), retain=True)
##    client.publish(f"{DISC_PREFIX}/sensor/assistant_pressure/config", json.dumps({
##        "name":"Assistente Pressione","uniq_id":"assistant_bmp180_press",
##        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.press_hpa }}",
##        "unit_of_meas":"hPa","dev_cla":"pressure","state_class":"measurement","avty_t":TOPIC_AVAIL,"device":dev
##    }), retain=True)
##    client.publish(TOPIC_AVAIL, "online", retain=True)
##
##def on_connect(c,u,f,rc):
##    print("[MQTT] Connected:", rc)
##    ha_discovery()
##
##client.on_connect = on_connect
##client.connect(MQTT_HOST, MQTT_PORT, 30)
##client.loop_start()
##
##def sigterm(*_):
##    try: client.publish(TOPIC_AVAIL, "offline", retain=True)
##    except: pass
##    sys.exit(0)
##signal.signal(signal.SIGINT, sigterm)
##signal.signal(signal.SIGTERM, sigterm)
##
##while True:
##    temp_c = round(float(bmp.temperature), 1)
##    press_hpa = round(float(bmp.pressure), 1)   # già in hPa
##    alt_m = getattr(bmp, "altitude", None)
##    alt_m = None if alt_m is None else round(float(alt_m), 1)
##    payload = {"temp_c": temp_c, "press_hpa": press_hpa, "alt_m": alt_m, "ts": datetime.now(timezone.utc).isoformat()}
##    client.publish(TOPIC_STATE, json.dumps(payload))
##    time.sleep(PUBLISH_EVERY)
