#include <Arduino.h>
#include <DHT.h>

// ---- CONFIG ----
#define PIN_DHT 7          // pin DHT
#define DHTTYPE DHT11      // o DHT22
#define PIN_LED 4         // LED integrato

DHT dht(PIN_DHT, DHTTYPE);

unsigned long lastSend = 0;
const unsigned long PERIOD_MS = 3000;  // invia lettura ogni 3s

void setup() {
  Serial.begin(9600);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  dht.begin();
  delay(1000);
  Serial.println(F("READY"));
}

void loop() {
  // 1) Comandi da seriale
  while (Serial.available()) {
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.equalsIgnoreCase("LED_ON")) {
    digitalWrite(PIN_LED, HIGH);
    Serial.println(F("{\"led\":\"on\"}"));
  } else if (cmd.equalsIgnoreCase("LED_OFF")) {
    digitalWrite(PIN_LED, LOW);
    Serial.println(F("{\"led\":\"off\"}"));
  } else if (cmd.equalsIgnoreCase("READ")) {
    // lettura immediata
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (!isnan(h) && !isnan(t)) {
      Serial.print(F("{\"temp\":"));
      Serial.print(t, 1);
      Serial.print(F(",\"hum\":"));
      Serial.print((int)round(h));
      Serial.println(F("}"));
    } else {
      Serial.println(F("{\"error\":\"dht_read_failed\"}"));
    }
  } else {
    Serial.print(F("{\"echo\":\""));
    Serial.print(cmd);
    Serial.println(F("\"}"));
  }
}


  // 2) Letture DHT periodiche
  /*
  if (millis() - lastSend >= PERIOD_MS) {
    lastSend = millis();
    float h = dht.readHumidity();
    float t = dht.readTemperature(); // °C
    if (!isnan(h) && !isnan(t)) {
      // JSON compatto (newline-terminated)
      Serial.print(F("{\"temp\":"));
      Serial.print(t, 1);
      Serial.print(F(",\"hum\":"));
      Serial.print((int)round(h));
      Serial.println(F("}"));
    } else {
      Serial.println(F("{\"error\":\"dht_read_failed\"}"));
    }
  }*/
}
