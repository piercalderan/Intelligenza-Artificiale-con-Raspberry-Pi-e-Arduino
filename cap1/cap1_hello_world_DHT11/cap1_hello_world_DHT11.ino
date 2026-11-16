// REQUIRES the following Arduino libraries:
// - DHT Sensor Library: https://github.com/adafruit/DHT-sensor-library
// - Adafruit Unified Sensor Lib: https://github.com/adafruit/Adafruit_Sensor
#include <DHT.h> //https://github.com/adafruit/DHT-sensor-library

#define DHTPIN 2      // Pin collegato al sensore
#define DHTTYPE DHT11 // oppure DHT22

DHT dht(DHTPIN, DHTTYPE);

void setup() {
  Serial.begin(9600);
  dht.begin();
  delay(1000);
}

void loop() {
  float h = dht.readHumidity();
  float t = dht.readTemperature(); // Celsius

  if (isnan(h) || isnan(t)) {
    Serial.println("ERR"); // in caso di errore lettura
  } else {
    // invio in formato: TEMPERATURA,UMIDITÀ
    Serial.print(t, 1);
    Serial.print(',');
    Serial.println(h, 1);
  }

  delay(10000); // ogni 10 secondi
}
