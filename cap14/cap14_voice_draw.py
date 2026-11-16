#!/usr/bin/env python3
import os, io, json, base64, datetime, threading, queue, time, pathlib
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

import sounddevice as sd

print(sd.query_devices())          # elenca dispositivi
print(sd.query_devices(kind="input"))

sd.default.device = (1, 1)  # opzionale: forza il mic della webcam

from vosk import Model, KaldiRecognizer
from openai import OpenAI

# === CONFIG ===
MODEL_PATH = os.path.expanduser("~/models/vosk-model-small-it-0.22")
SAMPLE_RATE = 48000 #in base al microfono p.es 48000, 44100, 16000
BLOCK_SIZE = 8000
OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)

IMG_MODEL = "gpt-image-1-mini"     # generazione immagini OpenAI
IMG_SIZES = ["1024x1024", "1536x1024"]  # 1024 su Pi è più lento

client = OpenAI()


class VoiceDrawApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap14 • Disegno a voce (Vosk + GPT Image)")
        self.geometry("900x680")
        self.minsize(820, 600)

        # stato
        self.model = None
        self.rec = None
        self.audio_q = queue.Queue()
        self.listening = False
        self.img_pil = None
        self.img_tk = None

        self._build_ui()
        self._load_vosk()

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Prompt:").grid(row=0, column=0, sticky="w")
        self.prompt_var = tk.StringVar(value="un piccolo robot sul tavolo, luce calda, stile cartoon")
        self.ent_prompt = ttk.Entry(top, textvariable=self.prompt_var, width=90)
        self.ent_prompt.grid(row=0, column=1, columnspan=5, sticky="we", padx=(6,0))

        ttk.Label(top, text="Misura:").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.size_var = tk.StringVar(value=IMG_SIZES[1])
        self.opt_size = ttk.OptionMenu(top, self.size_var, self.size_var.get(), *IMG_SIZES)
        self.opt_size.grid(row=1, column=1, sticky="w", pady=(8,0))

        self.btn_talk = ttk.Button(top, text="Parla", command=self.on_talk_toggle)
        self.btn_talk.grid(row=1, column=2, padx=6, pady=(8,0))
        self.btn_gen = ttk.Button(top, text="Genera immagine", command=self.on_generate)
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
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        for i in range(6):
            top.grid_columnconfigure(i, weight=1)

    def set_status(self, s):
        self.status_var.set(s)
        self.update_idletasks()

    # ---------- Vosk ----------
    def _load_vosk(self):
        try:
            if not os.path.isdir(MODEL_PATH):
                raise RuntimeError(f"Modello Vosk non trovato in {MODEL_PATH}")
            self.model = Model(MODEL_PATH)
            self.rec = KaldiRecognizer(self.model, SAMPLE_RATE)
            self.rec.SetWords(True)
            self.set_status("Vosk pronto. Premi Parla e inizia.")
        except Exception as e:
            messagebox.showerror("Vosk", str(e))
            self.set_status("Errore caricamento modello Vosk.")

    def on_talk_toggle(self):
        if not self.listening:
            self.start_listen()
        else:
            self.stop_listen()

    def start_listen(self):
        if self.rec is None:
            return
        self.listening = True
        self.btn_talk.config(text="Ferma")
        self.set_status("Ascolto in corso… parla chiaramente in italiano.")
        self.audio_q.queue.clear()

        def audio_cb(indata, frames, time_info, status):
            if status:  # buffer over/underrun
                pass
            self.audio_q.put(bytes(indata))

        self.stream = sd.RawInputStream(
            device=0,
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            dtype='int16',
            channels=1,
            callback=audio_cb   # <-- FIX qui (uguale, non chiamata)
        )

        
        self.stream.start()
        threading.Thread(target=self._stt_loop, daemon=True).start()

    def stop_listen(self):
        self.listening = False
        try:
            self.stream.stop(); self.stream.close()
        except Exception:
            pass
        self.btn_talk.config(text="Parla")
        self.set_status("Ascolto fermato.")

    def _stt_loop(self):
        last_partial = ""
        final_text = []
        silence_ticks = 0
        while self.listening:
            try:
                data = self.audio_q.get(timeout=0.5)
            except queue.Empty:
                silence_ticks += 1
                if silence_ticks > 6:
                    break
                continue

            if self.rec.AcceptWaveform(data):
                res = json.loads(self.rec.Result())
                if "text" in res and res["text"].strip():
                    final_text.append(res["text"].strip())
                    self.after(0, lambda t=" ".join(final_text): self.prompt_var.set(t))
            else:
                part = json.loads(self.rec.PartialResult()).get("partial", "")
                if part and part != last_partial:
                    last_partial = part
                    self.after(0, lambda p=part: self.prompt_var.set(p))

        # Fine ascolto -> prendi anche il FinalResult
        res = json.loads(self.rec.FinalResult())
        print(res)
        if "text" in res and res["text"].strip():
            final_text.append(res["text"].strip())
        full = " ".join(final_text).strip()
        if full:
            self.after(0, lambda t=full: self.prompt_var.set(t))
        self.after(0, self.stop_listen)

    # ---------- Generazione immagine ----------
    def on_generate(self):
        prompt = self.prompt_var.get().strip()
        if not prompt:
            messagebox.showwarning("Prompt vuoto", "Pronuncia o scrivi un prompt prima.")
            return
        size = self.size_var.get()
        self.btn_gen.config(state=tk.DISABLED)
        self.btn_save.config(state=tk.DISABLED)
        self.set_status("Generazione immagine…")
        threading.Thread(target=self._worker_generate, args=(prompt, size), daemon=True).start()


    def _worker_generate(self, prompt, size):
        try:
            # Assicurati che MODEL sia "gpt-image-1-mini"
            # e che size sia "1024x1024" (o "auto")
            resp = client.images.generate(
                model=IMG_MODEL,
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

            self.after(0, self._on_image_ready, img, out_path)

        except Exception as e:
            self.after(0, self._on_error, e)


    def _on_image_ready(self, pil_img, path):
        self.img_pil = pil_img
        self._refresh_canvas()
        self.btn_gen.config(state=tk.NORMAL)
        self.btn_save.config(state=tk.NORMAL)
        self.set_status(f"Immagine generata e salvata: {path}")

    def _on_error(self, err):
        self.btn_gen.config(state=tk.NORMAL)
        self.set_status("Errore."); messagebox.showerror("Errore", str(err))

    def _refresh_canvas(self):
        self.canvas.delete("all")
        if self.img_pil is None:
            self.canvas.create_text(self.canvas.winfo_width()//2, self.canvas.winfo_height()//2,
                                    text="Nessuna immagine", fill="#cccccc")
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = self.img_pil.size
        if iw == 0 or ih == 0 or cw <= 2 or ch <= 2:
            return
        scale = min(cw/iw, ch/ih)
        nw, nh = max(1, int(iw*scale)), max(1, int(ih*scale))
        preview = self.img_pil.resize((nw, nh), Image.LANCZOS)
        self.img_tk = ImageTk.PhotoImage(preview)
        self.canvas.create_image(cw//2, ch//2, image=self.img_tk, anchor="center")

    def on_save(self):
        if self.img_pil is None: return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"voice_draw_save_{ts}.png")
        self.img_pil.save(out_path, "PNG")
        self.set_status(f"Salvato: {out_path}")

if __name__ == "__main__":
    if not os.path.isdir(MODEL_PATH):
        print("ATTENZIONE: scarica Vosk IT in", MODEL_PATH)
    if not os.environ.get("OPENAI_API_KEY"):
        print("ATTENZIONE: manca OPENAI_API_KEY nell'ambiente.")
    # test audio rapido (facoltativo):
    sd.query_devices()
    app = VoiceDrawApp()
    app.mainloop()
