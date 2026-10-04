import argparse
import logging
import time

import pigpio
from gpiozero import PWMLED
from gpiozero.pins.pigpio import PiGPIOFactory

import config
from app.lib.hardware import GPIOController


class Light:
    def __init__(self, pin=config.LIGHT_PIN, frequency=config.LIGHT_FREQUENCY, pin_factory=None):
        # pigpiod is running on port 8888
        # Note: for docker: PiGPIOFactory(host='pigpiod', port=8888)
        self.pin = pin
        self.pin_factory = pin_factory if pin_factory else PiGPIOFactory()
        self.gpio = GPIOController(pin, self.pin_factory, pigpio.pi)
        # Seed PWMLED with whatever the pin is already doing.
        #
        # PWMLED defaults to value=0 and drives the pin low the moment it is
        # constructed, so creating a Light used to switch the real light off.
        # That was invisible from the API -- its Light is a long-lived module
        # singleton created at import -- but every other process that imports
        # anything under app.* (and therefore app/__init__.py, which builds the
        # routes and their devices) paid for it. The hourly timelapse capture
        # was the worst offender: it turned the light off once an hour, on no
        # logged write, because the damage happens inside gpiozero.
        # NB initial_value, not value: PWMLED's constructor keyword is
        # initial_value. Passing `value` raises TypeError from
        # PWMOutputDevice.__init__, which the routes catch and swallow -- the
        # device then silently never initialises at all.
        self.led = PWMLED(
            self.pin,
            pin_factory=self.pin_factory,
            initial_value=self._current_value(),
        )
        self.set_frequency(frequency)

    def _current_value(self):
        """The pin's present duty cycle as a 0.0-1.0 fraction.

        pigpio reports PWM duty cycle on 0-255; PWMLED takes 0.0-1.0. Falls
        back to 0 (light off) if the pin cannot be read, which is the safe
        direction for a device that is merely being constructed.
        """
        try:
            duty = float(self.gpio.pi.get_PWM_dutycycle(self.pin))
        except Exception as exc:  # noqa: BLE001 - construction must not raise
            logging.warning("Could not read current duty cycle for pin %s: %s", self.pin, exc)
            return 0.0
        return max(0.0, min(1.0, duty / 255.0))

    def on(self):
        """
        Turn on lights.
        """
        if self.led.value > 0:
            logging.info("Light already on, skipping")
            return

        logging.info("Turning light on")
        self.led.value = 1

    def off(self):
        """
        Turn off lights.
        """
        logging.info("Turning light off")
        self.led.value = 0

    def set_brightness(self, brightness_percentage):
        """
        Wrapper function around set_duty_cycle. Provides more intuitive function name.

        Args:
        - brightness_percentage (int): A value between 0 (off) and 100 (max brightness).
        """
        self.set_duty_cycle(brightness_percentage)

    def get_brightness(self):
        """
        Wrapper function around get_duty_cycle. Provides more intuitive function name.

        Returns:
        - float: The current duty cycle percentage.
        """
        return self.get_duty_cycle()

    def set_frequency(self, frequency):
        logging.info(f"Setting light frequency to {frequency}")
        self.gpio.set_frequency(frequency)

    def set_duty_cycle(self, duty_cycle_percentage):
        """
        Set the duty cycle percentage, i.e. brightness level.

        Args:
        - duty_cycle_percentage (int): A value between 0 (off) and 100 (full brightness).
        """
        if 0 <= duty_cycle_percentage <= 100:
            # gpiozero's PWMLED uses a 0-1 scale for duty cycle
            duty = duty_cycle_percentage / 100.0
            logging.info(f"Setting light duty_cycle to {duty_cycle_percentage}%")
            self.led.value = duty
        else:
            raise ValueError("Speed must be between 0 and 100")

    def get_duty_cycle(self):
        """
        Get the current duty cycle percentage.

        Returns:
        - float: The current duty cycle percentage.
        """
        duty_cycle = self.led.value * 100
        logging.info(f"Light duty_cycle is {duty_cycle}%")
        return duty_cycle

    def close(self):
        self.led.close()


def ramp_to(light, target, minutes):
    """Gradually move brightness from its current level to ``target`` over
    ``minutes`` (sunrise/sunset). ``target`` of 0 ends with the light off."""
    target = max(0, min(100, int(target)))
    start = light.get_brightness()
    total = max(0, int(minutes)) * 60
    if total <= 0:
        light.set_brightness(target)
        return
    steps = max(1, min(int(total), 60))  # at most ~1 update/sec, capped at 60
    delay = total / steps
    for i in range(1, steps + 1):
        value = start + (target - start) * i / steps
        light.set_brightness(int(round(value)))
        if i < steps:
            time.sleep(delay)
    light.set_brightness(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Control an IoT light.")
    parser.add_argument("--on", action="store_true", help="Turn the light on.")
    parser.add_argument("--off", action="store_true", help="Turn the light off.")
    parser.add_argument(
        "--brightness", type=int, default=None, help="Set the brightness level (0-100)."
    )
    parser.add_argument(
        "--ramp-minutes",
        type=int,
        default=0,
        help="Gradually ramp to --brightness over this many minutes (sunrise/sunset).",
    )

    args = parser.parse_args()

    light = Light()  # pins/frequency from config

    if args.ramp_minutes and args.brightness is not None:
        ramp_to(light, args.brightness, args.ramp_minutes)
    elif args.on:
        light.on()
        if args.brightness is not None:
            light.set_brightness(args.brightness)
    elif args.off:
        light.off()
    elif args.brightness is not None:
        light.on()
        light.set_brightness(args.brightness)
    else:
        logging.info("No action specified. Use --on, --off, or --brightness.")
