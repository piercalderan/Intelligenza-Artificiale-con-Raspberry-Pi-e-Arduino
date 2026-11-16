#!/usr/bin/env python3
import os, io, base64, datetime, threading
import tempfile, pathlib, subprocess, shutil, time
from openai import OpenAI
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox
from picamera2 import Picamera2
import cv2
import numpy as np

# ==== Config comuni ====
OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)

MODEL_IMG    = "gpt-image-1-mini"        # generazione
MODEL_VISION = "gpt-4o-mini"     # caption
MODEL_TTS    = "gpt-4o-mini-tts" # TTS
SIZES        = ["1024x1024", "1536x1024"]
BATCH_N      = 4
VOICE_CHOICES = ["alloy", "verse", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"]

client = OpenAI()  # richiede OPENAI_API_KEY nell'ambiente

def _draw_preview(self, pil_img):
    self.canvas.delete("all")
    if pil_img is None:
        return
    cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
    if cw <= 2 or ch <= 2:
        return

    iw, ih = pil_img.size
    scale = min(cw/iw, ch/ih)
    nw, nh = max(1, int(iw*scale)), max(1, int(ih*scale))
    preview = pil_img.resize((nw, nh), Image.LANCZOS)
    self.current_image_tk = ImageTk.PhotoImage(preview)

    cx, cy = cw // 2, ch // 2
    x0, y0 = cx - nw // 2, cy - nh // 2
    x1, y1 = x0 + nw, y0 + nh

    self.canvas.create_image(cx, cy, image=self.current_image_tk, anchor="center")
    self.canvas.bind("<Configure>", lambda e: self._draw_preview(self.current_image_pil) if self.current_image_pil else None)


    if self.show_grid_var.get():
        x_a = x0 + nw / 3.0
        x_b = x0 + 2.0 * nw / 3.0
        y_a = y0 + nh / 3.0
        y_b = y0 + 2.0 * nh / 3.0
        self.canvas.create_line(x_a, y0, x_a, y1, fill="#ffff66", width=1)
        self.canvas.create_line(x_b, y0, x_b, y1, fill="#ffff66", width=1)
        self.canvas.create_line(x0, y_a, x1, y_a, fill="#ffff66", width=1)
        self.canvas.create_line(x0, y_b, x1, y_b, fill="#ffff66", width=1)
        r = 4
        for px in (x_a, x_b):
            for py in (y_a, y_b):
                self.canvas.create_oval(px - r, py - r, px + r, py + r, outline="#ffff66")


# ==== util comuni ====
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

def speak_wav_aplay(text, voice="alloy", aplay_device=None, popen_holder=None):
    speech_path = pathlib.Path(tempfile.gettempdir()) / f"cap13_tts_{datetime.datetime.now().strftime('%H%M%S')}.wav"
    with client.audio.speech.with_streaming_response.create(
        model=MODEL_TTS, voice=voice, input=text, response_format="wav"
    ) as resp:
        resp.stream_to_file(speech_path)
    time.sleep(0.05)
    if not speech_path.exists() or speech_path.stat().st_size < 1024:
        raise RuntimeError("File audio non valido.")
    if not shutil.which("aplay"):
        raise RuntimeError("aplay non trovato (installa 'alsa-utils').")
    # stop vecchio aplay
    if popen_holder and popen_holder.get("proc") and popen_holder["proc"].poll() is None:
        popen_holder["proc"].terminate()
    cmd = ["aplay", "-q", str(speech_path)]
    if aplay_device:
        cmd = ["aplay", "-q", "-D", aplay_device, str(speech_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if popen_holder is not None:
        popen_holder["proc"] = proc

# ==========================
# Scheda 1: Generatore (DALL·E + Galleria + TTS + Miniature)
# ==========================
class GeneratorFrame(ttk.Frame):
    def __init__(self, master, voice_var, aplay_device_var):
        super().__init__(master, padding=10)
        self.voice_var = voice_var
        self.aplay_device_var = aplay_device_var

        self.current_image_pil = None
        self.current_image_tk  = None
        self.generating = False
        self.gallery_images = []
        self.gallery_paths  = []
        self.gallery_index  = 0
        self.aplay_state = {"proc": None}

        # strip miniature
        self.thumb_size = (120, 120)
        self.thumb_imgs_tk = []   # ImageTk per le miniature
        self.thumb_btns = []      # riferimenti pulsanti

        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Prompt:").grid(row=0, column=0, sticky="w")
        self.prompt_var = tk.StringVar(value="un piccolo robot sul tavolo, luce calda, stile cartoon")
        ttk.Entry(top, textvariable=self.prompt_var, width=90).grid(row=0, column=1, columnspan=8, sticky="we", padx=(6,0))

        ttk.Label(top, text="Misura:").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.size_var = tk.StringVar(value=SIZES[1])
        ttk.OptionMenu(top, self.size_var, self.size_var.get(), *SIZES).grid(row=1, column=1, sticky="w", pady=(8,0))

        ttk.Button(top, text="Genera (x4)", command=self.on_generate).grid(row=1, column=2, padx=6, pady=(8,0))
        ttk.Button(top, text="Rigenera (x4)", command=self.on_regenerate).grid(row=1, column=3, padx=6, pady=(8,0))

        self.btn_prev = ttk.Button(top, text="◀︎", width=3, command=self.on_prev, state=tk.DISABLED)
        self.btn_prev.grid(row=1, column=5, padx=(20,4), pady=(8,0))
        self.btn_next = ttk.Button(top, text="▶︎", width=3, command=self.on_next, state=tk.DISABLED)
        self.btn_next.grid(row=1, column=6, padx=4, pady=(8,0))

        self.btn_read_prompt = ttk.Button(top, text="🔊 Leggi Prompt", command=self.on_read_prompt)
        self.btn_read_prompt.grid(row=1, column=7, padx=6, pady=(8,0))

        mid = ttk.Frame(self)
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10,0))
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._refresh_canvas())

        # Striscia miniature
        thumbs = ttk.Frame(self)
        thumbs.pack(side=tk.TOP, fill=tk.X, pady=(8,0))
        self.thumbs_frame = thumbs

        bottom = ttk.Frame(self)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(8,0))
        self.desc_txt = tk.Text(bottom, height=3, wrap="word")
        self.desc_txt.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,10))
        ttk.Button(bottom, text="🔊 Leggi Descrizione", command=self.on_read_desc).pack(side=tk.LEFT)
        self.btn_stop_audio = ttk.Button(bottom, text="⏹️ Stop Audio", command=self.on_stop_audio, state=tk.DISABLED)
        self.btn_stop_audio.pack(side=tk.LEFT, padx=6)
        self.btn_save = ttk.Button(bottom, text="Salva immagine corrente", command=self.on_save_current, state=tk.DISABLED)
        self.btn_save.pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(side=tk.BOTTOM, fill=tk.X, pady=(6,0))

        for i in range(9):
            top.grid_columnconfigure(i, weight=1)

    def set_status(self, text):
        self.status_var.set(text)
        self.update_idletasks()

    # --- azioni immagini ---
    def on_generate(self):
        if self.generating: return
        p = self.prompt_var.get().strip()
        if not p: return messagebox.showwarning("Prompt mancante", "Scrivi un prompt 😊")
        self._run_async(self._worker_generate, p, self.size_var.get(), BATCH_N)

    def on_regenerate(self):
        if self.generating: return
        self.on_generate()

    def on_variations(self):
        if self.generating or self.current_image_pil is None: return
        return

        #self._run_async(self._worker_variations, self.current_image_pil, self.size_var.get(), BATCH_N)

    def on_prev(self):
        if not self.gallery_images: return
        self.gallery_index = (self.gallery_index - 1) % len(self.gallery_images)
        self._set_current_from_gallery()
        self.set_status(f"Galleria {self.gallery_index+1}/{len(self.gallery_images)}")

    def on_next(self):
        if not self.gallery_images: return
        self.gallery_index = (self.gallery_index + 1) % len(self.gallery_images)
        self._set_current_from_gallery()
        self.set_status(f"Galleria {self.gallery_index+1}/{len(self.gallery_images)}")

    def on_save_current(self):
        if self.current_image_pil is None: return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"saved_current_{ts}.png")
        self.current_image_pil.save(out_path, "PNG")
        self.set_status(f"Salvato: {out_path}")

    # --- TTS ---
    def on_read_prompt(self):
        text = self.prompt_var.get().strip()
        if not text: return
        self._start_tts(text)

    def on_read_desc(self):
        text = self.desc_txt.get("1.0", "end").strip()
        if not text: return
        self._start_tts(text)

    def _start_tts(self, text):
        voice = self.voice_var.get().strip() or "alloy"
        device = self.aplay_device_var.get().strip() or None
        self.btn_stop_audio.config(state=tk.NORMAL)
        self.set_status("Sintesi vocale…")
        threading.Thread(target=self._worker_tts, args=(text, voice, device), daemon=True).start()

    def on_stop_audio(self):
        try:
            if self.aplay_state.get("proc") and self.aplay_state["proc"].poll() is None:
                self.aplay_state["proc"].terminate()
                self.set_status("Audio interrotto.")
        except Exception as e:
            self.set_status(f"Stop audio: {e}")
        finally:
            self.btn_stop_audio.config(state=tk.DISABLED)

    # --- async helpers ---
    def _run_async(self, target, *args):
        self.generating = True
##        for b in [self.btn_vari, self.btn_prev, self.btn_next, self.btn_save]:
##            b.config(state=tk.DISABLED)
        self.set_status("Elaborazione in corso…")
        threading.Thread(target=target, args=args, daemon=True).start()

    def _worker_generate(self, prompt, size, n):
        try:
            resp = client.images.generate(model=MODEL_IMG, prompt=prompt, size=size, response_format="b64_json", n=max(1,int(n)))
            items = list(getattr(resp, "data", []))
            if not items: raise RuntimeError("Risposta vuota.")
            images, paths = [], []
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            for idx, it in enumerate(items, 1):
                img = Image.open(io.BytesIO(bytes_from_item(it))).convert("RGB")
                fp = os.path.join(OUT_DIR, f"dalle_gen_{ts}_{idx}.png"); img.save(fp, "PNG")
                images.append(img); paths.append(fp)
            self.after(0, self._on_gallery_ready, images, paths, "Generate")
        except Exception as e:
            self.after(0, self._on_error, e)

    def _worker_variations(self, base_pil, size, n):
        try:
            buf = io.BytesIO(); base_pil.save(buf, format="PNG")
            resp = client.images.variations(model=MODEL_IMG, image=buf.getvalue(), size=size, response_format="b64_json", n=max(1,int(n)))
            items = list(getattr(resp, "data", []))
            if not items: raise RuntimeError("Risposta vuota (variations).")
            images, paths = [], []
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            for idx, it in enumerate(items, 1):
                img = Image.open(io.BytesIO(bytes_from_item(it))).convert("RGB")
                fp = os.path.join(OUT_DIR, f"dalle_var_{ts}_{idx}.png"); img.save(fp, "PNG")
                images.append(img); paths.append(fp)
            self.after(0, self._on_gallery_ready, images, paths, "Variations")
        except Exception as e:
            self.after(0, self._on_error, e)

    def _worker_tts(self, text, voice, device):
        try:
            speak_wav_aplay(text, voice=voice, aplay_device=device, popen_holder=self.aplay_state)
            self.after(0, lambda: self.set_status("Riproduzione audio avviata (ALSA)."))
        except Exception as e:
            self.after(0, self._on_error, e)

    def _on_gallery_ready(self, images, paths, label):
        self.gallery_images, self.gallery_paths, self.gallery_index = images[:], paths[:], 0
        self.current_image_pil = images[0]; self._refresh_canvas()
        self._render_thumbnails()
        self.generating = False
        #self.btn_vari.config(state=tk.NORMAL)
        self.btn_prev.config(state=tk.NORMAL if len(images)>1 else tk.DISABLED)
        self.btn_next.config(state=tk.NORMAL if len(images)>1 else tk.DISABLED)
        self.btn_save.config(state=tk.NORMAL)
        base = os.path.basename(paths[0]) if paths else ""
        self.set_status(f"{label}: salvate {len(paths)} immagini. Mostrata 1/{len(paths)} – es. {base}")

    def _on_error(self, err):
        self.generating = False
        #self.btn_vari.config(state=tk.NORMAL if self.current_image_pil is not None else tk.DISABLED)
        self.set_status("Errore."); messagebox.showerror("Errore", str(err))

    def _set_current_from_gallery(self):
        if not self.gallery_images:
            self.current_image_pil = None; self._refresh_canvas(); self.btn_save.config(state=tk.DISABLED); return
        self.current_image_pil = self.gallery_images[self.gallery_index]
        self._refresh_canvas(); self.btn_save.config(state=tk.NORMAL)
        self._highlight_selected_thumb()

    def _refresh_canvas(self):
        self.canvas.delete("all")
        if self.current_image_pil is None:
            self.canvas.create_text(self.canvas.winfo_width()//2, self.canvas.winfo_height()//2, text="Nessuna immagine", fill="#ccc"); return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = self.current_image_pil.size
        if iw==0 or ih==0 or cw<=2 or ch<=2: return
        scale = min(cw/iw, ch/ih); nw, nh = max(1,int(iw*scale)), max(1,int(ih*scale))
        preview = self.current_image_pil.resize((nw, nh), Image.LANCZOS)
        self.current_image_tk = ImageTk.PhotoImage(preview)
        self.canvas.create_image(cw//2, ch//2, image=self.current_image_tk, anchor="center")

    # ---- thumbnails ----
    def _render_thumbnails(self):
        for w in self.thumbs_frame.winfo_children():
            w.destroy()
        self.thumb_imgs_tk.clear(); self.thumb_btns.clear()
        for idx, img in enumerate(self.gallery_images):
            th = img.copy()
            th.thumbnail(self.thumb_size, Image.LANCZOS)
            th_tk = ImageTk.PhotoImage(th)
            self.thumb_imgs_tk.append(th_tk)
            btn = ttk.Button(self.thumbs_frame, image=th_tk, command=lambda i=idx: self._on_thumb_click(i))
            btn.grid(row=0, column=idx, padx=6)
            self.thumb_btns.append(btn)
        self._highlight_selected_thumb()

    def _on_thumb_click(self, index):
        self.gallery_index = index
        self._set_current_from_gallery()
        self.set_status(f"Miniatura {index+1}/{len(self.gallery_images)}")

    def _highlight_selected_thumb(self):
        for i, btn in enumerate(self.thumb_btns):
            style = "TButton"
            if i == self.gallery_index:
                style = "Selected.TButton"
            btn.configure(style=style)

# ==========================
# Scheda 2: Camera (preview, bounding box, caption, TTS, usa caption come prompt)
# ==========================
class CameraFrame(ttk.Frame):
    def __init__(self, master, voice_var, aplay_device_var, on_use_caption_cb):
        super().__init__(master, padding=10)
        self.voice_var = voice_var
        self.aplay_device_var = aplay_device_var
        self.on_use_caption_cb = on_use_caption_cb  # callback verso Generatore

        self.picam2 = None
        self.preview_running = False
        self.preview_interval_ms = 80  # ~12.5 FPS
        self.current_image_pil = None
        self.current_image_tk  = None
        self.aplay_state = {"proc": None}

        self._build_ui()
        self._init_camera()

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X)

        self.btn_start_prev = ttk.Button(top, text="▶︎ Start Preview", command=self.start_preview)
        self.btn_stop_prev  = ttk.Button(top, text="⏸ Stop Preview",  command=self.stop_preview, state=tk.DISABLED)
        self.btn_snap       = ttk.Button(top, text="📷 Scatta",       command=self.on_snap, state=tk.DISABLED)
        self.btn_desc       = ttk.Button(top, text="📝 Descrivi",     command=self.on_describe, state=tk.DISABLED)
        self.btn_read       = ttk.Button(top, text="🔊 Leggi",        command=self.on_read, state=tk.DISABLED)
        self.btn_save       = ttk.Button(top, text="💾 Salva",        command=self.on_save, state=tk.DISABLED)
        self.btn_use_prompt = ttk.Button(top, text="➡️ Usa caption come prompt", command=self.on_use_caption, state=tk.DISABLED)
        self.show_grid_var = tk.BooleanVar(value=True)
        self.chk_grid = ttk.Checkbutton(top, text="Griglia 3×3 (terzi)", variable=self.show_grid_var, command=lambda: self._draw_preview(self.current_image_pil) if self.current_image_pil else None)
        self.chk_grid.grid(row=0, column=7, padx=8)

        self.btn_start_prev.grid(row=0, column=0, padx=4)
        self.btn_stop_prev.grid(row=0, column=1, padx=4)
        self.btn_snap.grid(row=0, column=2, padx=8)
        self.btn_desc.grid(row=0, column=3, padx=4)
        self.btn_read.grid(row=0, column=4, padx=4)
        self.btn_save.grid(row=0, column=5, padx=8)
        self.btn_use_prompt.grid(row=0, column=6, padx=8)

        mid = ttk.Frame(self)
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10,0))
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(self)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(8,0))
        ttk.Label(bottom, text="Descrizione:").pack(anchor="w")
        self.txt = tk.Text(bottom, height=3, wrap="word")
        self.txt.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="Inizializzazione camera…")
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(side=tk.BOTTOM, fill=tk.X, pady=(6,0))

    def set_status(self, text):
        self.status_var.set(text)
        self.update_idletasks()

    def _init_camera(self):
        try:
            self.picam2 = Picamera2()
            cfg = self.picam2.create_preview_configuration(main={"size": (960, 540), "format": "RGB888"})
            self.picam2.configure(cfg); self.picam2.start()
            self.btn_start_prev.config(state=tk.NORMAL)
            self.set_status("Camera pronta. Premi ▶︎ Start Preview.")
        except Exception as e:
            messagebox.showerror("Camera", str(e))
            self.set_status("Errore inizializzazione camera.")

    # --- preview loop con bounding box ---
    def start_preview(self):
        if self.preview_running or self.picam2 is None: return
        self.preview_running = True
        self.btn_start_prev.config(state=tk.DISABLED)
        self.btn_stop_prev.config(state=tk.NORMAL)
        self.btn_snap.config(state=tk.NORMAL)
        self._preview_tick()

    def _preview_tick(self):
        if not self.preview_running: return
        try:
            rgb = self.picam2.capture_array()
            frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            # overlay bounding box (centrale) e crocino
            h, w = frame.shape[:2]
            pad = int(min(w, h) * 0.2)
            x1, y1 = pad, pad
            x2, y2 = w - pad, h - pad
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)  # giallo
            cv2.drawMarker(frame, (w//2, h//2), (0, 255, 255), cv2.MARKER_CROSS, 20, 2)

            pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            self.current_image_pil = pil
            self._draw_preview(pil)
        except Exception:
            pass
        finally:
            self.after(self.preview_interval_ms, self._preview_tick)

    def _draw_preview(self, pil_img):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 2 or ch <= 2: return
        iw, ih = pil_img.size
        scale = min(cw/iw, ch/ih)
        nw, nh = max(1, int(iw*scale)), max(1, int(ih*scale))
        preview = pil_img.resize((nw, nh), Image.LANCZOS)
        self.current_image_tk = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(cw//2, ch//2, image=self.current_image_tk, anchor="center")

    def stop_preview(self):
        self.preview_running = False
        self.btn_start_prev.config(state=tk.NORMAL)
        self.btn_stop_prev.config(state=tk.DISABLED)

    def on_snap(self):
        if self.current_image_pil is None: return
        self.stop_preview()
        self.btn_desc.config(state=tk.NORMAL)
        self.btn_save.config(state=tk.NORMAL)
        self.set_status("Foto bloccata. Premi 📝 Descrivi.")

    def on_describe(self):
        if self.current_image_pil is None: return
        self.btn_desc.config(state=tk.DISABLED); self.btn_read.config(state=tk.DISABLED)
        self.set_status("Invio al modello (caption)…")
        threading.Thread(target=self._worker_caption, daemon=True).start()

    def _worker_caption(self):
        try:
            buf = io.BytesIO(); self.current_image_pil.convert("RGB").save(buf, format="JPEG", quality=90)
            data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            prompt = ("Descrivi l'immagine in 1-2 frasi brevi, neutrali, utili per un log video. "
                      "Indica azioni/oggetti rilevanti. Italiano.")
            resp = client.responses.create(
                model=MODEL_VISION,
                input=[{"role":"user","content":[
                    {"type":"input_text","text":prompt},
                    {"type":"input_image","image_url":data_url}
                ]}]
            )
            text = resp.output_text.strip()
            self.after(0, self._on_caption_ready, text)
        except Exception as e:
            self.after(0, lambda: (messagebox.showerror("Caption", str(e)), self.set_status("Errore descrizione."), self.btn_desc.config(state=tk.NORMAL)))

    def _on_caption_ready(self, text):
        self.txt.delete("1.0","end"); self.txt.insert("1.0", text)
        self.btn_read.config(state=tk.NORMAL)
        self.btn_use_prompt.config(state=tk.NORMAL)
        self.set_status("Descrizione pronta. 🔊 Leggi o ➡️ Usa come prompt.")

    def on_read(self):
        text = self.txt.get("1.0","end").strip()
        if not text: return
        voice = self.voice_var.get().strip() or "alloy"
        device = self.aplay_device_var.get().strip() or None
        threading.Thread(target=self._worker_tts, args=(text, voice, device), daemon=True).start()
        self.set_status("Sintesi vocale…")

    def _worker_tts(self, text, voice, device):
        try:
            speak_wav_aplay(text, voice=voice, aplay_device=device, popen_holder={"proc": None})
            self.after(0, lambda: self.set_status("Riproduzione audio avviata (ALSA)."))
        except Exception as e:
            self.after(0, lambda: (messagebox.showerror("TTS", str(e)), self.set_status("Errore TTS.")))

    def on_save(self):
        if self.current_image_pil is None: return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = os.path.join(OUT_DIR, f"capture_{ts}.jpg")
        self.current_image_pil.convert("RGB").save(fp, "JPEG", quality=92)
        self.set_status(f"Immagine salvata: {fp}")

    def on_use_caption(self):
        text = self.txt.get("1.0","end").strip()
        if not text: return
        try:
            self.on_use_caption_cb(text)  # manda alla scheda Generatore
            self.set_status("Caption inviata al Generatore come prompt.")
        except Exception as e:
            messagebox.showerror("Invio prompt", str(e))

    def destroy(self):
        try:
            if self.picam2: self.picam2.stop()
        except: pass
        super().destroy()


# ==========================
# App principale
# ==========================
class MainApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap13 • Generatore + Camera + TTS (ALSA)")
        self.geometry("1080x860")
        self.minsize(960, 760)

        # stile thumbnail selezionata
        style = ttk.Style(self)
        style.configure("Selected.TButton", relief="solid", borderwidth=2)

        # barra superiore con selettori voce e ALSA device
        ctrl = ttk.Frame(self, padding=(10,8))
        ctrl.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(ctrl, text="Voce TTS:").pack(side=tk.LEFT)
        self.voice_var = tk.StringVar(value=VOICE_CHOICES[0])
        ttk.OptionMenu(ctrl, self.voice_var, self.voice_var.get(), *VOICE_CHOICES).pack(side=tk.LEFT, padx=(6,16))

        ttk.Label(ctrl, text="ALSA device (opz):").pack(side=tk.LEFT)
        self.aplay_device_var = tk.StringVar(value="")
        ttk.Entry(ctrl, textvariable=self.aplay_device_var, width=12).pack(side=tk.LEFT, padx=(6,16))
        ttk.Label(ctrl, text="(es. hw:0,0 — vuoto=default)").pack(side=tk.LEFT)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # callback: usa caption come prompt (dalla Camera alla Generazione)
        def use_caption_as_prompt(caption_text):
            self.gen_tab.prompt_var.set(caption_text)
            nb.select(self.gen_tab)  # passa alla scheda Generatore

        self.gen_tab = GeneratorFrame(nb, self.voice_var, self.aplay_device_var)
        self.cam_tab = CameraFrame(nb, self.voice_var, self.aplay_device_var, on_use_caption_cb=use_caption_as_prompt)

        nb.add(self.gen_tab, text="Generatore")
        nb.add(self.cam_tab, text="Camera")

    def destroy(self):
        try:
            self.cam_tab.destroy()
        except: pass
        super().destroy()


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("ATTENZIONE: manca OPENAI_API_KEY nell'ambiente.")
    app = MainApp()
    app.mainloop()
