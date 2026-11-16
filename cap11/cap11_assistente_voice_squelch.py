import os, time, json, re, queue, threading, subprocess, sys
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv, find_dotenv
import paho.mqtt.client as mqtt
from gtts import gTTS

# ---- Env ----
load_dotenv(find_dotenv(filename=".env", usecwd=True))
INPUT_DEVICE_INDEX = int(os.getenv("INPUT_DEVICE_INDEX", "-1"))
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
WAKEWORD = os.getenv("WAKEWORD", "assistente")
WAKE_SENSITIVITY = float(os.getenv("WAKE_SENSITIVITY", "0.5"))
WAKE_DEBOUNCE_MS = int(os.getenv("WAKE_DEBOUNCE_MS", "1500"))
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "vosk").lower()
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BASE = os.getenv("BASE_TOPIC", "home/assistant")
TOPIC_CMDS = f"{BASE}/voice/commands"
TOPIC_LOG  = f"{BASE}/voice/log"

CEC_ENABLED = os.getenv("CEC_ENABLED", "true").lower() == "true"
HA_DISC_PREFIX = os.getenv("HA_DISC_PREFIX", "homeassistant")
DEVICE_ID = os.getenv("DEVICE_ID", "cap11_voice")
DEVICE_NAME = os.getenv("DEVICE_NAME", "Cap11 Voice")
SENSOR_ASR   = "cap11_last_asr"
SENSOR_INTENT= "cap11_last_intent"
SENSOR_PAY   = "cap11_last_payload"

# ---- Wake/session guards ----
SESSION_ACTIVE = False
SQUELCH_UNTIL = 0.0     # timestamp: ignora wake finché < now (anti-eco TTS)
COOLDOWN_UNTIL = 0.0    # evita retrigger ravvicinati post-ASR

text_asr = ""

def ha_discovery_configs():
    dev = {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": "Capitolo 11",
        "model": "Assistente vocale"
    }
    base = f"{HA_DISC_PREFIX}/sensor"
    return [
        {
            "cfg_t": f"{base}/{SENSOR_ASR}/config",
            "cfg": {
                "name": "Cap11 Ultimo ASR",
                "uniq_id": SENSOR_ASR,
                "stat_t": f"{base}/{SENSOR_ASR}/state",
                "icon": "mdi:microphone-message",
                "device": dev
            }
        },
        {
            "cfg_t": f"{base}/{SENSOR_INTENT}/config",
            "cfg": {
                "name": "Cap11 Ultimo Intento",
                "uniq_id": SENSOR_INTENT,
                "stat_t": f"{base}/{SENSOR_INTENT}/state",
                "icon": "mdi:play-circle",
                "device": dev
            }
        },
        {
            "cfg_t": f"{base}/{SENSOR_PAY}/config",
            "cfg": {
                "name": "Cap11 Ultimo Payload",
                "uniq_id": SENSOR_PAY,
                "stat_t": f"{base}/{SENSOR_PAY}/state",
                "icon": "mdi:code-json",
                "device": dev
            }
        }
    ]

def publish_discovery():
    for it in ha_discovery_configs():
        client.publish(it["cfg_t"], json.dumps(it["cfg"]), retain=True)

def publish_state(sensor: str, payload: str):
    topic = f"{HA_DISC_PREFIX}/sensor/{sensor}/state"
    client.publish(topic, payload, retain=True)


# ---- MQTT ----
client = mqtt.Client(client_id="cap11-voice", clean_session=True)
client.connect(MQTT_HOST, MQTT_PORT, 30)
client.loop_start()

publish_discovery()

def mqtt_log(obj):
    try: client.publish(TOPIC_LOG, json.dumps(obj))
    except: pass

# ---- TTS semplice: riusa gTTS per conferma (puoi sostituire col provider del Cap.10)
def say(text):
    """Parla e attiva squelch anti-eco per ~1.2s, drenando la coda audio."""
    global SQUELCH_UNTIL
    try:
        
        out = "cap11_tts.mp3"
        gTTS(text=text, lang="it", tld="it").save(out)
        # squelch: ignora wake durante la riproduzione + coda
        dur = 1.2  # stima semplice; se vuoi puoi calcolare la durata reale con mutagen
        SQUELCH_UNTIL = time.time() + dur
        # drena un po' la coda per evitare che l'eco attivi la wake
        t_end = time.time() + 0.4
        while time.time() < t_end:
            try: audio_q.get_nowait()
            except queue.Empty: break
        subprocess.run(["mpg123","-q",out], check=False)
        SQUELCH_UNTIL = time.time() + 0.6  # extra coda dopo il TTS
    except Exception as e:
        mqtt_log({"err":"tts", "msg":str(e)})


##def say(text):
##    print(text)
##    try:
##        
##        out = "cap11_tts.mp3"
##        gTTS(text=text, lang="it", tld="it").save(out)
##        subprocess.run(["mpg123","-q",out], check=False)
##    except Exception as e:
##        mqtt_log({"err":"tts", "msg":str(e)})

# --- Wake-word con Vosk ("assistente") ---
# Usa un piccolo recognizer che ascolta SOLTANTO
# la parola di attivazione (bassa CPU)
from vosk import Model as VoskModel, KaldiRecognizer

# percorso modello IT per wake
# puoi riusare lo stesso del .env
# o uno più pesante (1.2G): vosk-model-it-0.22	
VOSK_WAKE_MODEL_PATH = os.getenv("VOSK_WAKE_MODEL_PATH", VOSK_MODEL_PATH)
if not VOSK_WAKE_MODEL_PATH or not os.path.isdir(VOSK_WAKE_MODEL_PATH):
    raise RuntimeError("VOSK_WAKE_MODEL_PATH non valido (imposta nel .env)")

_vosk_wake_model = VoskModel(VOSK_WAKE_MODEL_PATH)
print(_vosk_wake_model)
say("ciao. dimmi pure la parola di comando.")

wake_rec = KaldiRecognizer(_vosk_wake_model, SAMPLE_RATE)
# Limita il vocabolario al solo wakeword (case-insensitive)
wake_rec.SetGrammar(f'["{WAKEWORD.lower()}"]')

def wake_detect(pcm16_bytes: bytes) -> bool:
    """
    Ritorna True se nel chunk viene riconosciuta la parola di attivazione.
    Usa PartialResult/Result del recognizer con grammatica ristretta.
    """
    if wake_rec.AcceptWaveform(pcm16_bytes):
        res = json.loads(wake_rec.Result())
        txt = (res.get("text") or "").strip().lower()
        return (txt == WAKEWORD.lower())
    else:
        pres = json.loads(wake_rec.PartialResult())
        ptxt = (pres.get("partial") or "").strip().lower()
        return (ptxt == WAKEWORD.lower())


# ---- ASR providers ----
def asr_transcribe(samples: np.ndarray) -> str:
    """samples: mono 16k np.float32 [-1,1]"""
    if ASR_PROVIDER == "vosk":
        try:
            from vosk import Model as VModel, KaldiRecognizer
            if not hasattr(asr_transcribe, "_vm"):
                if not VOSK_MODEL_PATH or not os.path.isdir(VOSK_MODEL_PATH):
                    raise RuntimeError("VOSK_MODEL_PATH non valido")
                asr_transcribe._vm = VModel(VOSK_MODEL_PATH)
            rec = KaldiRecognizer(asr_transcribe._vm, SAMPLE_RATE)
            rec.SetWords(False)
            # Vosk accetta bytes PCM 16-bit little endian
            pcm16 = (samples * 32767.0).astype(np.int16).tobytes()
            rec.AcceptWaveform(pcm16)
            res = json.loads(rec.FinalResult()).get("text","").strip()
            return res
        except Exception as e:
            mqtt_log({"err":"vosk", "msg":str(e)})
            return ""
    else:
        try:
            from openai import OpenAI
            import tempfile, wave
            client_oa = OpenAI(api_key=OPENAI_API_KEY)
            # scrivi WAV temporaneo
            fd, path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
                wf.writeframes((samples * 32767.0).astype(np.int16).tobytes())
            with open(path, "rb") as f:
                tr = client_oa.audio.transcriptions.create(model="whisper-1", file=f, language="it")
            try: os.remove(path)
            except: pass
            return (tr.text or "").strip().lower()
        except Exception as e:
            mqtt_log({"err":"openai", "msg":str(e)})
            return ""

# ---- Intent matching â†’ azioni ----
def do_publish(topic, payload):
    print(topic, json.dumps(payload))
    client.publish(topic, json.dumps(payload))

def do_cec(args):
    if not CEC_ENABLED: return
    try: subprocess.run(["cec-client","-s","-d","1"], input=args.encode(), check=False)
    except: pass

def handle_intent(text: str):
    publish_state(SENSOR_ASR, text_asr)

    t = text.lower().strip()
    # --- ROOM extraction (sinonimi + articoli) ---
    ROOMS = {
        "salotto": "salotto", "soggiorno": "salotto", "living": "salotto",
        "cucina": "cucina",
        "camera": "camera", "camera da letto": "camera", "stanza": "camera"
    }
    # cattura forme con articoli/preposizioni: "del/della/dello/in/sul ..."
    m_room = re.search(r"(?:del(?:la|lo)?|della|dello|nel(?:la)?|in|sul(?:la)?|in\s+zona|in\s+camera|in\s+salotto|in\s+cucina)?\s*(salotto|soggiorno|living|cucina|camera(?:\s+da\s+letto)?)",
                       t)
    room_key = (m_room.group(1).strip() if m_room else None) if m_room else None
    room = ROOMS.get(room_key, None) if room_key else None

    if re.search(r"\b(luci|luce).*(accend|on)\b", t):
        do_publish(f"{BASE}/voice/actions", {"type":"light","cmd":"on","room":room})
        say("Luci accese.")
        publish_state(SENSOR_INTENT, "light_on")
        publish_state(SENSOR_PAY, json.dumps({"type":"light","cmd":"on","room":room or ""}))
        return

    if re.search(r"\b(luci|luce).*(speng|off)\b", t):
        do_publish(f"{BASE}/voice/actions", {"type":"light","cmd":"off","room":room})
        say("Luci spente.")
        publish_state(SENSOR_INTENT, "light_off")
        publish_state(SENSOR_PAY, json.dumps({"type":"light","cmd":"off","room":room or ""}))
        return

    if not t: 
        say("Non ho capito.")
        return
    # luce/luci ON (ordine libero: accendi... luce | luce... accendi)
    if re.search(r"(?=.*\b(accend(?:i|ere)|on)\b)(?=.*\b(luci?|lampad[ae])\b)", t):
        do_publish(f"{BASE}/voice/actions", {"type":"light","cmd":"on","room":room})
        say("Luci accese.")
        publish_state(SENSOR_INTENT, "light_on")
        publish_state(SENSOR_PAY, json.dumps({"type":"light","cmd":"on","room":room or ""}))
        return

    # luce/luci OFF (ordine libero: spegni... luce | luce... spegni)
    if re.search(r"(?=.*\b(spegn(?:i|ere)|off)\b)(?=.*\b(luci?|lampad[ae])\b)", t):
        do_publish(f"{BASE}/voice/actions", {"type":"light","cmd":"off","room":room})
        say("Luci spente.")
        publish_state(SENSOR_INTENT, "light_off")
        publish_state(SENSOR_PAY, json.dumps({"type":"light","cmd":"off","room":room or ""}))
        return

    # Scene (es. relax/lettura/cinema)
    m = re.search(r"\b(scena|scenario)\s+(relax|lettura|cinema)\b", t)
    if m:
        scene = m.group(2)
        do_publish(f"{BASE}/actions", {"type":"scene","name":scene})
        say(f"Scena {scene} attivata.")
        publish_state(SENSOR_INTENT, f"scene_{scene}")
        publish_state(SENSOR_PAY, json.dumps({"type":"scene","name":scene}))
        return

    # TV base
    if "accendi tv" in t or "accendi la tv" in t:
        do_cec("on 0\n")
        say("Ho acceso la televisione.")
        publish_state(SENSOR_INTENT, "tv_power_on")  # o tv_power_off / tv_vol_up / tv_vol_down / tv_hdmi
        publish_state(SENSOR_PAY, json.dumps({"type":"tv","cmd":"power_on"}))
        # per HDMI (opzionale):
        # publish_state(SENSOR_INTENT, f"tv_hdmi_{n}")
        # publish_state(SENSOR_PAY, json.dumps({"type":"tv","cmd":"hdmi","hdmi":n}))
        return
    if "spegni tv" in t or "spegni la tv" in t:
        do_cec("standby 0\n")
        say("Ho spento la televisione.")
        return
    if re.search(r"\b(volume)\s*(su|giÃ¹|giu|alza|abbassa)\b", t):
        # esempio: manda comando cec di volume via TV (varia per marca)
        do_cec("volup\n" if ("su" in t or "alza" in t) else "voldown\n")
        say("Volume regolato.")
        return
    if re.search(r"\b(sorgente|hdmi)\s*(1|2|3|4)\b", t):
        n = re.search(r"(1|2|3|4)", t).group(1)
        # mapping indicativo
        do_cec(f"tx 10:44:{int(n)+3:02x}\n")  # command routing change (dipende dal TV)
        say(f"Sorgente HDMI {n}.")
        return

    # Buonanotte
    if "buonanotte" in t:
        do_publish(f"{BASE}/actions", {"type":"goodnight"})
        say("Buonanotte. Spengo le luci e metto tutto in sicurezza.")
        publish_state(SENSOR_INTENT, "goodnight")
        publish_state(SENSOR_PAY, json.dumps({"type":"goodnight"}))
        return

    # Allarme
    if "arma allarme" in t:
        do_publish(f"{BASE}/actions", {"type":"alarm","cmd":"arm_away"})
        say("Allarme inserito.")
        publish_state(SENSOR_INTENT, "alarm_arm_away")  # o alarm_disarm
        publish_state(SENSOR_PAY, json.dumps({"type":"alarm","cmd":"arm_away"}))
        return
    if "disarma allarme" in t:
        do_publish(f"{BASE}/actions", {"type":"alarm","cmd":"disarm"})
        say("Allarme disinserito.")
        return

    # Fallback
    do_publish(f"{BASE}/nlu/unmatched", {"text": t})
    publish_state(SENSOR_INTENT, "unmatched")
    publish_state(SENSOR_PAY, json.dumps({"text": t}))

    say("Comando non riconosciuto.")

# ---- Audio loop: wake capture comando ASR intent ----
audio_q = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    if status: pass
    # float32 [-1..1]
    audio_q.put(indata.copy())

def capture_command(sec=5, start_rms=0.012, hold_ms=900): #per squelch
#def capture_seconds(sec=8):
    """Raccoglie sec secondi in float32 mono @ SAMPLE_RATE."""
    need = int(sec * SAMPLE_RATE)
    buf = np.empty((0,1), dtype=np.float32)
    t0 = time.time()
    while buf.shape[0] < need and (time.time()-t0) < sec+1.0:
        try:
            chunk = audio_q.get(timeout=0.5)
            buf = np.vstack([buf, chunk])
        except queue.Empty:
            pass
    if buf.size == 0:
        return np.zeros((need,1), dtype=np.float32)
    return buf[:need]

def run_loop(): #per squelch
    global SESSION_ACTIVE, COOLDOWN_UNTIL
    last_wake_ms = 0
    print("[VOICE] in ascoltoÂ… pronuncia la parola di attivazione:", WAKEWORD)
    while True:
        try:
            data = audio_q.get(timeout=1.0)  # np.float32 (N,1)
        except queue.Empty:
            continue

        now = time.time()
        # niente wake se: stiamo parlando noi (squelch), siamo in sessione, o in cooldown
        if now < SQUELCH_UNTIL or SESSION_ACTIVE or now < COOLDOWN_UNTIL:
            continue

        # wake-detect attivo solo fuori sessione
        pcm16 = (data.flatten() * 32767.0).astype(np.int16).tobytes()
        if wake_detect(pcm16):
            now_ms = int(now * 1000)
            if now_ms - last_wake_ms < WAKE_DEBOUNCE_MS:
                continue
            last_wake_ms = now_ms

            SESSION_ACTIVE = True
            mqtt_log({"wake": True, "ts": now})
            print("[WAKE] rilevato:", WAKEWORD)

            say("Dimmi pure.")

            # cattura con VOX
            samples = capture_command().flatten()

            # se troppo corto / silenzio: non re-invitarlo subito
            if samples.size < int(0.4 * SAMPLE_RATE):
                print("[ASR] troppo corto / silenzio")
                mqtt_log({"asr":"", "reason":"short"})
                COOLDOWN_UNTIL = time.time() + 1.5  # breve pausa prima di nuova wake
                SESSION_ACTIVE = False
                # opzionale: niente voce per evitare loop
                # say("Non ho sentito bene. Riprova.")
                # svuota eventuali residui
                try:
                    for _ in range(3): audio_q.get_nowait()
                except queue.Empty:
                    pass
                continue

            text = asr_transcribe(samples)
            print("[ASR]", text)
            mqtt_log({"asr": text})
            if not text:
                COOLDOWN_UNTIL = time.time() + 1.5
                SESSION_ACTIVE = False
                # say("Non ho capito.")  # se vuoi, ma puÃ² creare eco
                continue

            # esegui intent
            try:
                handle_intent(text)
            finally:
                # piccolo cooldown post-comando per evitare retrigger da rumore/eco
                COOLDOWN_UNTIL = time.time() + 1.0
                SESSION_ACTIVE = False
                # svuota la coda per sicurezza
                t_end = time.time() + 0.3
                while time.time() < t_end:
                    try: audio_q.get_nowait()
                    except queue.Empty: break

##def run_loop():
##    last_wake = 0
##    print("[VOICE] in ascolto: pronuncia la parola di attivazione:", WAKEWORD)
##    while True:
##        try:
##            data = audio_q.get(timeout=1.0)   # np.float32 [-1..1], shape (N,1)
##        except queue.Empty:
##            continue
##
##        # converte a PCM16 bytes per Vosk
##        pcm16 = (data.flatten() * 32767.0).astype(np.int16).tobytes()
##
##        if wake_detect(pcm16):
##            now_ms = time.time()*1000
##            if now_ms - last_wake < WAKE_DEBOUNCE_MS:   # evita doppi trigger ravvicinati
##                continue
##            last_wake = now_ms
##
##            print("[WAKE] rilevato:", WAKEWORD)
##            mqtt_log({"wake": True, "ts": time.time()})
##            say("Dimmi pure.")
##
##            samples = capture_seconds(5.0).flatten()
##            text = asr_transcribe(samples)  # Vosk (full) o Whisper (online), come giÃ  fai
##            print("[ASR]", text)
##            publish_state(SENSOR_ASR, text or "")
##
##            mqtt_log({"asr": text})
##            handle_intent(text)




if __name__ == "__main__":
    stream_kwargs = {
        "samplerate": SAMPLE_RATE,
        "channels": 1,
        "dtype": "float32",
        "callback": audio_callback,
        "blocksize": 1024,
        "latency": "low",
    }
    if INPUT_DEVICE_INDEX >= 0:
        stream_kwargs["device"] = INPUT_DEVICE_INDEX

    with sd.InputStream(**stream_kwargs):
        run_loop()
