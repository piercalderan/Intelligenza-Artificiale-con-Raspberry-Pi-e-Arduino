# cap5_gui_async.py
import os, re, queue, threading, time, tempfile, subprocess, json
import tkinter as tk
from tkinter import ttk
from dotenv import load_dotenv
import serial
import openai

load_dotenv()
openai.api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE = int(os.getenv("BAUDRATE", "9600"))

VOICE_DEFAULT = "alloy"

# ------------------- TTS -------------------
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
        subprocess.run(["mpg123", "-q", path], check=False)
    except Exception as e:
        print("TTS error:", e)

def speak_async(text: str, voice: str = VOICE_DEFAULT):
    if text:
        threading.Thread(target=speak, args=(text, voice), daemon=True).start()

# ------------------- NLP Parser -------------------
SYSTEM_PROMPT = """
Sei un parser di comandi. Input in italiano. Output SOLO JSON valido:
{"commands":[{"type":"led","index":1,"action":"on"}]}
Tipi ammessi: "led","sensor".
Per led: index in [1,2,3], action in ["on","off","blink"].
Per sensor: action "read".
In caso di intenti multipli, includere più oggetti comando.
Nessun testo aggiuntivo."""

def nl_to_commands(phrase: str):
    try:
        r = openai.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            language="it",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": phrase}
            ]
        )
        txt = r.choices[0].message.content
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0) if m else txt)
        return data.get("commands", [])
    except Exception as e:
        print("NL parse error:", e)
        return []

# ------------------- Serial Worker -------------------
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

# ------------------- GUI -------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CAP5 - Parla col tuo sensore (Async)")
        self.geometry("1040x540")
        self.configure(bg="#0f1e2c")
        self.queue = queue.Queue()

        # --- top ---
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="Comando (italiano):").pack(side="left")
        self.entry = ttk.Entry(top, width=60)
        self.entry.pack(side="left", padx=6)
        self.voice = tk.StringVar(value=VOICE_DEFAULT)
        ttk.Label(top, text="Voce:").pack(side="left", padx=(16, 4))
        self.cmb = ttk.Combobox(top, textvariable=self.voice, width=8,
                                values=["alloy", "verse", "sage", "coral"])
        self.cmb.pack(side="left")
        ttk.Button(top, text="Invia (NL)", command=self.on_send_nl).pack(side="left", padx=6)
        ttk.Button(top, text="Leggi ultimo log", command=self.on_read_last).pack(side="left")

        # --- quick bar ---
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=4)
        for label, cmd in [
            ("LED1 ON", "LED 1 è acceso"),
            ("LED1 OFF", "LED 1 è spento"),
            ("LED2 ON", "LED 2 è acceso"),
            ("LED2 OFF", "LED 2 è spento"),
            ("LED3 BLINK", "LED 3 sta lampeggiando"),
            ("SENSOR?", "SENSOR?")
        ]:
            ttk.Button(bar, text=label, command=lambda c=cmd: self.send_serial(c)).pack(side="left", padx=3)

        # --- log ---
        self.log = tk.Text(self, height=22, bg="#0f1e2c", fg="#eaf4ff")
        self.log.pack(fill="both", expand=True, padx=10, pady=8)
        self.last_line = ""

        # --- style ---
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#0f1e2c", foreground="#eaf4ff", fieldbackground="#132434")
        style.configure("TEntry", fieldbackground="#132434")
        style.configure("TCombobox", fieldbackground="#132434")
        style.map("TButton", background=[("!disabled", "#5bb1ff")],
                  foreground=[("!disabled", "#001a2d")])

        # --- serial worker ---
        self.worker = SerialWorker(SERIAL_PORT, BAUDRATE, self.queue)
        self.worker.start()
        self.after(100, self.process_queue)

    # ---------- logging ----------
    def log_line(self, s):
        self.last_line = s
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def process_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "sys":
                    self.log_line("[SYS] " + payload)
                    speak_async(payload, self.voice.get())
                elif kind == "serial_in":
                    self.handle_serial_in(payload)
        except queue.Empty:
            pass
        self.after(100, self.process_queue)

    def handle_serial_in(self, line: str):
        self.log_line("[SER] " + line)
        v = self.voice.get()
        if line.startswith("BTN"):
            try:
                idx = int(line.split()[1])
                speak_async(f"Hai premuto il pulsante {idx}.", v)
            except:
                pass
        elif line.startswith("SENSOR t="):
            m = re.search(r"t=([0-9.]+)\s+h=([0-9.]+)", line)
            if m:
                t, h = m.groups()
                speak_async(f"Temperatura {t} gradi, umidità {h} percento.", v)
        elif "ACK LED" in line:
            speak_async(line.replace("ACK ", ""), v)

    # ---------- serial send ----------
    def send_serial(self, cmd: str):
        self.log_line("[TX] " + cmd)
        self.worker.send(cmd)

    # ---------- NLP ----------
    def on_send_nl(self):
        phrase = self.entry.get().strip()
        if not phrase:
            return
        self.log_line("[NL] " + phrase)
        v = self.voice.get()
        threading.Thread(target=self._do_nl_worker, args=(phrase, v), daemon=True).start()

    def _do_nl_worker(self, phrase, voice):
        cmds = nl_to_commands(phrase)
        if not cmds:
            self.queue.put(("sys", "Non ho capito. Riprova."))
            return
        for c in cmds:
            if c.get("type") == "led":
                idx = int(c.get("index", 1))
                act = str(c.get("action", "on")).upper()
                if act in ("ON", "OFF", "BLINK"):
                    self.worker.send(f"LED {idx} {act}")
            elif c.get("type") == "sensor":
                self.worker.send("SENSOR?")
        speak_async("Eseguo il comando.", voice)

    # ---------- read last ----------
    def on_read_last(self):
        if self.last_line:
            speak_async(self.last_line.replace("[SER] ", "").replace("[SYS] ", ""), self.voice.get())

    def destroy(self):
        try:
            self.worker.stop_flag = True
        finally:
            super().destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
