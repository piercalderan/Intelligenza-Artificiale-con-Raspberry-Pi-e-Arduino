import os, json, csv, threading, time, queue, sqlite3
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request, send_file, abort
from dotenv import load_dotenv
import logging as log

log.basicConfig(level=log.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------- Config ----------------
load_dotenv()
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUD = int(os.getenv("BAUD", "115200"))
SERIAL_BAUD = BAUD
HISTORY_MIN = int(os.getenv("HISTORY_MIN", "60"))  # minuti di history in RAM

DATA_DIR = "data"; os.makedirs(DATA_DIR, exist_ok=True)
CSV_PATH = os.path.join(DATA_DIR, "weather.csv")
DB_PATH  = os.path.join(DATA_DIR, "weather.db")
USE_SQLITE = os.getenv("USE_SQLITE", "0") == "1"
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai")  # default: openai

# --------------- Serial (Cap 15 style) ---------------
try:
    import serial
    from serial import SerialException
    HAVE_SERIAL = True
except Exception:
    HAVE_SERIAL = False
    log.warning("pyserial non installato: lettura Arduino disattivata.")

serial_in_q: "queue.Queue[str]" = queue.Queue()
serial_out_q: "queue.Queue[str]" = queue.Queue()

latest = {"temp": None, "hum": None, "ts": None}   # ultimo valore per /api/latest
ring = []  # [(ts_iso, temp, hum)]
ring_lock = threading.Lock()

def csv_append(ts_iso, temp, hum):
    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_iso", "temp", "hum"])
        w.writerow([ts_iso, temp, hum])

def prune_ring():
    cutoff = datetime.utcnow() - timedelta(minutes=HISTORY_MIN + 10)
    with ring_lock:
        keep = []
        for ts_iso, t, h in ring:
            try:
                ts = datetime.fromisoformat(ts_iso.replace("Z",""))
            except Exception:
                continue
            if ts >= cutoff:
                keep.append((ts_iso, t, h))
        ring[:] = keep

def serial_reader():
    if not HAVE_SERIAL:
        return
    while True:
        try:
            with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1, exclusive=True) as ser:
                log.info(f"[SERIAL] Aperto {SERIAL_PORT} @ {SERIAL_BAUD}")

                def sender():
                    while True:
                        try:
                            cmd = serial_out_q.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        if cmd:
                            try:
                                ser.write((cmd.strip() + "\n").encode("utf-8"))
                            except Exception as e:
                                log.warning(f"[SERIAL] errore invio: {e}")
                threading.Thread(target=sender, daemon=True).start()

                while True:
                    line = ser.readline()
                    if not line:
                        continue
                    raw = line.decode("utf-8", errors="ignore").strip()
                    serial_in_q.put(raw)

        except Exception as e:
            log.warning(f"[SERIAL] errore apertura/lettura: {e}; retry fra 2s")
            time.sleep(2)

def serial_parser():
    while True:
        raw = serial_in_q.get()
        try:
            data = json.loads(raw)  # atteso: {"temp":..,"hum":..,"ts":..}
            t = float(data.get("temp"))
            h = float(data.get("hum"))
            print("temp:",t,"hum",h)
            ts_iso = data.get("ts") or datetime.utcnow().isoformat(timespec="seconds")
            latest.update({"temp": t, "hum": h, "ts": ts_iso})
            with ring_lock:
                ring.append((ts_iso, t, h))
            csv_append(ts_iso, t, h)
            prune_ring()
        except Exception:
            if "t" in raw and "h" in raw:
                try:
                    parts = raw.replace(":", " ").replace(",", " ").split()
                    nums = [p for p in parts if any(c.isdigit() for c in p)]
                    vals = [float(s.replace("t=","").replace("h=","")) for s in nums[:2]]
                    t, h = vals[0], vals[1]
                    ts_iso = datetime.utcnow().isoformat(timespec="seconds")
                    latest.update({"temp": t, "hum": h, "ts": ts_iso})
                    with ring_lock:
                        ring.append((ts_iso, t, h))
                    csv_append(ts_iso, t, h)
                    prune_ring()
                except Exception:
                    pass

threading.Thread(target=serial_reader, daemon=True).start()
threading.Thread(target=serial_parser, daemon=True).start()

# --------------- TTS --------------------
def tts_openai(text, out_path):
    from openai import OpenAI
    client = OpenAI()
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=text,
        response_format="wav"
    ) as response:
        response.stream_to_file(out_path)
    return out_path

def tts_gtts(text, out_path):
    from gtts import gTTS
    tts = gTTS(text=text, lang="it")
    tts.save(out_path)
    return out_path

def speak(text):
    out = os.path.join(DATA_DIR, "voice.wav")
    if TTS_PROVIDER == "gtts":
        return tts_gtts(text, out)
    return tts_openai(text, out)

# --------------- Flask ------------------
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.get("/api/latest")
def api_latest():
    return jsonify(latest)

@app.get("/api/history")
def api_history():
    N = int(request.args.get("n", "200"))
    with ring_lock:
        rows = [{"timestamp_iso": ts, "temp": t, "hum": h} for (ts,t,h) in ring[-N:]]
    return jsonify(rows)

@app.post("/api/tts")
def api_tts():
    text = request.json.get("text","")
    if not text.strip():
        abort(400, "missing text")
    path = speak(text)
    return send_file(path, mimetype="audio/wav", as_attachment=False)

@app.post("/api/arduino/led")
def api_led():
    action = request.json.get("action")
    if action == "on":
        serial_out_q.put("LED_ON")
    elif action == "off":
        serial_out_q.put("LED_OFF")
    return jsonify({"ok": True})

# ================== HTTPS START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
            #ssl_context=("cert.pem", "key.pem"))
    
