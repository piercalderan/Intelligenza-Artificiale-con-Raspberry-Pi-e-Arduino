/*
  DHT_Serial_LED.ino
  - Legge DHT (DHT11/DHT22) e invia JSON su seriale ogni N secondi
  - Riceve comandi seriali: LED_ON, LED_OFF, READ_NOW
  - Baud: 115200
  - Compatibile UNO/Nano/Mega (SoftwareSerial non necessario)

  Formato output tipico:
    {"temp":21.4,"hum":38.0}
  In caso di lettura fallita:
    {"temp":null,"hum":null}
*/

#include <DHT.h>

// ====== CONFIG ======
#define DHT_PIN   7
#define DHT_TYPE  DHT11      // Cambia in DHT11 se usi un DHT11
#define LED_PIN   13         // LED onboard su molti Arduino
const unsigned long REPORT_MS = 10000; // invio dati ogni 5 secondi

// ====== GLOBALI ======
DHT dht(DHT_PIN, DHT_TYPE);
unsigned long lastReport = 0;

// Buffer per lettura comandi da Serial
const size_t CMD_BUF_LEN = 64;
char cmdBuf[CMD_BUF_LEN];
size_t cmdLen = 0;

// ====== UTILS ======
void printJson(float t, float h) {
  // Stampa una riga JSON con temp/umidità (null se NaN)
  Serial.print(F("{\"temp\":"));
  if (isnan(t)) Serial.print(F("null"));
  else Serial.print(t, 1); // 1 decimale

  Serial.print(F(",\"hum\":"));
  if (isnan(h)) Serial.print(F("null"));
  else Serial.print(h, 1);

  Serial.println(F("}"));
}

void doReadAndPrint() {
  float h = dht.readHumidity();
  float t = dht.readTemperature(); // in °C (default). Per °F usa readTemperature(true)

  // In alcuni casi il DHT vuole un piccolo delay tra le letture ravvicinate
  // ma con 5s di intervallo non serve nulla di extra.

  printJson(t, h);
}

void handleCommand(const char* cmd) {
  // Confronto case-insensitive semplice
  // Converte temporaneamente in maiuscolo
  char up[CMD_BUF_LEN];
  size_t n = min(strlen(cmd), (size_t)(CMD_BUF_LEN - 1));
  for (size_t i = 0; i < n; i++) up[i] = toupper(cmd[i]);
  up[n] = '\0';

  if (strcmp(up, "LED_ON") == 0) {
    digitalWrite(LED_PIN, HIGH);
    Serial.println(F("{\"ok\":true,\"event\":\"LED_ON\"}"));
  } else if (strcmp(up, "LED_OFF") == 0) {
    digitalWrite(LED_PIN, LOW);
    Serial.println(F("{\"ok\":true,\"event\":\"LED_OFF\"}"));
  } else if (strcmp(up, "READ_NOW") == 0) {
    doReadAndPrint();
  } else {
    Serial.print(F("{\"ok\":false,\"error\":\"unknown_cmd\",\"cmd\":\""));
    Serial.print(cmd);
    Serial.println(F("\"}"));
  }
}

void resetCmdBuffer() {
  cmdLen = 0;
  cmdBuf[0] = '\0';
}

void tryParseIncoming() {
  // Legge caratteri fino a newline; ignora CR
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      // Fine comando
      cmdBuf[cmdLen] = '\0';
      if (cmdLen > 0) handleCommand(cmdBuf);
      resetCmdBuffer();
      return;
    } else {
      if (cmdLen < CMD_BUF_LEN - 1) {
        cmdBuf[cmdLen++] = c;
        cmdBuf[cmdLen] = '\0';
      } else {
        // Overflow: scarta e segnala
        Serial.println(F("{\"ok\":false,\"error\":\"cmd_overflow\"}"));
        resetCmdBuffer();
        return;
      }
    }
  }
}

// ====== SETUP/LOOP ======
void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Serial.begin(115200);
  while (!Serial) { ; }  // per board con USB native

  dht.begin();

  // Primo annuncio
  Serial.println(F("{\"ok\":true,\"event\":\"boot\",\"baud\":115200}"));
  // Prima lettura immediata per popolare il frontend
  doReadAndPrint();

  lastReport = millis();
}

void loop() {
  // Gestione comandi in arrivo
  tryParseIncoming();

  // Invio periodico dei valori
  unsigned long now = millis();
  if ((now - lastReport) >= REPORT_MS) {
    doReadAndPrint();
    lastReport = now;
  }
}
