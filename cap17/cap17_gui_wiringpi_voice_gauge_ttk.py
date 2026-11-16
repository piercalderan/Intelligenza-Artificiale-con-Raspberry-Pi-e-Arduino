#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI Tkinter + GPIO (wiringPi) + OpenAI TTS/STT — skinnata con ttk
- LED ON/OFF con feedback TTS + badge pulsante fisico
- Servo su GPIO18 con soft-PWM (50 Hz) senza sudo
- Comandi vocali in italiano (arecord -> OpenAI STT)
- Indicatore visivo a "lancetta" su semicerchio 0..180°
Requisiti:
  sudo apt-get install -y wiringpi alsa-utils sox libsox-fmt-all
  pip install openai
  export OPENAI_API_KEY=sk-...
Opzionali:
  export OPENAI_TTS_MODEL=gpt-4o-mini-tts
  export OPENAI_STT_MODEL=gpt-4o-mini-transcribe
  export ARECORD_DEV="plughw:1,0"
"""

import os, sys, time, threading, tempfile, subprocess, re, shutil, math
import tkinter as tk
from tkinter import ttk, scrolledtext

# =========================
# OpenAI client (SDK 1.x)
# =========================
from openai import OpenAI
client = OpenAI()
TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
STT_MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")

# =========================
# wiringPi import / shim
# =========================
try:
    import wiringpi as wp  # se c'è il binding Python
except Exception:
    import ctypes
    _lib = None
    for _p in ("libwiringPi.so",
               "/usr/local/lib/libwiringPi.so",
               "/usr/lib/arm-linux-gnueabihf/libwiringPi.so",
               "/usr/lib/libwiringPi.so"):
        try:
            _lib = ctypes.CDLL(_p); break
        except Exception:
            _lib = None
    if not _lib:
        raise RuntimeError("libwiringPi.so non trovata. Installa: sudo apt-get install -y wiringpi")

    class _WP:
        INPUT=0; OUTPUT=1; PWM_OUTPUT=2
        PUD_OFF=0; PUD_DOWN=1; PUD_UP=2
        PWM_MODE_MS=0
        INT_EDGE_FALLING=1
    _lib.wiringPiSetupGpio.restype = ctypes.c_int
    _lib.pinMode.argtypes = [ctypes.c_int, ctypes.c_int]
    _lib.digitalWrite.argtypes = [ctypes.c_int, ctypes.c_int]
    _lib.digitalRead.argtypes  = [ctypes.c_int]; _lib.digitalRead.restype = ctypes.c_int
    _lib.pullUpDnControl.argtypes = [ctypes.c_int, ctypes.c_int]

    _setup = False
    def _ensure():
        global _setup
        if not _setup:
            if _lib.wiringPiSetupGpio() != 0:
                raise RuntimeError("wiringPiSetupGpio failed")
            _setup = True

    def pinMode(pin, mode):        _ensure(); _lib.pinMode(int(pin), int(mode))
    def digitalWrite(pin, val):    _ensure(); _lib.digitalWrite(int(pin), int(val))
    def digitalRead(pin):          _ensure(); return int(_lib.digitalRead(int(pin)))
    def pullUpDnControl(pin, pud): _ensure(); _lib.pullUpDnControl(int(pin), int(pud))

    _callbacks={}
    def _poll(pin):
        last = digitalRead(pin)
        while True:
            cur = digitalRead(pin)
            if last==1 and cur==0:
                cb=_callbacks.get(pin)
                if cb:
                    try: cb()
                    except: pass
            last = cur
            time.sleep(0.01)
    def wiringPiISR(pin, edge, cb):
        _ensure(); _callbacks[int(pin)] = cb
        threading.Thread(target=_poll, args=(int(pin),), daemon=True).start()

    class _WPMOD:
        INPUT=_WP.INPUT; OUTPUT=_WP.OUTPUT; PWM_OUTPUT=_WP.PWM_OUTPUT
        PUD_OFF=_WP.PUD_OFF; PUD_DOWN=_WP.PUD_DOWN; PUD_UP=_WP.PUD_UP
        PWM_MODE_MS=_WP.PWM_MODE_MS; INT_EDGE_FALLING=_WP.INT_EDGE_FALLING
        pinMode=staticmethod(pinMode)
        digitalWrite=staticmethod(digitalWrite)
        digitalRead=staticmethod(digitalRead)
        pullUpDnControl=staticmethod(pullUpDnControl)
        wiringPiISR=staticmethod(wiringPiISR)
    # aggiungi setup come staticmethod
    _WPMOD.wiringPiSetupGpio = staticmethod(lambda: _ensure())
    wp = _WPMOD()

# =========================
# Config pin BCM
# =========================
LED_PIN = 17
BTN_PIN = 27
SERVO_PIN = 18
DEFAULT_MIN_US = 1000
DEFAULT_MAX_US = 2000

# =========================
# Init GPIO
# =========================
wp.wiringPiSetupGpio()
wp.pinMode(LED_PIN, wp.OUTPUT)
wp.digitalWrite(LED_PIN, 0)
wp.pinMode(BTN_PIN, wp.Input if hasattr(wp, "Input") else wp.INPUT)
wp.pullUpDnControl(BTN_PIN, wp.PUD_UP)

# =========================
# Soft PWM Servo (50 Hz)
# =========================
class SoftServo:
    def __init__(self, pin, min_us=1000, max_us=2000, freq=50):
        self.pin = pin
        self.min_us = int(min_us)
        self.max_us = int(max_us)
        self.period = 1.0 / float(freq)  # 0.02 s
        self._pulse_us = 1500
        self._running = True
        wp.pinMode(self.pin, wp.OUTPUT)
        wp.digitalWrite(self.pin, 0)
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
    def _loop(self):
        while self._running:
            start = time.perf_counter()
            wp.digitalWrite(self.pin, 1)
            t_end = start + (self._pulse_us / 1_000_000.0)
            while time.perf_counter() < t_end:
                time.sleep(0)
            wp.digitalWrite(self.pin, 0)
            rest = self.period - (time.perf_counter() - start)
            if rest > 0:
                time.sleep(rest)
    def write_us(self, us):
        us = max(self.min_us, min(self.max_us, int(us)))
        self._pulse_us = us
    def write_deg(self, deg):
        deg = max(0, min(180, int(deg)))
        us = self.min_us + int((deg/180.0) * (self.max_us - self.min_us))
        self.write_us(us)
    def stop(self):
        self._running = False
        try: self._th.join(timeout=0.1)
        except Exception: pass
        wp.digitalWrite(self.pin, 0)

soft_servo = SoftServo(SERVO_PIN, DEFAULT_MIN_US, DEFAULT_MAX_US, 50)

def angle_to_us(deg):
    deg = max(0, min(180, int(deg)))
    return DEFAULT_MIN_US + int((deg/180.0)*(DEFAULT_MAX_US-DEFAULT_MIN_US))

# =========================
# Audio (arecord/aplay)
# =========================
ARECORD = shutil.which("arecord")
APLAY   = shutil.which("aplay")
ARECORD_DEV = os.getenv("ARECORD_DEV")
ARECORD_ARGS = ["arecord","-f","S16_LE","-r","16000","-d","4","-q"]
if ARECORD_DEV: ARECORD_ARGS += ["-D", ARECORD_DEV]
APLAY_ARGS   = ["aplay","-q"]

def speak(text):
    try:
        tmp = tempfile.NamedTemporaryFile(delete=True, suffix=".wav")
        tmp.close()
        with client.audio.speech.with_streaming_response.create(
            model=TTS_MODEL, voice="alloy", input=text, response_format="wav"
        ) as resp:
            resp.stream_to_file(tmp.name)
            #print(tmp.name)
        subprocess.run(APLAY_ARGS + [tmp.name], check=False)
        os.unlink(tmp.name)
    except Exception as e:
        print("TTS error:", e, file=sys.stderr)

def speak_async(text):
    threading.Thread(target=speak, args=(text,), daemon=True).start()

# =========================
# Logica comandi
# =========================
ANGLE_RE = re.compile(r'(\d{1,3})\s*gradi')
gui_state = {'servo_deg': 90}

def set_servo_angle(deg):
    deg = max(0, min(180, int(deg)))
    soft_servo.write_deg(deg)
    gui_state['servo_deg'] = deg
    servo_var.set(deg)
    update_gauge(deg)

def led_on():
    wp.digitalWrite(LED_PIN, 1); led_var.set("LED: ON")
def led_off():
    wp.digitalWrite(LED_PIN, 0); led_var.set("LED: OFF")

import unicodedata, difflib

def norm_it(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")  # senza accenti
    repl = {
        " accendere ": " accendi ", " spegnere ": " spegni ",
        " led ":" led ", " luce ":" led ",
        " centro ":" centra ", " centr ": " centra ",
        "sinistro":"sinistra","destro":"destra",
    }
    s = f" {s} "
    for k,v in repl.items(): s = s.replace(k, v)
    return " ".join(s.split())

CANONICAL = [
    "accendi led",
    "spegni led",
    "centra il servo",
    "vai a X gradi",
    "ruota a sinistra di X gradi",
    "ruota a destra di X gradi",
]

def snap_to_command(text: str) -> str:
    """
    Prova ad "agganciare" stringhe strane al comando più vicino.
    Mantiene i numeri (gradi) se presenti.
    """
    t = norm_it(text)
    # estrai eventuale numero
    import re
    m = re.search(r"(\d{1,3})", t)
    num = m.group(1) if m else None

    # mappe rapide
    if "accendi" in t and "led" in t: return "accendi led"
    if "spegni"  in t and "led" in t: return "spegni led"
    if "centra"  in t and "servo" in t: return "centra il servo"
    if ("vai a" in t or "porta a" in t or "metti a" in t) and num:
        return f"vai a {num} gradi"
    if "sinistra" in t and num:
        return f"ruota a sinistra di {num} gradi"
    if "destra" in t and num:
        return f"ruota a destra di {num} gradi"

    # fallback fuzzy contro frasi canoniche senza numero
    base = ["accendi led","spegni led","centra il servo"]
    match = difflib.get_close_matches(t, base, n=1, cutoff=0.6)
    if match:
        return match[0]
    return text  # lascia così se non siamo sicuri

def parse_and_execute(cmd: str):
    c = cmd.lower()
    if ("accendi" in c and "led" in c) or "led on" in c:
        led_on(); say("LED acceso"); return "LED acceso"
    if ("spegni" in c and "led" in c) or "led off" in c:
        led_off(); say("LED spento"); return "LED spento"
    if "centra" in c or "centro" in c:
        set_servo_angle(90); say("Servo al centro, 90 gradi"); return "servo=90"
    m = ANGLE_RE.search(c)
    if any(k in c for k in ["vai a","porta","metti a"]) and m:
        deg = int(m.group(1)); set_servo_angle(deg); say(f"Servo a {deg} gradi"); return f"servo={deg}"
    if m and "sinistra" in c:
        delta=int(m.group(1)); set_servo_angle(gui_state['servo_deg']-delta); say(f"Ruoto a sinistra di {delta} gradi"); return f"servo-={delta}"
    if m and "destra" in c:
        delta=int(m.group(1)); set_servo_angle(gui_state['servo_deg']+delta); say(f"Ruoto a destra di {delta} gradi"); return f"servo+={delta}"
    say("Comando non riconosciuto"); return "N/A"

# =========================
# Tk + ttk style
# =========================
root = tk.Tk()
root.title("WiringPi + OpenAI: LED & Servo (soft-PWM)")
# --- TTK STYLE (tema & palette) ---
style = ttk.Style()
try: style.theme_use("clam")
except Exception: pass

BG  = "#f6f7fb"; FG="#1f2430"; PRI="#2563eb"; DAN="#dc2626"; ACC="#4b5563"
root.configure(bg=BG)
style.configure("Card.TFrame", background="white", relief="flat")
style.configure("Main.TFrame", background=BG)
style.configure("Title.TLabel", font=("Arial", 16, "bold"), foreground=FG, background="white")
style.configure("Sub.TLabel",   font=("Arial", 11), foreground=ACC, background="white")
style.configure("Body.TLabel",  font=("Arial", 12), foreground=FG, background="white")
style.configure("Info.TLabel",  font=("Arial", 10), foreground=ACC, background="white")
style.configure("Primary.TButton", font=("Arial", 12, "bold"), padding=(12,8))
style.map("Primary.TButton", background=[("active", PRI)])
style.configure("Danger.TButton", font=("Arial", 12, "bold"), padding=(12,8))
style.map("Danger.TButton", background=[("active", DAN)])
style.configure("Thick.Horizontal.TScale", troughcolor="#e5e7eb")
style.layout("Thick.Horizontal.TScale",
             [('Horizontal.Scale.trough', {'sticky': 'we',
               'children':[('Horizontal.Scale.slider', {'side':'left','sticky':''})]})])
style.configure("Line.TSeparator", background="#e5e7eb")
GAUGE_BG = "white"
NEEDLE_COLOR = PRI

# container/card
container = ttk.Frame(root, style="Main.TFrame", padding=12); container.grid(row=0, column=0, sticky="nsew")
root.columnconfigure(0, weight=1); root.rowconfigure(0, weight=1)
main = ttk.Frame(container, style="Card.TFrame", padding=16); main.grid(row=0, column=0, sticky="nsew")
container.columnconfigure(0, weight=1); container.rowconfigure(0, weight=1)

# Header + separator
ttk.Label(main, text="Pannello controllo", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
ttk.Separator(main, style="Line.TSeparator").grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6,10))

# LED
led_var = tk.StringVar(value="LED: OFF")
ttk.Label(main, textvariable=led_var, style="Body.TLabel").grid(row=2, column=0, sticky="w")
ttk.Button(main, text="LED ON",  style="Primary.TButton",
           command=lambda:(led_on(),  say("Hai premuto LED ON"))
           ).grid(row=2, column=1, padx=6, pady=4, sticky="ew")
ttk.Button(main, text="LED OFF", style="Danger.TButton",
           command=lambda:(led_off(), say("Hai premuto LED OFF"))
           ).grid(row=2, column=2, pady=4, sticky="ew")

# Servo slider
servo_var = tk.IntVar(value=90)
ttk.Label(main, text="Servo (0–180°)", style="Sub.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(12,4))
def on_servo_slide(val): set_servo_angle(int(float(val)))
servo_scale = ttk.Scale(main, from_=0, to=180, orient="horizontal",
                        command=on_servo_slide, variable=servo_var,
                        style="Thick.Horizontal.TScale")
servo_scale.grid(row=4, column=0, columnspan=3, sticky="ew")

# Gauge Canvas
GA_W, GA_H = 420, 240
cx, cy, R = GA_W//2, GA_H-24, 175
canvas = tk.Canvas(main, width=GA_W, height=GA_H, highlightthickness=0, bg=GAUGE_BG)
canvas.grid(row=5, column=0, columnspan=3, pady=(10, 6), sticky="ew")

# arco + tacche
canvas.create_arc(cx-R, cy-R, cx+R, cy+R, start=0, extent=180, style="arc", width=3, outline="#888")
for d in range(0, 181, 30):
    ang = math.pi - (d*math.pi/180.0)
    x1 = cx + (R-18)*math.cos(ang); y1 = cy - (R-18)*math.sin(ang)
    x2 = cx + R*math.cos(ang);       y2 = cy - R*math.sin(ang)
    canvas.create_line(x1, y1, x2, y2, width=2)
    lx = cx + (R-36)*math.cos(ang); ly = cy - (R-36)*math.sin(ang)
    canvas.create_text(lx, ly, text=str(d), font=("Arial", 8))
needle = canvas.create_line(cx, cy, cx, cy-R+22, width=4, fill=NEEDLE_COLOR, capstyle="round")
hub    = canvas.create_oval(cx-6, cy-6, cx+6, cy+6, fill=NEEDLE_COLOR, outline="")
def update_gauge(deg: int):
    deg = max(0, min(180, int(deg)))
    ang = math.pi - (deg*math.pi/180.0)
    x = cx + (R-24)*math.cos(ang); y = cy - (R-24)*math.sin(ang)
    canvas.coords(needle, cx, cy, x, y)

# Separator Voce
ttk.Separator(main, style="Line.TSeparator").grid(row=6, column=0, columnspan=3, sticky="ew", pady=(6, 10))
ttk.Label(main, text="Comandi vocali", style="Sub.TLabel").grid(row=7, column=0, sticky="w")

# Badge pulsante fisico
btn_state_var = tk.StringVar(value="Pulsante: RILASCIATO")
btn_state_lbl = tk.Label(main, textvariable=btn_state_var, width=22, relief="groove", bg="white")
btn_state_lbl.grid(row=7, column=1, sticky="w")
BTN_IDLE_BG = btn_state_lbl.cget("bg"); BTN_IDLE_FG = btn_state_lbl.cget("fg")

def flash_button_visual():
    btn_state_var.set("Pulsante: PREMUTO")
    btn_state_lbl.config(bg="#d22", fg="white")
    root.after(200, lambda: (btn_state_var.set("Pulsante: RILASCIATO"),
                             btn_state_lbl.config(bg=BTN_IDLE_BG, fg=BTN_IDLE_FG)))

# Bottone Parla
def do_listen():
    log_var.set("Registro… parla per 4 secondi")
    try:
        wav = record_wav(4)
        log_var.set("Trascrivo…")
        txt = transcribe_wav(wav)
        txt_raw = txt
        txt = snap_to_command(txt)
        append_tts_text(f"Tu: {txt_raw}")



        
        try: os.unlink(wav)
        except Exception: pass
        if not txt:
            log_var.set("Nessun testo riconosciuto"); say("Non ho capito"); return
        append_tts_text(f"Tu: {txt}")
        result = parse_and_execute(txt)
        log_var.set(f"Tu: {txt}\n→ {result}")
    except Exception as e:
        log_var.set(f"Errore: {e}")
        print("listen error:", e, file=sys.stderr)

ttk.Button(main, text="▶ Parla", style="Primary.TButton",
           command=lambda: threading.Thread(target=do_listen, daemon=True).start()
           ).grid(row=7, column=2, sticky="e")

# Textarea TTS + log
log_var = tk.StringVar(value="Pronto. Premi 'Parla' e dai un comando.")
ttk.Label(main, textvariable=log_var, style="Info.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(6,2))
tts_box = scrolledtext.ScrolledText(main, height=6, width=64, state="disabled", font=("Arial", 10))
tts_box.grid(row=9, column=0, columnspan=3, sticky="nsew")

def append_tts_text(text: str):
    def _do():
        tts_box.config(state="normal")
        tts_box.insert("end", f"Assistente: {text}\n")
        tts_box.see("end")
        tts_box.config(state="disabled")
    root.after(0, _do)

def say(text: str):
    append_tts_text(text)
    speak_async(text)

# Layout weights
for c in range(3): main.columnconfigure(c, weight=1)
main.rowconfigure(5, weight=1)   # gauge cresce
main.rowconfigure(9, weight=1)   # textarea cresce

# =========================
# Record/Transcribe helpers
# =========================
def record_wav(seconds=4):
    if not ARECORD: raise RuntimeError("arecord non trovato (sudo apt-get install -y alsa-utils)")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav"); tmp.close()
    #print(tmp.name)
    subprocess.run(ARECORD_ARGS + ["-t","wav", tmp.name], check=False)
    return tmp.name

def transcribe_wav(path: str) -> str:
    try:
        with open(path, "rb") as f:
            tr = client.audio.transcriptions.create(
                model=STT_MODEL,
                file=f,
                language="it",  # <-- forza italiano
                prompt="Trascrivi in italiano comandi brevi tipo: accendi led, spegni led, centra il servo, vai a 90 gradi, ruota a sinistra di 45 gradi.",
            )
        return tr.text.strip() if hasattr(tr, "text") else str(tr)
    except Exception as e:
        print("STT error:", e, file=sys.stderr)
        return ""


# Pulsante fisico -> badge + voce
def on_btn_press():
    root.after(0, flash_button_visual)
    say("Hai premuto il pulsante")
try:
    wp.wiringPiISR(BTN_PIN, getattr(wp, "INT_EDGE_FALLING", 1), on_btn_press)
except Exception as e:
    print("ISR non disponibile, ignoro:", e, file=sys.stderr)

# Avvio: centro e gauge
def init_servo():
    set_servo_angle(90)
root.after(50, init_servo)

# Chiusura pulita
def _on_close():
    try: soft_servo.stop()
    finally: root.destroy()
root.protocol("WM_DELETE_WINDOW", _on_close)

root.mainloop()


