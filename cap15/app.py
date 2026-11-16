import os, io, csv, time, json, queue, threading, uuid, logging, subprocess, shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI

# ================== CONFIG ==================
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SERIAL_PORT    = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD    = int(os.getenv("SERIAL_BAUD", "115200"))
CSV_PATH       = os.getenv("CSV_PATH", "dht_log.csv")
HISTORY_MIN    = int(os.getenv("HISTORY_MIN", "180"))  # minuti di storico inviato al grafico

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__, static_folder="static", template_folder="templates")
os.makedirs(os.path.join(app.static_folder, "tts"), exist_ok=True)
logging.basicConfig(level=logging.INFO)
log = app.logger

# ================== SERIAL ==================
try:
    import serial
    HAVE_SERIAL = True
except Exception:
    HAVE_SERIAL = False
    log.warning("pyserial non installato: lettura Arduino disattivata.")

serial_in_q: "queue.Queue[str]" = queue.Queue()
serial_out_q: "queue.Queue[str]" = queue.Queue()
latest = {"temp": None, "hum": None, "ts": None}   # ultimo valore
ring = []  # [(ts_iso, temp, hum)]
ring_lock = threading.Lock()

def csv_append(ts_iso, temp, hum):
    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["ts", "temp", "hum"])
        w.writerow([ts_iso, temp, hum])

def prune_ring():
    cutoff = datetime.utcnow() - timedelta(minutes=HISTORY_MIN + 10)
    with ring_lock:
        # manteniamo solo ultimi N minuti circa
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
    print(HAVE_SERIAL)
    if not HAVE_SERIAL: 
        return
    while True:
        try:
            with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=1) as ser:
                log.info(f"[SERIAL] Aperto {SERIAL_PORT} @ {SERIAL_BAUD}")
                buf = b""
                # risponditore comandi in uscita
                def sender():
                    while True:
                        try:
                            cmd = serial_out_q.get(timeout=0.2)
                        except queue.Empty:
                            # flush
                            continue
                        if cmd:
                            ser.write((cmd.strip() + "\n").encode("utf-8"))
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
            print("temp hum")
            data = json.loads(raw)
            print(data.get("temp"))
            t = float(data.get("temp"))
            h = float(data.get("hum"))
            ts_iso = data.get("ts")
            print("temp:",t, "hum:",h)
            if not ts_iso:
                ts_iso = datetime.utcnow().isoformat(timespec="seconds")
            latest.update({"temp": t, "hum": h, "ts": ts_iso})
            with ring_lock:
                ring.append((ts_iso, t, h))
            csv_append(ts_iso, t, h)
            prune_ring()
        except Exception:
            # fallback: matcher semplice "t=.. h=.."
            if "t" in raw and "h" in raw:
                try:
                    # es: "t=21.0 h=40"
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

# avvio threads
threading.Thread(target=serial_reader, daemon=True).start()
threading.Thread(target=serial_parser, daemon=True).start()

# ================== INTENTI ==================
import re
INTENTS = {
    "LED_ON": [
        r"\baccend[i|ere]*\b.*\bled\b", r"\battiva\b.*\bled\b", r"\bανάψε\b.*\bled\b",
    ],
    "LED_OFF": [
        r"\bspegn[i|ere]*\b.*\bled\b", r"\bdisattiva\b.*\bled\b", r"\bσβήσε\b.*\bled\b",
    ],
    "GET_TEMPERATURE": [
        r"\btemperatura\b", r"\bπόσο\b.*\bθερμοκρασία\b",
    ],
    "GET_HUMIDITY": [
        r"\bumidità\b", r"\bυγρασία\b",
    ],
}

def detect_intent(text: str) -> dict:
    if not text:
        return {"name": "NONE", "confidence": 0.0}
    t = text.lower()
    for name, patterns in INTENTS.items():
        for pat in patterns:
            if re.search(pat, t):
                return {"name": name, "confidence": 0.95}
    return {"name": "NONE", "confidence": 0.2}

# ================== TTS / ASR ==================
def save_tts_mp3(text: str, voice: str = "alloy") -> str:
    fname = f"{uuid.uuid4().hex}.mp3"
    fpath = os.path.join(app.static_folder, "tts", fname)
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=text,
        response_format="mp3",
    ) as resp:
        resp.stream_to_file(fpath)
    return f"/static/tts/{fname}"

def transcribe_audio(file_storage, language: str = "it") -> str:
    raw = file_storage.read()
    mimetype = (file_storage.mimetype or "").lower()

    def _try(b: bytes, filename: str, mime: str) -> str:
        resp = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=(filename, io.BytesIO(b), mime),
            language=language,
            temperature=0
        )
        return (resp.text or "").strip()

    try:
        ext = ".webm" if "webm" in mimetype else (
              ".ogg"  if "ogg"  in mimetype else (
              ".wav"  if "wav"  in mimetype else (
              ".mp3"  if "mp3"  in mimetype else ".bin")))
        return _try(raw, f"audio{ext}", mimetype)
    except Exception as e1:
        if shutil.which("ffmpeg"):
            try:
                proc = subprocess.run(
                    ["ffmpeg","-v","error","-nostdin","-i","pipe:0","-f","wav",
                     "-acodec","pcm_s16le","-ac","1","-ar","16000","pipe:1"],
                    input=raw, capture_output=True, check=True
                )
                return _try(proc.stdout, "audio.wav", "audio/wav")
            except Exception as e2:
                raise e2
        raise e1

# ================== ROUTES ==================
@app.route("/")
def index():
    return render_template("index.html")

# Pulsanti → seriale
@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(silent=True) or {}
    cmd = (data.get("cmd") or "").strip().upper()
    if not cmd:
        return jsonify({"error": "cmd mancante"}), 400
    # Invia su seriale es. "LED_ON\n" o "LED_OFF\n"
    try:
        serial_out_q.put(cmd)
        return jsonify({"ok": True, "sent": cmd})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Dati: ultimo e storico
@app.route("/api/latest")
def api_latest():
    return jsonify(latest)

@app.route("/api/history")
def api_history():
    minutes = int(request.args.get("minutes", HISTORY_MIN))
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    out = []
    with ring_lock:
        for ts_iso, t, h in ring:
            try:
                ts = datetime.fromisoformat(ts_iso.replace("Z",""))
            except Exception:
                continue
            if ts >= cutoff:
                out.append({"ts": ts_iso, "temp": t, "hum": h})
    return jsonify(out)

# ASR (solo trascrizione)
@app.route("/api/asr", methods=["POST"])
def api_asr():
    if "audio" not in request.files:
        return jsonify({"error": "Nessun file audio"}), 400
    text = transcribe_audio(request.files["audio"], language="it")
    intent = detect_intent(text)

    # azioni immediate sugli intenti
    reply = ""
    if intent["name"] == "LED_ON":
        serial_out_q.put("LED_ON")
        reply = "Accendo il LED."
    elif intent["name"] == "LED_OFF":
        serial_out_q.put("LED_OFF")
        reply = "Spengo il LED."
    elif intent["name"] == "GET_TEMPERATURE":
        reply = f"La temperatura è {latest['temp']} gradi." if latest["temp"] is not None else "Non ho la temperatura al momento."
    elif intent["name"] == "GET_HUMIDITY":
        reply = f"L'umidità è {latest['hum']} percento." if latest["hum"] is not None else "Non ho l'umidità al momento."
    else:
        reply = "Ho capito."

    audio_url = save_tts_mp3(reply)
    return jsonify({"text": text, "intent": intent, "reply": reply, "audio_url": audio_url})

# Assistente testuale + TTS (senza visione)
@app.route("/api/assistant", methods=["POST"])
def api_assistant():
    data = request.get_json(silent=True) or {}
    user_text = (data.get("text") or "").strip()
    intent = detect_intent(user_text)

    reply = ""
    if intent["name"] == "LED_ON":
        serial_out_q.put("LED_ON")
        reply = "LED acceso."
    elif intent["name"] == "LED_OFF":
        serial_out_q.put("LED_OFF")
        reply = "LED spento."
    elif intent["name"] == "GET_TEMPERATURE":
        reply = f"La temperatura è {latest['temp']} gradi." if latest["temp"] is not None else "Non ho la temperatura al momento."
    elif intent["name"] == "GET_HUMIDITY":
        reply = f"L'umidità è {latest['hum']} percento." if latest["hum"] is not None else "Non ho l'umidità al momento."
    else:
        # fallback LLM (solo testo → risposta breve in italiano)
        comp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system","content":"Rispondi in italiano, breve e chiaro."},
                {"role":"user","content": user_text or "Ciao!"},
            ],
            temperature=0.4,
        )
        reply = comp.choices[0].message.content.strip()

    audio_url = save_tts_mp3(reply)
    return jsonify({"text": user_text, "intent": intent, "reply": reply, "audio_url": audio_url})

# ================== HTTPS START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False,
            ssl_context=("cert.pem", "key.pem"))
