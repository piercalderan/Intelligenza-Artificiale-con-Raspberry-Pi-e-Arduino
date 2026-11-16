#include <DHT.h>
#define DHTPIN 7
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);
unsigned long last=0;

void setup() {
  Serial.begin(115200);
  dht.begin();
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  // comandi da seriale "LED_ON\n" / "LED_OFF\n"
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "LED_ON")  digitalWrite(LED_BUILTIN, HIGH);
    if (cmd == "LED_OFF") digitalWrite(LED_BUILTIN, LOW);
  }

  if (millis()-last >= 3000) {
    last = millis();
    float h = dht.readHumidity();
    float t = dht.readTemperature(); // °C
    if (isnan(h) || isnan(t)) return;
    Serial.print("{\"temp\":"); Serial.print(t,1);
    Serial.print(",\"hum\":");   Serial.print(h,0);
    Serial.print(",\"ts\":\"");  Serial.print(millis()); Serial.println("\"}");
  }
}
