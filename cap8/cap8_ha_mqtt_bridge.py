#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bridge MQTT per Home Assistant, adattato ai topic del Cap.6:

TOPIC_BASE = lab/arduino1 (modificabile con env TOPIC_BASE)
Topic del bridge (input/output):
  lab/arduino1/sensor        -> JSON o testo con t/h (es: {"t":26.4,"h":34})
  lab/arduino1/sensortxt     -> testo libero (stringa)
  lab/arduino1/btn           -> 1/0
  lab/arduino1/status        -> online/offline
  lab/arduino1/cmd/led/{i}   -> comando verso Arduino (1/0)
  lab/arduino1/ack/led/{i}   -> ack da Arduino (1/0)

Crea in HA:
  sensor.<device>_temperature (°C)
  sensor.<device>_humidity (%)
  binary_sensor.<device>_button
  sensor.<device>_sensortxt (stringa)
  switch.<device>_led1 | led2 | led3
"""

import os
import re
import json
import time
import logging
from typing import Optional, Tuple
import paho.mqtt.client as mqtt

# ---------------- Config generali (override con env) ----------------
BROKER_HOST = os.getenv("MQTT_HOST", "192.168.1.108")
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
BROKER_USER = os.getenv("MQTT_USER", "")
BROKER_PASS = os.getenv("MQTT_PASS", "")

TOPIC_BASE  = os.getenv("TOPIC_BASE", "lab/arduino1").rstrip("/")
LED_COUNT   = int(os.getenv("LED_COUNT", "3"))  # quanti switch creare in HA (1..3)

DEVICE_ID   = os.getenv("DEVICE_ID", TOPIC_BASE.replace("/", "_"))
DEVICE_NAME = os.getenv("DEVICE_NAME", "Arduino1 Bridge")

CLIENT_ID   = os.getenv("CLIENT_ID", f"ha-bridge-{DEVICE_ID}")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s %(message)s")

# ---------------- Topic del bridge (Cap.6) ----------------
BR_SENSOR      = f"{TOPIC_BASE}/sensor"        # JSON: {"t":..,"h":..} o simile
BR_SENSOR_TXT  = f"{TOPIC_BASE}/sensortxt"     # testo
BR_BTN         = f"{TOPIC_BASE}/btn"           # 1/0
BR_STATUS      = f"{TOPIC_BASE}/status"        # online/offline
BR_CMD_LED     = f"{TOPIC_BASE}/cmd/led/+"     # publish 1/0 (HA -> bridge)
BR_ACK_LED     = f"{TOPIC_BASE}/ack/led/+"     # subscribe 1/0 (bridge -> HA)

# ---------------- Topic lato Home Assistant ----------------
HA_TEMPERATURE = f"iot/{DEVICE_ID}/temperature"
HA_HUMIDITY    = f"iot/{DEVICE_ID}/humidity"
HA_BUTTON      = f"iot/{DEVICE_ID}/button"
HA_SENSORTXT   = f"iot/{DEVICE_ID}/sensortxt"

CMD_ECHO_OPTIMISTIC = True  # aggiorna subito lo stato in HA, anche senza ACK

# Switch LED i: command/state
def ha_led_cmd(i: int) -> str:
    return f"iot/{DEVICE_ID}/led{i}/set"

def ha_led_state(i: int) -> str:
    return f"iot/{DEVICE_ID}/led{i}"

# Availability (collega le entità allo status del bridge)
AVAILABILITY_TOPIC = BR_STATUS
PAYLOAD_AVAILABLE  = "online"
PAYLOAD_UNAVAILABLE= "offline"

# ---------------- Utility ----------------
def parse_th(payload: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Cerca di estrarre temperatura e umidità da vari formati:
    - JSON: {"t": 26.5, "h": 34} oppure {"temp": 26.5, "humidity": 34}
    - CSV/kv: "t=26.5,h=34"  oppure "temp:26.5;hum:34"
    - solo numero: restituisce (val, None)
    """
    s = payload.strip()
    # JSON
    try:
        obj = json.loads(s)
        # comuni alias
        t = obj.get("t", obj.get("temp", obj.get("temperature")))
        h = obj.get("h", obj.get("hum", obj.get("humidity")))
        return (float(t) if t is not None else None,
                float(h) if h is not None else None)
    except Exception:
        pass
    # kv/CSV
    kv = dict(re.findall(r"([a-zA-Z]+)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", s))
    if kv:
        t = kv.get("t") or kv.get("temp") or kv.get("temperature")
        h = kv.get("h") or kv.get("hum") or kv.get("humidity")
        return (float(t) if t else None, float(h) if h else None)
    # solo numero?
    try:
        val = float(s)
        return (val, None)
    except Exception:
        return (None, None)

def is_on(x: str) -> bool:
    return str(x).strip().lower() in ("1", "on", "true", "yes")

# ---------------- MQTT Discovery ----------------
def discovery_messages():
    device = {
        "ids": [DEVICE_ID],
        "name": DEVICE_NAME,
        "mf": "Libro AI e hardware",
        "mdl": "MQTT-Serial Bridge",
        "sw": "1.0"
    }
    cfg = []

    # Sensor: temperature
    cfg.append((
        f"homeassistant/sensor/{DEVICE_ID}_temperature/config",
        {
            "unique_id": f"{DEVICE_ID}_temperature",
            "name": "Temperatura",
            "state_topic": HA_TEMPERATURE,
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": PAYLOAD_AVAILABLE,
            "payload_not_available": PAYLOAD_UNAVAILABLE,
            "device": device
        }
    ))

    # Sensor: humidity
    cfg.append((
        f"homeassistant/sensor/{DEVICE_ID}_humidity/config",
        {
            "unique_id": f"{DEVICE_ID}_humidity",
            "name": "Umidità",
            "state_topic": HA_HUMIDITY,
            "unit_of_measurement": "%",
            "device_class": "humidity",
            "state_class": "measurement",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": PAYLOAD_AVAILABLE,
            "payload_not_available": PAYLOAD_UNAVAILABLE,
            "device": device
        }
    ))

    # Binary sensor: button
    cfg.append((
        f"homeassistant/binary_sensor/{DEVICE_ID}_button/config",
        {
            "unique_id": f"{DEVICE_ID}_button",
            "name": "Pulsante",
            "state_topic": HA_BUTTON,
            "payload_on": "1",
            "payload_off": "0",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": PAYLOAD_AVAILABLE,
            "payload_not_available": PAYLOAD_UNAVAILABLE,
            "device": device
        }
    ))

    # Text sensor (sensor stringa)
    cfg.append((
        f"homeassistant/sensor/{DEVICE_ID}_sensortxt/config",
        {
            "unique_id": f"{DEVICE_ID}_sensortxt",
            "name": "Sensor Text",
            "state_topic": HA_SENSORTXT,
            "icon": "mdi:note-text",
            "availability_topic": AVAILABILITY_TOPIC,
            "payload_available": PAYLOAD_AVAILABLE,
            "payload_not_available": PAYLOAD_UNAVAILABLE,
            "device": device
        }
    ))

    # Switch LED 1..LED_COUNT
    for i in range(1, LED_COUNT + 1):
        cfg.append((
            f"homeassistant/switch/{DEVICE_ID}_led{i}/config",
            {
                "unique_id": f"{DEVICE_ID}_led{i}",
                "name": f"LED {i}",
                "command_topic": ha_led_cmd(i),
                "state_topic": ha_led_state(i),
                "payload_on": "ON",
                "payload_off": "OFF",
                "availability_topic": AVAILABILITY_TOPIC,
                "payload_available": PAYLOAD_AVAILABLE,
                "payload_not_available": PAYLOAD_UNAVAILABLE,
                "device": device
            }
        ))
    return cfg

# ---------------- MQTT Callbacks ----------------
def on_connect(client, userdata, flags, rc, properties=None):
    # LWT: se il bridge cade, Mosquitto pubblica "offline"
    client.will_set(AVAILABILITY_TOPIC, PAYLOAD_UNAVAILABLE, qos=1, retain=True)

    if rc == 0:
        logging.info("Connesso al broker MQTT")
        client.publish(AVAILABILITY_TOPIC, PAYLOAD_AVAILABLE, qos=1, retain=True)

        # Pubblica discovery (retain)
        for t, body in discovery_messages():
            client.publish(t, json.dumps(body), qos=1, retain=True)
            logging.info(f"Discovery pubblicato: {t}")

        # Subscribe ai topic del bridge
        client.subscribe(BR_SENSOR, qos=0)
        client.subscribe(BR_SENSOR_TXT, qos=0)
        client.subscribe(BR_BTN, qos=0)
        client.subscribe(BR_STATUS, qos=0)
        client.subscribe(BR_ACK_LED, qos=0)   # wildcard +

        # Subscribe ai comandi HA per ogni LED
        for i in range(1, LED_COUNT + 1):
            client.subscribe(ha_led_cmd(i), qos=0)

    else:
        logging.error(f"Connessione fallita rc={rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode(errors="ignore").strip()
    
    # logging.debug(f"RX {topic} = {payload}")

    # --- Dati dal bridge ---
    if topic == BR_SENSOR:
        t, h = parse_th(payload)
        if t is not None:
            client.publish(HA_TEMPERATURE, f"{t}", qos=0, retain=False)
        if h is not None:
            client.publish(HA_HUMIDITY, f"{h}", qos=0, retain=False)
        return

    if topic == BR_SENSOR_TXT:
        print(topic, payload, BR_SENSOR_TXT, HA_SENSORTXT)    
        client.publish(HA_SENSORTXT, payload, qos=0, retain=False)
        return

    if topic == BR_BTN:
        client.publish(HA_BUTTON, "1" if is_on(payload) else "0", qos=0, retain=False)
        return

    if topic == BR_STATUS:
        # solo log (HA leggerà availability da questo topic)
        logging.info(f"Availability: {payload}")
        return

    # ack led: lab/arduino1/ack/led/{i}
    if topic.startswith(f"{TOPIC_BASE}/ack/led/"):
        try:
            idx = int(topic.split("/")[-1])
        except Exception:
            return
        state = "ON" if is_on(payload) else "OFF"
        client.publish(ha_led_state(idx), state, qos=0, retain=False)
        return

    # --- Comandi da HA ---
    for i in range(1, LED_COUNT + 1):
        if topic == ha_led_cmd(i):
            raw = payload.strip().lower()
            # normalizza ON/OFF/1/0
            want_on = raw in ("on", "1", "true", "yes")

            # invia verso Arduino sul topic del Cap.6
            # 1) forma numerica (classica del tuo bridge)
            client.publish(f"{TOPIC_BASE}/cmd/led/{i}", "1" if want_on else "0", qos=0, retain=False)
            # 2) forma testuale (se lo sketch si aspetta 'on'/'off')
            client.publish(f"{TOPIC_BASE}/cmd/led/{i}", "on" if want_on else "off", qos=0, retain=False)

            logging.info(f"HA?Arduino: led{i} -> {'ON' if want_on else 'OFF'}")

            # opzionale: aggiorna subito lo stato in HA (in attesa dell'ACK)
            if CMD_ECHO_OPTIMISTIC:
                client.publish(ha_led_state(i), "ON" if want_on else "OFF", qos=0, retain=False)
            return


def on_disconnect(client, userdata, rc, properties=None):
    logging.warning("Disconnesso dal broker, tenterò il reconnect...")

def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID, clean_session=True)
    if BROKER_USER:
        client.username_pw_set(BROKER_USER, BROKER_PASS)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
