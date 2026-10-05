import datetime
import unittest
import unittest.mock
from unittest.mock import patch

from app.sensors.schedule import schedule as sched
from app.sensors.schedule.schedule import light_state_now


class MissingScriptsTestCase(unittest.TestCase):
    """Cron reports nothing when its target command is absent.

    On this unit /usr/local/bin/light and /usr/local/bin/water were missing
    entirely: every scheduled job failed with "No such file or directory"
    while the UI still showed a saved, enabled schedule. These guard the
    detection that turns that into a visible warning.
    """

    def test_reports_nothing_when_all_installed(self):
        with patch.object(sched.os.path, "exists", return_value=True):
            self.assertEqual(sched.missing_scripts(), [])

    def test_reports_the_missing_commands(self):
        absent = {sched.LIGHT_CMD, sched.WATER_CMD}
        with patch.object(sched.os.path, "exists", lambda p: p not in absent):
            self.assertEqual(sorted(sched.missing_scripts()), sorted(absent))

    def test_reports_partial_install(self):
        with patch.object(sched.os.path, "exists", lambda p: p != sched.WATER_CMD):
            self.assertEqual(sched.missing_scripts(), [sched.WATER_CMD])

    def test_covers_every_command_the_schedule_writes(self):
        # Every command compiled into crontab lines must be accounted for, so a
        # newly added command cannot slip past the check.
        with patch.object(sched.os.path, "exists", return_value=False):
            self.assertEqual(
                sorted(sched.missing_scripts()),
                sorted({sched.LIGHT_CMD, sched.WATER_CMD, sched.REFRESH_CMD}),
            )


class BuildCronLinesTestCase(unittest.TestCase):
    def test_lights_per_day_emits_on_and_off_with_dow(self):
        s = {
            "lights": {
                "enabled": True,
                "days": {"mon": [{"onTime": "08:30", "offTime": "22:15", "brightness": 60}]},
            },
            "pump": {"enabled": False},
        }
        lines = sched.build_cron_lines(s)
        self.assertEqual(len(lines), 2)
        # Monday -> cron day-of-week 1; light.sh takes positional args.
        self.assertIn("30 8 * * 1 /usr/local/bin/light 60", lines[0])
        self.assertIn("15 22 * * 1 /usr/local/bin/light off", lines[1])
        self.assertTrue(all(sched.CRON_MARKER in ln for ln in lines))

    def test_light_ramp_emits_ramp_commands(self):
        s = {
            "lights": {
                "enabled": True,
                "days": {
                    "mon": [
                        {
                            "onTime": "06:00",
                            "offTime": "22:00",
                            "brightness": 70,
                            "rampMinutes": 30,
                        }
                    ]
                },
            }
        }
        lines = sched.build_cron_lines(s)
        self.assertEqual(len(lines), 2)
        self.assertIn("0 6 * * 1 /usr/local/bin/light ramp 70 30", lines[0])
        self.assertIn("0 22 * * 1 /usr/local/bin/light ramp 0 30", lines[1])

    def test_multiple_entries_per_day(self):
        s = {
            "lights": {
                "enabled": True,
                "days": {
                    "fri": [
                        {"onTime": "06:00", "offTime": "09:00", "brightness": 35},
                        {"onTime": "18:00", "offTime": "22:00", "brightness": 70},
                    ]
                },
            }
        }
        lines = sched.build_cron_lines(s)
        self.assertEqual(len(lines), 4)  # two windows -> two on + two off
        self.assertTrue(all(" * * 5 " in ln for ln in lines))  # all on Friday

    def test_legacy_single_window_migrates_to_every_day(self):
        s = {
            "lights": {"enabled": True, "onTime": "08:00", "offTime": "22:00", "brightness": 70},
            "pump": {"enabled": False, "runs": []},
        }
        lines = sched.build_cron_lines(s)
        self.assertEqual(len(lines), 14)  # 7 days x (on + off)
        dows = {ln.split()[4] for ln in lines}
        self.assertEqual(dows, {"0", "1", "2", "3", "4", "5", "6"})

    def test_pump_runs_convert_minutes_to_seconds_with_dow(self):
        s = {"pump": {"enabled": True, "days": {"tue": [{"time": "06:30", "duration": 3}]}}}
        lines = sched.build_cron_lines(s)
        self.assertEqual(len(lines), 1)
        self.assertIn("30 6 * * 2 /usr/local/bin/water 180", lines[0])

    def test_pump_duration_clamped_to_safety_cap(self):
        # 20 minutes requested, but the hard cap is 15 minutes (900s).
        s = {"pump": {"enabled": True, "days": {"wed": [{"time": "12:00", "duration": 20}]}}}
        lines = sched.build_cron_lines(s)
        self.assertEqual(len(lines), 1)
        self.assertIn(f"/usr/local/bin/water {sched.config.MAX_PUMP_RUN_SECONDS} ", lines[0])
        self.assertNotIn("water 1200", lines[0])

    def test_disabled_emits_nothing(self):
        self.assertEqual(sched.build_cron_lines(sched.DEFAULT_SCHEDULE), [])

    def test_invalid_time_raises(self):
        s = {
            "lights": {"enabled": True, "days": {"mon": [{"onTime": "99:99", "offTime": "22:00"}]}}
        }
        with self.assertRaises(ValueError):
            sched.build_cron_lines(s)


class VacationModeTestCase(unittest.TestCase):
    def test_is_vacation_active_respects_until(self):
        base = {"vacation": {"enabled": True, "until": "2026-06-30"}}
        self.assertTrue(sched.is_vacation_active(base, today=datetime.date(2026, 6, 28)))
        self.assertFalse(sched.is_vacation_active(base, today=datetime.date(2026, 7, 1)))
        self.assertFalse(sched.is_vacation_active({"vacation": {"enabled": False}}))
        # Enabled with no end date stays active.
        self.assertTrue(sched.is_vacation_active({"vacation": {"enabled": True}}))

    def test_active_overrides_with_reduced_profile_and_refresh(self):
        s = {
            "lights": {
                "enabled": True,
                "days": {"mon": [{"onTime": "08:00", "offTime": "22:00", "brightness": 70}]},
            },
            "pump": {"enabled": False},
            "vacation": {"enabled": True, "until": "2999-12-31"},
        }
        lines = sched.build_cron_lines(s)
        joined = "\n".join(lines)
        self.assertIn("/usr/local/bin/light 50", joined)  # reduced brightness
        self.assertNotIn("/usr/local/bin/light 70", joined)  # normal overridden
        self.assertTrue(any("schedule-refresh.sh" in ln for ln in lines))  # nightly expiry

    def test_expired_falls_back_to_normal(self):
        s = {
            "lights": {
                "enabled": True,
                "days": {"mon": [{"onTime": "08:00", "offTime": "22:00", "brightness": 70}]},
            },
            "vacation": {"enabled": True, "until": "2000-01-01"},
        }
        lines = sched.build_cron_lines(s)
        joined = "\n".join(lines)
        self.assertIn("/usr/local/bin/light 70", joined)
        self.assertFalse(any("schedule-refresh.sh" in ln for ln in lines))


class NormalizeScheduleTestCase(unittest.TestCase):
    def test_legacy_pump_runs_apply_to_all_days(self):
        s = {"pump": {"enabled": True, "runs": [{"time": "12:00", "duration": 5}]}}
        norm = sched.normalize_schedule(s)
        self.assertEqual(set(norm["pump"]["days"]), set(sched.DAYS))
        for day in sched.DAYS:
            self.assertEqual(norm["pump"]["days"][day], [{"time": "12:00", "duration": 5}])

    def test_fills_missing_days(self):
        norm = sched.normalize_schedule({"lights": {"enabled": True, "days": {"mon": []}}})
        self.assertEqual(set(norm["lights"]["days"]), set(sched.DAYS))
        self.assertEqual(norm["lights"]["days"]["sun"], [])


class LightStateNowTestCase(unittest.TestCase):
    """light_state_now: what the schedule says the lights should be doing."""

    def _schedule(self, windows, day="sun", enabled=True):
        """A schedule with ``windows`` on ``day`` and nothing on other days."""
        if isinstance(windows, dict):
            windows = [windows]
        days = {d: [] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
        for window in windows:
            days[day].append(dict(window))
        return {
            "lights": {"enabled": enabled, "days": days},
            "pump": {"enabled": False, "days": {}},
        }

    def test_inside_the_window_is_on_with_the_scheduled_brightness(self):
        sched = self._schedule({"onTime": "06:00", "offTime": "22:00", "brightness": 35})
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 12, 0))  # Sunday
        self.assertTrue(got["on"])
        self.assertEqual(got["brightness"], 35)

    def test_before_the_window_is_off(self):
        sched = self._schedule({"onTime": "06:00", "offTime": "22:00"})
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 3, 0))
        self.assertFalse(got["on"])

    def test_after_the_window_is_off(self):
        sched = self._schedule({"onTime": "06:00", "offTime": "22:00"})
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 23, 0))
        self.assertFalse(got["on"])

    def test_boundary_is_inclusive_at_start_and_exclusive_at_end(self):
        sched = self._schedule({"onTime": "06:00", "offTime": "22:00"})
        on = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 6, 0))
        off = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 22, 0))
        self.assertTrue(on["on"])
        self.assertFalse(off["on"])

    def test_a_window_on_another_day_does_not_apply(self):
        sched = self._schedule({"onTime": "00:00", "offTime": "23:59"}, day="mon")
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 12, 0))  # Sunday
        self.assertFalse(got["on"])

    def test_overnight_window_is_on_after_midnight(self):
        sched = self._schedule({"onTime": "22:00", "offTime": "06:00"})
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 23, 30))
        self.assertTrue(got["on"])

    def test_overnight_window_is_still_on_just_after_midnight(self):
        # This half belongs to the *previous* day's entry -- Sunday's window
        # started Saturday at 22:00.
        sched = self._schedule({"onTime": "22:00", "offTime": "06:00"}, day="sat")
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 2, 0))  # Sunday
        self.assertTrue(got["on"])

    def test_overnight_window_uses_yesterdays_brightness_in_the_small_hours(self):
        sched = {
            "lights": {
                "enabled": True,
                "days": {
                    d: (
                        [{"onTime": "22:00", "offTime": "06:00", "brightness": 80}]
                        if d == "sat"
                        else []
                    )
                    for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
                },
            },
            "pump": {"enabled": False, "days": {}},
        }
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 2, 0))  # Sunday
        self.assertTrue(got["on"])
        self.assertEqual(got["brightness"], 80)

    def test_zero_length_window_never_matches(self):
        sched = self._schedule({"onTime": "08:00", "offTime": "08:00"})
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 8, 0))
        self.assertFalse(got["on"])

    def test_disabled_lights_gives_no_opinion_rather_than_off(self):
        # Otherwise saving an unrelated schedule edit would switch the lights
        # off on a machine that has never had a light schedule.
        sched = self._schedule({"onTime": "06:00", "offTime": "22:00"}, enabled=False)
        self.assertIsNone(light_state_now(sched, now=datetime.datetime(2026, 10, 4, 12, 0)))

    def test_vacation_mode_overrides_a_window(self):
        sched = self._schedule({"onTime": "00:00", "offTime": "23:59"})
        sched["vacation"] = {"enabled": True, "until": "2026-10-10"}
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 12, 0))
        self.assertFalse(got["on"])
        self.assertIn("vacation", got["reason"])

    def test_default_brightness_when_the_window_omits_it(self):
        sched = self._schedule({"onTime": "00:00", "offTime": "23:59"})
        got = light_state_now(sched, now=datetime.datetime(2026, 10, 4, 12, 0))
        self.assertEqual(got["brightness"], 70)


class ReconcileLightsTestCase(unittest.TestCase):
    """reconcile_lights makes the hardware agree with the schedule on save."""

    def setUp(self):
        from app import create_app

        app = create_app("default")
        app.config["TESTING"] = True
        self.client = app.test_client()

    def _light(self, brightness, recorder=None):
        """A stand-in for the module-level light_control singleton."""
        light = unittest.mock.MagicMock()
        if recorder is not None:
            light.set_brightness.side_effect = lambda v: recorder.append(("set", v))
            light.off.side_effect = lambda: recorder.append(("off", 0))
        return light

    def _patched(self, light, actual_on=True, actual_brightness=50.0):
        """Patch both the device and the independent pin read.

        reconcile_lights deliberately does not ask the device what it thinks it
        set -- it reads the pin. Patch hw_state.light_actual, or the test
        silently exercises the old cache-based path.
        """
        import contextlib

        import app.sensors.light.routes as light_routes

        @contextlib.contextmanager
        def both():
            with (
                patch.object(light_routes, "light_control", light),
                patch(
                    "app.sensors.schedule.routes.hw_state.light_actual",
                    return_value={"on": actual_on, "brightness": actual_brightness},
                ),
            ):
                yield

        return both()

    def _post(self, schedule, light, actual_on=True, actual_brightness=50.0):
        with (
            self._patched(light, actual_on, actual_brightness),
            patch.object(sched, "_write_crontab", lambda lines: None),
        ):
            return self.client.post("/schedule", json=schedule)

    def test_turns_the_light_on_when_inside_a_window(self):
        calls = []
        light = self._light(0.0, calls)
        schedule = {
            "lights": {
                "enabled": True,
                "days": {
                    d: [{"onTime": "00:00", "offTime": "23:59", "brightness": 45}]
                    for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
                },
            },
            "pump": {"enabled": False, "days": {}},
        }
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": True, "brightness": 45, "reason": "inside"},
        ):
            resp = self._post(schedule, light, actual_on=False, actual_brightness=0.0)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, [("set", 45)])
        self.assertTrue(resp.get_json()["lights_reconciled"])

    def test_leaves_an_already_lit_light_alone(self):
        # A manual brightness override is not something a schedule save should
        # stomp just because the numbers differ.
        calls = []
        light = self._light(80.0, calls)
        schedule = {"lights": {"enabled": True, "days": {}}, "pump": {"enabled": False, "days": {}}}
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": True, "brightness": 45, "reason": "inside"},
        ):
            resp = self._post(schedule, light, actual_on=True, actual_brightness=80.0)
        self.assertEqual(calls, [])
        self.assertNotIn("lights_reconciled", resp.get_json())

    def test_turns_the_light_off_outside_every_window(self):
        calls = []
        light = self._light(60.0, calls)
        schedule = {"lights": {"enabled": True, "days": {}}, "pump": {"enabled": False, "days": {}}}
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": False, "brightness": None, "reason": "outside"},
        ):
            resp = self._post(schedule, light, actual_on=True, actual_brightness=60.0)
        self.assertEqual(calls, [("off", 0)])
        self.assertEqual(resp.get_json()["action"], "turned off")

    def test_does_nothing_when_already_off(self):
        calls = []
        light = self._light(0.0, calls)
        schedule = {"lights": {"enabled": True, "days": {}}, "pump": {"enabled": False, "days": {}}}
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": False, "brightness": None, "reason": "outside"},
        ):
            resp = self._post(schedule, light, actual_on=False, actual_brightness=0.0)
        self.assertEqual(calls, [])
        self.assertNotIn("lights_reconciled", resp.get_json())

    def test_treats_a_residual_duty_cycle_as_off(self):
        # gpiozero can report a hair above zero; rewriting the pin on every
        # save for that would be noise.
        calls = []
        light = self._light(0.2, calls)
        schedule = {"lights": {"enabled": True, "days": {}}, "pump": {"enabled": False, "days": {}}}
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": True, "brightness": 45, "reason": "inside"},
        ):
            self._post(schedule, light, actual_on=False, actual_brightness=0.2)
        self.assertEqual(calls, [("set", 45)])

    def test_schedule_save_survives_a_dead_light(self):
        light = unittest.mock.MagicMock()
        light.get_brightness.side_effect = OSError("pigpiod is not running")
        schedule = {"lights": {"enabled": True, "days": {}}, "pump": {"enabled": False, "days": {}}}
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": True, "brightness": 45, "reason": "inside"},
        ):
            resp = self._post(schedule, light)
        self.assertEqual(resp.status_code, 200, "a broken light must not fail the save")

    def test_schedule_save_survives_a_light_that_raises_on_write(self):
        light = self._light(0.0)
        light.set_brightness.side_effect = RuntimeError("pin busy")
        schedule = {"lights": {"enabled": True, "days": {}}, "pump": {"enabled": False, "days": {}}}
        with patch(
            "app.sensors.schedule.routes.sched.light_state_now",
            return_value={"on": True, "brightness": 45, "reason": "inside"},
        ):
            resp = self._post(schedule, light, actual_on=False, actual_brightness=0.0)
        self.assertEqual(resp.status_code, 200)

    def test_get_reports_what_the_schedule_thinks_right_now(self):
        resp = self.client.get("/schedule")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("lights_now", resp.get_json())


if __name__ == "__main__":
    unittest.main()
