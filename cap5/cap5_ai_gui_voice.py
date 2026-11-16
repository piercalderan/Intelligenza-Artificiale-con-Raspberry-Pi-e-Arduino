# cap5_gui_voice.py
import os, re, queue, threading, time, tempfile, subprocess, json
import tkinter as tk
from tkinter import ttk
from dotenv import load_dotenv

# Serial
import serial

# OpenAI
import openai

# Speech (offline)
import sounddevice as sd
from vosk import Model, KaldiRecognizer

# ---------- CONFIG ----------
load_dotenv()
openai.api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
SERIAL_PORT     = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE        = int(os.getenv("BAUDRATE", "9600"))
VOICE_DEFAULT   = "alloy"
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", os.path.expanduser("~/models/vosk-model-small-it-0.22"))
SAMPLE_RATE     = int(os.getenv("SAMPLE_RATE", "16000"))
MIC_DEVICE_ENV  = os.getenv("MIC_DEVICE","1")  # es. "3"
MIC_DEVICE      = int(MIC_DEVICE_ENV) if MIC_DEVICE_ENV and MIC_DEVICE_ENV.isdigit() else None

# ---------- TTS (thread-safe wrapper) ----------
def speak(text: str, voice: str = VOICE_DEFAULT):
    if not text:
        return
    try:
        resp = openai.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=text
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            f.write(resp.read())
            path = f.name
        subprocess.Popen(["mpg123", "-q", path])  # non blocca la UI
    except Exception as e:
        print("TTS error:", e)

def speak_async(text: str, voice: str = VOICE_DEFAULT):
    if text:
        threading.Thread(target=speak, args=(text, voice), daemon=True).start()

# ---------- NLP (prompt “parser” → JSON) ----------
SYSTEM_PROMPT = """You are a command parser. Italian input. Output ONLY valid JSON:
{"commands":[{"type":"led","index":1,"action":"on"}]}
Allowed types: "led","sensor".
For led: index in [1,2,3], action in ["on","off","blink"].
For sensor: action "read".
If multiple intents, include multiple command objects. No extra text."""

def nl_to_commands(phrase: str):
    try:
        r = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role":"system","content":SYSTEM_PROMPT},
                {"role":"user","content":phrase}
            ]
        )
        txt = r.choices[0].message.content
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0) if m else txt)
        return data.get("commands", [])
    except Exception as e:
        print("NLP error:", e)
        return []

# ---------- Serial Worker ----------
class SerialWorker(threading.Thread):
    def __init__(self, port, baud, q_out):
        super().__init__(daemon=True)
        self.port, self.baud = port, baud
        self.q_out = q_out
        self.stop_flag = False
        self.ser = None

    def run(self):
        while not self.stop_flag:
            try:
                if self.ser is None:
                    self.ser = serial.Serial(self.port, self.baud, timeout=0.2)
                    self.q_out.put(("sys", f"Seriale aperta su {self.port}"))
                line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    self.q_out.put(("serial_in", line))
            except Exception as e:
                self.q_out.put(("sys", f"Seriale: {e}. Riprovo..."))
                time.sleep(1)
                self.ser = None

    def send(self, s: str):
        try:
            if self.ser:
                self.ser.write((s + "\n").encode("utf-8"))
        except Exception as e:
            self.q_out.put(("sys", f"Errore invio: {e}"))

# ---------- Speech Worker (Vosk offline) ----------
class SpeechWorker(threading.Thread):
    def __init__(self, q_out, device=None):
        super().__init__(daemon=True)
        self.q_out = q_out
        self.device = device
        self.model = None
        self.rec = None
        self.stream = None
        self.running = False

    def load_model(self):
        if not os.path.isdir(VOSK_MODEL_PATH):
            self.q_out.put(("sys", f"Modello Vosk non trovato: {VOSK_MODEL_PATH}"))
            return False
        try:
            self.model = Model(VOSK_MODEL_PATH)
            self.rec = KaldiRecognizer(self.model, SAMPLE_RATE)
            return True
        except Exception as e:
            self.q_out.put(("sys", f"Errore Vosk: {e}"))
            return False

    def start_recording(self):
        if self.running:
            return
        if self.model is None:
            if not self.load_model():
                return
        try:
            self.stream = sd.InputStream(device=self.device, channels=1,
                                         samplerate=SAMPLE_RATE, dtype='int16')
            self.stream.start()
            self.running = True
            self.q_out.put(("mic_state", "recording"))
        except Exception as e:
            self.q_out.put(("sys", f"Microfono: {e}"))

    def stop_recording(self):
        if not self.running:
            return
        try:
            if self.stream:
                self.stream.stop(); self.stream.close()
        finally:
            self.stream = None
            self.running = False
            self.q_out.put(("mic_state", "idle"))

    def run(self):
        # ciclo di lettura continua quando running=True
        while True:
            if self.running and self.stream:
                try:
                    data, _ = self.stream.read(4000)  # ~250ms @16kHz
                    if len(data) == 0:
                        time.sleep(0.01); continue
                    if self.rec.AcceptWaveform(data.tobytes()):
                        res = json.loads(self.rec.Result())
                        text = (res.get("text") or "").strip()
                        if text:
                            self.q_out.put(("stt_text", text))
                except Exception as e:
                    self.q_out.put(("sys", f"Audio error: {e}"))
                    self.stop_recording()
            else:
                time.sleep(0.05)

# ---------- GUI ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CAP5 - Parla col tuo sensore (Voice)")
        self.geometry("900x600")
        self.configure(bg="#0f1e2c")
        self.queue = queue.Queue()

        # top row
        top = ttk.Frame(self); top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="Comando (italiano):").pack(side="left")
        self.entry = ttk.Entry(top, width=60); self.entry.pack(side="left", padx=6)
        self.voice = tk.StringVar(value=VOICE_DEFAULT)
        ttk.Label(top, text="Voce:").pack(side="left", padx=(16,4))
        self.cmb = ttk.Combobox(top, textvariable=self.voice, width=8,
                                values=["alloy","verse","sage","coral"])
        self.cmb.pack(side="left")
        self.btn_send = ttk.Button(top, text="Invia (NL)", command=self.on_send_nl)
        self.btn_send.pack(side="left", padx=6)
        ttk.Button(top, text="Leggi ultimo log", command=self.on_read_last).pack(side="left", padx=6)

        # mic controls
        micbar = ttk.Frame(self); micbar.pack(fill="x", padx=10, pady=4)
        self.mic_btn = ttk.Button(micbar, text="🎙️ Parla (PTT)", command=self.toggle_mic)
        self.mic_btn.pack(side="left")
        self.mic_state = tk.StringVar(value="Idle")
        ttk.Label(micbar, textvariable=self.mic_state).pack(side="left", padx=10)

        # quick buttons
        bar = ttk.Frame(self); bar.pack(fill="x", padx=10, pady=4)
        for label, cmd in [
            ("LED1 ON", "LED 1 è acceso"),
            ("LED1 OFF", "LED 1 è spento"),
            ("LED2 ON", "LED 2 è acceso"),
            ("LED2 OFF", "LED 2 è spento"),
            ("LED3 BLINK", "LED 3 sta lampeggiando"),
            ("SENSOR?", "SENSOR?")
        ]:
            ttk.Button(bar, text=label, command=lambda c=cmd: self.send_serial(c)).pack(side="left", padx=3)

        # log
        self.log = tk.Text(self, height=22, bg="#0f1e2c", fg="#eaf4ff")
        self.log.pack(fill="both", expand=True, padx=10, pady=8)
        self.last_line = ""

        # style
        style = ttk.Style(); style.theme_use("clam")
        style.configure(".", background="#0f1e2c", foreground="#eaf4ff", fieldbackground="#132434")
        style.configure("TEntry", fieldbackground="#132434")
        style.configure("TCombobox", fieldbackground="#132434")
        style.map("TButton", background=[("!disabled","#5bb1ff")], foreground=[("!disabled","#001a2d")])

        # workers
        self.worker  = SerialWorker(SERIAL_PORT, BAUDRATE, self.queue); self.worker.start()
        self.speech  = SpeechWorker(self.queue, device=MIC_DEVICE); self.speech.start()

        # hotkey PTT: barra spaziatrice tiene premuto/release
        self.bind("<KeyPress-space>", lambda e: self._ptt_on())
        self.bind("<KeyRelease-space>", lambda e: self._ptt_off())

        self.after(100, self.process_queue)

    # queue/UI
    def log_line(self, s):
        self.last_line = s
        self.log.insert("end", s+"\n"); self.log.see("end")

    def process_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "sys":
                    self.log_line("[SYS] " + payload)
                elif kind == "serial_in":
                    self.handle_serial_in(payload)
                elif kind == "stt_text":
                    self.on_stt_text(payload)
                elif kind == "mic_state":
                    self.mic_state.set("Recording..." if payload=="recording" else "Idle")
        except queue.Empty:
            pass
        self.after(80, self.process_queue)

    # mic
    def toggle_mic(self):
        if self.speech.running:
            self._ptt_off()
        else:
            self._ptt_on()

    def _ptt_on(self):
        speak_async("Dimmi pure.", self.voice.get())
        self.speech.start_recording()

    def _ptt_off(self):
        self.speech.stop_recording()

    def on_stt_text(self, text:str):
        text = text.strip()
        if not text: return
        self.log_line("[MIC] " + text)
        threading.Thread(target=self._do_nl_worker, args=(text, self.voice.get()), daemon=True).start()

    # serial/NLP/TTS
    def handle_serial_in(self, line:str):
        self.log_line("[SER] " + line)
        v = self.voice.get()
        if line.startswith("BTN"):
            try:
                idx = int(line.split()[1]); speak_async(f"Hai premuto il pulsante {idx}.", v)
            except: pass
        elif line.startswith("SENSOR t="):
            m = re.search(r"t=([0-9.]+)\s+h=([0-9.]+)", line)
            if m: t,h = m.groups(); speak_async(f"Temperatura {t} gradi, umidità {h} percento.", v)
        elif "ACK LED" in line:
            speak_async(line.replace("ACK ",""), v)

    def send_serial(self, cmd:str):
        self.log_line("[TX] " + cmd); self.worker.send(cmd)

    def on_send_nl(self):
        phrase = self.entry.get().strip()
        if not phrase: return
        self.log_line("[NL] " + phrase)
        self.btn_send.state(["disabled"])
        threading.Thread(target=self._do_nl_worker, args=(phrase, self.voice.get(), True), daemon=True).start()

    def _do_nl_worker(self, phrase, voice, from_button=False):
        try:
            cmds = nl_to_commands(phrase)
            if not cmds:
                speak_async("Non ho capito. Riprova.", voice); return
            for c in cmds:
                if c.get("type") == "led":
                    idx = int(c.get("index",1))
                    act = str(c.get("action","on")).upper()
                    if act in ("ON","OFF","BLINK"):
                        self.worker.send(f"LED {idx} {act}")
                elif c.get("type") == "sensor":
                    self.worker.send("SENSOR?")
            speak_async("Eseguo il comando.", voice)
        finally:
            if from_button:
                # riabilita il bottone sull'UI thread
                self.after(0, lambda: self.btn_send.state(["!disabled"]))

    def on_read_last(self):
        if self.last_line:
            speak_async(self.last_line.replace("[SER] ","").replace("[SYS] ",""), self.voice.get())

    def destroy(self):
        try:
            self.worker.stop_flag = True
            self.speech.stop_recording()
        finally:
            super().destroy()

if __name__ == "__main__":
    app = App()
    app.mainloop()
