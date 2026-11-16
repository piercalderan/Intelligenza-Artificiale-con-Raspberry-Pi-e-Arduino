

#!/usr/bin/env python3
"""
Capitolo 13 – Generazione immagini (GPT Image) con interfaccia Tkinter
- Prompt di testo
- Scelta misura (1024 / 1536)
- Genera, visualizza e salva PNG in ./out/ con timestamp
"""

import os, io, base64, datetime, threading
from openai import OpenAI
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox

# ========== CONFIG ==========
# Legge la chiave da variabile d'ambiente.
# In alternativa, decommenta e inserisci la tua chiave qui sotto (sconsigliato nel codice finale):
#os.environ["OPENAI_API_KEY"] = "sk-........"
# ============================
client = OpenAI()

OUT_DIR = "out_img"
os.makedirs(OUT_DIR, exist_ok=True)

MODEL = "gpt-image-1-mini" # modello semplice per generazione da testo
SIZES = ["1024x1024", "1536x1024"]  # 1024x1024 a volte più lento/costante
print(os.environ.get("OPENAI_API_KEY"))
class DalleApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap13 • GPT Image (Tkinter)")
        self.geometry("720x560")
        self.minsize(640, 520)

        self.current_image_pil = None
        self.current_image_tk = None
        self.generating = False

        self._build_ui()

    def _build_ui(self):
        # Frame superiore: prompt + opzioni
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Prompt:").grid(row=0, column=0, sticky="w")
        self.prompt_var = tk.StringVar(value="un piccolo robot sul tavolo, luce calda, stile cartoon")
        self.prompt_entry = ttk.Entry(top, textvariable=self.prompt_var, width=70)
        self.prompt_entry.grid(row=0, column=1, columnspan=3, sticky="we", padx=(6,0))

        ttk.Label(top, text="Misura:").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.size_var = tk.StringVar(value=SIZES[1])
        self.size_menu = ttk.OptionMenu(top, self.size_var, self.size_var.get(), *SIZES)
        self.size_menu.grid(row=1, column=1, sticky="w", pady=(8,0))

        self.btn_gen = ttk.Button(top, text="Genera", command=self.on_generate)
        self.btn_gen.grid(row=1, column=2, padx=6, pady=(8,0), sticky="w")

        self.btn_save = ttk.Button(top, text="Salva come...", command=self.on_save_manual, state=tk.DISABLED)
        self.btn_save.grid(row=1, column=3, padx=6, pady=(8,0), sticky="w")

        # Status
        self.status_var = tk.StringVar(value="Pronto.")
        self.status_lbl = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 4))
        self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X)

        # Canvas immagine (scroll-friendly)
        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(mid, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Binding resize per riscalare preview mantenendo proporzioni
        self.canvas.bind("<Configure>", lambda e: self._refresh_canvas())

        for i in range(4):
            top.grid_columnconfigure(i, weight=1)

    def set_status(self, text):
        self.status_var.set(text)
        self.update_idletasks()


    def on_generate(self):
        if self.generating:
            return
        prompt = self.prompt_var.get().strip()
        if not prompt:
            messagebox.showwarning("Prompt mancante", "Scrivi qualcosa nel prompt 😊")
            return
        size = self.size_var.get()

        self.generating = True
        self.btn_gen.config(state=tk.DISABLED)
        self.btn_save.config(state=tk.DISABLED)
        self.set_status("Generazione in corso…")

        # Thread per non bloccare la UI
        t = threading.Thread(target=self._generate_worker, args=(prompt, size), daemon=True)
        t.start()
    def _generate_worker(self, prompt, size):
        try:
            # Assicurati che MODEL sia "gpt-image-1-mini"
            # e che size sia "1024x1024" (o "auto")
            resp = client.images.generate(
                model=MODEL,
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
            



    

    def _on_image_ready(self, pil_img, saved_path):
        self.current_image_pil = pil_img
        self._refresh_canvas()
        self.btn_save.config(state=tk.NORMAL)
        self.set_status(f"Immagine generata e salvata: {saved_path}")
        self.generating = False
        self.btn_gen.config(state=tk.NORMAL)

    def _on_error(self, err):
        self.generating = False
        self.btn_gen.config(state=tk.NORMAL)
        self.set_status("Errore.")
        messagebox.showerror("Errore generazione", str(err))

    def _refresh_canvas(self):
        if self.current_image_pil is None:
            self.canvas.delete("all")
            self.canvas.create_text(
                self.canvas.winfo_width()//2,
                self.canvas.winfo_height()//2,
                text="Nessuna immagine (ancora) 😄",
                fill="#cccccc"
            )
            return

        # Fit-to-window mantenendo proporzioni
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = self.current_image_pil.size
        if iw == 0 or ih == 0 or cw <= 2 or ch <= 2:
            return
        scale = min(cw / iw, ch / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        preview = self.current_image_pil.resize((nw, nh), Image.LANCZOS)
        self.current_image_tk = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(cw//2, ch//2, image=self.current_image_tk, anchor="center")

    def on_save_manual(self):
        if self.current_image_pil is None:
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"manual_{ts}.png")
        try:
            self.current_image_pil.save(out_path, format="PNG")
            self.set_status(f"Salvato: {out_path}")
        except Exception as e:
            messagebox.showerror("Errore salvataggio", str(e))


if __name__ == "__main__":
    # Controllo chiave basico
    if not os.environ.get("OPENAI_API_KEY"):
        print("ATTENZIONE: manca OPENAI_API_KEY nell'ambiente.")
        print("  export OPENAI_API_KEY='sk-...'  # Linux/macOS")
        print("  setx OPENAI_API_KEY \"sk-...\"    # Windows (poi riapri il terminale)")
    app = DalleApp()
    app.mainloop()
