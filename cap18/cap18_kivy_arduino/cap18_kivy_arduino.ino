#include <DHT.h>
#include <Servo.h>

#define DHTPIN 7
#define DHTTYPE DHT11
#define SERVO_PIN 12
#define LED_PIN 9
#define BTN_PIN 2

DHT dht(DHTPIN, DHTTYPE);
Servo myservo;

volatile bool btnPressed = false;
bool btnLast = HIGH;      // pull-up interno
bool ledState = LOW;      // LED logico (0/1)
int  servoDeg = 90;

unsigned long lastTelem = 0;

void setup() {
  Serial.begin(115200);
  dht.begin();
  pinMode(LED_PIN, OUTPUT);
  pinMode(BTN_PIN, INPUT_PULLUP);
  myservo.attach(SERVO_PIN);
  myservo.write(servoDeg);
  digitalWrite(LED_PIN, ledState ? HIGH : LOW);

  delay(200);
  Serial.println(F("HELLO:KIVYARDUINO"));
  sendStat(); // prima stat all’avvio
}

void loop() {
  // --- Debounce pulsante ---
  bool b = digitalRead(BTN_PIN);
  if (b != btnLast) {
    delay(10);
    b = digitalRead(BTN_PIN);
    if (b != btnLast) {
      btnLast = b;
      if (b == LOW) { // pressione
        // Trattiamo il "momentary" come toggle logico
        ledState = !ledState;
        digitalWrite(LED_PIN, ledState ? HIGH : LOW);
        Serial.print(F("EVT:BTN:TOGGLE:"));
        Serial.println(ledState ? 1 : 0);
        sendStat(); // aggiorna subito
      }
    }
  }

  // --- Telemetria periodica ---
  if (millis() - lastTelem > 12000) {
    lastTelem = millis();
    sendStat();
  }

  // --- Comandi seriali ---
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) return;

    if (line.startsWith("LED")) {
      int v = parseIntAfterSpace(line);
      ledState = (v != 0);
      digitalWrite(LED_PIN, ledState ? HIGH : LOW);
      sendStat();
    } else if (line.startsWith("SERVO")) {
      int v = parseIntAfterSpace(line);
      v = constrain(v, 0, 180);
      servoDeg = v;
      myservo.write(servoDeg);
      sendStat();
    } else if (line == "ASK") {
      sendStat();
    } else {
      // Unknown -> ignore
    }
  }
}

int parseIntAfterSpace(const String &s) {
  int idx = s.indexOf(' ');
  if (idx < 0) return 0;
  return s.substring(idx + 1).toInt();
}

void sendStat() {
  float t = dht.readTemperature();   // °C
  float h = dht.readHumidity();      // %
  // In caso di letture NaN, inviamo -100 per segnalare errore (gestito lato Kivy)
  if (isnan(t)) t = -100.0;
  if (isnan(h)) h = -1.0;

  Serial.print(F("STAT:T=")); Serial.print(t, 1);
  Serial.print(F(";H=")); Serial.print(h, 0);
  Serial.print(F(";BTN=")); Serial.print((btnLast == LOW) ? 1 : 0); // stato fisico (premuto = 1 finché giù)
  Serial.print(F(";LED=")); Serial.print(ledState ? 1 : 0);
  Serial.print(F(";SERVO=")); Serial.print(servoDeg);
  Serial.println();
}
