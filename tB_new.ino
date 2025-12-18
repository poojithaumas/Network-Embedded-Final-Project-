#include <WiFi.h>

// ---------------- IDENTIFY THIS DEVICE ----------------
// THIS IS ESP32-B
#define DEVICE_ID "B"

// ---------------- PIN DEFINITIONS ----------------
#define TRIG_PIN 4
#define ECHO_PIN 15
#define LED_PIN 2

// ---------------- WIFI CONFIG --------------------
const char* ssid = "147A1";
const char* password = "12345678";
const char* host = "10.0.0.193";
const uint16_t port = 5000;

WiFiClient client;

// ---------------- ISR VARIABLES ------------------
volatile unsigned long rising_ts_us = 0;
volatile unsigned long falling_ts_us = 0;
volatile bool echo_done = false;

// Event index
unsigned long event_count = 0;

// ---------------- ISR HANDLER --------------------
void IRAM_ATTR echoISR() {
  unsigned long now = esp_timer_get_time();

  if (digitalRead(ECHO_PIN) == HIGH) {
    rising_ts_us = now;
  } else {
    falling_ts_us = now;
    echo_done = true;
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);

  attachInterrupt(digitalPinToInterrupt(ECHO_PIN), echoISR, CHANGE);

  digitalWrite(TRIG_PIN, LOW);
  digitalWrite(LED_PIN, LOW);

  // ---------------- WIFI CONNECT ------------------
  Serial.print("Connecting to WiFi");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(300);
  }
  Serial.println("\nWiFi Connected!");

  // ---------------- TCP CONNECT -------------------
  Serial.print("Connecting to server");
  while (!client.connect(host, port)) {
    Serial.print(".");
    delay(500);
  }
  Serial.println("\nConnected to Python server!");

  // ---------------- SEND DEVICE ID ----------------
  Serial.print("Sending ID: ");
  Serial.println(DEVICE_ID);

  client.print("ID:");
  client.println(DEVICE_ID);

  // Send boot timestamp also (for reboot detection)
  client.print("BOOT=");
  client.println(esp_timer_get_time());
}

void loop() {
  echo_done = false;

  // Trigger ultrasonic pulse
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long start = millis();
  while (!echo_done && millis() - start < 50);

  if (echo_done) {
    unsigned long pw = falling_ts_us - rising_ts_us;
    float distance_cm = (pw * 0.0343) / 2.0;

    if (distance_cm > 0 && distance_cm < 35) {

      event_count++;

      // *********** CORRECT B OUTPUT ************
      Serial.print("t");
      Serial.print(DEVICE_ID);   // prints "tB"
      Serial.print(event_count);
      Serial.print(" = ");
      Serial.println(rising_ts_us);
      // *****************************************

      // Send to Python
      if (client.connected()) {
        client.print("t");
        client.print(DEVICE_ID);
        client.print(event_count);
        client.print("=");
        client.println(rising_ts_us);
      }

      // LED blink
      digitalWrite(LED_PIN, HIGH);
      delay(30);
      digitalWrite(LED_PIN, LOW);
    }

  }

  delay(100);
}
