// Setup in modalità BCM; ritorna 0 se ok, -1 se errore
int gpio_setup_bcm() {
    if (wiringPiSetupGpio() == -1) return -1;
    return 0;
}
void gpio_set_mode(int pin, int mode) {
    // mode: 0=input, 1=output
    pinMode(pin, mode ? OUTPUT : INPUT);
}
void gpio_pull(int pin, int pud) {
    // pud: 0=off, 1=down, 2=up
    pullUpDnControl(pin, pud);
}
void gpio_write(int pin, int value) {
    digitalWrite(pin, value ? HIGH : LOW);
}
int gpio_read(int pin) {
    return digitalRead(pin);
}
void gpio_pwm_mode_ms() {
    pwmSetMode(PWM_MODE_MS);
}
void gpio_pwm_write(int pin, int value) {
    pwmWrite(pin, value);
}
