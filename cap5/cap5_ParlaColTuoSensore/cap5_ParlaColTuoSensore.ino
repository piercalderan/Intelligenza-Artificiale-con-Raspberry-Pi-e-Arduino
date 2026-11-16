// CAP5_ParlaColTuoSensore.ino
#include <DHT.h>

#define DHTPIN 2
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

const int LED1 = 7;
const int LED2 = 8;
const int LED3 = 9;

const int BTN1 = 3;
const int BTN2 = 4;

bool blink3 = false;
unsigned long lastBlink = 0;
bool led3State = false;

void setup() {
  Serial.begin(9600);
  pinMode(LED1, OUTPUT);
  pinMode(LED2, OUTPUT);
  pinMode(LED3, OUTPUT);
  pinMode(BTN1, INPUT_PULLUP);
  pinMode(BTN2, INPUT_PULLUP);
  dht.begin();
  delay(1000);
  Serial.println("READY");
}

void handleCommand(String line) {
  line.trim();
  line.toUpperCase();
  if (line.startsWith("LED")) {
    // es. "LED 1 ON"
    int idx = line.substring(4,5).toInt();
    String action = line.substring(6);
    int pin = (idx==1)?LED1 : (idx==2)?LED2 : LED3;

    if (action == "ON")  { digitalWrite(pin, HIGH); if (idx==3) blink3=false; }
    if (action == "OFF") { digitalWrite(pin, LOW);  if (idx==3) blink3=false; }
    if (action == "BLINK" && idx==3) { blink3=true; }

    Serial.print("ACK LED "); Serial.print(idx);
    Serial.print(" "); Serial.println(action);
  }
  else if (line.startsWith("SENSOR?")) {
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (isnan(t) || isnan(h)) {
      Serial.println("SENSOR ERR");
    } else {
      Serial.print("SENSOR t=");
      Serial.print(t,1);
      Serial.print(" h=");
      Serial.println(h,0);
    }
  }
}

void loop() {
  // pulsanti (low = premuto)
  static bool b1prev=true, b2prev=true;
  bool b1 = digitalRead(BTN1);
  bool b2 = digitalRead(BTN2);
  if (b1==LOW && b1prev==HIGH) Serial.println("BTN 1 PRESSED");
  if (b2==LOW && b2prev==HIGH) Serial.println("BTN 2 PRESSED");
  b1prev=b1; b2prev=b2;

  // lampeggio LED3 se attivo
  if (blink3) {
    unsigned long now = millis();
    if (now - lastBlink > 400) {
      led3State = !led3State;
      digitalWrite(LED3, led3State ? HIGH : LOW);
      lastBlink = now;
    }
  }

  // comandi seriali
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    handleCommand(line);
  }
}
