#!/usr/bin/env python3
import os, io, base64, json, datetime, threading, queue, time, tempfile, pathlib, subprocess, shutil
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

import sounddevice as sd
from vosk import Model, KaldiRecognizer
from openai import OpenAI

from picamera2 import Picamera2
import cv2
import numpy as np
print(sd.query_devices())          # elenca dispositivi
print(sd.query_devices(kind="input"))

sd.default.device = (1, 1)  # opzionale: forza il mic della webcam
# ======= CONFIG COMUNE =======
OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)

OPENAI_IMG_MODEL   = "gpt-image-1-mini"       # per generazione immagini (Scheda 1)
OPENAI_VISION_MODEL= "gpt-4o-mini"    # per caption (Schede 2-3)
OPENAI_TTS_MODEL   = "gpt-4o-mini-tts"
VOICE_CHOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer', 'coral', 'verse', 'ballad', 'ash', 'sage', 'marin', 'cedar']


VOSK_MODEL_PATH = os.path.expanduser("~/models/vosk-model-small-it-0.22")
ASR_SAMPLE_RATE = 48000
ASR_BLOCK = 8000

client = OpenAI()

# ======= UTILITY =======
def speak_wav_aplay(text, voice="alloy", device=None, popen_holder=None):
    tmp = pathlib.Path(tempfile.gettempdir()) / f"cap14_tts_{datetime.datetime.now().strftime('%H%M%S')}.wav"
    with client.audio.speech.with_streaming_response.create(
        model=OPENAI_TTS_MODEL, voice=voice, input=text, response_format="wav"
    ) as resp:
        resp.stream_to_file(tmp)
    time.sleep(0.05)
    if not tmp.exists() or tmp.stat().st_size < 1024:
        raise RuntimeError("File audio non valido.")
    if not shutil.which("aplay"):
        raise RuntimeError("aplay non trovato (installa alsa-utils).")
    if popen_holder and popen_holder.get("proc") and popen_holder["proc"].poll() is None:
        popen_holder["proc"].terminate()
    cmd = ["aplay", "-q", str(tmp)]
    if device:
        cmd = ["aplay", "-q", "-D", device, str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if popen_holder is not None:
        popen_holder["proc"] = proc

def bytes_from_item(item):
    b64 = getattr(item, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    url = getattr(item, "url", None)
    if not url:
        raise RuntimeError("Né b64_json né url presenti.")
    import requests
    r = requests.get(url, timeout=60); r.raise_for_status()
    return r.content

def image_to_data_url(pil_img):
    buf = io.BytesIO()
    pil_img.convert("RGB").save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

class CameraManager:
    def __init__(self):
        self.picam2 = None
        self.owner = None  # id della scheda che ha il lock

    def acquire(self, owner_id):
        if self.owner and self.owner != owner_id:
            return False
        if self.picam2 is None:
            from picamera2 import Picamera2
            self.picam2 = Picamera2()
            cfg = self.picam2.create_preview_configuration(main={"size": (960, 540), "format": "RGB888"})
            self.picam2.configure(cfg)
            self.picam2.start()
            time.sleep(0.1)
        self.owner = owner_id
        return True

    def release(self, owner_id):
        if self.owner == owner_id:
            self.owner = None

    def capture_array(self):
        if self.picam2 is None:
            raise RuntimeError("Camera non inizializzata")
        return self.picam2.capture_array()

    def shutdown(self):
        try:
            if self.picam2:
                self.picam2.stop()
        except Exception:
            pass
        self.picam2 = None
        self.owner = None

CAM = CameraManager()


# ======= SCHEDA 1: Disegno a Voce =======
class VoiceDraw(ttk.Frame):
    def __init__(self, master, shared_voice_var, shared_device_var):
        super().__init__(master, padding=10)
        self.voice_var = shared_voice_var
        self.device_var= shared_device_var

        self.asr_model = None
        self.rec = None
        self.audio_q = queue.Queue()
        self.listening = False
        self.stream = None

        self.img_pil = None
        self.img_tk  = None

        self._build_ui()
        self._load_vosk()

    def _build_ui(self):
        top = ttk.Frame(self); top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Prompt:").grid(row=0, column=0, sticky="w")
        self.prompt_var = tk.StringVar(value="un piccolo robot sul tavolo, luce calda, stile cartoon")
        ttk.Entry(top, textvariable=self.prompt_var, width=90).grid(row=0, column=1, columnspan=5, sticky="we", padx=(6,0))

        ttk.Label(top, text="Misura:").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.size_var = tk.StringVar(value="1024x1024")
        ttk.OptionMenu(top, self.size_var, self.size_var.get(), "1024x1024", "1536x1024").grid(row=1, column=1, sticky="w", pady=(8,0))

        self.btn_talk = ttk.Button(top, text="Parla", command=self.on_talk_toggle)
        self.btn_talk.grid(row=1, column=2, padx=6, pady=(8,0))
        self.btn_gen = ttk.Button(top, text="Genera", command=self.on_generate)
        self.btn_gen.grid(row=1, column=3, padx=6, pady=(8,0))
        self.btn_save = ttk.Button(top, text="Salva immagine", command=self.on_save, state=tk.DISABLED)
        self.btn_save.grid(row=1, column=4, padx=6, pady=(8,0))

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._refresh_canvas())

        status = ttk.Frame(self, padding=(10, 6, 10, 10))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(status, textvariable=self.status, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        for i in range(6): top.grid_columnconfigure(i, weight=1)

    def set_status(self, s): self.status.set(s); self.update_idletasks()

    def _load_vosk(self):
        try:
            if not os.path.isdir(VOSK_MODEL_PATH):
                raise RuntimeError(f"Modello Vosk non trovato: {VOSK_MODEL_PATH}")
            self.asr_model = Model(VOSK_MODEL_PATH)
            self.rec = KaldiRecognizer(self.asr_model, ASR_SAMPLE_RATE)
            self.rec.SetWords(True)
            self.set_status("ASR pronto. Premi Parla.")
        except Exception as e:
            messagebox.showerror("Vosk", str(e))
            self.set_status("Errore ASR.")

    def on_talk_toggle(self):
        if not self.listening: self.start_listen()
        else: self.stop_listen()

    def start_listen(self):
        if self.rec is None: return
        self.listening = True
        self.btn_talk.config(text="Ferma")
        self.set_status("Ascolto… parla in italiano.")
        self.audio_q.queue.clear()

        def audio_cb(indata, frames, time_info, status):
            if status: pass
            self.audio_q.put(bytes(indata))

        self.stream = sd.RawInputStream(
            device=0,
            samplerate=ASR_SAMPLE_RATE,
            blocksize=ASR_BLOCK,
            dtype='int16',
            channels=1,
            callback=audio_cb
        )
        self.stream.start()
        threading.Thread(target=self._stt_loop, daemon=True).start()

    def stop_listen(self):
        self.listening = False
        try:
            if self.stream: self.stream.stop(); self.stream.close()
        except: pass
        self.btn_talk.config(text="Parla")
        self.set_status("Ascolto fermato.")

    def _stt_loop(self):
        final = []
        last_partial = ""
        silence_ticks = 0
        while self.listening:
            try:
                data = self.audio_q.get(timeout=0.5)
            except queue.Empty:
                silence_ticks += 1
                if silence_ticks > 6: break
                continue
            if self.rec.AcceptWaveform(data):
                res = json.loads(self.rec.Result())
                if "text" in res and res["text"].strip():
                    final.append(res["text"].strip())
                    self.after(0, lambda t=" ".join(final): self.prompt_var.set(t))
            else:
                part = json.loads(self.rec.PartialResult()).get("partial","")
                if part and part != last_partial:
                    last_partial = part
                    self.after(0, lambda p=part: self.prompt_var.set(p))
        res = json.loads(self.rec.FinalResult())
        if "text" in res and res["text"].strip():
            final.append(res["text"].strip())
        full = " ".join(final).strip()
        if full: self.after(0, lambda t=full: self.prompt_var.set(t))
        self.after(0, self.stop_listen)

    def on_generate(self):
        p = self.prompt_var.get().strip()
        if not p: return messagebox.showwarning("Prompt", "Pronuncia o scrivi un prompt.")
        size = self.size_var.get()
        self.btn_gen.config(state=tk.DISABLED); self.btn_save.config(state=tk.DISABLED)
        self.set_status("Generazione immagine…")
        threading.Thread(target=self._worker_generate, args=(p, size), daemon=True).start()



    def _worker_generate(self, prompt, size):
        try:
            # Assicurati che MODEL sia es. "gpt-image-1" o "gpt-image-1-mini"
            # e che size sia "1024x1024" (o "auto")
            resp = client.images.generate(
                model=OPENAI_IMG_MODEL,
                prompt=prompt,
                size=size,
                n=1
            )

            item = resp.data[0] if resp and getattr(resp, "data", None) else None
            if not item:
                raise RuntimeError("Risposta vuota dall'API Images.")

            # I nuovi modelli restituiscono sempre b64_json di default
            b64 = getattr(item, "b64_json", None)
            if not b64:
                raise RuntimeError("Nessun dato b64_json presente nella risposta.")

            import base64, io, datetime, os
            from PIL import Image

            img_bytes = base64.b64decode(b64)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(OUT_DIR, f"img_{ts}.png")
            img.save(out_path, format="PNG")

            self.after(0, self._on_img_ready, img, out_path)

        except Exception as e:
           #self.after(0, self._on_error, e)
            print(e)

            self.after(0, lambda: (self.btn_gen.config(state=tk.NORMAL),
                                   self.set_status("Errore"),
                                   )) #messagebox.showerror("Generazione", str(e))


    def _on_img_ready(self, pil, path):
        self.img_pil = pil; self._refresh_canvas()
        self.btn_gen.config(state=tk.NORMAL); self.btn_save.config(state=tk.NORMAL)
        self.set_status(f"Immagine salvata: {path}")

    def _refresh_canvas(self):
        self.canvas.delete("all")
        if self.img_pil is None:
            self.canvas.create_text(self.canvas.winfo_width()//2, self.canvas.winfo_height()//2, text="Nessuna immagine", fill="#ccc"); return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = self.img_pil.size
        scale = min(cw/iw, ch/ih); nw, nh = max(1,int(iw*scale)), max(1,int(ih*scale))
        prev = self.img_pil.resize((nw, nh), Image.LANCZOS)
        self.img_tk = ImageTk.PhotoImage(prev)
        self.canvas.create_image(cw//2, ch//2, image=self.img_tk, anchor="center")

    def on_save(self):
        if self.img_pil is None: return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = os.path.join(OUT_DIR, f"voice_draw_save_{ts}.png")
        self.img_pil.save(fp, "PNG")
        self.set_status(f"Salvato: {fp}")

# ======= SCHEDA 2: Occhio Parlante =======
class TalkingEye(ttk.Frame):
    def __init__(self, master, shared_voice_var, shared_device_var):
        super().__init__(master, padding=10)
        self.voice_var = shared_voice_var
        self.device_var= shared_device_var

        self.picam2 = None
        self.preview_running = False
        self.current_pil = None
        self.current_tk  = None

        self.loop_running = False
        self.stop_flag = threading.Event()
        self.aplay_state = {"proc": None}
        self.prev_small_gray = None

        self._build_ui()
        #self._init_camera()

    def _build_ui(self):
        top = ttk.Frame(self); top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Intervallo (s):").grid(row=0, column=0, sticky="e")
        self.sec_var = tk.IntVar(value=6)
        ttk.Spinbox(top, from_=2, to=60, textvariable=self.sec_var, width=4).grid(row=0, column=1, sticky="w", padx=(6,16))

        ttk.Label(top, text="Modalità:").grid(row=0, column=2, sticky="e")
        self.mode_var = tk.StringVar(value="Sempre")
        ttk.OptionMenu(top, self.mode_var, "Sempre", "Sempre", "Solo se movimento").grid(row=0, column=3, sticky="w", padx=(6,16))

        self.thr_var = tk.IntVar(value=12)
        self.thr_scale = ttk.Scale(top, from_=0, to=100, orient="horizontal", variable=self.thr_var)
        self.thr_scale.grid(row=0, column=4, columnspan=3, sticky="we", padx=(6,16))
        self.thr_label = ttk.Label(top, text="Soglia movimento: 12")
        self.thr_label.grid(row=0, column=7, sticky="w")
        self.thr_scale.bind("<B1-Motion>", lambda e: self.thr_label.config(text=f"Soglia movimento: {int(self.thr_var.get())}"))
        self.thr_scale.bind("<ButtonRelease-1>", lambda e: self.thr_label.config(text=f"Soglia movimento: {int(self.thr_var.get())}"))

        self.btn_prev = ttk.Button(top, text="▶︎ Preview", command=self.start_preview)
        self.btn_prev.grid(row=1, column=0, pady=(8,0))
        self.btn_prev_stop = ttk.Button(top, text="⏸ Stop Preview", command=self.stop_preview, state=tk.DISABLED)
        self.btn_prev_stop.grid(row=1, column=1, pady=(8,0))
        self.btn_start = ttk.Button(top, text="Avvia descrizione", command=self.start_loop, state=tk.DISABLED)
        self.btn_start.grid(row=1, column=2, pady=(8,0))
        self.btn_stop  = ttk.Button(top, text="Ferma", command=self.stop_loop, state=tk.DISABLED)
        self.btn_stop.grid(row=1, column=3, pady=(8,0))

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_preview(self.current_pil) if self.current_pil else None)

        bottom = ttk.Frame(self, padding=(10,0,10,10))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bottom, text="Ultima descrizione:").pack(anchor="w")
        self.txt = tk.Text(bottom, height=4, wrap="word"); self.txt.pack(fill=tk.X)

        self.status = tk.StringVar(value="Inizializzazione camera…")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(side=tk.BOTTOM, fill=tk.X)

        for c in range(8): top.grid_columnconfigure(c, weight=1)

    def set_status(self, s): self.status.set(s); self.update_idletasks()

    def _init_camera(self):
        try:
            self.picam2 = Picamera2()
            cfg = self.picam2.create_preview_configuration(main={"size": (960, 540), "format": "RGB888"})
            self.picam2.configure(cfg); self.picam2.start()
            self.btn_prev.config(state=tk.NORMAL)
            self.set_status("Camera pronta. Premi ▶︎ Preview.")
        except Exception as e:
            messagebox.showerror("Camera", str(e)); self.set_status("Errore camera.")
########
    def start_preview(self):
        if self.preview_running: return
        if not CAM.acquire(id(self)):
            messagebox.showwarning("Camera", "La camera è in uso da un'altra scheda.")
            return
        self.preview_running = True
        self.btn_prev.config(state=tk.DISABLED)
        self.btn_prev_stop.config(state=tk.NORMAL)
        self.btn_start.config(state=tk.NORMAL)
        self.set_status("Preview attiva.")
        self._preview_tick()

    def _preview_tick(self):
        if not self.preview_running: return
        try:
            rgb = CAM.capture_array()
            frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            h, w = frame.shape[:2]
            pad = int(min(w, h) * 0.2)
            x1, y1 = pad, pad; x2, y2 = w - pad, h - pad
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.drawMarker(frame, (w//2, h//2), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)
            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.current_pil = pil
            self._draw_preview(pil)
        except Exception:
            pass
        finally:
            self.after(80, self._preview_tick)

    def stop_preview(self):
        if not self.preview_running: return
        self.preview_running = False
        CAM.release(id(self))
        self.btn_prev.config(state=tk.NORMAL)
        self.btn_prev_stop.config(state=tk.DISABLED)

    def destroy(self):
        try:
            self.stop_loop()
        except: pass
        try:
            self.stop_preview()
        except: pass
        super().destroy()


    def _draw_preview(self, pil_img):
        self.canvas.delete("all")
        if pil_img is None: return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 2 or ch <= 2: return
        iw, ih = pil_img.size
        scale = min(cw/iw, ch/ih); nw, nh = max(1,int(iw*scale)), max(1,int(ih*scale))
        prev = pil_img.resize((nw, nh), Image.LANCZOS)
        self.current_tk = ImageTk.PhotoImage(prev)
        cx, cy = cw//2, ch//2
        self.canvas.create_image(cx, cy, image=self.current_tk, anchor="center")

        # griglia 3x3
        x0, y0 = cx - nw//2, cy - nh//2
        x1, y1 = x0 + nw, y0 + nh
        xa, xb = x0 + nw/3, x0 + 2*nw/3
        ya, yb = y0 + nh/3, y0 + 2*nh/3
        for (xA,yA,xB,yB) in [(xa,y0,xa,y1),(xb,y0,xb,y1),(x0,ya,x1,ya),(x0,yb,x1,yb)]:
            self.canvas.create_line(xA,yA,xB,yB, fill="#ffff66", width=1)
        r=4
        for px in (xa,xb):
            for py in (ya,yb):
                self.canvas.create_oval(px-r,py-r,px+r,py+r, outline="#ffff66")


    def start_loop(self):
        if self.loop_running: return
        self.loop_running = True
        self.stop_flag.clear()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        threading.Thread(target=self._loop_worker, daemon=True).start()
        self.set_status("Loop attivo.")

    def stop_loop(self):
        self.stop_flag.set()
        self.loop_running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.set_status("Loop fermato.")

    def _loop_worker(self):
        interval = max(2, int(self.sec_var.get()))
        mode = self.mode_var.get()
        thr = int(self.thr_var.get())
        self.prev_small_gray = None
        while not self.stop_flag.is_set():
            try:
#                rgb = self.picam2.capture_array()
                rgb = CAM.capture_array()

                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

                small = cv2.resize(bgr, (320, 180))
                gray  = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (7,7), 0)

                trigger = True
                if mode != "Sempre":
                    if self.prev_small_gray is not None:
                        diff = cv2.absdiff(gray, self.prev_small_gray)
                        score = float(np.mean(diff))
                        pct = int(min(100, (score / 10.0) * 100.0 / 255.0 * 25))
                        trigger = (pct >= thr)
                        self.after(0, lambda p=pct: self.set_status(f"Motion: {p}% | soglia {thr}%"))
                    else:
                        trigger = False
                    self.prev_small_gray = gray
                else:
                    self.prev_small_gray = gray

                if trigger:
                    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                    data_url = image_to_data_url(pil)
                    prompt = ("Descrivi l'immagine in 1-2 frasi chiare, neutrali, utili per un log video. "
                              "Indica oggetti/azioni rilevanti. Italiano.")
                    resp = client.responses.create(
                        model=OPENAI_VISION_MODEL,
                        input=[{"role":"user","content":[
                            {"type":"input_text","text":prompt},
                            {"type":"input_image","image_url":data_url}
                        ]}]
                    )
                    text = resp.output_text.strip()
                    self.after(0, lambda t=text: (self.txt.delete("1.0","end"), self.txt.insert("1.0", t)))
                    voice = self.voice_var.get().strip() or "alloy"
                    device= self.device_var.get().strip() or None
                    speak_wav_aplay(text, voice=voice, device=device, popen_holder=self.aplay_state)

                for _ in range(interval*10):
                    if self.stop_flag.is_set(): break
                    time.sleep(0.1)
            except Exception as e:
                self.after(0, lambda: (messagebox.showerror("Loop", str(e)), self.stop_loop()))
                break

# ======= SCHEDA 3: Camera + Prompt =======
class CameraPrompt(ttk.Frame):
    def __init__(self, master, shared_voice_var, shared_device_var,
                 set_prompt_callback):
        super().__init__(master, padding=10)
        self.voice_var = shared_voice_var
        self.device_var= shared_device_var
        self.send_prompt = set_prompt_callback

        self.picam2 = None
        self.preview_running = False
        self.current_pil = None
        self.current_tk  = None
        self.aplay_state = {"proc": None}

        self._build_ui()
        #self._init_camera()

    def _build_ui(self):
        top = ttk.Frame(self); top.pack(side=tk.TOP, fill=tk.X)

        self.btn_prev = ttk.Button(top, text="▶︎ Start Preview", command=self.start_preview)
        self.btn_stop = ttk.Button(top, text="⏸ Stop", command=self.stop_preview, state=tk.DISABLED)
        self.btn_snap = ttk.Button(top, text="Scatta", command=self.on_snap, state=tk.DISABLED)
        self.btn_desc = ttk.Button(top, text="Caption", command=self.on_caption, state=tk.DISABLED)
        self.btn_read = ttk.Button(top, text="🔊 Leggi", command=self.on_read, state=tk.DISABLED)
        self.btn_send = ttk.Button(top, text="➡️ Usa caption in Disegno a Voce", command=self.on_send_prompt, state=tk.DISABLED)

        self.btn_prev.grid(row=0, column=0, padx=4)
        self.btn_stop.grid(row=0, column=1, padx=4)
        self.btn_snap.grid(row=0, column=2, padx=8)
        self.btn_desc.grid(row=0, column=3, padx=4)
        self.btn_read.grid(row=0, column=4, padx=4)
        self.btn_send.grid(row=0, column=5, padx=8)

        mid = ttk.Frame(self); mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10,0))
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_preview(self.current_pil) if self.current_pil else None)

        bottom = ttk.Frame(self, padding=(10,0,10,10)); bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bottom, text="Caption:").pack(anchor="w")
        self.txt = tk.Text(bottom, height=3, wrap="word"); self.txt.pack(fill=tk.X)

        self.status = tk.StringVar(value="Inizializzazione camera…")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(side=tk.BOTTOM, fill=tk.X)

    def set_status(self, s): self.status.set(s); self.update_idletasks()

    def _init_camera(self):
        try:
            self.picam2 = Picamera2()
            cfg = self.picam2.create_preview_configuration(main={"size": (960, 540), "format": "RGB888"})
            self.picam2.configure(cfg); self.picam2.start()
            self.btn_prev.config(state=tk.NORMAL)
            self.set_status("Camera pronta.")
        except Exception as e:
            messagebox.showerror("Camera", str(e)); self.set_status("Errore camera.")


##########
    def start_preview(self):
        if self.preview_running: return
        if not CAM.acquire(id(self)):
            messagebox.showwarning("Camera", "La camera è in uso da un'altra scheda.")
            return
        self.preview_running = True
        self.btn_prev.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_snap.config(state=tk.NORMAL)
        self.set_status("Preview attiva.")
        self._tick()

    def _tick(self):
        if not self.preview_running: return
        try:
            rgb = CAM.capture_array()
            frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            h, w = frame.shape[:2]
            pad = int(min(w, h) * 0.2)
            x1, y1 = pad, pad; x2, y2 = w - pad, h - pad
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.drawMarker(frame, (w//2, h//2), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)
            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.current_pil = pil
            self._draw_preview(pil)
        except Exception:
            pass
        finally:
            self.after(80, self._tick)

    def stop_preview(self):
        if not self.preview_running: return
        self.preview_running = False
        CAM.release(id(self))
        self.btn_prev.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)


    def _draw_preview(self, pil_img):
        self.canvas.delete("all")
        if pil_img is None: return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = pil_img.size
        scale = min(cw/iw, ch/ih); nw, nh = max(1,int(iw*scale)), max(1,int(ih*scale))
        prev = pil_img.resize((nw, nh), Image.LANCZOS)
        self.current_tk = ImageTk.PhotoImage(prev)
        cx, cy = cw//2, ch//2
        self.canvas.create_image(cx, cy, image=self.current_tk, anchor="center")
        # griglia 3x3
        x0, y0 = cx - nw//2, cy - nh//2
        x1, y1 = x0 + nw, y0 + nh
        xa, xb = x0 + nw/3, x0 + 2*nw/3
        ya, yb = y0 + nh/3, y0 + 2*nh/3
        for (xA,yA,xB,yB) in [(xa,y0,xa,y1),(xb,y0,xb,y1),(x0,ya,x1,ya),(x0,yb,x1,yb)]:
            self.canvas.create_line(xA,yA,xB,yB, fill="#ffff66", width=1)

    def on_snap(self):
        if self.current_pil is None: return
        self.stop_preview()
        self.btn_desc.config(state=tk.NORMAL); self.set_status("Foto bloccata, pronta per caption.")

    def on_caption(self):
        if self.current_pil is None: return
        self.btn_desc.config(state=tk.DISABLED); self.set_status("Richiesta caption…")
        threading.Thread(target=self._worker_caption, daemon=True).start()

    def _worker_caption(self):
        try:
            data_url = image_to_data_url(self.current_pil)
            prompt = ("Descrivi l'immagine in 1-2 frasi, neutrale e utile per un prompt creativo. Italiano.")
            resp = client.responses.create(
                model=OPENAI_VISION_MODEL,
                input=[{"role":"user","content":[
                    {"type":"input_text","text":prompt},
                    {"type":"input_image","image_url":data_url}
                ]}]
            )
            text = resp.output_text.strip()
            self.after(0, lambda t=text: self._on_caption_ready(t))
        except Exception as e:
            self.after(0, lambda: (messagebox.showerror("Caption", str(e)), self.set_status("Errore caption"), self.btn_desc.config(state=tk.NORMAL)))

    def _on_caption_ready(self, text):
        self.txt.delete("1.0","end"); self.txt.insert("1.0", text)
        self.btn_read.config(state=tk.NORMAL); self.btn_send.config(state=tk.NORMAL)
        self.set_status("Caption pronta.")

    def on_read(self):
        text = self.txt.get("1.0","end").strip()
        if not text: return
        voice = self.voice_var.get().strip() or "alloy"
        device= self.device_var.get().strip() or None
        threading.Thread(target=lambda: speak_wav_aplay(text, voice=voice, device=device, popen_holder={"proc":None}), daemon=True).start()
        self.set_status("TTS avviato (ALSA).")

    def on_send_prompt(self):
        text = self.txt.get("1.0","end").strip()
        if not text: return
        try:
            self.send_prompt(text)
            self.set_status("Caption inviata alla scheda Disegno a Voce.")
        except Exception as e:
            messagebox.showerror("Invio", str(e))

# ======= APP PRINCIPALE =======
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap14 • Assistente visivo: Disegno a Voce + Occhio Parlante + Camera")
        self.geometry("1100x860")
        self.minsize(960, 760)

        # barra controlli condivisi
        bar = ttk.Frame(self, padding=(10,8)); bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="Voce TTS:").pack(side=tk.LEFT)
        self.voice_var = tk.StringVar(value=VOICE_CHOICES[0])
        ttk.OptionMenu(bar, self.voice_var, self.voice_var.get(), *VOICE_CHOICES).pack(side=tk.LEFT, padx=(6,16))
        ttk.Label(bar, text="ALSA device (opz):").pack(side=tk.LEFT)
        self.dev_var = tk.StringVar(value="")
        ttk.Entry(bar, textvariable=self.dev_var, width=12).pack(side=tk.LEFT, padx=(6,16))
        ttk.Label(bar, text="(es. hw:0,0; vuoto=default)").pack(side=tk.LEFT)

        nb = ttk.Notebook(self); nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # callback: imposta prompt in scheda 1
        def set_prompt_in_voice_draw(text):
            self.tab1.prompt_var.set(text)
            nb.select(self.tab1)

        self.tab1 = VoiceDraw(nb, self.voice_var, self.dev_var)
        self.tab2 = TalkingEye(nb, self.voice_var, self.dev_var)
        self.tab3 = CameraPrompt(nb, self.voice_var, self.dev_var, set_prompt_in_voice_draw)

        nb.add(self.tab1, text="Disegno a Voce")
        nb.add(self.tab2, text="Occhio Parlante")
        nb.add(self.tab3, text="Camera + Prompt")

if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("ATTENZIONE: manca OPENAI_API_KEY nell'ambiente.")
    try:
        app = App()
        app.mainloop()
    finally:
        CAM.shutdown()

