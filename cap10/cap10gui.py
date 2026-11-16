import os, subprocess, threading
import tkinter as tk
from tkinter import ttk, messagebox
from dotenv import load_dotenv, find_dotenv

# Carica .env prima di importare gcloud client
load_dotenv(find_dotenv(filename=".env", usecwd=True))

# Google Cloud TTS
from google.cloud import texttospeech as tts

APP_TITLE = "Cap10 • Demo Google Cloud TTS"
DEFAULT_LANG = os.getenv("GCLOUD_LANG", "it-IT")
DEFAULT_VOICE = os.getenv("GCLOUD_VOICE", "it-IT-Wavenet-E")
DEFAULT_RATE = float(os.getenv("GCLOUD_SPEAKING_RATE", "1.0"))
DEFAULT_PITCH = float(os.getenv("GCLOUD_PITCH", "0.0"))
OUT_MP3 = "cap10_tts_gui_out.mp3"

def synthesize_and_play(text, lang, voice_name, rate, pitch):
    try:
        client = tts.TextToSpeechClient()
        synthesis_input = tts.SynthesisInput(text=text)
        voice = tts.VoiceSelectionParams(language_code=lang, name=voice_name)
        audio_config = tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.MP3,
            speaking_rate=float(rate),
            pitch=float(pitch)
        )
        resp = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        with open(OUT_MP3, "wb") as f:
            f.write(resp.audio_content)
        # usa mpg123 per riprodurre
        subprocess.run(["mpg123", "-q", OUT_MP3], check=False)
    except Exception as e:
        messagebox.showerror("Errore TTS", str(e))

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("720x520")
        self.minsize(680, 480)

        # Stato
        self.voices = []  # elenco voci (objects)
        self.lang_var = tk.StringVar(value=DEFAULT_LANG)
        self.voice_var = tk.StringVar(value=DEFAULT_VOICE)
        self.rate_var = tk.DoubleVar(value=DEFAULT_RATE)   # 0.25–4.0
        self.pitch_var = tk.DoubleVar(value=DEFAULT_PITCH) # -20–20
        self.sample_text = tk.StringVar(value="Ciao! Questa è una prova della voce femminile italiana.")

        # UI
        self._build_ui()

        # Carica voci iniziali
        self.refresh_voices()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # riga lingua
        row_lang = ttk.Frame(self); row_lang.pack(fill="x", **pad)
        ttk.Label(row_lang, text="Lingua (BCP-47):").pack(side="left")
        self.lang_entry = ttk.Entry(row_lang, textvariable=self.lang_var, width=15)
        self.lang_entry.pack(side="left", padx=8)
        ttk.Button(row_lang, text="Aggiorna voci", command=self.refresh_voices).pack(side="left")

        # riga voce
        row_voice = ttk.Frame(self); row_voice.pack(fill="x", **pad)
        ttk.Label(row_voice, text="Voce:").pack(side="left")
        self.voice_combo = ttk.Combobox(row_voice, textvariable=self.voice_var, width=40, state="readonly")
        self.voice_combo.pack(side="left", padx=8, fill="x", expand=True)

        # rate & pitch
        row_controls = ttk.Frame(self); row_controls.pack(fill="x", **pad)
        ttk.Label(row_controls, text="Velocità:").grid(row=0, column=0, sticky="w")
        self.rate_scale = ttk.Scale(row_controls, from_=0.25, to=4.0, orient="horizontal", variable=self.rate_var)
        self.rate_scale.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(row_controls, textvariable=self.rate_var, width=6).grid(row=0, column=2, sticky="e")

        ttk.Label(row_controls, text="Pitch:").grid(row=1, column=0, sticky="w")
        self.pitch_scale = ttk.Scale(row_controls, from_=-20.0, to=20.0, orient="horizontal", variable=self.pitch_var)
        self.pitch_scale.grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Label(row_controls, textvariable=self.pitch_var, width=6).grid(row=1, column=2, sticky="e")

        row_controls.columnconfigure(1, weight=1)

        # testo
        frm_text = ttk.LabelFrame(self, text="Testo da sintetizzare")
        frm_text.pack(fill="both", expand=True, **pad)
        self.text_widget = tk.Text(frm_text, height=8, wrap="word")
        self.text_widget.pack(fill="both", expand=True, padx=8, pady=8)
        self.text_widget.insert("1.0", self.sample_text.get())

        # bottoni azione
        row_actions = ttk.Frame(self); row_actions.pack(fill="x", **pad)
        ttk.Button(row_actions, text="Parla", command=self.on_speak).pack(side="left")
        ttk.Button(row_actions, text="Salva selezione nel .env", command=self.save_to_env).pack(side="left", padx=8)
        ttk.Button(row_actions, text="Apri file MP3", command=self.open_file).pack(side="right")

        # hint credenziali
        cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        hint = f"Credenziali: {cred if cred else '(manca GOOGLE_APPLICATION_CREDENTIALS nel .env)'}"
        ttk.Label(self, text=hint, foreground="#666").pack(anchor="w", padx=12, pady=(0,10))

    def open_file(self):
        try:
            if os.path.exists(OUT_MP3):
                subprocess.Popen(["xdg-open", OUT_MP3])
            else:
                messagebox.showinfo("Info", "Nessun file /tmp generato ancora.")
        except Exception as e:
            messagebox.showerror("Errore", str(e))

    def refresh_voices(self):
        lang = self.lang_var.get().strip() or "it-IT"
        try:
            client = tts.TextToSpeechClient()
            resp = client.list_voices(language_code=lang)
            self.voices = resp.voices
            # costruiamo etichette leggibili: "name (F/M, Standard/Wavenet/Neural)"
            items = []
            for v in self.voices:
                gender = v.ssml_gender.name if hasattr(v, "ssml_gender") else "UNKNOWN"
                name = v.name
                # modello in v.name (Wavenet, Neural2, Chirp, ecc.)
                items.append(f"{name}  ({gender})")
            self.voice_combo["values"] = items
            # preseleziona la voce del .env se presente
            pre = DEFAULT_VOICE
            match_idx = 0
            for i, v in enumerate(self.voices):
                if v.name == pre:
                    match_idx = i; break
            self.voice_combo.current(match_idx)
            self.voice_var.set(self.voices[match_idx].name)
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile caricare voci per {lang}.\n\n{e}")

        # quando cambia selezione nella combo, allinea voice_var al vero name
        def on_combo_change(event):
            idx = self.voice_combo.current()
            if 0 <= idx < len(self.voices):
                self.voice_var.set(self.voices[idx].name)
        self.voice_combo.bind("<<ComboboxSelected>>", on_combo_change)

    def on_speak(self):
        txt = self.text_widget.get("1.0", "end").strip()
        if not txt:
            messagebox.showinfo("Info", "Inserisci del testo da sintetizzare.")
            return
        lang = self.lang_var.get().strip() or "it-IT"
        voice = self.voice_var.get().strip()
        rate = self.rate_var.get()
        pitch = self.pitch_var.get()
        # esegui in thread per non bloccare la GUI
        threading.Thread(target=synthesize_and_play, args=(txt, lang, voice, rate, pitch), daemon=True).start()

    def save_to_env(self):
        # aggiorna .env con voce/rate/pitch correnti
        try:
            path = find_dotenv(filename=".env", usecwd=True)
            if not path:
                path = os.path.join(os.getcwd(), ".env")
            # leggi esistente
            current = {}
            if os.path.exists(path):
                with open(path, "r") as f:
                    for line in f:
                        if "=" in line and not line.strip().startswith("#"):
                            k, v = line.strip().split("=", 1)
                            current[k] = v
            # aggiorna
            current["GCLOUD_VOICE"] = self.voice_var.get().strip()
            current["GCLOUD_SPEAKING_RATE"] = str(round(self.rate_var.get(), 3))
            current["GCLOUD_PITCH"] = str(round(self.pitch_var.get(), 3))
            current["GCLOUD_LANG"] = self.lang_var.get().strip() or "it-IT"
            # riscrivi
            with open(path, "w") as f:
                for k, v in current.items():
                    f.write(f"{k}={v}\n")
            messagebox.showinfo("Salvato", f"Impostazioni aggiornate in {path}.")
        except Exception as e:
            messagebox.showerror("Errore", f"Salvataggio .env fallito:\n\n{e}")

if __name__ == "__main__":
    app = App()
    app.mainloop()
