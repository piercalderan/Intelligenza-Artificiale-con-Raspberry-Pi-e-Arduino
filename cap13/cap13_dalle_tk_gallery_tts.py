#!/usr/bin/env python3
import os, io, base64, datetime, threading, tempfile, pathlib
from openai import OpenAI
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox
import simpleaudio as sa

client = OpenAI()

OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)
MODEL_TTS = "gpt-4o-mini-tts"

MODEL_IMG = "gpt-image-1-mini" # modello semplice per generazione da testo
SIZES = ["1024x1024", "1536x1024"]  # 1024x1024 a volte più lento/costante

BATCH_N = 4

class DalleApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap13 • GPT Image + TTS")
        self.geometry("980x780")
        self.minsize(820, 640)

        self.current_image_pil = None
        self.current_image_tk  = None
        self.generating = False

        self.gallery_images, self.gallery_paths, self.gallery_index = [], [], 0
        self.audio_play_obj = None  # per stop
        self._build_ui()

        self.bind("<Left>",  lambda e: self.on_prev())
        self.bind("<Right>", lambda e: self.on_next())

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Prompt:").grid(row=0, column=0, sticky="w")
        self.prompt_var = tk.StringVar(value="un piccolo robot sul tavolo, luce calda, stile cartoon")
        self.prompt_entry = ttk.Entry(top, textvariable=self.prompt_var, width=100)
        self.prompt_entry.grid(row=0, column=1, columnspan=8, sticky="we", padx=(6,0))

        ttk.Label(top, text="Misura:").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.size_var = tk.StringVar(value=SIZES[1])
        self.size_menu = ttk.OptionMenu(top, self.size_var, self.size_var.get(), *SIZES)
        self.size_menu.grid(row=1, column=1, sticky="w", pady=(8,0))

        self.btn_gen  = ttk.Button(top, text="Genera (x4)",   command=self.on_generate)
        self.btn_reg  = ttk.Button(top, text="Rigenera (x4)", command=self.on_regenerate)
        self.btn_var  = ttk.Button(top, text="Varia (x4)",     command=self.on_variations, state=tk.DISABLED)
        self.btn_prev = ttk.Button(top, text="◀︎", width=3, command=self.on_prev, state=tk.DISABLED)
        self.btn_next = ttk.Button(top, text="▶︎", width=3, command=self.on_next, state=tk.DISABLED)

        self.btn_gen.grid(row=1, column=2, padx=6, pady=(8,0), sticky="w")
        self.btn_reg.grid(row=1, column=3, padx=6, pady=(8,0), sticky="w")
        self.btn_var.grid(row=1, column=4, padx=6, pady=(8,0), sticky="w")
        self.btn_prev.grid(row=1, column=5, padx=(20,4), pady=(8,0), sticky="e")
        self.btn_next.grid(row=1, column=6, padx=4, pady=(8,0), sticky="w")

        # --- Pannello TTS ---
        ttk.Label(top, text="Voce:").grid(row=1, column=7, sticky="e", pady=(8,0))
        self.voice_var = tk.StringVar(value="alloy")
        self.voice_entry = ttk.Entry(top, textvariable=self.voice_var, width=10)
        self.voice_entry.grid(row=1, column=8, sticky="w", padx=(4,0), pady=(8,0))

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._refresh_canvas())

        # Box descrizione da leggere (opzionale)
        bottom_top = ttk.Frame(self, padding=(10, 0, 10, 0))
        bottom_top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bottom_top, text="Descrizione (facoltativa, letta da TTS):").pack(anchor="w")
        self.desc_txt = tk.Text(bottom_top, height=3, wrap="word")
        self.desc_txt.pack(fill=tk.X)

        # Bottoni TTS
        tts_bar = ttk.Frame(self, padding=(10, 6, 10, 6))
        tts_bar.pack(side=tk.TOP, fill=tk.X)
        self.btn_read_prompt = ttk.Button(tts_bar, text="Leggi Prompt", command=self.on_read_prompt)
        self.btn_read_desc   = ttk.Button(tts_bar, text="Leggi Descrizione", command=self.on_read_desc)
        self.btn_stop_audio  = ttk.Button(tts_bar, text="Stop Audio",
                                          command=self.on_stop_audio)
        self.btn_save_img    = ttk.Button(tts_bar, text="Salva immagine corrente", command=self.on_save_current, state=tk.DISABLED)
        self.btn_read_prompt.pack(side=tk.LEFT, padx=4)
        self.btn_read_desc.pack(side=tk.LEFT, padx=4)
        #self.btn_stop_audio.pack(side=tk.LEFT, padx=4)
        self.btn_save_img.pack(side=tk.RIGHT)

        status = ttk.Frame(self, padding=(10, 0, 10, 10))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        for i in range(9): top.grid_columnconfigure(i, weight=1)

    def _set_status(self, text):
        self.status_var.set(text)
        self.update_idletasks()

    # ===== Azioni DALL·E =====
    def on_generate(self):
        if self.generating: return
        p = self.prompt_var.get().strip()
        if not p: return messagebox.showwarning("Prompt mancante", "Scrivi un prompt 😊")
        self._run_async(self._worker_generate, p, self.size_var.get(), BATCH_N)

    def on_regenerate(self):
        if self.generating: return
        p = self.prompt_var.get().strip()
        if not p: return messagebox.showwarning("Prompt mancante", "Scrivi un prompt 😊")
        self._run_async(self._worker_generate, p, self.size_var.get(), BATCH_N)

    def on_variations(self):
        if self.generating: return
        if self.current_image_pil is None:
            return messagebox.showinfo("Nessuna immagine", "Genera prima un'immagine.")
        self._run_async(self._worker_variations, self.current_image_pil, self.size_var.get(), BATCH_N)

    def on_prev(self):
        if not self.gallery_images: return
        self.gallery_index = (self.gallery_index - 1) % len(self.gallery_images)
        self._set_current_from_gallery()
        self._set_status(f"Galleria {self.gallery_index+1}/{len(self.gallery_images)}")

    def on_next(self):
        if not self.gallery_images: return
        self.gallery_index = (self.gallery_index + 1) % len(self.gallery_images)
        self._set_current_from_gallery()
        self._set_status(f"Galleria {self.gallery_index+1}/{len(self.gallery_images)}")

    def on_save_current(self):
        if self.current_image_pil is None: return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"saved_current_{ts}.png")
        try:
            self.current_image_pil.save(out_path, "PNG")
            self._set_status(f"Salvato: {out_path}")
        except Exception as e:
            messagebox.showerror("Errore salvataggio", str(e))

    # ===== Azioni TTS =====
    def on_read_prompt(self):
        text = self.prompt_var.get().strip()
        if not text:
            return messagebox.showinfo("Prompt vuoto", "Scrivi qualcosa nel prompt.")
        self._run_tts_async(text)

    def on_read_desc(self):
        text = self.desc_txt.get("1.0", "end").strip()
        if not text:
            return messagebox.showinfo("Descrizione vuota", "Scrivi una descrizione nel box.")
        self._run_tts_async(text)

    def on_stop_audio(self):
        try:
            #if self.audio_play_obj and self.audio_play_obj.is_playing():
            if self.audio_play_obj.is_playing():
                self.audio_play_obj.stop()
                self._set_status("Audio fermato.")
        except Exception:
            pass

    # ===== Thread helpers =====
    def _run_async(self, target, *args):
        self.generating = True
        for b in (self.btn_gen, self.btn_reg, self.btn_var, self.btn_prev, self.btn_next, self.btn_save_img):
            b.config(state=tk.DISABLED)
        self._set_status("Elaborazione in corso…")
        threading.Thread(target=target, args=args, daemon=True).start()


    def _worker_generate(self, prompt, size, n):
        try:
            resp = client.images.generate(
                model=MODEL_IMG,  # es. "gpt-image-1-mini" 
                prompt=prompt,
                size=size,
                n=max(1, int(n))
            )

            items = list(getattr(resp, "data", []))
            if not items:
                raise RuntimeError("Risposta vuota dall'API Images.")

            images, paths = [], []
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            for idx, item in enumerate(items, 1):
                img_bytes = self._bytes_from_item(item)
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                fp = os.path.join(OUT_DIR, f"gen_{ts}_{idx}.png")
                img.save(fp, "PNG")
                images.append(img)
                paths.append(fp)

            self.after(0, self._on_gallery_ready, images, paths, "Generate")
        except Exception as e:
            self.after(0, self._on_error, e)

    def _worker_variations(self, base_pil, size, n, prompt=None):
        try:
            buf = io.BytesIO()
            base_pil.save(buf, format="PNG")
            image_bytes = buf.getvalue()

            # I modelli GPT Image usano images.edit al posto di images.variations
            # e richiedono una tupla (nome_file, bytes, mime_type) e un prompt
            var_prompt = prompt or "Create a creative variation of this image, keeping the main subject and style while varying details and composition."

            resp = client.images.edit(
                model=MODEL_IMG,
                image=("base.png", image_bytes, "image/png"),
                prompt=var_prompt,
                size=size,
                n=max(1, int(n))
            )

            items = list(getattr(resp, "data", []))
            if not items:
                raise RuntimeError("Risposta vuota dall'API Images (variations).")

            images, paths = [], []
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            for idx, item in enumerate(items, 1):
                img_bytes = self._bytes_from_item(item)
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                fp = os.path.join(OUT_DIR, f"var_{ts}_{idx}.png")
                img.save(fp, "PNG")
                images.append(img)
                paths.append(fp)

            self.after(0, self._on_gallery_ready, images, paths, "Variations")
        except Exception as e:
            self.after(0, self._on_error, e)







##    def _worker_generate(self, prompt, size, n):
##        try:
##            resp = client.images.generate(
##                model=MODEL_IMG,
##                prompt=prompt,
##                size=size,
##                response_format="b64_json",
##                n=max(1,int(n))
##                )
##            
##            items = list(getattr(resp, "data", []))
##            if not items: raise RuntimeError("Risposta vuota dall'API Images.")
##            images, paths = [], []
##            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
##            for idx, item in enumerate(items, 1):
##                img_bytes = self._bytes_from_item(item)
##                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
##                fp = os.path.join(OUT_DIR, f"dalle_gen_{ts}_{idx}.png")
##                img.save(fp, "PNG")
##                images.append(img); paths.append(fp)
##            self.after(0, self._on_gallery_ready, images, paths, "Generate")
##        except Exception as e:
##            self.after(0, self._on_error, e)
##
##    def _worker_variations(self, base_pil, size, n):
##        try:
##            buf = io.BytesIO(); base_pil.save(buf, format="PNG"); image_bytes = buf.getvalue()
##            resp = client.images.variations(model=MODEL_IMG, image=image_bytes, size=size, response_format="b64_json", n=max(1,int(n)))
##            items = list(getattr(resp, "data", []))
##            if not items: raise RuntimeError("Risposta vuota dall'API Images (variations).")
##            images, paths = [], []
##            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
##            for idx, item in enumerate(items, 1):
##                img_bytes = self._bytes_from_item(item)
##                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
##                fp = os.path.join(OUT_DIR, f"dalle_var_{ts}_{idx}.png")
##                img.save(fp, "PNG")
##                images.append(img); paths.append(fp)
##            self.after(0, self._on_gallery_ready, images, paths, "Variations")
##        except Exception as e:
##            self.after(0, self._on_error, e)

    def _run_tts_async(self, text):
        self._set_status("Sintesi vocale…")
        voice = self.voice_var.get().strip() or "alloy"
        threading.Thread(target=self._worker_tts, args=(text, voice), daemon=True).start()

    def _worker_tts(self, text, voice):
        try:
            import tempfile, pathlib, datetime, time, os, shutil, subprocess
            speech_path = pathlib.Path(tempfile.gettempdir()) / f"cap13_tts_{datetime.datetime.now().strftime('%H%M%S')}.wav"

            # 1) genera WAV (streaming corretto)
            with client.audio.speech.with_streaming_response.create(
                model=MODEL_TTS,           # es. "gpt-4o-mini-tts"
                voice=voice or "alloy",
                input=text,
                response_format="wav"
            ) as response:
                response.stream_to_file(speech_path)

            # 2) verifica file
            time.sleep(0.05)
            if not speech_path.exists() or speech_path.stat().st_size < 1024:
                raise RuntimeError(f"File audio non valido: {speech_path}")

            # 3) riproduzione con aplay (ALSA)
            if not shutil.which("aplay"):
                raise RuntimeError("aplay non trovato (installa 'alsa-utils').")

            # opzionale: imposta un device ALSA, es. "hw:0,0" oppure None per default
            APLAY_DEVICE = None  # esempio: "hw:0,0"
            cmd = ["aplay", "-q", str(speech_path)]
            if APLAY_DEVICE:
                cmd = ["aplay", "-q", "-D", APLAY_DEVICE, str(speech_path)]

            # non blocchiamo la UI: avvia in sottoprocesso
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.after(0, lambda: self._set_status(f"TTS riproduzione (ALSA): {speech_path.name}"))
        except Exception as e:
            self.after(0, self._on_error, e)

    # ===== Helpers =====
    def _bytes_from_item(self, item):
        b64 = getattr(item, "b64_json", None)
        if b64: return base64.b64decode(b64)
        url = getattr(item, "url", None)
        if not url: raise RuntimeError("Né b64_json né url presenti nella risposta.")
        import requests
        r = requests.get(url, timeout=60); r.raise_for_status()
        return r.content

    def _on_gallery_ready(self, images, paths, label):
        self.gallery_images, self.gallery_paths, self.gallery_index = images[:], paths[:], 0
        self._set_current_from_gallery(); self._refresh_canvas()
        self.generating = False
        self.btn_gen.config(state=tk.NORMAL); self.btn_reg.config(state=tk.NORMAL)
        self.btn_var.config(state=tk.NORMAL); self.btn_prev.config(state=tk.NORMAL if len(images)>1 else tk.DISABLED)
        self.btn_next.config(state=tk.NORMAL if len(images)>1 else tk.DISABLED)
        self.btn_save_img.config(state=tk.NORMAL if images else tk.DISABLED)
        base = os.path.basename(paths[0]) if paths else ""
        self._set_status(f"{label}: salvate {len(paths)} immagini. Mostrata 1/{len(paths)} – es. {base}")

    def _set_current_from_gallery(self):
        if not self.gallery_images:
            self.current_image_pil = None; self._refresh_canvas(); self.btn_save_img.config(state=tk.DISABLED); return
        self.current_image_pil = self.gallery_images[self.gallery_index]
        self._refresh_canvas()
        self.btn_save_img.config(state=tk.NORMAL)

    def _on_error(self, err):
        self.generating = False
        self.btn_gen.config(state=tk.NORMAL); self.btn_reg.config(state=tk.NORMAL)
        self.btn_var.config(state=tk.NORMAL if self.current_image_pil is not None else tk.DISABLED)
        self._set_status("Errore."); messagebox.showerror("Errore", str(err))

    def _refresh_canvas(self):
        self.canvas.delete("all")
        if self.current_image_pil is None:
            self.canvas.create_text(self.canvas.winfo_width()//2, self.canvas.winfo_height()//2,
                                    text="Nessuna immagine (ancora) 😄", fill="#cccccc"); return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = self.current_image_pil.size
        if iw == 0 or ih == 0 or cw <= 2 or ch <= 2: return
        scale = min(cw/iw, ch/ih)
        nw, nh = max(1, int(iw*scale)), max(1, int(ih*scale))
        preview = self.current_image_pil.resize((nw, nh), Image.LANCZOS)
        self.current_image_tk = ImageTk.PhotoImage(preview)
        self.canvas.create_image(cw//2, ch//2, image=self.current_image_tk, anchor="center")

if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("ATTENZIONE: manca OPENAI_API_KEY nell'ambiente.")
    app = DalleApp()
    app.mainloop()
