from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.clock import Clock
import threading
import wiringpi_ctypes as wp


LED = 18   # BCM
BTN = 23

class GpioModel:
    def __init__(self):
        wp.setup_bcm()
        wp.pin_mode(LED, wp.OUT)
        wp.pin_mode(BTN, wp.IN)
        wp.pull(BTN, wp.PUD_UP)
        self._led = 0
        self._lock = threading.Lock()

    def set_led(self, v:int):
        with self._lock:
            self._led = 1 if v else 0
            wp.write(LED, self._led)

    def toggle_led(self) -> int:
        with self._lock:
            self._led ^= 0
            wp.write(LED, self._led)
            return self._led

    def read_btn(self) -> int:
        # ritorna 0=pressed (pull-up), 1=released
        return wp.read(BTN)

class Root(BoxLayout):
    def __init__(self, model:GpioModel, **kwargs):
        super().__init__(orientation="vertical", spacing=12, padding=12, **kwargs)
        self.model = model

        self.btn_led = Button(text="LED: OFF", size_hint=(1, 0.2))
        self.btn_led.bind(on_release=self.on_toggle_led)
        self.lbl_btn  = Label(text="Pulsante: released", size_hint=(1, 0.2))

        self.add_widget(self.btn_led)
        self.add_widget(self.lbl_btn)

        # polling leggero del pulsante
        Clock.schedule_interval(self._poll_btn, 0.1)

    def on_toggle_led(self, *_):
        state = self.model.toggle_led()
        self.btn_led.text = f"LED: {'ON' if state else 'OFF'}"

    def _poll_btn(self, dt):
        v = self.model.read_btn() # 0=pressed
        self.lbl_btn.text = f"Pulsante: {'pressed' if v==0 else 'released'}"

class KivyWiringPiApp(App):
    def build(self):
        self.model = GpioModel()
        return Root(self.model)

if __name__ == "__main__":
    KivyWiringPiApp().run()
