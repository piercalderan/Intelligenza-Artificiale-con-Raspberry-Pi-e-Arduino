import os
import json
import queue
import threading
import subprocess
from dotenv import load_dotenv

import sounddevice as sd
import vosk

from intents import detect_intent
from gpt_client import ask_gpt
from tts import tts_gtts, tts_openai
from sensors import start_mqtt_listener, sensor_cache, read_dht_local
from mqtt_bridge import MqttBridge
from sensors import start_serial_and_mqtt_bridge, sensor_cache, read_dht_local, request_one_shot
# ...
print("[Init] Avvio ponte Seriale-MQTT")
start_serial_and_mqtt_bridge()
    
load_dotenv()

ASR_LANG = os.getenv("ASR_LANG", "it").lower()
MODEL_NAME = "vosk-model-small-it-0.22" if ASR_LANG == "it" else "vosk-model-small-el-0.22"
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models", MODEL_NAME)

RATE = 16000
DEVICE = 1  # lascia None per il microfono predefinito

# ======================================================
# FUNZIONI AUSILIARIE
# ======================================================

def speak(text: str, prefer_openai: bool = False):
    """
    Riproduce la frase con OpenAI TTS o gTTS a seconda di prefer_openai.
    """
    try:
        fn = tts_openai(text) if prefer_openai else tts_gtts(text, lang="it")
        subprocess.run(["mpg123", "-q", fn], check=True)
    except Exception as e:
        print(f"[TTS] Errore riproduzione: {e}")

def help_text():
    return ("Puoi dirmi: 'temperatura', 'umidità', 'accendi led', 'spegni led' ")

# ======================================================
# INTENTI E AZIONI
# ======================================================

def handle_intent(intent: str, slots: dict, mqtt: MqttBridge) -> tuple[str, bool]:
    """
    Esegue l'azione associata all'intento e restituisce (testo_risposta, prefer_openai).
    prefer_openai indica se usare la voce OpenAI TTS.
    """
    # Intento 'help'
    if intent == "help":
        return help_text(), False

    # Intento temperatura
    if intent == "temperature_query":
        t, h = sensor_cache.get()
        if t is None:
            # chiedi ad Arduino una lettura immediata e attendi
            t, h = request_one_shot(timeout_sec=2.0)
        if t is not None:
            return f"La temperatura è di {round(t,1)} gradi.", False
        #return "Non ho dati di temperatura al momento.", False
        return "", False
    

    if intent == "humidity_query":
        t, h = sensor_cache.get()
        if h is None:
            t, h = request_one_shot(timeout_sec=2.0)
        if h is not None: 
            return f"L'umidità è {int(h)} percento.", False
        return "Non ho dati di umidità al momento.", False

    # Intento accensione LED
    if intent == "led_on":
        mqtt.send_led(True)
        return "Ho acceso il LED.", False

    # Intento spegnimento LED
    if intent == "led_off":
        mqtt.send_led(False)
        return "Ho spento il LED.", False

    # Tutto il resto → GPT
    return ask_gpt(slots.get("text") or ""), True


# ======================================================
# RICONOSCIMENTO VOCALE (ASR)
# ======================================================

def asr_loop(text_queue: queue.Queue):
    if not os.path.isdir(MODEL_DIR):
        raise RuntimeError(f"Modello Vosk non trovato: {MODEL_DIR}")

    model = vosk.Model(MODEL_DIR)
    rec = vosk.KaldiRecognizer(model, RATE)
    rec.SetWords(True)

    sd.default.samplerate = RATE
    sd.default.channels = 1

    def audio_cb(indata, frames, time, status):
        try:
            if rec.AcceptWaveform(bytes(indata)):  # fix per buffer CFFI
                result = json.loads(rec.Result())
                txt = (result.get("text") or "").strip()
                if txt:
                    text_queue.put(txt)
        except Exception as e:
            print(f"[ASR] errore: {e}")

    with sd.RawInputStream(samplerate=RATE, blocksize=8000, device=DEVICE,
                           dtype='int16', channels=1, callback=audio_cb):
        while True:
            sd.sleep(1000)


# ======================================================
# PROGRAMMA PRINCIPALE
# ======================================================

def main():
    print("[Init] Avvio MQTT listener…")
    start_mqtt_listener()
    bridge = MqttBridge()

    print(f"[Init] Avvio Vosk ASR ({MODEL_NAME})…")
    qtxt = queue.Queue()
    threading.Thread(target=asr_loop, args=(qtxt,), daemon=True).start()

    speak("Assistente vocale avviato. " + help_text(), prefer_openai=False)

    while True:
        text = qtxt.get()
        print(f"[ASR] {text}")
        intent, slots = detect_intent(text)
        if intent == "general":
            slots["text"] = text

        reply, prefer_openai = handle_intent(intent, slots, bridge)
        print(f"[BOT] {reply}")
        bridge.send_say(reply)
        speak(reply, prefer_openai=prefer_openai)

if __name__ == "__main__":
    main()
