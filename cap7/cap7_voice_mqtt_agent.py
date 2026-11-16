# voice_mqtt_agent.py
import os, json, time, base64, threading, subprocess, re
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import sounddevice as sd
from vosk import Model, KaldiRecognizer
import openai as openai_sdk

# ============ CONFIG ============
load_dotenv()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()


MQTT_HOST = os.getenv("MQTT_HOST","127.0.0.1")

MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TB_DEV    = os.getenv("TOPIC_BASE_DEVICE", "lab/arduino1")
TB_V      = os.getenv("TOPIC_BASE_VOICE",  "lab/voice")

VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", os.path.expanduser("~/models/vosk-model-small-it-0.22"))
AUDIO_HOST = (os.getenv("AUDIO_HOST") or "").lower()     # "pulse" | "alsa" | ""
MIC_DEVICE_ENV = os.getenv("MIC_DEVICE")                 # "1" oppure "webcam"
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))

openai_sdk.api_key = OPENAI_API_KEY

# topics
T_PTT       = "lab/voice/ptt"
T_STT       = "lab/voice/stt"
T_NLU       = "lab/voice/nlu"
T_REPLY     = "lab/voice/reply"
T_TTS_AUDIO = "lab/voice/tts_audio"
T_TTS_TEXT  = "lab/voice/tts_text"
T_STATUS    = "lab/voice/status"
T_DEBUG     = "lab/voice/debug"
T_LED_ANY   = f"lab/arduino1/cmd/led/{{i}}"
T_SENSOR    = "lab/arduino1/cmd/sensor"
T_SENSOR_OUT= "lab/arduino1/sensor"

print(T_PTT)
print(T_STT)
print(T_NLU)
print(T_REPLY)
print(T_TTS_AUDIO)
print(T_TTS_TEXT)
print(T_STATUS)
print(T_DEBUG)
print(T_LED_ANY)
print(T_SENSOR)
print(T_SENSOR_OUT)



# memoria ultima lettura sensore
last_sensor = {"t": None, "h": None, "ts": 0.0}

# ============ MQTT ============
cli = mqtt.Client(client_id="pi-voice-agent", clean_session=True, protocol=mqtt.MQTTv311)
cli.will_set(T_STATUS, payload="offline", qos=1, retain=True)

def pub(topic, payload, qos=1, retain=False, as_json=False):
    try:
        if as_json and not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        cli.publish(topic, payload, qos=qos, retain=retain)
    except Exception as e:
        print("MQTT publish error:", e)

def on_connect(cli, userdata, flags, rc, props=None):
    print("MQTT connected rc=", rc)
    # subscribe qui (garantito connesso)
    cli.subscribe([
        (T_PTT, 1),
        (T_TTS_TEXT, 1),
        (T_SENSOR_OUT, 1),
    ])
    cli.publish(T_STATUS, "online", qos=1, retain=True)

def on_message(cli, userdata, msg):
    print(msg)
    global last_sensor
    try:
        payload_raw = (msg.payload or b"").decode("utf-8", "ignore").strip()
        if msg.topic == T_PTT:
            if payload_raw.upper() == "START":
                print("PTT START"); agent.start()
            elif payload_raw.upper() == "STOP":
                print("PTT STOP"); agent.stop()
        elif msg.topic == T_TTS_TEXT:
            agent.say(payload_raw)
        elif msg.topic == T_SENSOR_OUT:
            try:
                data = json.loads(payload_raw)
                if isinstance(data, dict) and "t" in data and "h" in data:
                    last_sensor["t"]  = float(data["t"])
                    last_sensor["h"]  = float(data["h"])
                    last_sensor["ts"] = time.time()
            except Exception:
                pass
    except Exception as e:
        print("MQTT msg err:", e)

cli.on_connect = on_connect
cli.on_message = on_message
cli.connect_async(MQTT_HOST, MQTT_PORT, 30)
cli.loop_start()

# ============ AUDIO UTILS ============
def resolve_hostapi(prefer: str | None):
    try:
        has = sd.query_hostapis()
        if prefer:
            p = prefer.lower()
            for i, ha in enumerate(has):
                name = (ha.get("name") or "").lower()
                if p in name:
                    return i
    except Exception:
        pass
    return None

def resolve_input_device(selector):
    try:
        devs = sd.query_devices()
        if isinstance(selector, int):
            if 0 <= selector < len(devs) and devs[selector].get("max_input_channels",0) > 0:
                return selector
        if isinstance(selector, str) and selector.strip():
            s = selector.strip().lower()
            for i, d in enumerate(devs):
                if d.get("max_input_channels",0) > 0:
                    name = (d.get("name") or "").lower()
                    if s in name:
                        return i
    except Exception:
        pass
    return None

def mute_output(on: bool):
    try:
        subprocess.run(["pactl","set-sink-mute","@DEFAULT_SINK@", "1" if on else "0"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        try:
            subprocess.run(["amixer","-q","set","Master","mute" if on else "unmute"],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

# ============ OPENAI HELPERS ============
def tts_mp3_bytes(text: str, voice="alloy"):
    resp = openai_sdk.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice=voice,
        input=text
    )
    return resp.read()

SYSTEM_PROMPT = """You are a command parser. Italian input. Output ONLY valid JSON:
{"commands":[{"type":"led","index":1,"action":"on"}]}
Allowed types: "led","sensor".
For led: index in [1,2,3], action in ["on","off","blink","blink off"].
For sensor: action "read".
If multiple intents, include multiple command objects. No extra text."""
def nlu_parse(phrase: str):
    try:
        r = openai_sdk.chat.completions.create(
            model="gpt-4o-mini", temperature=0,
            messages=[{"role":"system","content":SYSTEM_PROMPT},
                      {"role":"user","content":phrase}]
        )
        txt = r.choices[0].message.content
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0) if m else txt)
        return data.get("commands", [])
    except Exception as e:
        print("NLU error:", e)
        return []

def confirmation_phrase(cmds):
    """Ritorna (frase_LED, has_sensor)."""
    parts = []
    has_sensor = False
    for c in cmds:
        if c.get("type") == "led":
            idx = int(c.get("index", 1))
            act = (str(c.get("action","on")).lower())
            if act == "on":
                parts.append(f"Accendo il LED {idx}.")
            elif act == "off":
                parts.append(f"Spengo il LED {idx}.")
            elif act == "blink":
                parts.append(f"Faccio lampeggiare il LED {idx}.")
            elif act == "blink off":
                parts.append(f"Stop lampeggio sul LED {idx}.")
        elif c.get("type") == "sensor":
            has_sensor = True
    phrase = " ".join(parts) if parts else "Eseguo il comando."
    return phrase, has_sensor

def speak_sensor_when_ready(agent, timeout=3.0):
    """Attende una lettura recente del sensore e la pronuncia."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if last_sensor["ts"] and (time.time() - last_sensor["ts"] < 2.5):
            t = last_sensor["t"]; h = last_sensor["h"]
            if t is not None and h is not None:
                agent.say(f"Temperatura {t:.1f} gradi, umidità {h:.0f} percento.")
                return
        time.sleep(0.1)
    agent.say("Richiesta del sensore inviata.")

# ============ SPEECH PTT AGENT ============
class SpeechPTT:
    def __init__(self):
        self.model = None
        self.rec = None
        self.stream = None
        self.running = False
        self.tts_queue = []
        self.voice = "alloy"

        self.host_pref = AUDIO_HOST if AUDIO_HOST else None
        self.device_sel = None
        if MIC_DEVICE_ENV:
            self.device_sel = int(MIC_DEVICE_ENV) if MIC_DEVICE_ENV.isdigit() else MIC_DEVICE_ENV

        self.stop_event = threading.Event()
        self.lock = threading.Lock()

    def load_model(self):
        if not os.path.isdir(VOSK_MODEL_PATH):
            print("Modello Vosk non trovato:", VOSK_MODEL_PATH)
            return False
        self.model = Model(VOSK_MODEL_PATH)
        self.rec = KaldiRecognizer(self.model, SAMPLE_RATE)
        return True

    def start(self):
        with self.lock:
            if self.running: return
            if self.model is None and not self.load_model():
                return
            self.stop_event.clear()
            mute_output(True)
            try:
                ha_index = resolve_hostapi(self.host_pref) if self.host_pref else None
                if ha_index is not None:
                    sd.default.hostapi = ha_index

                dev_index = resolve_input_device(self.device_sel)
                sd.default.samplerate = SAMPLE_RATE

                self.stream = sd.InputStream(
                    device=dev_index,    # None = default
                    channels=1,
                    samplerate=SAMPLE_RATE,
                    dtype='int16',
                    blocksize=0
                )
                self.stream.start()
                self.running = True
            except Exception as e:
                print("Audio start error:", e)
                mute_output(False)

    def stop(self):
        with self.lock:
            if not self.running: return
            self.stop_event.set()
            try:
                if self.stream:
                    try:
                        self.stream.abort()   # interrompe read() senza errori ALSA
                    except Exception:
                        pass
                    self.stream.stop()
                    self.stream.close()
            finally:
                self.stream = None
                self.running = False

        # unmute + flush TTS fuori dal lock
        def unmute_and_flush():
            mute_output(False)
            time.sleep(0.25)
            while self.tts_queue:
                say = self.tts_queue.pop(0)
                try:
                    b = tts_mp3_bytes(say, self.voice)
                    pub(T_TTS_AUDIO, {"audio_b64": base64.b64encode(b).decode("ascii")}, as_json=True)
                    pub(T_REPLY, say)
                except Exception as e:
                    print("TTS flush error:", e)
        threading.Thread(target=unmute_and_flush, daemon=True).start()

    def say(self, text: str):
        if not text: return
        if self.running:
            self.tts_queue.append(text)
        else:
            try:
                b = tts_mp3_bytes(text, self.voice)
                pub(T_TTS_AUDIO, {"audio_b64": base64.b64encode(b).decode("ascii")}, as_json=True)
                pub(T_REPLY, text)
            except Exception as e:
                print("TTS speak error:", e)

    def loop(self):
        while True:
            if not self.running or not self.stream:
                time.sleep(0.05)
                continue
            try:
                if self.stop_event.is_set():
                    time.sleep(0.01)
                    continue

                data, _ = self.stream.read(4000)
                if len(data) == 0:
                    time.sleep(0.01); continue

                if self.rec.AcceptWaveform(data.tobytes()):
                    res = json.loads(self.rec.Result())
                    text = (res.get("text") or "").strip()
                    if text:
                        pub(T_STT, text)
                        cmds = nlu_parse(text)
                        pub(T_NLU, {"commands": cmds}, as_json=True)

                        asked_sensor = False
                        for c in cmds:
                            if c.get("type") == "led":
                                idx = int(c.get("index",1))
                                act = str(c.get("action","on")).upper()
                                if act in ("ON","OFF","BLINK","BLINK OFF"):
                                    pub(T_LED_ANY.format(i=idx), act)
                            elif c.get("type") == "sensor":
                                asked_sensor = True
                                print("PUB →", T_SENSOR, "READ")
                                pub(T_SENSOR, "READ")
                                # alias hard-coded di sicurezza (se TB_DEV differisse)
                                if T_SENSOR != "lab/arduino1/cmd/sensor":
                                    pub("lab/arduino1/cmd/sensor", "READ")
                                pub(T_DEBUG, {"sent":"sensor_read","topic":T_SENSOR}, as_json=True)

                        if cmds:
                            phrase, has_sensor = confirmation_phrase(cmds)
                            if phrase:
                                self.say(phrase)
                            if asked_sensor or has_sensor:
                                threading.Thread(target=speak_sensor_when_ready,
                                                 args=(self, 3.0), daemon=True).start()
                        else:
                            self.say("Non ho capito. Riprova.")
            except Exception as e:
                if not self.stop_event.is_set():
                    print("Audio loop:", e)
                self.stop()

# ============ RUN ============
agent = SpeechPTT()
thr = threading.Thread(target=agent.loop, daemon=True); thr.start()

print("Voice MQTT Agent ready.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    agent.stop()
    cli.loop_stop()
    cli.publish(T_STATUS, "offline", qos=1, retain=True)
