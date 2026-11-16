
import serial
import time
import subprocess

SERIAL_PORT = "/dev/ttyACM0" # oppure /dev/ttyUSB0 (guarda con il comando ls /dev/tty* da terminale la porta a cui e collegato Arduino

BAUDRATE = 9600 # stesso baud rate di Arduino

def speak(text): # funzione che lancia espeak
    
    subprocess.run(["espeak", "-v", "it", text])

# legge dalla seriale e stampa temperatura e umidita da Arduino
def main(): 
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=2)
    except Exception as e:
        print("Errore apertura seriale:", e)
        return

    print("In ascolto sulla seriale:", SERIAL_PORT)
    while True:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        if line == "ERR":
            print("Errore dal sensore.")
            continue

        parts = line.split(",")
        if len(parts) >= 2:
            temp, hum = parts[0], parts[1]
            phrase=f"Temperatura {temp} gradi, umidita {hum} percento."
            print("Frase:", phrase)
            speak(phrase) # manda la frase alla funzione speak
        else:
            print("Linea non valida:", line)

        time.sleep(0.2)

if __name__ == "__main__":
    main()
