#!/usr/bin/env python3
import os, io, json, base64, datetime, threading, queue, tempfile, subprocess, shutil
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import sounddevice as sd
from vosk import Model, KaldiRecognizer
from openai import OpenAI
from picamera2 import Picamera2
import cv2
from tkinter import scrolledtext

sd.default.device = (1, 1)  # opzionale: forza il mic della webcam

print(sd.query_devices())
print(sd.query_devices(kind="input"))

# ---------- CONFIG ----------
MODEL_VOSK = os.path.expanduser("~/models/vosk-model-small-it-0.22")
MODEL_VISION = "gpt-4o-mini"
MODEL_TTS = "gpt-4o-mini-tts"
MODEL_IMG = "gpt-image-1-mini"
OUT_DIR = "out"
os.makedirs(OUT_DIR, exist_ok=True)

client = OpenAI()

# ---------- TTS ----------
def speak(text, voice="alloy"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.close()
    with client.audio.speech.with_streaming_response.create(
        model=MODEL_TTS, voice=voice, input=text, response_format="wav"
    ) as r:
        r.stream_to_file(tmp.name)
    subprocess.Popen(["aplay", "-q", tmp.name])

# ---------- Vision ----------
def image_to_data_url(pil):
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def describe_scene(pil):
    data_url = image_to_data_url(pil)
    prompt = "Descrivi brevemente la scena in italiano."
    r = client.responses.create(
        model=MODEL_VISION,
        input=[{"role":"user","content":[
            {"type":"input_text","text":prompt},
            {"type":"input_image","image_url":data_url}
        ]}]
    )
    return r.output_text.strip()

def generate_image(prompt):
    r = client.images.generate(
        model=MODEL_IMG, prompt=prompt, size="1024x1024", response_format="b64_json", n=1
    )
    b64 = r.data[0].b64_json
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUT_DIR, f"dalle_{ts}.png")
    img.save(path, "PNG")
    return img, path

# ---------- ASR ----------
def listen_once(rec, stream, q):
    while True:
        buf, _ = stream.read(4000)     # buf è un buffer/memoryview
        data = bytes(buf)              # <-- conversione esplicita a bytes
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            if "text" in res and res["text"].strip():
                q.put(res["text"].strip())
                break

# ---------- GUI ----------
class VisualAssistant(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cap14 • Mini-Assistente Visivo")
        self.geometry("900x720")
        self.picam2 = Picamera2()
        cfg = self.picam2.create_preview_configuration(main={"size":(960,540),"format":"RGB888"})
        self.picam2.configure(cfg); self.picam2.start()
        self.voice = "alloy"
        self.model = Model(MODEL_VOSK)
        self.rec = KaldiRecognizer(self.model, 16000)
        self.q = queue.Queue()
        self.build_ui()
        self.after(100, self.update_preview)

    def build_ui(self):
        top = ttk.Frame(self, padding=10); top.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(top, text="Ascolta comando", command=self.on_listen).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Ripeti ultima risposta", command=lambda:speak(self.last_reply)).pack(side=tk.LEFT, padx=6)
        self.status = tk.StringVar(value="Pronto")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=6).pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas = tk.Canvas(self, bg="#111", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        # pannello descrizione/log scrollabile
        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.pack(side=tk.BOTTOM, fill=tk.BOTH)
        ttk.Label(log_frame, text="Descrizione / Log:").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(
            log_frame, height=8, wrap="word"
        )
        self.log.pack(fill=tk.BOTH, expand=True)


        self.last_img = None
        self.last_reply = ""
    def set_desc(self, text: str):
        self.log.delete("1.0", "end")
        self.log.insert("1.0", text)
        self.log.see("end")

    def append_desc(self, text: str):
        self.log.insert("end", ("\n" if self.log.index("end-1c") != "1.0" else "") + text)
        self.log.see("end")


    def update_preview(self):
        rgb = self.picam2.capture_array()
        frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = frame.shape[:2]
        cv2.rectangle(frame,(w//3,h//3),(2*w//3,2*h//3),(0,255,255),2)
        pil = Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
        self.last_img = pil
        cw,ch = self.canvas.winfo_width(),self.canvas.winfo_height()
        if cw>2 and ch>2:
            scale=min(cw/pil.width,ch/pil.height)
            imtk=ImageTk.PhotoImage(pil.resize((int(pil.width*scale),int(pil.height*scale))))
            self.canvas.create_image(cw//2,ch//2,image=imtk,anchor="center")
            self.canvas.image=imtk
        self.after(100,self.update_preview)

    def on_listen(self):
        self.status.set("Ascolto… parla ora.")
        threading.Thread(target=self.listen_thread, daemon=True).start()

    def listen_thread(self):
        with sd.RawInputStream(
            device=0,
            samplerate=48000,
            blocksize=8000,
            dtype='int16',
            channels=1) as stream:
            
        #with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16', channels=1) as stream:
            
            self.rec.Reset()
            listen_once(self.rec, stream, self.q)
        text = self.q.get().lower()
        print(text)
        self.after(0, lambda:self.handle_command(text))

    def handle_command(self, text):
        self.status.set(f"Comando: {text}")
        print("Comando:", text)
        if "che cosa vedi" in text or "descrivi" in text:
            img = self.last_img
            desc = describe_scene(img)
            self.last_reply = desc
            self.set_desc(desc)      # <-- aggiunta
            speak(desc)
            self.status.set(desc)
        elif "disegna" in text:
            img = self.last_img
            desc = describe_scene(img)
            self.set_desc(f"{desc}\n\nImmagine salvata: {path}")   # <-- aggiunta
            dalle_img, path = generate_image(desc)
            self.last_reply = f"Ho disegnato: {path}"
            speak("Ecco come lo immagino.")
            self.show_image(dalle_img)
            self.status.set(f"Immagine salvata: {path}")
        elif "salva" in text:
            ts=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path=os.path.join(OUT_DIR,f"foto_{ts}.jpg")
            self.last_img.save(path)
            self.last_reply=f"Foto salvata in {path}"
            self.set_desc(self.last_reply)   # <-- aggiunta
            speak("Fatto, foto salvata.")
            self.status.set(self.last_reply)
        else:
            reply="Non ho capito il comando."
            self.last_reply=reply
            self.set_desc(reply)     # <-- aggiunta
            speak(reply)
            self.status.set(reply)

    def show_image(self,pil):
        cw,ch=self.canvas.winfo_width(),self.canvas.winfo_height()
        scale=min(cw/pil.width,ch/pil.height)
        imtk=ImageTk.PhotoImage(pil.resize((int(pil.width*scale),int(pil.height*scale))))
        self.canvas.create_image(cw//2,ch//2,image=imtk,anchor="center")
        self.canvas.image=imtk

if __name__=="__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  Manca OPENAI_API_KEY.")
    app=VisualAssistant()
    app.mainloop()
