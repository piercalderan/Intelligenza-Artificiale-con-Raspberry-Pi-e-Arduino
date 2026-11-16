# cap6_pi_mqtt_serial_bridge.py
import os, time, json, threading
from dotenv import load_dotenv
import serial, serial.tools.list_ports
import paho.mqtt.client as mqtt

#lab/arduino1/cmd/led/1

load_dotenv()
MQTT_HOST = os.getenv("MQTT_HOST","127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None
CLIENT_ID = os.getenv("CLIENT_ID")
TOPIC_BASE = os.getenv("TOPIC_BASE")
#TOPIC_BASE = "lab/arduino1"
SERIAL_PORT = os.getenv("SERIAL_PORT")
BAUDRATE    = int(os.getenv("BAUDRATE"))


TOP_CMD_LED  = f"{TOPIC_BASE}/cmd/led/+"      # + = 1..3
TOP_CMD_SENS = f"{TOPIC_BASE}/cmd/sensor"
TOP_ACK_LED  = f"{TOPIC_BASE}/ack/led/{{idx}}"
TOP_SENSOR   = f"{TOPIC_BASE}/sensor"
TOP_SENSOR_TXT   = f"{TOPIC_BASE}/sensortxt"
TOP_BTN      = f"{TOPIC_BASE}/btn"
TOP_STATUS   = f"{TOPIC_BASE}/status"

print(TOP_CMD_LED)
print(TOP_CMD_SENS)
print(TOP_ACK_LED)
print(TOP_SENSOR)
print(TOP_SENSOR_TXT)
print(TOP_BTN)
print(TOP_STATUS)

ser = None
ser_lock = threading.Lock()
import os, json, requests, datetime

SHEETS_URL = "https://script.google.com/macros/s/AKfycbzBGivdTS8ZQja9DPiMohLiwuSfqvkBCwhMguKpa77tnKSMgrYs_pe2UlWqCH2vMHjmRw/exec"

def send_to_sheet(temp, hum):
    url = "https://script.google.com/macros/s/AKfycbxj9HxsebvftTUeXv1UTyPPAIjaaAcITGIzSTSqrAQFq0nlIajoJ6Fx-VLf8KNHxslOUA/exec"
    payload = {
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "temperature": temp,
    "humidity": hum
    }
    r = requests.post(url, json=payload, timeout=15)
    print(r.text)  # atteso: {"ok":true}



def open_serial():
    global ser
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.2)
            print("Seriale aperta:", SERIAL_PORT)
            publish(TOP_STATUS, f"SERIAL_OPEN {SERIAL_PORT}")
            return
        except Exception as e:
            print("Seriale non disponibile:", e)
            publish(TOP_STATUS, "SERIAL_WAIT")
            time.sleep(1)

def publish(topic, payload, qos=1, retain=False):
    try:
        client.publish(topic, payload, qos=qos, retain=retain)
    except Exception as e:
        print("MQTT publish error:", e)

def send_serial_line(line):
    print(line)

    with ser_lock:
        try:
            if ser:
                ser.write((line+"\n").encode("utf-8"))
        except Exception as e:
            print("Errore seriale TX:", e)

def on_connect(cli, userdata, flags, rc, props=None):
    print("MQTT connesso:", rc)
    cli.subscribe(TOP_CMD_LED, qos=1)
    cli.subscribe(TOP_CMD_SENS, qos=1)
    publish(TOP_STATUS, "MQTT_CONNECTED")


def on_message(cli, userdata, msg):
    topic = msg.topic
    print(msg)
    payload = (msg.payload or b"").decode("utf-8", "ignore").strip().upper()
    # led cmd
    if topic.startswith(f"{TOPIC_BASE}/cmd/led/"):
        try:
            idx = int(topic.rsplit("/", 1)[-1])
        except:
            return
        action = payload  # gia upper()

        # normalizza alcune varianti
        if action in ("ON", "OFF", "BLINK", "BLINK OFF"):
            send_serial_line(f"LED {idx} {action}")
        elif action in ("STOP", "STOP BLINK", "BLINK_STOP"):
            send_serial_line(f"LED {idx} BLINK OFF")
        else:
            print("Azione LED non valida:", action)


    elif topic == TOP_CMD_SENS:
        if payload in ("READ","",None):
            send_serial_line("SENSOR?")

def serial_reader():
    global ser
    buf = b""
    while True:
        try:
            if ser is None or not ser.is_open:
                open_serial()
            b = ser.readline()
            
            if not b:
                continue
            line = b.decode("utf-8", "ignore").strip()
            
            if not line: 
                continue
            # Parse risposte Arduino:
            # "ACK LED 1 ON" | "BTN 1 PRESSED" | "SENSOR t=23.1 h=41" | "SENSOR ERR"
            if line.startswith("ACK LED"):
                parts = line.split()
                try:
                    idx = int(parts[2])
                    action = " ".join(parts[3:])  # es: "BLINK OFF"
                    publish(TOP_ACK_LED.format(idx=idx), action)
                except:
                    pass

            elif line.startswith("BTN"):
                # BTN 1 PRESSED
                print(line)
                try:
                    idx = int(line.split()[1])
                    nr=json.dumps({"id": idx})
                    publish(TOP_BTN, nr)
                    
                    print(TOP_BTN,nr)
                except: pass
            elif line.startswith("SENSOR t="):
                # SENSOR t=23.1 h=41
                try:
                    p = line.replace("SENSOR","").strip()
                    # t=... h=...
                    kv = dict([tuple(x.split("=")) for x in p.split()])
                    t = float(kv.get("t", "nan")); h = float(kv.get("h","nan"))
                    publish(TOP_SENSOR, json.dumps({"t": t,"h": h}))
                    
                    publish(TOP_SENSOR_TXT, "t\t" + str(t) + "\t" + "h\t" + str(h))
                    print(t,h)
                    #send_to_sheet(t, h)
                except Exception as e:
                    print("Parse sensore fallita:", e)
            elif line.startswith("SENSOR ERR"):
                publish(TOP_SENSOR, json.dumps({"err":"read_failed"}))
            else:
                publish(TOP_STATUS, f"RAW {line}")
        except Exception as e:
            print("Serial reader err:", e)
            publish(TOP_STATUS, "SERIAL_ERROR")
            try:
                ser.close()
            except: pass
            ser = None
            time.sleep(1)

# MQTT client
client = mqtt.Client(client_id=CLIENT_ID,
                     clean_session=True, protocol=mqtt.MQTTv311)

if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_start()

# thread seriale
thr = threading.Thread(target=serial_reader, daemon=True)
thr.start()

print("Bridge avviato. MQTT→Serial su", MQTT_HOST, " / ", SERIAL_PORT)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    client.loop_stop()
    try:
        if ser: ser.close()
    except: pass
