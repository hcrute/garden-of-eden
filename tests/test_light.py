import os
import sys
import unittest
from unittest.mock import patch

# Add the root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.sensors.light.light import Light


class TestLight(unittest.TestCase):
    @patch("app.sensors.light.light.PWMLED")
    @patch("app.sensors.light.light.PiGPIOFactory")
    @patch("app.sensors.light.light.pigpio.pi")
    def setUp(self, MockPi, MockFactory, MockPWMLED):
        self.mock_led = MockPWMLED.return_value
        self.mock_led.value = 0
        self.mock_pi = MockPi.return_value
        self.light = Light(18)

    def test_turn_on_from_0(self):
        self.mock_led.value = 0
        self.light.on()
        self.assertEqual(self.mock_led.value, 1)
        self.assertLogs("Turning light on")

    def test_turn_on_from_nonzero(self):
        self.mock_led.value = 0.5
        self.light.on()
        self.assertEqual(self.mock_led.value, 0.5)
        self.assertLogs("Light already on, skipping")

    def test_off(self):
        self.mock_led.value = 1
        self.light.off()
        self.assertEqual(self.mock_led.value, 0)
        self.assertLogs("Turning light off")

    def test_set_brightness_valid(self):
        valid_brightness = 70
        self.light.set_brightness(valid_brightness)
        self.assertEqual(self.mock_led.value * 100, valid_brightness)

    def test_set_brightness_invalid(self):
        with self.assertRaises(ValueError):
            self.light.set_brightness(110)

    def test_set_frequency(self):
        freq = 10000  # 10kHz
        self.light.set_frequency(freq)
        self.mock_pi.set_PWM_frequency.assert_called_with(18, freq)

    def test_close(self):
        self.light.close()
        self.mock_led.close.assert_called_once()


class LightConstructionIsNonDestructive(unittest.TestCase):
    """Constructing a Light must not switch the real light off.

    Regression: PWMLED defaults to value=0 and drives the pin low the moment it
    is constructed, so every Light() zeroed the pin. The API hid this -- its
    Light is a module singleton built at import -- but app/__init__.py builds
    the routes, and therefore the devices, for *any* process that imports
    anything under app.*. scripts/capture-frames.sh does exactly that, so the
    hourly timelapse capture switched the light off with nothing logged and no
    cron entry involved.
    """

    def _build(self, dutycycle):
        with (
            patch("app.sensors.light.light.PWMLED") as mock_led,
            patch("app.sensors.light.light.PiGPIOFactory"),
            patch("app.sensors.light.light.pigpio.pi") as mock_pi,
        ):
            mock_pi.return_value.get_PWM_dutycycle.return_value = dutycycle
            light = Light(18)
            return mock_led, light

    def test_construction_preserves_a_lit_pin(self):
        mock_led, _ = self._build(128)  # ~50% on a 0-255 scale
        self.assertAlmostEqual(mock_led.call_args.kwargs["initial_value"], 128 / 255, places=3)

    def test_construction_preserves_a_full_pin(self):
        mock_led, _ = self._build(255)
        self.assertAlmostEqual(mock_led.call_args.kwargs["initial_value"], 1.0, places=3)

    def test_construction_preserves_an_off_pin(self):
        mock_led, _ = self._build(0)
        self.assertEqual(mock_led.call_args.kwargs["initial_value"], 0.0)

    def test_construction_never_exceeds_full_scale(self):
        # A driver that returned nonsense must not wrap into a valid-looking
        # duty cycle and overdrive the MOSFET.
        mock_led, _ = self._build(9999)
        self.assertLessEqual(mock_led.call_args.kwargs["initial_value"], 1.0)

    def test_unreadable_pin_falls_back_to_off_rather_than_raising(self):
        with (
            patch("app.sensors.light.light.PWMLED") as mock_led,
            patch("app.sensors.light.light.PiGPIOFactory"),
            patch("app.sensors.light.light.pigpio.pi") as mock_pi,
        ):
            mock_pi.return_value.get_PWM_dutycycle.side_effect = OSError("pigpiod went away")
            Light(18)  # must not raise: construction runs at import time
            self.assertEqual(mock_led.call_args.kwargs["initial_value"], 0.0)


if __name__ == "__main__":
    unittest.main()
