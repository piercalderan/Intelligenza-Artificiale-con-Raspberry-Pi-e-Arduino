import serial
import time
import subprocess

SERIAL_PORT = "/dev/ttyACM0"
BAUDRATE = 9600

import openai
import tempfile

openai.api_key = "INSERISCI_LA_TUA_API_KEY" #vedi il Capitolo 2 per come ottenerla


def speak(text):
    response = openai.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=text
    )
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
        f.write(response.read())
        audio_file = f.name
    subprocess.run(["mpg123", audio_file])

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
            phrase = f"Temperatura {temp} gradi, umidita {hum} percento."
            print("Frase:", phrase)
            speak(phrase)
        else:
            print("Linea non valida:", line)

        time.sleep(0.2)

if __name__ == "__main__":
    main()
