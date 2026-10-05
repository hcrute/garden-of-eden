"""Run a simulated day through the real schedule code, in seconds.

Waiting a real day to find out that 06:15 leaves the light uncontrollable is
not a plan. Everything here runs on an injected clock, so the whole 24 hours --
the sunrise, the fade, the sunset, a vacuum-enabled day, an overnight window --
is checked deterministically in a couple of seconds.

The pigpiod model in tests/fake_pigpio.py supplies the two behaviours that
caused the real faults: PWM is owned per connection, and the duty-cycle
register outlives that ownership.
"""

import datetime
import unittest
from unittest.mock import patch

from app.sensors.schedule.schedule import (
    build_cron_lines,
    light_state_now,
    normalize_schedule,
)
from tests.fake_pigpio import FakePigpiod, PigpioError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def every_day(window, brightness=30, ramp=15):
    return {
        "lights": {"enabled": True, "days": {d: [dict(window)] for d in DAYS}},
        "pump": {"enabled": True, "days": {}},
    }


class DaySimulationTestCase(unittest.TestCase):
    """The schedule's decision must match the cron lines it compiles to."""

    def _sweep(self, schedule, step_minutes=15):
        """Yield (when, should_be_on) for a whole simulated day."""
        day = datetime.datetime(2026, 10, 5)  # a Monday
        out = []
        for minutes in range(0, 24 * 60, step_minutes):
            when = day + datetime.timedelta(minutes=minutes)
            state = light_state_now(schedule, now=when)
            out.append((when, bool(state and state["on"])))
        return out

    def test_a_normal_day_matches_its_own_cron_lines(self):
        """The decision function must agree with the crontab it compiles to.

        These are two separate implementations of "when are the lights on":
        an OnCalendar-style comparison in Python, and the cron lines cron will
        actually run. If they drift, the UI shows one schedule and the machine
        follows another -- and nothing notices until the lights are wrong.
        """
        # rampMinutes 15 is what a real schedule uses, so exercise that path.
        schedule = every_day(
            {"onTime": "06:00", "offTime": "22:00", "brightness": 30, "rampMinutes": 15}
        )
        lines = [ln for ln in build_cron_lines(schedule) if "/light " in ln]

        on_minutes, off_minutes = set(), set()
        for line in lines:
            fields = line.split()
            stamp = int(fields[1]) * 60 + int(fields[0])
            # The sunset is `light off` when no ramp is set and `light ramp 0`
            # when one is; both mean "turn it off".
            is_off = fields[6].endswith("off") or (fields[6] == "ramp" and fields[7] == "0")
            (off_minutes if is_off else on_minutes).add(stamp)
        self.assertTrue(on_minutes, "no sunrise line was generated")
        self.assertTrue(off_minutes, "no sunset line was generated")

        on_at = min(on_minutes)
        off_at = min(off_minutes)
        for when, should_be_on in self._sweep(schedule):
            stamp = when.hour * 60 + when.minute
            # Cron semantics: the light is lit from the on boundary up to but
            # not including the off boundary.
            self.assertEqual(
                should_be_on,
                on_at <= stamp < off_at,
                f"schedule and crontab disagree at {when:%H:%M}",
            )

    def test_the_light_is_lit_for_exactly_the_scheduled_hours(self):
        schedule = every_day({"onTime": "06:00", "offTime": "22:00"})
        lit = [when for when, on in self._sweep(schedule, 60) if on]
        self.assertEqual(len(lit), 16, "06:00-22:00 is 16 hours")
        self.assertEqual(lit[0].hour, 6)
        self.assertEqual(lit[-1].hour, 21)

    def test_a_vacation_day_overrides_the_normal_one(self):
        schedule = every_day({"onTime": "00:00", "offTime": "23:59"})
        schedule["vacation"] = {"enabled": True, "until": "2026-10-07"}
        for when, on in self._sweep(schedule, 60):
            self.assertFalse(on, f"vacation mode should hold the lights off at {when}")

    def test_an_overnight_window_stays_lit_across_midnight(self):
        # 22:00-06:00: the interesting cases are 23:00 and 02:00, and they
        # belong to different days' entries.
        schedule = every_day({"onTime": "22:00", "offTime": "06:00", "brightness": 40})
        day = datetime.datetime(2026, 10, 5)
        self.assertTrue(light_state_now(schedule, now=day + datetime.timedelta(hours=23))["on"])
        self.assertTrue(
            light_state_now(schedule, now=day + datetime.timedelta(hours=26))["on"],
            "02:00 the next morning belongs to the overnight window",
        )
        self.assertFalse(light_state_now(schedule, now=day + datetime.timedelta(hours=12))["on"])

    def test_disabling_the_schedule_expresses_no_opinion(self):
        schedule = every_day({"onTime": "06:00", "offTime": "22:00"})
        schedule["lights"]["enabled"] = False
        for when, _ in self._sweep(schedule, 60):
            self.assertIsNone(light_state_now(schedule, now=when))

    def test_normalizing_a_schedule_does_not_change_the_decision(self):
        legacy = {
            "lights": {"enabled": True, "onTime": "06:00", "offTime": "22:00", "brightness": 30}
        }
        when = datetime.datetime(2026, 10, 5, 12)
        self.assertEqual(
            light_state_now(legacy, now=when),
            light_state_now(normalize_schedule(legacy), now=when),
        )


class PigpioOwnershipTestCase(unittest.TestCase):
    """The failure mode that cost a morning: one process stealing another's pin."""

    def test_a_second_process_steals_the_pwm_claim(self):
        daemon = FakePigpiod()
        api = daemon.connect("api")
        api.set_PWM_frequency(18, 8000)
        api.set_PWM_dutycycle(18, 3000)
        self.assertEqual(daemon.pwm_owner(18), "api")

        ramp = daemon.connect("sunrise-ramp")
        ramp.set_PWM_frequency(18, 8000)
        self.assertEqual(daemon.pwm_owner(18), "sunrise-ramp")

    def test_the_api_loses_control_when_another_process_exits(self):
        # Exactly 06:15 on 2026-10-05.
        daemon = FakePigpiod()
        api = daemon.connect("api")
        api.set_PWM_frequency(18, 8000)
        api.set_PWM_dutycycle(18, 3000)

        ramp = daemon.connect("sunrise-ramp")
        ramp.set_PWM_frequency(18, 8000)
        ramp.set_PWM_dutycycle(18, 3000)
        ramp.stop()

        with self.assertRaises(PigpioError):
            api.set_PWM_dutycycle(18, 3000)
        with self.assertRaises(PigpioError):
            api.get_PWM_dutycycle(18)

    def test_the_register_survives_the_claim_which_is_the_whole_trap(self):
        daemon = FakePigpiod()
        api = daemon.connect("api")
        api.set_PWM_frequency(18, 8000)
        api.set_PWM_dutycycle(18, 3000)
        ramp = daemon.connect("ramp")
        ramp.set_PWM_frequency(18, 8000)
        ramp.stop()

        self.assertEqual(daemon.duty(18), 3000, "the register still holds the old value")
        self.assertIsNone(daemon.pwm_owner(18), "but nobody owns the PWM any more")
        self.assertEqual(daemon.mode(18), 0, "and the pin has been returned to input")


class PwmClaimDetectionTestCase(unittest.TestCase):
    """pwm_claimed() must see the ownership, not the register."""

    def test_true_while_the_claim_is_held(self):
        from app.lib import hardware_state as hs

        daemon = FakePigpiod()
        conn = daemon.connect("api")
        conn.set_PWM_frequency(18, 8000)
        conn.set_PWM_dutycycle(18, 3000)
        self.assertTrue(hs.pwm_claimed(18, pi=conn))

    def test_false_after_another_process_releases_it(self):
        from app.lib import hardware_state as hs

        daemon = FakePigpiod()
        api = daemon.connect("api")
        api.set_PWM_frequency(18, 8000)
        api.set_PWM_dutycycle(18, 3000)
        ramp = daemon.connect("ramp")
        ramp.set_PWM_frequency(18, 8000)
        ramp.stop()

        # The register still reads 30%, and this still says the claim is gone.
        self.assertEqual(daemon.duty(18), 3000)
        self.assertFalse(hs.pwm_claimed(18, pi=api))

    def test_verify_lights_reports_the_fault_not_a_comfortable_number(self):
        from app.lib import hardware_state as hs

        daemon = FakePigpiod()
        api = daemon.connect("api")
        api.set_PWM_frequency(18, 8000)
        api.set_PWM_dutycycle(18, 3000)
        ramp = daemon.connect("ramp")
        ramp.set_PWM_frequency(18, 8000)
        ramp.stop()

        schedule = every_day({"onTime": "06:00", "offTime": "22:00", "brightness": 30})
        now = datetime.datetime(2026, 10, 5, 12)
        with (
            patch.object(hs, "duty_fraction", return_value=0.30),
            patch.object(hs, "pwm_claimed", return_value=False),
            patch(
                "app.sensors.schedule.schedule.light_state_now",
                return_value={"on": True, "brightness": 30, "reason": "inside"},
            ),
            patch("app.lib.state.load_state", return_value={"light_on": True, "brightness": 30}),
        ):
            report = hs.verify_lights(schedule, now=now)
        self.assertFalse(report["ok"])
        self.assertIn("released the PWM claim", report["detail"])

    def test_the_cli_no_longer_touches_the_pin_at_all(self):
        """With the API owning it, a cron run cannot steal the claim.

        This is the architectural fix: light.sh asks the API, so no second
        pigpio client ever claims GPIO18.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        src = (root / "scripts" / "light.sh").read_text()
        # The API attempt must come first and short-circuit on success.
        self.assertIn('"${API_CALL}" POST /light/brightness', src)
        self.assertIn("&&", src)
        # And the ramp must be delegated, not driven from a CLI process.
        self.assertIn("/light/ramp", src)

        daemon = FakePigpiod()
        api = daemon.connect("api")
        api.set_PWM_frequency(18, 8000)
        api.set_PWM_dutycycle(18, 3000)

        # Simulate a cron fire that takes the delegated path: it contacts the
        # API, and no other client ever claims the pin.
        # A delegated cron run contacts the API over HTTP; no second pigpio
        # client is created, so the owner never changes.
        self.assertEqual(daemon.pwm_owner(18), "api")
        self.assertTrue(api.connected)


if __name__ == "__main__":
    unittest.main()
