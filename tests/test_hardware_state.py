"""Tests for the independent hardware read in app/lib/hardware_state.py."""

import unittest
from unittest.mock import MagicMock, patch

from app.lib import hardware_state as hs


class FakePi:
    """Stands in for a pigpio client."""

    def __init__(self, duty, rng, connected=True):
        self.duty = duty
        self.rng = rng
        self.connected = connected
        self.stopped = False

    def get_PWM_dutycycle(self, pin):
        return self.duty

    def get_PWM_range(self, pin):
        return self.rng

    def stop(self):
        self.stopped = True


class DutyFractionTestCase(unittest.TestCase):
    """The pin's duty cycle must be read on pigpiod's scale, not 0-255.

    gpiozero writes 0.0-1.0 and pigpiod stores it on its own configurable
    range, measured at 10000 on this hardware. A hardcoded /255 turns a 50%
    light into 3921%.
    """

    def test_uses_the_pins_own_range(self):
        pi = FakePi(5000, 10000)
        self.assertAlmostEqual(hs.duty_fraction(18, pi=pi), 0.5, places=4)

    def test_full_and_empty(self):
        self.assertAlmostEqual(hs.duty_fraction(18, pi=FakePi(10000, 10000)), 1.0)
        self.assertAlmostEqual(hs.duty_fraction(18, pi=FakePi(0, 10000)), 0.0)

    def test_a_255_range_machine_still_works(self):
        self.assertAlmostEqual(hs.duty_fraction(18, pi=FakePi(128, 255)), 128 / 255, places=4)

    def test_clamps_a_nonsense_reading(self):
        self.assertEqual(hs.duty_fraction(18, pi=FakePi(99999, 10000)), 1.0)
        self.assertEqual(hs.duty_fraction(18, pi=FakePi(-5, 10000)), 0.0)

    def test_returns_none_for_a_zero_range(self):
        self.assertIsNone(hs.duty_fraction(18, pi=FakePi(100, 0)))

    def test_returns_none_when_pigpiod_is_not_connected(self):
        self.assertIsNone(hs.duty_fraction(18, pi=FakePi(100, 10000, connected=False)))

    def test_returns_none_when_the_read_raises(self):
        pi = MagicMock()
        pi.connected = True
        pi.get_PWM_dutycycle.side_effect = OSError("socket closed")
        self.assertIsNone(hs.duty_fraction(18, pi=pi))

    def test_closes_a_connection_it_opened_itself(self):
        pi = FakePi(100, 10000)
        fake = MagicMock()
        fake.pi.return_value = pi
        with patch.dict("sys.modules", {"pigpio": fake}):
            hs.duty_fraction(18)
        self.assertTrue(pi.stopped, "a connection opened here must be closed here")

    def test_does_not_close_a_connection_it_was_given(self):
        pi = FakePi(100, 10000)
        hs.duty_fraction(18, pi=pi)
        self.assertFalse(pi.stopped, "the caller's pigpio connection must be left open")


class PwmClaimTestCase(unittest.TestCase):
    """A released pin still reads a healthy-looking duty cycle.

    gpiozero releases GPIO when a Light is collected, and the 06:00 sunrise
    ramp is a short-lived process: when it exits, pigpiod drops its PWM claim
    while the register keeps the value it last held. That is how the light read
    "30%" and was not actually being driven.
    """

    def test_true_when_the_write_succeeds(self):
        pi = MagicMock()
        pi.connected = True
        pi.get_PWM_dutycycle.return_value = 3000
        self.assertTrue(hs.pwm_claimed(18, pi=pi))

    def test_false_when_the_write_is_rejected(self):
        pi = MagicMock()
        pi.connected = True
        pi.get_PWM_dutycycle.return_value = 3000
        pi.set_PWM_dutycycle.side_effect = Exception("GPIO is not in use for PWM")
        self.assertFalse(hs.pwm_claimed(18, pi=pi))

    def test_none_when_pigpiod_is_unreachable(self):
        pi = MagicMock()
        pi.connected = False
        self.assertIsNone(hs.pwm_claimed(18, pi=pi))

    def test_rewrites_the_same_value_so_nothing_changes(self):
        pi = MagicMock()
        pi.connected = True
        pi.get_PWM_dutycycle.return_value = 3000
        hs.pwm_claimed(18, pi=pi)
        pi.set_PWM_dutycycle.assert_called_once_with(18, 3000)

    def _verify(self, claimed, actual):
        with (
            patch.object(hs, "light_actual", return_value=actual),
            patch.object(hs, "pwm_claimed", return_value=claimed),
            patch(
                "app.sensors.schedule.schedule.light_state_now",
                return_value={"on": True, "brightness": 30, "reason": "inside"},
            ),
            patch("app.lib.state.load_state", return_value={"light_on": True, "brightness": 30}),
        ):
            return hs.verify_lights({})

    def test_a_lost_claim_is_a_fault_even_when_the_register_looks_fine(self):
        # This is the exact state of the Pi at 06:19 this morning.
        r = self._verify(False, {"on": True, "brightness": 30.0})
        self.assertFalse(r["ok"])
        self.assertIn("released the PWM claim", r["detail"])

    def test_a_held_claim_with_a_matching_level_is_ok(self):
        self.assertTrue(self._verify(True, {"on": True, "brightness": 30.0})["ok"])

    def test_an_unreadable_claim_does_not_manufacture_a_fault(self):
        # None means "could not tell", which is not the same as "lost".
        r = self._verify(None, {"on": True, "brightness": 30.0})
        self.assertTrue(r["ok"])
        self.assertIsNone(r["pwm_claimed"])


if __name__ == "__main__":

    def test_light_actual_reads_the_light_pin(self):
        with patch.object(hs, "duty_fraction", return_value=0.3) as m:
            got = hs.light_actual()
        self.assertEqual(got, {"on": True, "brightness": 30.0})
        self.assertEqual(m.call_args.args[0], 18)

    def test_a_dark_pin_is_off(self):
        with patch.object(hs, "duty_fraction", return_value=0.0):
            self.assertFalse(hs.light_actual()["on"])

    def test_an_unreadable_pin_is_unknown_not_off(self):
        # Reporting a dead pigpiod as "the lights are off" would look exactly
        # like the fault this whole module exists to detect.
        with patch.object(hs, "duty_fraction", return_value=None):
            self.assertIsNone(hs.light_actual())
            self.assertIsNone(hs.pump_actual())

    def test_pump_actual_reads_the_pump_pin(self):
        with patch.object(hs, "duty_fraction", return_value=0.5) as m:
            got = hs.pump_actual()
        self.assertEqual(got, {"on": True, "speed": 50.0})
        self.assertEqual(m.call_args.args[0], 24)


class VerifyLightsTestCase(unittest.TestCase):
    """expected vs commanded vs actual."""

    ON = {"on": True, "brightness": 30, "reason": "inside"}
    OFF = {"on": False, "brightness": None, "reason": "outside"}

    def _verify(self, expected, actual, state=None):
        with (
            patch.object(hs, "light_actual", return_value=actual),
            patch("app.sensors.schedule.schedule.light_state_now", return_value=expected),
            patch(
                "app.lib.state.load_state",
                return_value=state if state is not None else {"light_on": True, "brightness": 30},
            ),
        ):
            return hs.verify_lights({})

    def test_catches_a_pin_someone_else_zeroed(self):
        # The API's own cache still says 30; the pin disagrees. This is the
        # capture-script bug, and it is invisible without reading the pin.
        r = self._verify(self.ON, {"on": False, "brightness": 0.0})
        self.assertFalse(r["ok"])
        self.assertIn("pin is off", r["detail"])

    def test_catches_a_light_left_on_outside_the_window(self):
        r = self._verify(self.OFF, {"on": True, "brightness": 30.0})
        self.assertFalse(r["ok"])

    def test_reports_no_verdict_when_the_schedule_has_no_opinion(self):
        r = self._verify(None, {"on": False, "brightness": 0.0})
        self.assertIsNone(r["ok"])

    def test_reports_no_verdict_when_the_pin_cannot_be_read(self):
        r = self._verify(self.ON, None)
        self.assertIsNone(r["ok"])
        self.assertIn("could not be read", r["detail"])

    def test_keeps_the_three_numbers_separate(self):
        r = self._verify(self.ON, {"on": False, "brightness": 0.0})
        self.assertEqual(r["expected"]["brightness"], 30)
        self.assertEqual(r["commanded"]["brightness"], 30)
        self.assertEqual(r["actual"]["brightness"], 0.0)


if __name__ == "__main__":
    unittest.main()
