#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, time, threading, hashlib, io, queue, subprocess, json
from ctypes import CDLL, c_int
from flask import Flask, request, jsonify, send_file, Response
from gtts import gTTS
import requests
NODE_RED_HOOK = "http://192.168.1.108:1880/hook/gpio"  # cambia IP/porta se serve

# --- Anti-jitter / controllo movimento ---
SERVO_DEADBAND_DEG = 3         # ignora variazioni < 3°
SERVO_MIN_INTERVAL_S = 0.40    # non muovere più spesso di 400 ms
SERVO_BURST_MS = 300           # durata del burst PWM (poi duty=0)
SERVO_FREEWHEEL = True         # True = stacca PWM dopo il burst (meno tremolio)

# stato comando/annuncio
_last_servo_cmd_angle = 90
_last_servo_cmd_ts = 0.0
_last_servo_tts_ts = 0.0
SERVO_TTS_MIN_INTERVAL_S = 2.0  # non parlare più spesso di ogni 2s

# --- Debounce coda servo ---
_servo_cmd_lock = threading.Lock()
_servo_cmd_pending = None
SERVO_CMD_DEBOUNCE_MS = 120  # applica solo l'ultimo dopo 120ms

def _servo_cmd_worker():
    global _servo_cmd_pending
    while True:
        try:
            time.sleep(SERVO_CMD_DEBOUNCE_MS / 1000.0)
            with _servo_cmd_lock:
                ang = _servo_cmd_pending
                _servo_cmd_pending = None
            if ang is None:
                continue
            set_servo(ang, speak=True)
        except Exception as e:
            # Log non invasivo e continua
            print("[servo-cmd-worker] errore:", e)

def _notify_state_changed():
    with _state_lock:
        snap = dict(_state)
    _post_to_nodered(snap)


def _post_to_nodered(snapshot: dict):
    try:
        requests.post(NODE_RED_HOOK, json=snapshot, timeout=2)
    except Exception:
        pass

# =========================
# Config
# =========================
ENABLE_TTS = True       # disattiva se non vuoi l'audio
TTS_LANG   = "it"
TTS_PLAYER = ["mpg123", "-q"]   # lettore mp3
LED_PIN  = 17          # BCM
BTN_PIN  = 27
SERVO_PIN = 18

# =========================
# WiringPi load
# =========================
def load_wiringpi():
    for path in [
        "libwiringPi.so",
        "/usr/local/lib/libwiringPi.so",
        "/usr/lib/libwiringPi.so",
        "/usr/lib/aarch64-linux-gnu/libwiringPi.so",
    ]:
        try:
            return CDLL(path)
        except OSError:
            pass
    raise RuntimeError("Impossibile caricare libwiringPi.so (verifica wiringPi 3.x)")

wpi = load_wiringpi()
wpi.wiringPiSetupGpio.restype = c_int
wpi.pinMode.argtypes = [c_int, c_int]
wpi.digitalWrite.argtypes = [c_int, c_int]
wpi.digitalRead.argtypes  = [c_int]
wpi.pullUpDnControl.argtypes = [c_int, c_int]
wpi.softPwmCreate.argtypes = [c_int, c_int, c_int]
wpi.softPwmCreate.restype = c_int
wpi.softPwmWrite.argtypes  = [c_int, c_int]

INPUT, OUTPUT = 0, 1
PUD_OFF, PUD_DOWN, PUD_UP = 0, 1, 2

# =========================
# Stato condiviso
# =========================
_state = {"led": 0, "button": 0, "servo_angle": 90}
_state_lock = threading.Lock()

# =========================
# TTS: cache + coda riproduzione
# =========================
_tts_q = queue.Queue()

def _tts_cache_path(text, lang):
    h = hashlib.md5((lang + "|" + text).encode("utf-8")).hexdigest()
    return os.path.join("/tmp", f"tts_{h}.mp3")

def tts_bytes(text, lang="it"):
    p = _tts_cache_path(text, lang)
    if not os.path.exists(p):
        tts = gTTS(text=text, lang=lang)
        tts.save(p)
    with open(p, "rb") as f:
        return f.read()

def tts_enqueue(text, lang=TTS_LANG):
    if not ENABLE_TTS: 
        return
    _tts_q.put((text, lang))

def _tts_worker():
    while True:
        try:
            text, lang = _tts_q.get()
            p = _tts_cache_path(text, lang)
            if not os.path.exists(p):
                tts = gTTS(text=text, lang=lang)
                tts.save(p)
            # riproduzione sequenziale
            try:
                subprocess.run(TTS_PLAYER + [p], check=False)
            except Exception:
                pass
        except Exception:
            time.sleep(0.1)

def gpio_setup():
    if wpi.wiringPiSetupGpio() != 0:
        raise RuntimeError("wiringPiSetupGpio() failed")

    wpi.pinMode(LED_PIN, OUTPUT); wpi.digitalWrite(LED_PIN, 0)
    wpi.pinMode(BTN_PIN, INPUT);  wpi.pullUpDnControl(BTN_PIN, PUD_UP)

    rc = wpi.softPwmCreate(SERVO_PIN, 0, 100)
    if rc != 0:
        raise RuntimeError("softPwmCreate() failed")
    wpi.softPwmWrite(SERVO_PIN, servo_angle_to_softpwm(_state["servo_angle"]))

def read_button_raw():
    # pull-up: premuto=0 → invertiamo
    v = wpi.digitalRead(BTN_PIN)
    
    return 0 if v else 1

def set_led(value: int, speak=True):
    val = 1 if int(value) else 0
    wpi.digitalWrite(LED_PIN, val)
    with _state_lock:
        _state["led"] = val
    if speak:
        tts_enqueue("LED acceso" if val else "LED spento")

def servo_angle_to_softpwm(angle):
    angle = max(0, min(180, int(angle)))
    # duty 5..25 su range 0..100 (burst più “morbido”)
    return int(5 + (angle / 180.0) * (25 - 5))

def _speak_servo_if_needed(ang):
    global _last_servo_tts_ts
    now = time.time()
    if now - _last_servo_tts_ts >= SERVO_TTS_MIN_INTERVAL_S:
        tts_enqueue(f"Il servo è ruotato a {ang} gradi")
        _last_servo_tts_ts = now

def _apply_servo_pwm(angle):
    """Applica fisicamente la posizione con burst, poi (opzionale) freewheel."""
    duty = servo_angle_to_softpwm(angle)
    # burst ON
    wpi.softPwmWrite(SERVO_PIN, duty)
    time.sleep(SERVO_BURST_MS / 1000.0)
    # freewheel: rimuovi PWM per evitare tremolio a riposo
    if SERVO_FREEWHEEL:
        wpi.softPwmWrite(SERVO_PIN, 0)

def set_servo(angle: int, speak=True, force=False):
    """Muove il servo con deadband + rate limit + burst. 'force' salta i filtri."""
    global _last_servo_cmd_angle, _last_servo_cmd_ts
    angle = max(0, min(180, int(angle)))
    now = time.time()

    if not force:
        # deadband: non notificare né muovere se variazione troppo piccola
        if abs(angle - _last_servo_cmd_angle) < SERVO_DEADBAND_DEG:
            return  # ignora micro-cambi
        # rate-limit: troppo ravvicinato
        if (now - _last_servo_cmd_ts) < SERVO_MIN_INTERVAL_S:
            return  # ignora comandi ravvicinati

    # Applica
    _apply_servo_pwm(angle)

    with _state_lock:
        _state["servo_angle"] = angle
    _last_servo_cmd_angle = angle
    _last_servo_cmd_ts = now

    _notify_state_changed()
    if speak:
        _speak_servo_if_needed(angle)

def update_button_state_and_speak(prev):
    cur = read_button_raw()
    if cur != prev:
        with _state_lock:
            _state["button"] = cur
        # voce solo sugli edge
        tts_enqueue("Pulsante premuto" if cur else "Pulsante rilasciato")
    return cur

# thread polling pulsante + debounce
def button_poll_loop():
    last_hw = -1
    stable_cnt = 0
    logical = 0
    while True:
        hw = read_button_raw()
        if hw == last_hw:
            stable_cnt += 1
        else:
            stable_cnt = 0
            last_hw = hw
        if stable_cnt >= 2:  # ~100ms
            logical = update_button_state_and_speak(logical)
        time.sleep(0.05)


# =========================
# Flask
# =========================
app = Flask(__name__)

@app.route("/api/gpio/status")
def api_status():
    with _state_lock:
        data = dict(_state)
    return jsonify(data)

# ---- LED (robusto + comandi rapidi) ----
def _parse_led_value():
    js = request.get_json(silent=True)
    if isinstance(js, dict):
        for k in ("state", "value", "on", "payload"):
            if k in js:
                v = js[k]
                if isinstance(v, dict):
                    for kk in ("state","value","on"):
                        if kk in v:
                            v = v[kk]
                            break
                return v
    for q in ("state","value","on"):
        v = request.args.get(q)
        if v is not None: return v
    raw = (request.get_data(as_text=True) or "").strip().lower()
    if raw in ("1","on","true"): return 1
    if raw in ("0","off","false"): return 0
    if raw.isdigit(): return raw
    return None

@app.route("/api/gpio/led", methods=["POST","GET"])
def api_led():
    val = _parse_led_value()
    if val is None:
        return jsonify({"ok": False, "error": "no-state", "hint": "invia {\"state\":1} o ?state=1"}), 400
    val = 1 if str(val).lower() in ("1","on","true") else 0
    set_led(val, speak=True)
    return jsonify({"ok": True, "led": val})

@app.route("/api/gpio/led/<int:val>", methods=["GET","POST"])
def api_led_val(val):
    set_led(1 if val else 0, speak=True)
    return jsonify({"ok": True, "led": 1 if val else 0})

@app.route("/api/gpio/led/toggle", methods=["GET","POST"])
def api_led_toggle():
    with _state_lock:
        cur = _state["led"]
    newv = 0 if cur else 1
    set_led(newv, speak=True)
    return jsonify({"ok": True, "led": newv})

# ---- SERVO ----
@app.route("/api/gpio/servo", methods=["POST"])
def api_servo():
    global _servo_cmd_pending     # <<-- IMPORTANTE

    js = request.get_json(silent=True) or {}
    angle = js.get("angle", js.get("value", 90))
    try:
        a = int(angle)
        with _servo_cmd_lock:
            global _servo_cmd_pending
            _servo_cmd_pending = a
        return jsonify({"ok": True, "queued_angle": a})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    
@app.route("/api/gpio/servo/mode", methods=["POST"])
def api_servo_mode():
    # JSON: {"freewheel": true/false}
    js = request.get_json(silent=True) or {}
    fw = js.get("freewheel", None)
    if isinstance(fw, bool):
        global SERVO_FREEWHEEL
        SERVO_FREEWHEEL = fw
        return jsonify({"ok": True, "freewheel": SERVO_FREEWHEEL})
    return jsonify({"ok": False, "error": "Missing boolean 'freewheel'"}), 400

@app.route("/api/gpio/servo/force", methods=["POST"])
def api_servo_force():
    # JSON: {"angle": 0..180}
    js = request.get_json(silent=True) or {}
    angle = js.get("angle", None)
    if angle is None:
        return jsonify({"ok": False, "error": "Missing 'angle'"}), 400
    try:
        set_servo(int(angle), speak=True, force=True)  # salta deadband/rate-limit
        return jsonify({"ok": True, "servo_angle": int(angle)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ---- BUTTON ----
@app.route("/api/gpio/button")
def api_button():
    with _state_lock:
        b = _state["button"]
    return jsonify({"button": b})

# ---- TTS endpoints (se vuoi usarli anche da Node-RED) ----
@app.route("/tts")
def api_tts():
    text = request.args.get("text", "").strip()
    if not text: return "Parametro 'text' mancante.", 400
    lang = request.args.get("lang", TTS_LANG)
    audio = tts_bytes(text, lang=lang)
    return send_file(io.BytesIO(audio), mimetype="audio/mpeg", as_attachment=False, download_name="tts.mp3")

# ---- SSE opzionale per stato push (facile da collegare in Node-RED) ----
@app.route("/events")
def sse_events():
    def stream():
        last = None
        while True:
            with _state_lock:
                snap = dict(_state)
            if snap != last:
                yield f"data: {json.dumps(snap)}\n\n"
                last = snap
            time.sleep(0.1)
    return Response(stream(), mimetype="text/event-stream")

# =========================
# Avvio
# =========================
if __name__ == "__main__":
    gpio_setup()
    threading.Thread(target=button_poll_loop, daemon=True).start()
    threading.Thread(target=_tts_worker, daemon=True).start()
    threading.Thread(target=_servo_cmd_worker, daemon=True).start()  # dopo la definizione della variabile globale
    app.run(host="0.0.0.0", port=5000, debug=True)
