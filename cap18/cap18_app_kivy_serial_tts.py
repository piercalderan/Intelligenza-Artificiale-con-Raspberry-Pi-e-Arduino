import os, sys, threading, time, queue, subprocess, platform
from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock
from kivy.properties import NumericProperty, StringProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.core.window import Window
from kivy.uix.slider import Slider
import serial, serial.tools.list_ports
from gtts import gTTS

# --- OpenAI (opzionale) ---
USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
if USE_OPENAI:
    try:
        from openai import OpenAI
        openai_client = OpenAI()
    except Exception:
        USE_OPENAI = False


# ===============================================================
#  AUDIO PLAYER  (mpg123 / afplay)
# ===============================================================
def play_audio(filepath: str):
    sysname = platform.system()
    try:
        if sysname in ("Windows", "Linux"):
            subprocess.run(["mpg123", "-q", filepath], check=True)
        elif sysname == "Darwin":
            subprocess.run(["afplay", filepath], check=True)
    except Exception as e:
        print("[Audio error]", e)


# ===============================================================
#  TTS  (OpenAI -> gTTS fallback)
# ===============================================================
def tts_speak(text: str, lang="it"):
    if not text:
        return
    outdir = os.path.join(os.path.dirname(__file__), "tts_audio")
    os.makedirs(outdir, exist_ok=True)
    # cleanup vecchi file (>1 giorno)
    now = time.time()
    for f in os.listdir(outdir):
        try:
            fp = os.path.join(outdir, f)
            if os.path.isfile(fp) and now - os.path.getmtime(fp) > 86400:
                os.remove(fp)
        except Exception:
            pass
    fname = f"tts_{int(now*1000)}.mp3"
    fpath = os.path.join(outdir, fname)

    # --- OpenAI ---
    if USE_OPENAI:
        try:
            with open(fpath, "wb") as f:
                audio = openai_client.audio.speech.create(
                    model="gpt-4o-mini-tts",
                    voice="alloy",
                    input=text
                )
                f.write(audio.read())
            play_audio(fpath)
            return
        except Exception as e:
            print("[TTS OpenAI error]", e)

    # --- gTTS fallback ---
    try:
        gTTS(text=text, lang=lang, slow=False).save(fpath)
        play_audio(fpath)
    except Exception as e:
        print("[TTS gTTS error]", e)


# ===============================================================
#  Slider specializzato: un solo rilascio affidabile
# ===============================================================
class ServoSlider(Slider):
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        # Se il grab Ã¨ nostro, Ã¨ un rilascio valido per questo slider
        if touch.grab_current is self:
            touch.ungrab(self)
            # risali al RootUI e chiama on_servo_release
            root = self
            while root is not None and not hasattr(root, "on_servo_release"):
                root = getattr(root, "parent", None)
            if root is not None:
                root.on_servo_release(self.value)
        return super().on_touch_up(touch)


# ===============================================================
#  GUI KV
# ===============================================================
KV = r"""
<ArcGauge@Widget>:
    value: 0
    min: 0
    max: 100
    label: ""
    canvas:
        Color:
            rgba: 0.2, 0.2, 0.2, 1
        Line:
            circle: (self.center_x+100, self.y-100, min(self.width, self.height), -90, 90)
            width: 4
        Color:
            rgba: 0.1, 0.6, 1, 1
        Line:
            circle: (self.center_x+100, self.y-100, min(self.width, self.height), -90, -90 + 180 * ((self.value - self.min)/(self.max - self.min) if self.max>self.min else 0))
            width: 8
    Label:
        text: f"{root.label}: {int(root.value)}"
        font_size: '18sp'
        center_x: root.center_x +100
        center_y: root.y-100 + root.height*0.3
        size_hint: None, None
        size: self.texture_size

<VBar@Widget>:
    value: 0
    min: 0
    max: 100
    label: ""
    canvas:
        Color:
            rgba: 0.2,0.2,0.2,1
        Rectangle:
            pos: self.x + self.width*0.65, self.y-100
            size: self.width*0.3, self.height
        Color:
            rgba: 0.1,0.8,0.3,1
        Rectangle:
            pos: self.x + self.width*0.65, self.y-100
            size: self.width*0.3, self.height * ((self.value - self.min)/(self.max - self.min) if self.max>self.min else 0)
    Label:
        text: f"{root.label}: {int(root.value)}%"
        font_size: '18sp'
        pos: root.center_x+40, root.center_y-10
        #center_x: root.center_x
        #y: root.top - 5
        size_hint: None, None
        size: self.texture_size

<RootUI>:
    orientation: "vertical"
    padding: 10
    spacing: 10

    BoxLayout:        
        size_hint_y: 0.12
        spacing: 10
        ToggleButton:
            id: led_toggle
            text: "LED OFF"
            on_state: root.on_led_toggle(self.state)
        ToggleButton:
            id: voice_toggle
            text: "Voce: OFF"
            on_state: root.on_voice_toggle(self.state)
        Button:
            text: "ASK (aggiorna)"
            on_release: root.on_ask()
        Label:
            id: port_lbl
            text: root.port_status
            halign: "right"
            valign: "middle"
            text_size: self.size

    BoxLayout:        
        size_hint_y: 0.12
        spacing: 10
        
        ToggleButton:            
            id: btn_momentary
            text: "Pulsante (simula pressione)"
            on_state: root.on_virtual_button(self.state)
        Label:
            id: btn_state
            text: "BTN: 0"
        Label:
            id: led_state
            text: "LED: 0"
        Label:
            id: servo_state
            text: "Servo: 90°"

            

    BoxLayout:        
        size_hint_y: 0.4
        spacing: 10
        ArcGauge:
            id: temp_g            
            label: "Temp °C"
            min: -10
            max: 50
        VBar:
            id: hum_b
            label: "Umidità"
            min: 0
            max: 100
        BoxLayout:            
            orientation: "vertical"
            spacing: 8
            ArcGauge:
                id: servo_g
                label: "Servo"
                min: 0
                max: 180
                size_hint_y: 0.7

        Slider:
            id: servo_s            
            min: 0
            max: 180
            value: 90
            on_value: root.on_servo_slide(self.value)   # solo UI live
            on_touch_up:
                root.on_servo_release(self.value) if self.collide_point(*args[1].pos) else None
                

    BoxLayout:
        orientation: "vertical"        
        Label:
            #text: "Log eventi"
            size_hint_y: 0.8
        TextInput:
            id: log
            readonly: True
            size_hint_y: 0.9
"""


# ===============================================================
#  ROOT UI
# ===============================================================
class RootUI(BoxLayout):
    port_status = StringProperty("Porta: (ricerca...)")
    voice_enabled = BooleanProperty(False)
    temp_val = NumericProperty(0)
    hum_val = NumericProperty(0)
    led_val = NumericProperty(0)
    servo_val = NumericProperty(90)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._syncing_led = False   # evita doppi trigger on_state quando sync da codice
        self._servo_last_value = None
        self._servo_last_ts = 0.0   # debounce on_servo_release
        self._servo_last_from_stat = None  # <-- ultimo angolo confermato da Arduino (idempotenza)

    # -------------------------------------------------------
    def on_led_toggle(self, state):
        if self._syncing_led:
            return
        new_v = 1 if state == "down" else 0
        self.ids.led_toggle.text = "LED ON" if new_v else "LED OFF"
        App.get_running_app().mark_local_led_action()
        App.get_running_app().send_cmd(f"LED {new_v}")
        self.log(f"TX: LED {new_v}")
        if self.voice_enabled:
            tts_speak("Ho acceso il LED" if new_v else "Ho spento il LED")

    def on_voice_toggle(self, state):
        self.voice_enabled = (state == "down")
        self.ids.voice_toggle.text = "Voce: ON" if self.voice_enabled else "Voce: OFF"
        if self.voice_enabled:
            tts_speak("Voce attivata in italiano")

    def on_virtual_button(self, state):
        if state == "down":
            new_led = 0 if self.led_val else 1
            App.get_running_app().send_cmd(f"LED {new_led}")
            self.log("TX: (virtual BTN) -> toggle LED")
            if self.voice_enabled:
                tts_speak("Pulsante premuto")

    def on_servo_slide(self, v):
        v = int(v)
        self.ids.servo_g.value = v
        self.ids.servo_state.text = f"Servo: {v}Â°"

    def on_servo_release(self, v):
        v = int(v)

        # --- Idempotenza: non inviare se uguale all'ultimo confermato da Arduino ---
        if self._servo_last_from_stat is not None and v == self._servo_last_from_stat:
            self.log(f"(idempotente) Nessun invio: SERVO giÃ  a {v}")
            return

        # Debounce/dedup: ignora duplicati ravvicinati dello stesso valore
        now = time.monotonic()
        App.get_running_app().send_cmd(f"SERVO {v}")
        self.log(f"TX: SERVO {v}")
        if v == self._servo_last_value and (now - self._servo_last_ts) < 1.0:
            if self.voice_enabled:
                tts_speak(f"Servo posizionato a {v} gradi")
                self.log(f"(debounce) Ignoro SERVO {v}")
            return
        self._servo_last_value = v
        self._servo_last_ts = now

##        App.get_running_app().send_cmd(f"SERVO {v}")
##        self.log(f"TX: SERVO {v}")
##        if self.voice_enabled:
##            tts_speak(f"Servo posizionato a {v} gradi")

    def on_ask(self):
        App.get_running_app().send_cmd("ASK")
        self.log("TX: ASK")
        # opzionale: pronuncia letture correnti
        t, h = self.temp_val, self.hum_val
        if self.voice_enabled and t > -90 and h >= 0:
            tts_speak(f"La temperatura Ã¨ di {t:.1f} gradi e l'umiditÃ  Ã¨ del {h:.0f} percento")

    # -------------------------------------------------------
    def apply_stat(self, t, h, btn, led, servo):
        if t > -90:
            self.temp_val = t
            self.ids.temp_g.value = t
        if h >= 0:
            self.hum_val = h
            self.ids.hum_b.value = h

        self.ids.btn_state.text = f"BTN: {btn}"
        self.ids.led_state.text = f"LED: {led}"
        self.ids.servo_state.text = f"Servo: {servo}Â°"

        changed_led = (led != self.led_val)
        self.led_val = led

        # memorizza ultimo angolo confermato da Arduino (per idempotenza)
        self.servo_val = servo
        self._servo_last_from_stat = servo

        # --- sincronizza toggle LED senza riattivare on_state ---
        self._syncing_led = True
        try:
            self.ids.led_toggle.state = "down" if led else "normal"
            self.ids.led_toggle.text  = "LED ON" if led else "LED OFF"
        finally:
            self._syncing_led = False

        # --- parla solo se evento esterno (suppress flag gestito nell'App) ---
        if changed_led and self.voice_enabled:
            app = App.get_running_app()
            if getattr(app, "suppress_led_tts_once", 0) > 0:
                app.suppress_led_tts_once -= 1  # consumiamo il gettone
            else:
                tts_speak("Ho acceso il LED" if led else "Ho spento il LED")

    def log(self, msg):
        ti = self.ids.log
        ti.text += msg + "\n"
        ti.cursor = (0, len(ti.text))


# ===============================================================
#  SERIAL THREAD
# ===============================================================
class SerialWorker(threading.Thread):
    def __init__(self, ui_ref, q_in, on_port_status):
        super().__init__(daemon=True)
        self.ui_ref = ui_ref
        self.q_in = q_in
        self.on_port_status = on_port_status
        self.stop_flag = False
        self.ser = None

    def open_port(self):
        ports = list(serial.tools.list_ports.comports())
        self.on_port_status("Porta: scansioneâ€¦")
        for p in ports:
            try:
                s = serial.Serial(p.device, 115200, timeout=0.2)
                time.sleep(0.3)
                s.reset_input_buffer()
                s.write(b"ASK\n")
                time.sleep(0.2)
                t0 = time.time()
                while time.time() - t0 < 2.0:
                    if s.in_waiting:
                        line = s.readline().decode(errors="ignore").strip()
                        if line.startswith("HELLO:KIVYARDUINO"):
                            self.ser = s
                            self.on_port_status(f"Porta: {p.device}")
                            return True
                s.close()
            except Exception:
                pass
        self.on_port_status("Porta: non trovata")
        return False

    def run(self):
        if not self.open_port():
            return
        while not self.stop_flag:
            try:
                try:
                    cmd = self.q_in.get_nowait()
                    if self.ser and self.ser.writable():
                        self.ser.write((cmd.strip() + "\n").encode())
                except queue.Empty:
                    pass

                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode(errors="ignore").strip()
                    if not line:
                        continue
                    Clock.schedule_once(lambda *_: App.get_running_app().root.log(f"RX: {line}"))
                    if line.startswith("STAT:"):
                        data = self.parse_stat(line[5:])
                        if data:
                            Clock.schedule_once(lambda *_: self.ui_ref.apply_stat(
                                data.get("T", -100),
                                data.get("H", -1),
                                data.get("BTN", 0),
                                data.get("LED", 0),
                                data.get("SERVO", 90)
                            ))
            except Exception:
                time.sleep(0.1)

    @staticmethod
    def parse_stat(payload: str):
        out = {}
        try:
            for part in payload.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k in ("BTN", "LED", "SERVO", "H"):
                        out[k] = int(float(v))
                    elif k == "T":
                        out[k] = float(v)
            return out
        except Exception:
            return {}


# ===============================================================
#  APP
# ===============================================================
class KivyApp(App):
    title = "Cap.18 â€“ Kivy + Arduino + Voce IT"

    def build(self):
        Window.size = (1100, 620)
        Builder.load_string(KV)
        self.ui = RootUI()
        self.tx_queue = queue.Queue()
        # flag di soppressione TTS per eco LED (consumato alla prima STAT successiva)
        self.suppress_led_tts_once = 0
        self.worker = SerialWorker(self.ui, self.tx_queue, self._set_port_status)
        self.worker.start()
        return self.ui

    def _set_port_status(self, msg):
        Clock.schedule_once(lambda *_: setattr(self.ui, "port_status", msg))

    def send_cmd(self, line: str):
        try:
            self.tx_queue.put_nowait(line)
        except Exception:
            pass

    def mark_local_led_action(self):
        self.suppress_led_tts_once += 1

    def on_stop(self):
        if getattr(self, "worker", None) and self.worker.ser:
            self.worker.stop_flag = True


if __name__ == "__main__":
    KivyApp().run()


##
##import os, sys, threading, time, queue, json, subprocess, platform
##from functools import partial
##
### --- Kivy ---
##from kivy.app import App
##from kivy.lang import Builder
##from kivy.clock import Clock
##from kivy.properties import NumericProperty, StringProperty, BooleanProperty
##from kivy.uix.boxlayout import BoxLayout
##from kivy.core.window import Window
##
### --- Serial ---
##import serial, serial.tools.list_ports
##
### --- TTS ---
##from gtts import gTTS
##
##USE_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
##if USE_OPENAI:
##    try:
##        from openai import OpenAI
##        openai_client = OpenAI()
##    except Exception:
##        USE_OPENAI = False
##
### --- funzione player: mpg123 o afplay ---
##def play_audio(filepath: str):
##    sysname = platform.system()
##    try:
##        if sysname == "Windows" or sysname == "Linux":
##            subprocess.run(["mpg123", "-q", filepath], check=True)            
##        elif sysname == "Darwin":  # macOS
##            subprocess.run(["afplay", filepath], check=True)
##    except Exception as e:
##        print(f"[Audio error] {e}")
##
##
### --- funzione TTS migliorata ---
##def tts_speak(text: str, lang="it"):
##    if not text:
##        return
##
##    # Crea la cartella locale
##    outdir = os.path.join(os.path.dirname(__file__), "tts_audio")
##    os.makedirs(outdir, exist_ok=True)
##
##    # Pulizia vecchi file (>50 MB o più vecchi di 1 giorno)
##    for f in os.listdir(outdir):
##        try:
##            fp = os.path.join(outdir, f)
##            if os.path.isfile(fp) and time.time() - os.path.getmtime(fp) > 86400:
##                os.remove(fp)
##        except Exception:
##            pass
##
##    filename = f"tts_{int(time.time()*1000)}.mp3"
##    filepath = os.path.join(outdir, filename)
##    print(text)
##    
##
##    # --- OpenAI TTS se disponibile ---
##    if USE_OPENAI:
##        try:
##            with open(filepath, "wb") as f:
##                audio = openai_client.audio.speech.create(
##                    model="gpt-4o-mini-tts",
##                    voice="alloy",
##                    input=text
##                )
##                f.write(audio.read())
##            play_audio(filepath)
##            return
##        except Exception as e:
##            print("[TTS OpenAI error]", e)
##
##    # --- gTTS fallback ---
##    try:
##        gTTS(text=text, lang=lang, slow=False).save(filepath)
##        play_audio(filepath)
##    except Exception as e:
##        print("[TTS gTTS error]", e)
##
##
##KV = r"""
##<ArcGauge@Widget>:
##    value: 0
##    min: 0
##    max: 100
##    label: ""
##    canvas:
##        Color:
##            rgba: 0.2, 0.2, 0.2, 1
##        Line:
##            circle: (self.center_x+100, self.y-100, min(self.width, self.height), -90, 90)
##            width: 4
##        Color:
##            rgba: 0.1, 0.6, 1, 1
##        Line:
##            circle: (self.center_x+100, self.y-100, min(self.width, self.height), -90, -90 + 180 * ((self.value - self.min)/(self.max - self.min) if self.max>self.min else 0))
##            width: 8
##    Label:
##        text: f"{root.label}: {int(root.value)}"
##        font_size: '18sp'
##        center_x: root.center_x +100
##        center_y: root.y-100 + root.height*0.3
##        size_hint: None, None
##        size: self.texture_size
##
##<VBar@Widget>:
##    value: 0
##    min: 0
##    max: 100
##    label: ""
##    canvas:
##        Color:
##            rgba: 0.2,0.2,0.2,1
##        Rectangle:
##            pos: self.x + self.width*0.65, self.y-100
##            size: self.width*0.3, self.height
##        Color:
##            rgba: 0.1,0.8,0.3,1
##        Rectangle:
##            pos: self.x + self.width*0.65, self.y-100
##            size: self.width*0.3, self.height * ((self.value - self.min)/(self.max - self.min) if self.max>self.min else 0)
##    Label:
##        text: f"{root.label}: {int(root.value)}%"
##        font_size: '18sp'
##        pos: root.center_x+40, root.center_y-10
##        #center_x: root.center_x
##        #y: root.top - 5
##        size_hint: None, None
##        size: self.texture_size
##
##<RootUI>:
##    orientation: "vertical"
##    padding: 10
##    spacing: 10
##
##    BoxLayout:        
##        size_hint_y: 0.12
##        spacing: 10
##        ToggleButton:
##            id: led_toggle
##            text: "LED OFF"
##            on_state: root.on_led_toggle(self.state)
##        ToggleButton:
##            id: voice_toggle
##            text: "Voce: OFF"
##            on_state: root.on_voice_toggle(self.state)
##        Button:
##            text: "ASK (aggiorna)"
##            on_release: root.on_ask()
##        Label:
##            id: port_lbl
##            text: root.port_status
##            halign: "right"
##            valign: "middle"
##            text_size: self.size
##
##    BoxLayout:        
##        size_hint_y: 0.12
##        spacing: 10
##        
##        ToggleButton:            
##            id: btn_momentary
##            text: "Pulsante (simula pressione)"
##            on_state: root.on_virtual_button(self.state)
##        Label:
##            id: btn_state
##            text: "BTN: 0"
##        Label:
##            id: led_state
##            text: "LED: 0"
##        Label:
##            id: servo_state
##            text: "Servo: 90°"
##
##            
##
##    BoxLayout:        
##        size_hint_y: 0.4
##        spacing: 10
##        ArcGauge:
##            id: temp_g            
##            label: "Temp °C"
##            min: -10
##            max: 50
##        VBar:
##            id: hum_b
##            label: "Umidità"
##            min: 0
##            max: 100
##        BoxLayout:            
##            orientation: "vertical"
##            spacing: 8
##            ArcGauge:
##                id: servo_g
##                label: "Servo"
##                min: 0
##                max: 180
##                size_hint_y: 0.7
##
##        Slider:
##            id: servo_s            
##            min: 0
##            max: 180
##            value: 90
##            on_value: root.on_servo_slide(self.value)   # solo UI live
##            on_touch_up:
##                root.on_servo_release(self.value) if self.collide_point(*args[1].pos) else None
##                
##
##    BoxLayout:
##        orientation: "vertical"        
##        Label:
##            #text: "Log eventi"
##            size_hint_y: 0.8
##        TextInput:
##            id: log
##            readonly: True
##            size_hint_y: 0.9
##"""
##
##class RootUI(BoxLayout):
##    port_status = StringProperty("Porta: (ricerca...)")
##    voice_enabled = BooleanProperty(False)
##    temp_val = NumericProperty(0)
##    hum_val = NumericProperty(0)
##    led_val = NumericProperty(0)
##    btn_val = NumericProperty(0)
##    servo_val = NumericProperty(90)    
##    temp=0
##    hum=0
##    
##
##    def on_led_toggle(self, state):        
##        new_v = 1 if state == "down" else 0
##        self.ids.led_toggle.text = "LED ON" if new_v else "LED OFF"
##        App.get_running_app().send_cmd(f"LED {new_v}")
##        self.log(f"TX: LED {new_v}")
##        if self.voice_enabled:            
##            phrase = "Ho acceso il LED" if new_v else "Ho spento il LED"            
##            tts_speak(phrase)            
##
##    def on_voice_toggle(self, state):
##        self.voice_enabled = (state == "down")
##        self.ids.voice_toggle.text = "Voce: ON" if self.voice_enabled else "Voce: OFF"
##        if self.voice_enabled:
##            tts_speak("Voce attivata in italiano")
##
##    def on_servo_slide(self, v):
##        v = int(v)
##        self.ids.servo_g.value = v
##        self.ids.servo_state.text = f"Servo: {v}°"
##        # NIENTE invio comandi e NIENTE TTS qui
##
##    def on_servo_release(self, v):
##        v = int(v)
##        App.get_running_app().send_cmd(f"SERVO {v}")
##        self.log(f"TX: SERVO {v}")
##        if self.voice_enabled:
##            tts_speak(f"Servo posizionato a {v} gradi")
##        
##    def on_virtual_button(self, state):
##        if state == "down":
##            new_led = 0 if self.led_val else 1
##            App.get_running_app().send_cmd(f"LED {new_led}")
##            self.log("TX: (virtual BTN) -> toggle LED")
##            if self.voice_enabled:
##                tts_speak("Pulsante premuto")
##                
##
##    def on_temp(self, valuet,valueh):
##        print (valuet,valueh)
##        if self.voice_enabled:
##            text= f"La temperatura è di {valuet} gradi e l'umidità è del {valueh} percento"
##            tts_speak(text)
##            
##    def send_ask(self):
##        t=self.ids.temp_g.value
##        h=self.ids.hum_b.value
##        App.get_running_app().send_cmd("ASK")                
##        self.on_temp(t,h)
##        #print(t)
##        self.log("TX: ASK")
##
##    def apply_stat(self, t, h, btn, led, servo):        
##        if t > -90:
##            self.ids.temp_g.value = t
##        if h >= 0:
##            self.ids.hum_b.value = h
##        self.ids.btn_state.text = f"BTN: {btn}"
##        self.ids.led_state.text = f"LED: {led}"
##        self.ids.servo_state.text = f"Servo: {servo}°"
##
##        changed = []
##        if led != self.led_val:
##            changed.append(("LED", led))
##        if servo != self.servo_val:
##            changed.append(("SERVO", servo))
##
##        self.led_val = led
##        self.btn_val = btn
##        self.servo_val = servo
##
##        for what, val in changed:
##            
##            if self.voice_enabled:
##                if what == "LED":
##                    
##                    tts_speak("Ho acceso il LED" if val else "Ho spento il LED")
##                    
##                # niente TTS per SERVO qui: lo fa on_servo_release
##                
##        self.ids.led_toggle.state = "down" if self.led_val else "normal"
##        self.ids.led_toggle.text = "LED ON" if self.led_val else "LED OFF"
##
##    def log(self, msg):
##        ti = self.ids.log
##        ti.text += msg + "\n"
##        ti.cursor = (0, len(ti.text))
##
##
##class SerialWorker(threading.Thread):
##    def __init__(self, ui_ref, q_in, on_port_status):
##        super().__init__(daemon=True)
##        self.ui_ref = ui_ref
##        self.q_in = q_in
##        self.on_port_status = on_port_status
##        self.stop_flag = False
##        self.ser = None
##
##    def open_port(self):
##        ports = list(serial.tools.list_ports.comports())
##        self.on_port_status("Porta: scansione…")
##        for p in ports:
##            try:
##                s = serial.Serial(p.device, 115200, timeout=0.2)
##                time.sleep(0.3)
##                s.reset_input_buffer()
##                s.write(b"ASK\n")
##                time.sleep(0.2)
##                t0 = time.time()
##                while time.time() - t0 < 2.0:
##                    if s.in_waiting:
##                        line = s.readline().decode(errors="ignore").strip()
##                        if line.startswith("HELLO:KIVYARDUINO"):
##                            self.ser = s
##                            self.on_port_status(f"Porta: {p.device}")
##                            return True
##                s.close()
##            except Exception:
##                pass
##        self.on_port_status("Porta: non trovata")
##        return False
##
##    def run(self):
##        if not self.open_port():
##            return
##        while not self.stop_flag:
##            try:
##                try:
##                    cmd = self.q_in.get_nowait()
##                    if self.ser and self.ser.writable():
##                        self.ser.write((cmd.strip() + "\n").encode())
##                except queue.Empty:
##                    pass
##                if self.ser and self.ser.in_waiting:
##                    line = self.ser.readline().decode(errors="ignore").strip()
##                    if not line:
##                        continue
##                    Clock.schedule_once(lambda *_: App.get_running_app().root.log(f"RX: {line}"))
##                    if line.startswith("STAT:"):
##                        data = self.parse_stat(line[5:])
##                        if data:
##                            Clock.schedule_once(lambda *_: self.ui_ref.apply_stat(
##                                data.get("T", -100), data.get("H", -1),
##                                data.get("BTN", 0), data.get("LED", 0), data.get("SERVO", 90)
##                            ))
##            except Exception:
##                time.sleep(0.1)
##
##    @staticmethod
##    def parse_stat(payload: str):
##        out = {}
##        try:
##            for part in payload.split(";"):
##                
##                if "=" in part:
##                    k, v = part.split("=", 1)
##                    k, v = k.strip(), v.strip()
##                    if k in ("BTN","LED","SERVO"):                        
##                        out[k] = int(float(v))                        
##                        hum = out[k]
##                    elif k == "T":
##                        out[k] = float(v)                        
##                        temp = out[k]
##                    elif k == "H":
##                        out[k] = float(v)                        
##                        hum = out[k]
##                        
##            return out
##        except Exception:
##            return {}
##
##
##class KivyApp(App):
##    title = "Cap. 18 - Kivy + Arduino + Voce IT"
##
##    def build(self):
##        Window.size = (1200, 620)
##        Builder.load_string(KV)
##        self.ui = RootUI()
##        self.tx_queue = queue.Queue()
##        self.servo_last_sent = time.time()
##        self.servo_pending = None
##        self.worker = SerialWorker(self.ui, self.tx_queue, self._set_port_status)
##        self.worker.start()
##        return self.ui
##
##    def _set_port_status(self, msg):
##        Clock.schedule_once(lambda *_: setattr(self.ui, "port_status", msg))
##
##    def send_cmd(self, line: str):
##        try:
##            self.tx_queue.put_nowait(line)
##        except Exception:
##            pass
##
##    def schedule_servo(self, angle: int):
##        self.servo_pending = int(angle)
##        now = time.time()
##        if now - self.servo_last_sent > 0.1:
##            self._flush_servo()
##        Clock.unschedule(self._flush_servo_cb)
##        Clock.schedule_once(self._flush_servo_cb, 0.12)
##
##    def _flush_servo_cb(self, *args):
##        self._flush_servo()
##
##    def _flush_servo(self):
##        if self.servo_pending is None:
##            return
##        v = self.servo_pending
##        self.servo_pending = None
##        self.servo_last_sent = time.time()
##        self.send_cmd(f"SERVO {v}")
##        self.ui.log(f"TX: SERVO {v}")
##        if self.ui.voice_enabled:
##            tts_speak(f"Servo posizionato a {v} gradi")
##
##    def on_stop(self):
##        if hasattr(self, "worker") and self.worker and self.worker.ser:
##            try:
##                self.worker.stop_flag = True
##            except:
##                pass
##
##
##if __name__ == "__main__":
##    KivyApp().run()
