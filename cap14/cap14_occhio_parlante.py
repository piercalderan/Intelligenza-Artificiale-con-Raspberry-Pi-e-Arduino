#!/usr/bin/env python3
import os, io, time, base64, datetime, threading, tempfile, pathlib, subprocess, shutil
from openai import OpenAI
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox

from picamera2 import Picamera2
import cv2
import numpy as np

# ==== Config ====
MODEL_VISION = "gpt-4o-mini"      # caption
MODEL_TTS    = "gpt-4o-mini-tts"  # voce
OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)

client = OpenAI()

def speak_wav_aplay(text, voice="alloy", device=None, popen_holder=None):
    speech_path = pathlib.Path(tempfile.gettempdir()) / f"occhio_tts_{datetime.datetime.now().strftime('%H%M%S')}.wav"
    with client.audio.speech.with_streaming_response.create(
        model=MODEL_TTS, voice=voice, input=text, response_format="wav"
    ) as resp:
        resp.stream_to_file(speech_path)
    time.sleep(0.05)
    if not speech_path.exists() or speech_path.stat().st_size < 1024:
        raise RuntimeError("File audio non valido.")
    if not shutil.which("aplay"):
        raise RuntimeError("aplay non trovato (installa 'alsa-utils').")
    if popen_holder and popen_holder.get("proc") and popen_holder["proc"].poll() is None:
        popen_holder["proc"].terminate()
    cmd = ["aplay", "-q", str(speech_path)]
    if device:
        cmd = ["aplay", "-q", "-D", device, str(speech_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if popen_holder is not None:
        popen_holder["proc"] = proc

def image_to_data_url(pil_img):
    buf = io.BytesIO()
    pil_img.convert("RGB").save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + b64

def describe_scene(pil_img):
    data_url = image_to_data_url(pil_img)
    prompt = ("Descrivi l'immagine in 1-2 frasi chiare, neutrali e utili per un log di sorveglianza. "
              "Evita ipotesi su identità; indica oggetti/azioni rilevanti. Italiano.")
    resp = client.responses.create(
        model=MODEL_VISION,
        input=[{"role":"user","content":[
            {"type":"input_text","text":prompt},
            {"type":"input_image","image_url":data_url}
        ]}]
    )
    return resp.output_text.strip()

class OcchioParlante(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap14 • Occhio Parlante (Caption + TTS)")
        self.geometry("1040x780")
        self.minsize(900, 680)

        self.picam2 = None
        self.preview_running = False
        self.current_pil = None
        self.current_tk  = None

        self.timer_running = False
        self.loop_thread = None
        self.stop_flag = threading.Event()
        self.aplay_state = {"proc": None}
        self.prev_small_gray = None  # per motion

        self._build_ui()
        self._init_camera()

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Voce:").grid(row=0, column=0, sticky="w")
        self.voice_var = tk.StringVar(value="alloy")
        ttk.OptionMenu(top, self.voice_var, self.voice_var.get(), "alloy","verse","sage","coral","shimmer").grid(row=0, column=1, sticky="w", padx=(6,16))

        ttk.Label(top, text="ALSA dev (opz):").grid(row=0, column=2, sticky="e")
        self.dev_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.dev_var, width=10).grid(row=0, column=3, sticky="w", padx=(6,16))

        ttk.Label(top, text="Intervallo (s):").grid(row=0, column=4, sticky="e")
        self.sec_var = tk.IntVar(value=6)
        ttk.Spinbox(top, from_=2, to=60, textvariable=self.sec_var, width=4).grid(row=0, column=5, sticky="w", padx=(6,16))

        ttk.Label(top, text="Modalità:").grid(row=0, column=6, sticky="e")
        self.mode_var = tk.StringVar(value="Sempre")
        ttk.OptionMenu(top, self.mode_var, "Sempre", "Sempre", "Solo se movimento").grid(row=0, column=7, sticky="w")

        self.thr_var = tk.IntVar(value=12)  # soglia movimento (0-100)
        self.thr_scale = ttk.Scale(top, from_=0, to=100, orient="horizontal", variable=self.thr_var)
        self.thr_label = ttk.Label(top, text="Soglia movimento: 12")
        self.thr_label.grid(row=1, column=6, columnspan=2, sticky="we", pady=(8,0))
        self.thr_scale.grid(row=1, column=0, columnspan=6, sticky="we", padx=(0,16), pady=(8,0))
        self.thr_scale.bind("<B1-Motion>", lambda e: self._update_thr_label())
        self.thr_scale.bind("<ButtonRelease-1>", lambda e: self._update_thr_label())

        self.btn_start_prev = ttk.Button(top, text="▶︎ Preview", command=self.start_preview)
        self.btn_stop_prev  = ttk.Button(top, text="⏸ Stop Preview", command=self.stop_preview, state=tk.DISABLED)
        self.btn_start_prev.grid(row=2, column=0, pady=(10,0))
        self.btn_stop_prev.grid(row=2, column=1, pady=(10,0), padx=(6,16))

        self.btn_start = ttk.Button(top, text="Avvia descrizione", command=self.start_loop, state=tk.DISABLED)
        self.btn_stop  = ttk.Button(top, text="Ferma", command=self.stop_loop, state=tk.DISABLED)
        self.btn_start.grid(row=2, column=2, pady=(10,0))
        self.btn_stop.grid(row=2, column=3, pady=(10,0), padx=(6,16))

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_preview(self.current_pil) if self.current_pil else None)

        bottom = ttk.Frame(self, padding=(10,0,10,10))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bottom, text="Ultima descrizione:").pack(anchor="w")
        self.txt = tk.Text(bottom, height=4, wrap="word")
        self.txt.pack(fill=tk.X)

        self.status = tk.StringVar(value="Inizializzazione camera…")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(10,0)).pack(side=tk.BOTTOM, fill=tk.X)

        for c in range(8): top.grid_columnconfigure(c, weight=1)

    def _update_thr_label(self):
        self.thr_label.config(text=f"Soglia movimento: {int(self.thr_var.get())}")

    def _init_camera(self):
        try:
            self.picam2 = Picamera2()
            cfg = self.picam2.create_preview_configuration(main={"size": (960, 540), "format": "RGB888"})
            self.picam2.configure(cfg); self.picam2.start()
            self.btn_start_prev.config(state=tk.NORMAL)
            self.set_status("Camera pronta. Premi ▶︎ Preview.")
        except Exception as e:
            messagebox.showerror("Camera", str(e))
            self.set_status("Errore inizializzazione camera.")

    def set_status(self, msg):
        self.status.set(msg); self.update_idletasks()

    # ---------- Preview ----------
    def start_preview(self):
        if self.preview_running or self.picam2 is None: return
        self.preview_running = True
        self.btn_start_prev.config(state=tk.DISABLED)
        self.btn_stop_prev.config(state=tk.NORMAL)
        self.btn_start.config(state=tk.NORMAL)
        self._preview_tick()

    def _preview_tick(self):
        if not self.preview_running: return
        try:
            rgb = self.picam2.capture_array()
            frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            h, w = frame.shape[:2]
            pad = int(min(w, h) * 0.2)
            x1, y1 = pad, pad
            x2, y2 = w - pad, h - pad
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.drawMarker(frame, (w//2, h//2), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)

            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.current_pil = pil
            self._draw_preview(pil)
        except Exception:
            pass
        finally:
            self.after(80, self._preview_tick)

    def _draw_preview(self, pil_img):
        self.canvas.delete("all")
        if pil_img is None: return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 2 or ch <= 2: return
        iw, ih = pil_img.size
        scale = min(cw/iw, ch/ih)
        nw, nh = max(1, int(iw*scale)), max(1, int(ih*scale))
        preview = pil_img.resize((nw, nh), Image.LANCZOS)
        self.current_tk = ImageTk.PhotoImage(preview)
        cx, cy = cw//2, ch//2
        self.canvas.create_image(cx, cy, image=self.current_tk, anchor="center")

    def stop_preview(self):
        self.preview_running = False
        self.btn_start_prev.config(state=tk.NORMAL)
        self.btn_stop_prev.config(state=tk.DISABLED)

    # ---------- Loop descrizione ----------
    def start_loop(self):
        if self.timer_running: return
        self.stop_flag.clear()
        self.timer_running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.set_status("Loop attivo.")
        self.loop_thread = threading.Thread(target=self._loop_worker, daemon=True)
        self.loop_thread.start()

    def stop_loop(self):
        self.stop_flag.set()
        self.timer_running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.set_status("Loop fermato.")

    def _loop_worker(self):
        interval = max(2, int(self.sec_var.get()))
        mode = self.mode_var.get()
        thr = int(self.thr_var.get())  # 0-100 (percentuale motion score)
        self.prev_small_gray = None

        while not self.stop_flag.is_set():
            try:
                # cattura singolo frame e prepara versione small gray per motion
                rgb = self.picam2.capture_array()
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                small = cv2.resize(bgr, (320, 180))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (7,7), 0)

                trigger = True
                score = 0.0
                if mode != "Sempre":
                    if self.prev_small_gray is not None:
                        diff = cv2.absdiff(gray, self.prev_small_gray)
                        score = float(np.mean(diff))  # 0..255 circa
                        # normalizza a 0..100 per UI
                        pct = int(min(100, (score / 10.0) * 100.0 / 255.0 * 25))  # taratura empirica
                        trigger = (pct >= thr)
                        self.after(0, lambda p=pct: self.set_status(f"Motion: {p}%  | soglia {thr}%"))
                    else:
                        trigger = False  # servono due frame per la prima misura
                    self.prev_small_gray = gray
                else:
                    self.prev_small_gray = gray

                if trigger:
                    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    img_path = os.path.join(OUT_DIR, f"occhio_{ts}.jpg")
                    pil.save(img_path, "JPEG", quality=90)

                    caption = describe_scene(pil)
                    self.after(0, lambda c=caption: self._on_caption(c))

                    voice = self.voice_var.get().strip() or "alloy"
                    device = self.dev_var.get().strip() or None
                    speak_wav_aplay(caption, voice=voice, device=device, popen_holder=self.aplay_state)

                # attesa intervallo (interrompibile)
                for _ in range(interval * 10):
                    if self.stop_flag.is_set(): break
                    time.sleep(0.1)
            except Exception as e:
                self.after(0, lambda: (messagebox.showerror("Loop", str(e)), self.stop_loop()))
                break

    def _on_caption(self, text):
        self.txt.delete("1.0","end"); self.txt.insert("1.0", text)
        self.set_status("Descrizione pronta e pronunciata.")

    def destroy(self):
        try:
            self.stop_loop()
        except: pass
        try:
            if self.picam2: self.picam2.stop()
        except: pass
        super().destroy()

if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("ATTENZIONE: manca OPENAI_API_KEY nell'ambiente.")
    app = OcchioParlante()
    app.mainloop()
