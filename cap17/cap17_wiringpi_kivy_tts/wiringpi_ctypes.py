import os
import ctypes
from ctypes import c_int
from pathlib import Path
from ctypes.util import find_library


def _load_gpio_shim():
    here = Path(__file__).resolve().parent
    candidates = [
        here / "libgpio_shim.so",
        Path.cwd() / "libgpio_shim.so",
        Path("/usr/local/lib/libgpio_shim.so"),
        Path("/usr/lib/libgpio_shim.so"),
    ]
    for p in candidates:
        if p.exists():
            return ctypes.CDLL(str(p))
    name = find_library("gpio_shim") or find_library("libgpio_shim")
    if name:
        return ctypes.CDLL(name)
    raise OSError("libgpio_shim.so non trovata (mettila accanto a questo file o in /usr/local/lib, poi 'sudo ldconfig').")

_lib = _load_gpio_shim()
# --- prototipi C ---
_lib.gpio_setup_bcm.restype = c_int
_lib.gpio_set_mode.argtypes = [c_int, c_int]
_lib.gpio_pull.argtypes     = [c_int, c_int]
_lib.gpio_write.argtypes    = [c_int, c_int]
_lib.gpio_read.argtypes     = [c_int]
_lib.gpio_read.restype      = c_int
_lib.gpio_softpwm_create.argtypes = [c_int, c_int, c_int]
_lib.gpio_softpwm_create.restype  = c_int
_lib.gpio_softpwm_write.argtypes  = [c_int, c_int]
_lib.gpio_softpwm_stop.argtypes   = [c_int]

# --- costanti "amichevoli" ---
IN, OUT = 0, 1
PUD_OFF, PUD_DOWN, PUD_UP = 0, 1, 2

# --- wrapper Python ---
def setup_bcm():
    if _lib.gpio_setup_bcm() != 0:
        raise RuntimeError("wiringPiSetupGpio() failed")

def pin_mode(pin:int, mode:int): _lib.gpio_set_mode(pin, mode)
def pull(pin:int, pud:int):      _lib.gpio_pull(pin, pud)
def write(pin:int, value:int):   _lib.gpio_write(pin, 1 if value else 0)
def read(pin:int) -> int:        return _lib.gpio_read(pin)
def softpwm_create(pin:int, initial:int, range_:int):
    if _lib.gpio_softpwm_create(pin, initial, range_) != 0:
        raise RuntimeError("softPwmCreate failed")
def softpwm_write(pin:int, value:int):  _lib.gpio_softpwm_write(pin, value)
def softpwm_stop(pin:int):              _lib.gpio_softpwm_stop(pin)

