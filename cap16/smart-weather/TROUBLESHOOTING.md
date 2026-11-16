# Smart Weather Station — Troubleshooting

## Serial non disponibile
- Verifica la porta: `ls /dev/ttyACM*` o `dmesg | grep tty`
- Imposta `SERIAL_PORT` in `.env` (es. `/dev/ttyUSB0`).
- Permessi: `sudo usermod -aG dialout $USER` e riavvia.

## Disconnessioni / porta occupata
- Chiudi Serial Monitor Arduino IDE, node-red serial, screen/minicom, ecc.
- Chi usa la porta? `fuser -v /dev/ttyACM0` / `lsof /dev/ttyACM0`.
- Alias udev stabile consigliato (es. `/dev/arduino_dht`).

## DHT non legge
- Aggiungi resistenza di pull-up 4.7k tra DATA e VCC.
- Controlla il pin DHT e il tipo (DHT11 vs DHT22).

## TTS OpenAI
- Aggiungi `OPENAI_API_KEY` nel `.env`.
- Con openai>=1.40 usa `response_format="wav"` (già impostato).

## Chart non appare
- Controlla rete/Internet per CDN di Chart.js.
- Apri console browser (F12) per eventuali errori CORS o 404.

## CSV/SQLite
- Se `weather.csv` non si crea, controlla permessi della cartella `data/`.
- Per SQLite, esporta `USE_SQLITE=1` nel `.env`.