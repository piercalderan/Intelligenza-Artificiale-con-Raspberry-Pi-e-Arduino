# assistente_bmp180_tts.py â€” BMP180 via SMBus â†’ MQTT + HA Discovery + OpenAI TTS
import os, json, time, sys, signal, math, subprocess
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from smbus2 import SMBus

# ---------- Config ----------
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DISC_PREFIX = "homeassistant"
BASE = "home/assistant"
TOPIC_STATE = f"{BASE}/bmp180"
TOPIC_AVAIL = f"{BASE}/status"
TOPIC_ERR = f"{BASE}/errors"
TTS_PROVIDER="gtts"

PUBLISH_EVERY = int(os.getenv("PUBLISH_EVERY", "10"))      # s
SPEAK_EVERY   = int(os.getenv("SPEAK_EVERY", "60"))        # s (0 = disabilitato)
I2C_BUS       = int(os.getenv("I2C_BUS", "1"))
I2C_ADDR      = int(os.getenv("I2C_ADDR", "0x77"), 16)
OSS           = int(os.getenv("BMP180_OSS", "1"))          # 0..3
#SEA_LEVEL_HPA = float(os.getenv("SEA_LEVEL_HPA", ""))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SEA_LEVEL_HPA=1016.80
OPENAI_TTS_MODEL="tts-1"
OPENAI_TTS_VOICE_IT="alloy"
OPENAI_API_KEY="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"



# ---------- BMP180 registers ----------
REG_CHIP_ID=0xD0; REG_CONTROL=0xF4
REG_DATA_MSB=0xF6; REG_DATA_LSB=0xF7; REG_DATA_XLSB=0xF8
CMD_TEMP=0x2E
def CMD_PRESSURE(oss): return 0x34 + (oss<<6)
PRESSURE_WAIT_MS=[5,8,14,26]

# ---------- BMP180 driver (SMBus puro) ----------
class BMP180:
    def __init__(self, bus: SMBus, addr: int, oss: int = 0):
        self.bus, self.addr = bus, addr
        self.oss = max(0, min(3, int(oss)))
        chip = self.bus.read_byte_data(self.addr, REG_CHIP_ID)
        if chip != 0x55:
            raise RuntimeError(f"Chip-ID 0x{chip:02X} (atteso 0x55)")

        self.AC1 = self._read_s16(0xAA); self.AC2 = self._read_s16(0xAC); self.AC3 = self._read_s16(0xAE)
        self.AC4 = self._read_u16(0xB0); self.AC5 = self._read_u16(0xB2); self.AC6 = self._read_u16(0xB4)
        self.B1  = self._read_s16(0xB6); self.B2  = self._read_s16(0xB8)
        self.MB  = self._read_s16(0xBA); self.MC  = self._read_s16(0xBC); self.MD  = self._read_s16(0xBE)

    def _read_u16(self, reg):
        d=self.bus.read_i2c_block_data(self.addr, reg, 2); return (d[0]<<8)|d[1]
    def _read_s16(self, reg):
        v=self._read_u16(reg); return v-65536 if v>32767 else v

    def _read_uncomp_temp(self, timeout_ms=60):
        self.bus.write_byte_data(self.addr, REG_CONTROL, CMD_TEMP)
        t0=time.time()
        while True:
            time.sleep(0.006)
            try:
                d=self.bus.read_i2c_block_data(self.addr, REG_DATA_MSB,2)
                ut=(d[0]<<8)|d[1]
                if ut!=0: return ut
            except OSError: pass
            if (time.time()-t0)*1000>timeout_ms:
                d=self.bus.read_i2c_block_data(self.addr, REG_DATA_MSB,2)
                return (d[0]<<8)|d[1]

    def _read_uncomp_press(self, timeout_ms=100):
        self.bus.write_byte_data(self.addr, REG_CONTROL, CMD_PRESSURE(self.oss))
        t0=time.time(); base_wait=PRESSURE_WAIT_MS[self.oss]/1000.0+0.004
        while True:
            time.sleep(base_wait)
            try:
                d=self.bus.read_i2c_block_data(self.addr, REG_DATA_MSB,3)
                up=((d[0]<<16)|(d[1]<<8)|d[2])>>(8-self.oss)
                if up!=0: return up
            except OSError: pass
            if (time.time()-t0)*1000>timeout_ms:
                d=self.bus.read_i2c_block_data(self.addr, REG_DATA_MSB,3)
                return ((d[0]<<16)|(d[1]<<8)|d[2])>>(8-self.oss)

    def read(self):
        # --- temperatura ---
        UT=self._read_uncomp_temp()
        X1=((UT-self.AC6)*self.AC5)/(1<<15)
        denom=(X1+self.MD)
        X2=(self.MC*(1<<11))/denom if denom!=0 else 0.0
        B5=X1+X2
        temp_c=(B5+8)/(1<<4)/10.0

        # --- pressione (integer math) ---
        UP=self._read_uncomp_press()
        B6=int(B5-4000)
        X1p=int((self.B2*((B6*B6)>>12))>>11)
        X2p=int((self.AC2*B6)>>11)
        X3=X1p+X2p
        B3=int(((((int(self.AC1)*4)+int(X3))<<self.oss)+2)>>2)
        X1p=int((self.AC3*B6)>>13)
        X2p=int((self.B1*((B6*B6)>>12))>>16)
        X3=int((X1p+X2p+2)>>2)
        B4=int((int(self.AC4)*(X3+32768))>>15)
        B7=int((int(UP)-B3)*(50000>>self.oss))
        if B7<0x80000000: p=(B7<<1)//B4
        else:             p=(B7//B4)<<1
        X1p=(p>>8)*(p>>8)
        X1p=int((X1p*3038)>>16)
        X2p=int((-7357*p)>>16)
        p=p+((X1p+X2p+3791)>>4)  # Pa
        press_hpa=p/100.0

        # Altitudine simulata rispetto a SEA_LEVEL_HPA (QNH)
        try:
            alt_sim_m = 44330.0 * (1.0 - (press_hpa / SEA_LEVEL_HPA) ** (1.0/5.255))
        except Exception:
            alt_sim_m = None
        print(round(temp_c,1), round(press_hpa,1))

        return round(temp_c,1), round(press_hpa,1), (None if alt_sim_m is None else round(alt_sim_m,1))

# ---------- MQTT ----------

client = mqtt.Client(client_id="assistant-bmp180-tts", protocol=mqtt.MQTTv311, transport="tcp")

def on_connect(c,u,f,rc):
    dev={"identifiers":["assistant_bmp180"],"name":"Assistente BMP180","manufacturer":"Bosch","model":"BMP180"}
    # Temperatura
    c.publish(f"{DISC_PREFIX}/sensor/assistant_temperature/config", json.dumps({
        "name":"Assistente Temperatura","uniq_id":"assistant_bmp180_temp",
        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.temp_c }}",
        "unit_of_meas":"Â°C","dev_cla":"temperature","state_class":"measurement",
        "avty_t":TOPIC_AVAIL,"device":dev
    }), retain=True)
    # Pressione
    c.publish(f"{DISC_PREFIX}/sensor/assistant_pressure/config", json.dumps({
        "name":"Assistente Pressione","uniq_id":"assistant_bmp180_press",
        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.press_hpa }}",
        "unit_of_meas":"hPa","dev_cla":"pressure","state_class":"measurement",
        "avty_t":TOPIC_AVAIL,"device":dev
    }), retain=True)
    # Altitudine simulata (da QNH)
    c.publish(f"{DISC_PREFIX}/sensor/assistant_altitude/config", json.dumps({
        "name":"Assistente Altitudine","uniq_id":"assistant_bmp180_alt",
        "stat_t":TOPIC_STATE,"val_tpl":"{{ value_json.alt_sim_m }}",
        "unit_of_meas":"m","dev_cla":"distance","state_class":"measurement",
        "avty_t":TOPIC_AVAIL,"device":dev
    }), retain=True)
    c.publish(TOPIC_AVAIL, "online", retain=True)

client.on_connect = on_connect
client.connect(MQTT_HOST, MQTT_PORT, 30)
client.loop_start()

##def publish_err(where, e):
##    client.publish(TOPIC_ERR, json.dumps({"where":where,"error":str(e),"ts":datetime.now(timezone.utc).isoformat()}))

# ---------- OpenAI TTS ----------
def publish_err(where, e):
    # stampa anche a console per debug rapido
    try:
        import traceback
        print(f"[TTS-ERR] {where}: {e}", flush=True)
        traceback.print_exc()
    except Exception:
        pass
    client.publish(TOPIC_ERR, json.dumps({"where": where, "error": str(e), "ts": datetime.now(timezone.utc).isoformat()}))

def speak_it(text: str, voice_it: str = "alloy"):
    """
    TTS con selettore provider:
    - gtts: usa Google TTS (IT naturale)
    - openai: usa OpenAI tts-1 / tts-1-hd
    - auto: prova OpenAI, se fallisce cade su gTTS
    """
    out = "/tmp/tts_out.mp3"

    def _play(p):
        subprocess.run(["mpg123", "-q", p], check=False)

    def _gtts():
        from gtts import gTTS
        gTTS(text=text, lang="it").save(out)
        _play(out)

    def _openai():
        from openai import OpenAI
        import inspect
        client_oa = OpenAI(api_key=OPENAI_API_KEY)
        create_fn = client_oa.audio.speech.create
        # autodetect nome parametro per formato
        try:
            params = inspect.signature(create_fn).parameters
            if "format" in params: fmt_kw = "format"
            elif "response_format" in params: fmt_kw = "response_format"
            elif "output_format" in params: fmt_kw = "output_format"
            else: fmt_kw = None
        except Exception:
            fmt_kw = "format"
        candidates = [m for m in [OPENAI_TTS_MODEL, "tts-1", "tts-1-hd"] if m]
        last_err = None
        for model in candidates:
            try:
                kwargs = {"model": model, "voice": OPENAI_TTS_VOICE_IT, "input": text}
                if fmt_kw: kwargs[fmt_kw] = "mp3"
                resp = create_fn(**kwargs)
                # estrai bytes
                audio_bytes = getattr(resp, "read", None)() if hasattr(resp, "read") \
                              else getattr(resp, "content", None) \
                              or (bytes(resp) if not hasattr(resp, "content") else None)
                if not audio_bytes:
                    raise RuntimeError("OpenAI TTS: risposta senza audio")
                with open(out, "wb") as f:
                    f.write(audio_bytes)
                _play(out)
                return
            except Exception as e:
                last_err = e
                continue
        if last_err: raise last_err

    try:
        if TTS_PROVIDER == "gtts":
            _gtts()
        elif TTS_PROVIDER == "openai":
            if not OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY mancante")
            _openai()
        else:  # auto
            if OPENAI_API_KEY:
                try:
                    _openai(); return
                except Exception as e:
                    print(f"[TTS] OpenAI fallito, passo a gTTS: {e}", flush=True)
            _gtts()
    except Exception as e:
        publish_err("tts", e)


##
##def speak_it(text: str, voice_it: str = "alloy"):
##    """
##    OpenAI TTS robusto (no streaming):
##    - prova il modello dall'ENV, poi 'tts-1', poi 'tts-1-hd'
##    - autodetect del parametro per il formato mp3
##    - scrive su /tmp/tts_out.mp3 e riproduce con mpg123
##    """
##    out = "/tmp/tts_out.mp3"
##    if not OPENAI_API_KEY:
##        print("[TTS] OPENAI_API_KEY non impostata; salto TTS", flush=True)
##        return
##
##    try:
##        from openai import OpenAI, __version__ as oa_ver
##        import inspect
##        print(f"[TTS] OpenAI SDK version: {oa_ver}", flush=True)
##        client_oa = OpenAI(api_key=OPENAI_API_KEY)
##
##        # ordine di tentativi modello
##        candidates = []
##        if os.getenv("OPENAI_TTS_MODEL"):
##            candidates.append(os.getenv("OPENAI_TTS_MODEL"))
##        candidates += ["tts-1", "tts-1-hd"]
##
##        # rileva come si chiama il parametro per il formato nella tua versione SDK
##        create_fn = client_oa.audio.speech.create
##        try:
##            params = inspect.signature(create_fn).parameters
##            if "format" in params:
##                fmt_kw = "format"
##            elif "response_format" in params:
##                fmt_kw = "response_format"
##            elif "output_format" in params:
##                fmt_kw = "output_format"
##            else:
##                fmt_kw = None
##        except Exception:
##            fmt_kw = "format"
##
##        last_err = None
##        for model in candidates:
##            try:
##                print(f"[TTS] Provo modello: {model}", flush=True)
##                kwargs = {"model": model, "voice": os.getenv("OPENAI_TTS_VOICE_IT", voice_it), "input": text}
##                if fmt_kw:
##                    kwargs[fmt_kw] = "mp3"
##                resp = create_fn(**kwargs)
##
##                # estrai bytes audio
##                audio_bytes = None
##                if hasattr(resp, "read"):
##                    audio_bytes = resp.read()
##                elif hasattr(resp, "content"):
##                    audio_bytes = resp.content
##                elif isinstance(resp, (bytes, bytearray)):
##                    audio_bytes = bytes(resp)
##                else:
##                    try:
##                        audio_bytes = bytes(resp)
##                    except Exception:
##                        audio_bytes = None
##
##                if not audio_bytes:
##                    raise RuntimeError("OpenAI TTS: risposta senza audio (bytes)")
##
##                with open(out, "wb") as f:
##                    f.write(audio_bytes)
##
##                print(f"[TTS] Playing {out}", flush=True)
##                subprocess.run(["mpg123", "-q", out], check=False)
##                return  # successo con questo modello
##
##            except Exception as e:
##                print(f"[TTS] Modello {model} fallito: {e}", flush=True)
##                last_err = e
##                continue
##
##        # se siamo qui, tutti i modelli hanno fallito
##        raise last_err if last_err else RuntimeError("OpenAI TTS: nessun modello ha funzionato")
##
##    except Exception as e:
##        publish_err("tts-openai", e)
##


# ---------- Main ----------
def sigterm(*_):
    try: client.publish(TOPIC_AVAIL,"offline",retain=True)
    except: pass
    sys.exit(0)

signal.signal(signal.SIGINT, sigterm)
signal.signal(signal.SIGTERM, sigterm)

last_spoken = 0
with SMBus(I2C_BUS) as bus:
    try:
        chip=bus.read_byte_data(I2C_ADDR, REG_CHIP_ID)
        if chip!=0x55: raise RuntimeError(f"Chip-ID 0x{chip:02X} (atteso 0x55)")
        sensor=BMP180(bus, I2C_ADDR, oss=OSS)
    except Exception as e:
        publish_err("init", e); sigterm()

    while True:
        try:
            temp_c, press_hpa, alt_sim_m = sensor.read()
            payload = {
                "temp_c": temp_c,
                "press_hpa": press_hpa,
                "sea_level_hpa": round(SEA_LEVEL_HPA,2),
                "alt_sim_m": alt_sim_m,
                "ts": datetime.now(timezone.utc).isoformat()
            }
            client.publish(TOPIC_STATE, json.dumps(payload))

            # Annuncio vocale ogni SPEAK_EVERY secondi (se API key presente)
            now = time.time()
            if OPENAI_API_KEY and SPEAK_EVERY > 0 and (now - last_spoken) >= SPEAK_EVERY:
                temp_str = f"{temp_c:.1f}".replace('.', ',')
                pres_str = f"{press_hpa:.1f}".replace('.', ',')
                if alt_sim_m is not None:
                    alt_str = f"{alt_sim_m:.0f}".replace('.', ',')  # interi per naturalezza
                    # frasetta pulita: “a meno 31 metri” se negativa
                    if alt_sim_m < 0:
                        msg = f"La temperatura è {temp_str} gradi, la pressione è {pres_str} ettopascal e l'altitudine simulata è a meno {alt_str.lstrip('-')} metri."
                    else:
                        msg = f"La temperatura è {temp_str} gradi, la pressione è {pres_str} ettopascal e l'altitudine simulata è {alt_str} metri."
                else:
                    msg = f"La temperatura è {temp_str} gradi e la pressione è {pres_str} ettopascal."
                speak_it(msg)
                last_spoken = now


##            if OPENAI_API_KEY and SPEAK_EVERY > 0 and (now - last_spoken) >= SPEAK_EVERY:
##                # Frase naturale in italiano
##                pres_str = f"{press_hpa:.1f}".replace('.', ',')
##                temp_str = f"{temp_c:.1f}".replace('.', ',')
##                msg = f"La temperatura Ã¨ {temp_str} gradi e la pressione Ã¨ {pres_str} ettopascal."
##                speak_it(msg)
##                last_spoken = now

        except Exception as e:
            publish_err("loop", e)
        time.sleep(PUBLISH_EVERY)
