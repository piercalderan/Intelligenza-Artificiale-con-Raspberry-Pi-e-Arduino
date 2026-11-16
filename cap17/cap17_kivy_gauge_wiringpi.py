# main.py
import threading, time, math,queue 
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.slider import Slider
from kivy.uix.widget import Widget
from kivy.properties import NumericProperty, ListProperty, BooleanProperty
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Triangle
import serial, serial.tools.list_ports
# in cima
from kivy.clock import Clock


# --- Pin BCM ---
LED1 = 21       # toggle ON/OFF
LED2 = 20       # blinking
BTN_TOGGLE = 16 # pulsante con logica toggle (pull-up)
BTN_ONOFF  = 12 # pulsante momentary on/off (pull-up)
SERVO = 18      # soft-PWM demo

# --- SoftPWM parametri servo ---
SERVO_RANGE = 200  # risoluzione softPwm
SERVO_MIN   = 20   # ~10% (≈1.0 ms)
SERVO_MAX   = 40   # ~20% (≈2.0 ms)

def map_angle_to_softpwm(angle: float) -> int:
    a = max(0.0, min(180.0, angle))
    return int(SERVO_MIN + (SERVO_MAX - SERVO_MIN) * (a / 180.0))

# ---------- Widget grafici ----------

class LedIndicator(Widget):
    """Cerchio pieno con alone, acceso/spento via proprietà 'on'."""
    on = BooleanProperty(False)
    on_color = ListProperty([0.1, 0.9, 0.1])   # verde
    off_color = ListProperty([0.3, 0.3, 0.3])  # grigio

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, on=self._redraw)
        with self.canvas:
            self._col = Color(1,1,1,1)
            self._circle = Ellipse()
            self._halo_col = Color(1,1,1,0.15)
            self._halo = Ellipse()
        self._redraw()

    def _redraw(self, *args):
        r = min(self.width, self.height)
        x = self.center_x - r/2
        y = self.center_y - r/2
        # halo leggero
        self._halo.size = (r*1.2, r*1.2)
        self._halo.pos  = (self.center_x - self._halo.size[0]/2,
                           self.center_y - self._halo.size[1]/2)
        col = self.on_color if self.on else self.off_color
        self._col.rgba = (*col, 1.0 if self.on else 0.9)
        self._halo_col.rgba = (*col, 0.25 if self.on else 0.08)
        self._circle.pos = (x, y)
        self._circle.size = (r, r)

from kivy.graphics import Color, Ellipse, Rectangle, Line

class ButtonIndicator(Widget):
    """Pillola piena arrotondata, centrata nel suo riquadro."""
    pressed = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, pressed=self._redraw)
        self._redraw()

    def _redraw(self, *args):
        self.canvas.clear()
        w, h = self.width, self.height
        x, y = self.x, self.y
        r = min(w, h) / 2.0  # raggio degli estremi

        with self.canvas:
            # colore riempimento
            if self.pressed:
                Color(1.0, 0.6, 0.0, 1.0)   # arancio
            else:
                Color(0.55, 0.55, 0.55, 0.65)  # grigio

            # parte centrale rettangolare
            #Rectangle(pos=(x + r, y), size=(max(0.0, w - 2*r), h))
            # estremi arrotondati
            Ellipse(pos=(x, y), size=(2*r, 2*r))             # sinistra
            #Ellipse(pos=(x + w - 2*r, y), size=(2*r, 2*r))   # destra

            # bordo leggero
            Color(0, 0, 0, 0.22)
            # outline: due semicerchi + due segmenti
            #Line(ellipse=(x, y, 2*r, 2*r, 90, 270), width=1.2)
            #Line(points=[x + r, y, x + w - r, y], width=1.2)
            #Line(ellipse=(x + w - 2*r, y, 2*r, 2*r, -90, 90), width=1.2)
            #Line(points=[x + r, y + h, x + w - r, y + h], width=1.2)

class ServoGauge(Widget):
    """Semicerchio 0..180° con ago. Colore scala: verde(0)→giallo(90)→rosso(180)."""
    angle = NumericProperty(0.0)    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, angle=self._redraw)
        self._redraw()

    def _grad_color(self, deg_0_180):
        t = max(0.0, min(180.0, deg_0_180)) / 180.0
        if t <= 0.5:   # verde → giallo
            k = t / 0.5
            return (k, 1.0, 0.0)
        else:          # giallo → rosso
            k = (t - 0.5) / 0.5
            return (1.0, 1.0 - k, 0.0)

    def _redraw(self, *args):
        self.canvas.clear()
        w, h = self.width /2, self.height/2
        cx = 150 #self.center_x/2 
        # posizioniamo il centro un po' più in basso per stare comodi
        cy = self.y + h * 0.18
        radius = min(w * 0.9, h * 1.8) / 2.0

        with self.canvas:
            # arco di fondo (grigio scuro)
            Color(0.2, 0.2, 0.2, 1)
            Line(circle=(cx, cy, radius, -90, 0), width=5, cap='round')

            # scala colorata a segmenti (passi da 5°)
            seg = 5
            for deg in range(90, 0, -seg):
                d1 = deg
                d2 = max(0, deg - seg)
                #r, g, b = self._grad_color(180 - d1)  # 0 a sinistra, 180 a destra
                r, g, b = self._grad_color(180 - d1)  # 0 a sinistra, 180 a destra
                Color(r, g, b, 1)
                Line(circle=(cx, cy, radius, d1, d2), width=5)

            # tacche ogni 30°
            Color(0.92, 0.92, 0.92, 1)
            for d in range(0, 181, 30):
                rad = math.radians(180 - d)
                x1 = cx + (radius - 8) * math.cos(rad)
                y1 = cy + (radius - 8) * math.sin(rad)
                x2 = cx + (radius - 26) * math.cos(rad)
                y2 = cy + (radius - 26) * math.sin(rad)
                Line(points=[x1, y1, x2, y2], width=1)

            # ago (stessa mappa angolare)
            a = max(0.0, min(180.0, self.angle))
            r, g, b = self._grad_color(a)
            Color(r, g, b, 1)
            rad = math.radians(180 - a)
            xN = cx + (radius - 28) * math.cos(rad)
            yN = cy + (radius - 28) * math.sin(rad)
            Line(points=[cx, cy, xN, yN], width=2, cap='round')

            # pivot
            Color(0.15, 0.15, 0.15, 1)
            Ellipse(pos=(cx - 8, cy - 8), size=(16, 16))

#######################
class TempGauge(Widget):
    """Semicerchio 0..180° con ago. Colore scala: verde(0)→giallo(90)→rosso(180)."""
    angle = NumericProperty(0.0)    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, angle=self._redraw)
        self._redraw()

    def _grad_color(self, deg_0_180):
        t = max(0.0, min(180.0, deg_0_180)) / 180.0
        if t <= 0.5:   # verde → giallo
            k = t / 0.5
            return (k, 1.0, 0.0)
        else:          # giallo → rosso
            k = (t - 0.5) / 0.5
            return (1.0, 1.0 - k, 0.0)

    def _redraw(self, *args):
        self.canvas.clear()
        w, h = self.width /2, self.height/2
        cx = self.center_x/2 + 50
        # posizioniamo il centro un po' più in basso per stare comodi
        cy = self.y + h * 0.18
        radius = min(w * 0.9, h * 1.8) / 2.0

        with self.canvas:
            # arco di fondo (grigio scuro)
            Color(0.2, 0.2, 0.2, 1)
            Line(circle=(cx, cy, radius, -90, 0), width=5, cap='round')

            # scala colorata a segmenti (passi da 5°)
            seg = 5
            for deg in range(90, 0, -seg):
                d1 = deg
                d2 = max(0, deg - seg)
                #r, g, b = self._grad_color(180 - d1)  # 0 a sinistra, 180 a destra
                r, g, b = self._grad_color(180 - d1)  # 0 a sinistra, 180 a destra
                Color(r, g, b, 1)
                Line(circle=(cx, cy, radius, d1, d2), width=5)

            # tacche ogni 30°
            Color(0.92, 0.92, 0.92, 1)
            for d in range(0, 181, 30):
                rad = math.radians(180 - d)
                x1 = cx + (radius - 8) * math.cos(rad)
                y1 = cy + (radius - 8) * math.sin(rad)
                x2 = cx + (radius - 26) * math.cos(rad)
                y2 = cy + (radius - 26) * math.sin(rad)
                Line(points=[x1, y1, x2, y2], width=1)

            # ago (stessa mappa angolare)
            a = max(0.0, min(180.0, self.angle))
            r, g, b = self._grad_color(a)
            Color(r, g, b, 1)
            rad = math.radians(180 - a)
            xN = cx + (radius - 28) * math.cos(rad)
            yN = cy + (radius - 28) * math.sin(rad)
            Line(points=[cx, cy, xN, yN], width=2, cap='round')

            # pivot
            Color(0.15, 0.15, 0.15, 1)
            Ellipse(pos=(cx - 8, cy - 8), size=(16, 16))
            




            
# ---------- SerialWorker ----------
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
        self.on_port_status("Porta: scansione…")
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
                            print(line)
                            self.ser = s
                            self.on_port_status(f"Porta: {p.device}")
                            return True
                s.close()
            except Exception:
                pass
        self.on_port_status("Porta: non trovata")
        return False

    def run(self):
        print("run")
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
                    #Clock.schedule_once(lambda *_: App.get_running_app().root.log(f"RX: {line}"))
                    if line.startswith("STAT:"):
                        data = self.parse_stat(line)
                        print(data)
                        {'H': 66.0, 'BTN': 0, 'LED': 0, 'SERVO': 90}
                        if data:
                            temp=data.get("T")
                            hum=data.get("H")
                            btn=data.get("BTN")
                            led= data.get("LED")
                            servo=data.get("SERVO")
                            print(hum)
                            print(temp)
                            print(led)
                            print(btn)
                            
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
                    
                    if k in ("T","H","BTN","LED","SERVO"):                        
                        out[k] = int(float(v))                        
                    elif k == "T":
                        out[k] = float(v)                        
                        temp = out[k]                        
                    elif k == "H":
                        out[k] = float(v)                        
                        hum = out[k]
                        
                        
            return out
        except Exception:
            return {}


##############################################
class GpioModel:
    def __init__(self):
        
        # LED
        
        # Pulsanti
        
        # Servo soft-PWM
        
        # Stati logici
        self._led1 = 0
        self._blink_enabled = False
        self._btn_toggle_state = 0
        self._btn_toggle_prev = 1

    # LED1
    def toggle_led1(self):
        self._led1 ^= 1
        
        return self._led1

    def set_led1(self, v:int):
        self._led1 = 1 if v else 0
        

    # LED2 blink
    def set_blink(self, enable: bool):
        self._blink_enabled = bool(enable)

    def blink_step(self):
        if not self._blink_enabled:
            return
        cur = 1
        

    # Pulsanti
    def read_btn_toggle(self) -> int:
        return BTN_TOGGLE # 0=pressed
    def read_btn_onoff(self) -> int:
        return BTN_ONOFF

    def update_btn_logic(self):
        v = self.read_btn_toggle()
        if self._btn_toggle_prev == 1 and v == 0:
            self._btn_toggle_state ^= 1
            self.set_led1(self._btn_toggle_state)
            #say("Pulsante toggle premuto, LED uno acceso"
##            if self._btn_toggle_state else
##            "Pulsante toggle premuto, LED uno spento")
        self._btn_toggle_prev = v
##            # edge detect per BTN_ONOFF:
##        if not hasattr(self, "_btn_onoff_prev"):
##            self._btn_onoff_prev = 1
##        v2 = self.read_btn_onoff()
##
##        if self._btn_onoff_prev == 1 and v2 == 0:
##            say("Pulsante on off premuto")
##        elif self._btn_onoff_prev == 0 and v2 == 1:
##            say("Pulsante on off rilasciato")
    

        v2 = self.read_btn_onoff()
        if v2 == 0:
            self.set_led1(1)
        else:
            if self._btn_toggle_state == 0:
                self.set_led1(0)
                

    # Servo
    def set_servo_angle(self, angle: float) -> int:
        val = map_angle_to_softpwm(angle)
        
        return val

    def cleanup(self):
        try:
            pass
        except Exception:
            pass
        

# ---------- UI ----------

class Root(BoxLayout):
    def __init__(self, model:GpioModel, **kwargs):
        super().__init__(orientation="vertical", spacing=14, padding=14, **kwargs)
        self.model = model
        self._servo_say_ev = None

        # Row 1: LED controls + indicatori
        row1 = GridLayout(cols=4, size_hint=(1, 0.26), spacing=10)
        self.btn_led1 = Button(text="LED1 Toggle", font_size='20sp')
        self.ind_led1 = LedIndicator(size_hint=(0.25, 1))
        self.btn_blink = Button(text="LED2 Blink: OFF", font_size='20sp')
        self.ind_led2 = LedIndicator(size_hint=(0.25, 1))
        row1.add_widget(self.btn_led1)
        row1.add_widget(self.ind_led1)
        row1.add_widget(self.btn_blink)
        row1.add_widget(self.ind_led2)
        self.add_widget(row1)

        # Row 2: Pulsanti fisici + indicatori
        row2 = GridLayout(cols=3, size_hint=(1, 0.2), spacing=10)
        self.lbl_btn_tog = Label(text="BTN_TOGGLE", font_size='18sp')
        self.ind_btn_tog = ButtonIndicator()
        self.lbl_btn_on  = Label(text="BTN_ONOFF", font_size='18sp')
        self.ind_btn_on  = ButtonIndicator()
        self.gauge = ServoGauge()

        self.tempgauge = TempGauge()


##        row2.add_widget(self.lbl_btn_tog)
##        row2.add_widget(self.ind_btn_tog)
##        row2.add_widget(self.lbl_btn_on)
##        row2.add_widget(self.ind_btn_on)

        self.add_widget(row2)

        # Row 3: Servo gauge + slider
        #row3 = GridLayout(cols=2, size_hint=(1, 0.38), spacing=10)
        row3 = GridLayout(cols=2, size_hint=(1, 1), spacing=10)
        col_servo = BoxLayout(orientation='vertical', spacing=8)        
        
        self.lbl_temp = Label(text="Temp: 0°", font_size='14sp', size_hint=(1,1))
        row3.add_widget(self.lbl_temp)        
        row3.add_widget(self.gauge)
        row3.add_widget(self.tempgauge)
        self.lbl_servo = Label(text="Servo: 0° (soft-PWM)", font_size='14sp', size_hint=(1,1))
        self.slider_servo = Slider(min=0, max=180, value=0, step=1, size_hint=(0.2,0.2))
        row3.add_widget(self.lbl_servo)
        row3.add_widget(self.slider_servo)

        #row3.add_widget(col_servo)
        self.add_widget(row3)

        # Bind UI events
        self.btn_led1.bind(on_release=self.on_toggle_led1)
        self.btn_blink.bind(on_release=self.on_toggle_blink)
        self.slider_servo.bind(value=self.on_servo_change)

        # Loops periodici
        Clock.schedule_interval(self._poll_inputs, 0.05)  # 20 Hz
        Clock.schedule_interval(self._blink_tick, 0.2)

    # ---- Handlers ----
    def on_toggle_led1(self, *_):
        st = self.model.toggle_led1()
        self._sync_led1_text(st)

        st = self.model.toggle_led1() #dd
        self._sync_led1_text(st)
        #say("LED uno acceso" if st else "LED uno spento")


    def on_toggle_blink(self, *_):
        # inverti
        new_state = "ON" if "OFF" in self.btn_blink.text else "OFF"
        self.btn_blink.text = f"LED2 Blink: {new_state}"
        self.model.set_blink(new_state == "ON")
#add
##        new_state = "ON" if "OFF" in self.btn_blink.text else "OFF"
##        self.btn_blink.text = f"LED2 Blink: {new_state}"
##        self.model.set_blink(new_state == "ON")
        #say("Lampeggio attivato"
##            if new_state == "ON"
##            else "Lampeggio disattivato")


    def on_servo_change(self, *_):
        angle = float(self.slider_servo.value)
        self.model.set_servo_angle(angle)
        self.gauge.angle = angle
        self.lbl_servo.text = f"Servo: {int(angle)}° (soft-PWM)"
        # debounce: parla 600ms dopo l’ultima modifica
        if self._servo_say_ev:
            self._servo_say_ev.cancel()
##        self._servo_say_ev = Clock.schedule_once(
##            lambda dt: say(f"Servo a {int(self.slider_servo.value)} gradi"), 0.6
##        )

##        def on_servo_change(self, *_):
##        angle = float(self.slider_servo.value)
##        self.model.set_servo_angle(angle)
##        self.gauge.angle = angle
##        self.lbl_servo.text = f"Servo: {int(angle)}° (soft-PWM)"

    # ---- Timers ----
    def _poll_inputs(self, dt):
        # logica dei pulsanti
        self.model.update_btn_logic()

        # LED1 stato e indicatori
        led1_state = 1
        
        self.ind_led1.on = bool(led1_state)
        self._sync_led1_text(led1_state)

        # LED2 stato
        self.ind_led2.on = 1

        # indicatori pulsanti fisici
        v1 = self.model.read_btn_toggle()  # 0=pressed
        v2 = self.model.read_btn_onoff()
        self.ind_btn_tog.pressed = (v1 == 0)
        self.ind_btn_on.pressed  = (v2 == 0)

    def _blink_tick(self, dt):
        self.model.blink_step()

    def _sync_led1_text(self, st):
        self.btn_led1.text = f"LED1 {'ON' if st else 'OFF'}  (toggle)"

class KivyGpioApp(App):
    title = "Kivy GPIO Dashboard (Bookworm, wiringPi + softPWM)"
  

    def build(self):
        
        self.tx_queue = queue.Queue()
        self.servo_last_sent = time.time()
        self.servo_pending = None
        
        self.model = GpioModel()
        self.worker = SerialWorker(self, self.tx_queue, self._set_port_status)
        self.worker.start()
        return Root(self.model)
    
    def _set_port_status(self, msg):
        Clock.schedule_once(lambda *_: setattr(self, "port_status", msg))

    def send_cmd(self, line: str):
        try:
            self.tx_queue.put_nowait(line)
        except Exception:
            pass

    def schedule_servo(self, angle: int):
        self.servo_pending = int(angle)
        now = time.time()
        if now - self.servo_last_sent > 0.1:
            self._flush_servo()
        Clock.unschedule(self._flush_servo_cb)
        Clock.schedule_once(self._flush_servo_cb, 0.12)

    def _flush_servo_cb(self, *args):
        self._flush_servo()

    def _flush_servo(self):
        if self.servo_pending is None:
            return
        v = self.servo_pending
        self.servo_pending = None
        self.servo_last_sent = time.time()
        self.send_cmd(f"SERVO {v}")
        self.ui.log(f"TX: SERVO {v}")
        if self.ui.voice_enabled:
            tts_speak(f"Servo posizionato a {v} gradi")

    def on_stop(self):
        if hasattr(self, "worker") and self.worker and self.worker.ser:
            try:
                self.worker.stop_flag = True
            except:
                pass
    
##    def on_stop(self):
##        self.model.cleanup()

if __name__ == "__main__":
    KivyGpioApp().run()
