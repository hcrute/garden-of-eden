import os
import sys
import unittest
import unittest.mock
from unittest.mock import patch

# Add the root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.lib.guards import check_sensor_guard
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

    Regression: PWMLED defaults to initial_value=0 and drives the pin low at
    construction time, so every Light() zeroed the pin. The API hid this -- its
    Light is a module singleton built at import -- but app/__init__.py builds
    the routes, and therefore the devices, for *any* process that imports
    anything under app.*. scripts/capture-frames.sh does exactly that, so the
    hourly timelapse capture switched the light off with nothing logged and no
    cron entry involved.

    The value comes from the persisted actuator state, not from reading the
    pin: gpiozero's pigpio pins are PinPWMFixedValue (always 0-255) while
    pigpio's configured range on this hardware is 10000, so the two scales
    cannot be converted between. An earlier attempt divided the raw duty cycle
    by a hardcoded 255 and drove the light to full on every capture.
    """

    def _initial_value(self, state):
        """The value Light.__init__ would seed PWMLED with."""
        with (
            patch("app.sensors.light.light.PWMLED") as mock_led,
            patch("app.sensors.light.light.PiGPIOFactory"),
            patch("app.sensors.light.light.pigpio.pi"),
            patch("app.lib.state.load_state", return_value=state),
        ):
            Light(18)
            return mock_led.call_args.kwargs["initial_value"]

    def test_seeds_the_persisted_brightness(self):
        got = self._initial_value({"light_on": True, "brightness": 45})
        self.assertAlmostEqual(got, 0.45, places=3)

    def test_starts_off_when_the_state_says_off(self):
        # The reported brightness may still be 45 from earlier in the day; what
        # matters is that the light is off.
        self.assertEqual(self._initial_value({"light_on": False, "brightness": 45}), 0.0)

    def test_starts_off_when_there_is_no_saved_state(self):
        self.assertEqual(self._initial_value({}), 0.0)

    def test_clamps_a_nonsense_brightness(self):
        self.assertEqual(self._initial_value({"light_on": True, "brightness": 9999}), 1.0)
        self.assertEqual(self._initial_value({"light_on": True, "brightness": -5}), 0.0)

    def test_survives_a_non_numeric_brightness(self):
        self.assertEqual(self._initial_value({"light_on": True, "brightness": "bright"}), 0.0)

    def test_unreadable_state_falls_back_to_off_rather_than_raising(self):
        with (
            patch("app.sensors.light.light.PWMLED") as mock_led,
            patch("app.sensors.light.light.PiGPIOFactory"),
            patch("app.sensors.light.light.pigpio.pi"),
            patch("app.lib.state.load_state", side_effect=OSError("no state file")),
        ):
            Light(18)  # must not raise: construction runs at import time
            self.assertEqual(mock_led.call_args.kwargs["initial_value"], 0.0)

    def test_uses_initial_value_not_value(self):
        """PWMLED's constructor keyword is initial_value, not value.

        Passing `value` raises TypeError from PWMOutputDevice.__init__, which
        the routes catch and log as "Failed to initialize" -- leaving
        light_control as None and every light endpoint dead. Mocking PWMLED hid
        this completely, so the real signature is checked here.

        Skipped where gpiozero is the tests/_hwstub.py stand-in, which has no
        real signature to inspect; this is a check that needs the real library,
        so it belongs on the Pi, not on a laptop.
        """
        import inspect

        import gpiozero

        from app.sensors.light import light as light_module

        # tests/_hwstub.py substitutes a synthetic module with no __file__.
        if not getattr(gpiozero, "__file__", None):
            self.skipTest("gpiozero is stubbed on this host")
        params = inspect.signature(gpiozero.PWMLED.__init__).parameters
        self.assertIn("initial_value", params)
        self.assertNotIn("value", params)
        source = inspect.getsource(light_module.Light.__init__)
        self.assertIn("initial_value=self._initial_value()", source)


if __name__ == "__main__":
    unittest.main()


class ReclaimOnLostPwmClaim(unittest.TestCase):
    """The light routes must survive another process releasing the pin.

    gpiozero releases GPIO when a Light is collected. The 06:00 sunrise ramp is
    a short-lived process, so when it exits pigpiod drops its PWM claim on the
    pin and the long-lived API -- holding a Light since yesterday -- can no
    longer write to it. Every light request then fails with 'GPIO is not in use
    for PWM' while the duty-cycle register still reports the old brightness.
    """

    def setUp(self):
        import app.sensors.light.routes as routes

        self.routes = routes
        self.app = create_app("default")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.routes.check_sensor = check_sensor_guard(
            sensor=self.routes.light_control, sensor_name="Light"
        )
        # Rebuild the wrapped views so they close over the patched guard.
        for rule in self.app.url_map.iter_rules():
            pass

    def test_reclaim_rebuilds_the_device(self):
        with patch.object(self.routes, "LightControl") as new_cls:
            new_cls.return_value = unittest.mock.MagicMock()
            self.assertTrue(self.routes.reclaim_light())
        new_cls.assert_called_once()

    def test_reclaim_survives_a_device_that_will_not_close(self):
        old = unittest.mock.MagicMock()
        old.close.side_effect = RuntimeError("already gone")
        with (
            patch.object(self.routes, "light_control", old),
            patch.object(self.routes, "LightControl") as new_cls,
        ):
            new_cls.return_value = unittest.mock.MagicMock()
            self.assertTrue(self.routes.reclaim_light())

    def test_reclaim_reports_failure_when_the_pin_cannot_be_rebuilt(self):
        with patch.object(self.routes, "LightControl", side_effect=OSError("pigpiod down")):
            self.assertFalse(self.routes.reclaim_light())

    def test_retry_wrapper_reruns_after_reclaiming(self):
        calls = []

        @self.routes.with_reclaim
        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise Exception("GPIO is not in use for PWM")
            return "done"

        with patch.object(self.routes, "reclaim_light", return_value=True):
            self.assertEqual(flaky(), "done")
        self.assertEqual(len(calls), 2)

    def test_retry_wrapper_does_not_swallow_other_errors(self):
        @self.routes.with_reclaim
        def broken():
            raise OSError("i2c bus gone")

        with patch.object(self.routes, "reclaim_light") as rec:
            with self.assertRaises(OSError):
                broken()
        rec.assert_not_called()

    def test_retry_wrapper_reraises_when_reclaim_fails(self):
        @self.routes.with_reclaim
        def flaky():
            raise Exception("GPIO is not in use for PWM")

        with patch.object(self.routes, "reclaim_light", return_value=False):
            with self.assertRaises(Exception):
                flaky()
