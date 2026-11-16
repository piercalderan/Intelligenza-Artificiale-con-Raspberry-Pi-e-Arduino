//cap6_mqtt_serial_arduino.ino
#include <DHT.h>

#define DHTPIN 7
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

#define LED1 2
#define LED2 3
#define LED3 4
#define BTN1 5
#define BTN2 6

unsigned long lastBlink = 0;
bool blinkActive = false;
int blinkLed = -1;
int blinkInterval = 500; // ms
bool blinkState = false;

void setup() {
  Serial.begin(9600);
  dht.begin();

  pinMode(LED1, OUTPUT);
  pinMode(LED2, OUTPUT);
  pinMode(LED3, OUTPUT);
  pinMode(BTN1, INPUT_PULLUP);
  pinMode(BTN2, INPUT_PULLUP);

  Serial.println(F("READY"));
}

void loop() {
  handleBlink();
  handleButtons();
  handleSerial();
}

// --- gestione lampeggio automatico ---
void handleBlink() {
  if (!blinkActive) return;

  unsigned long now = millis();
  if (now - lastBlink >= blinkInterval) {
    lastBlink = now;
    blinkState = !blinkState;
    digitalWrite(blinkLed, blinkState ? HIGH : LOW);
  }
}

// --- lettura pulsanti ---
void handleButtons() {
  static bool lastBtn1 = HIGH, lastBtn2 = HIGH;
  bool b1 = digitalRead(BTN1);
  bool b2 = digitalRead(BTN2);

  if (b1 != lastBtn1 && b1 == LOW) {
    Serial.println(F("BTN 1 PRESSED"));
  }
  if (b2 != lastBtn2 && b2 == LOW) {
    Serial.println(F("BTN 2 PRESSED"));
  }

  lastBtn1 = b1;
  lastBtn2 = b2;
}

// --- lettura comandi seriali ---
void handleSerial() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("LED")) {
    handleLedCommand(cmd);
  } 
  else if (cmd.startsWith("SENSOR?")) {
    handleSensorRead();
  }
}

// --- gestione LED ON/OFF/BLINK/BLINK OFF ---
void handleLedCommand(String cmd) {
  int ledIndex = cmd.substring(4, 5).toInt();
  String action = cmd.substring(6);
  action.trim();

  int pin;
  switch (ledIndex) {
    case 1: pin = LED1; break;
    case 2: pin = LED2; break;
    case 3: pin = LED3; break;
    default:
      Serial.println(F("ACK LED ERR INVALID_INDEX"));
      return;
  }

  if (action == "ON") {
    digitalWrite(pin, HIGH);
    if (blinkLed == pin) blinkActive = false;
    Serial.print(F("ACK LED "));
    Serial.print(ledIndex);
    Serial.println(F(" ON"));
  } 
  else if (action == "OFF") {
    digitalWrite(pin, LOW);
    if (blinkLed == pin) blinkActive = false;
    Serial.print(F("ACK LED "));
    Serial.print(ledIndex);
    Serial.println(F(" OFF"));
  } 
  else if (action == "BLINK") {
    blinkLed = pin;
    blinkActive = true;
    Serial.print(F("ACK LED "));
    Serial.print(ledIndex);
    Serial.println(F(" BLINK"));
  } 
  else if (action == "BLINK OFF") {
    if (blinkLed == pin) {
      blinkActive = false;
      digitalWrite(pin, LOW);
      Serial.print(F("ACK LED "));
      Serial.print(ledIndex);
      Serial.println(F(" BLINK OFF"));
    }
  } 
  else {
    Serial.print(F("ACK LED "));
    Serial.print(ledIndex);
    Serial.println(F(" ERR_UNKNOWN"));
  }
}

// --- lettura DHT11 ---
void handleSensorRead() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println(F("SENSOR ERR"));
    return;
  }
  Serial.print(F("SENSOR t="));
  Serial.print(t, 1);
  Serial.print(F(" h="));
  Serial.println(h, 0);
}
