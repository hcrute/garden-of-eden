"""The API is the single owner of the actuator pins.

gpiozero releases a pin when its device object is collected, and pigpiod drops
the PWM claim with it. Three processes used to touch GPIO18 and GPIO24: the API,
the cron CLIs behind /usr/local/bin, and anything that merely imported app.*.
Every one of them released the pin on exit, and the long-lived API was the one
left unable to write -- which is how the light failed at 06:15 this morning,
and how it used to go off once an hour.

These tests pin the three properties that remove that class of failure.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent


class ImportingAppDoesNotTouchHardware(unittest.TestCase):
    """Importing app.sensors.camera.camera must not build any device.

    scripts/capture-frames.sh imports it purely to archive a JPEG, and used to
    zero the light pin on every hourly run as a side effect.
    """

    def test_app_init_does_not_import_routes_at_module_scope(self):
        source = (ROOT / "app" / "__init__.py").read_text()
        # Every blueprint is imported inside create_app, never at the top.
        for line in source.split("def create_app")[0].splitlines():
            self.assertNotIn("import light_blueprint", line)
            self.assertNotIn("from .sensors", line)
        self.assertIn("BLUEPRINTS", source)

    def test_importing_a_camera_helper_constructs_no_light_or_pump(self):
        # Run in a subprocess so module-level state from other tests cannot
        # mask the side effect.
        probe = (
            "import sys;"
            "import app.sensors.camera.camera as cam;"
            "sys.exit(3 if ('app.sensors.light.routes' in sys.modules"
            " or 'app.sensors.pump.routes' in sys.modules) else 0)"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        self.assertNotEqual(
            proc.returncode,
            3,
            "importing the camera module pulled in the light/pump routes",
        )


class ClisDelegateToTheApi(unittest.TestCase):
    def setUp(self):
        self.light = (ROOT / "scripts" / "light.sh").read_text()
        self.water = (ROOT / "scripts" / "water.sh").read_text()

    def test_both_clis_go_through_the_shared_caller(self):
        helper = "garden-api-call.sh"
        self.assertIn(helper, self.light)
        self.assertIn(helper, self.water)

    def test_light_ramp_is_delegated_not_driven_directly(self):
        # The ramp is what runs at 06:00 and 22:00, and its process exiting is
        # what dropped the PWM claim.
        self.assertIn("/light/ramp", self.light)

    def test_water_delegates_the_timed_dose(self):
        self.assertIn("/pump/run", self.water)

    def test_direct_gpio_remains_only_as_a_fallback(self):
        for src in (self.light, self.water):
            self.assertIn("app/sensors/light/light.py" if src is self.light else "pump.py", src)
            self.assertIn("&&", src, "the API attempt must be short-circuited on success")

    def test_the_helper_exists_and_parses(self):
        helper = ROOT / "scripts" / "garden-api-call.sh"
        self.assertTrue(helper.exists())
        proc = subprocess.run(["bash", "-n", str(helper)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_helper_reads_the_password_without_sourcing_the_file(self):
        # `source .env` is how a quoted value or a trailing space becomes a
        # shell syntax error in a cron job at 3am. It greps for the one key.
        import re as _re

        helper = (ROOT / "scripts" / "garden-api-call.sh").read_text()
        self.assertIsNone(
            _re.search(r"(^|\n)\s*(source|\.)\s+[^\n]*\.env", helper),
            "the helper must not source .env",
        )
        self.assertIn("grep -E", helper)


class RampEndpointTestCase(unittest.TestCase):
    def setUp(self):
        import app.sensors.light.routes as routes
        from app import create_app

        self.routes = routes
        app = create_app("default")
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._gen = routes._ramp_generation

    def _post(self, body):
        with patch.object(self.routes, "threading") as th:
            th.Thread.return_value.start = lambda: None
            return self.client.post("/light/ramp", json=body)

    def test_ramp_returns_202_immediately(self):
        resp = self._post({"brightness": 30, "minutes": 15})
        self.assertEqual(resp.status_code, 202)

    def test_rejects_a_non_positive_duration(self):
        self.assertEqual(self._post({"brightness": 30, "minutes": 0}).status_code, 400)
        self.assertEqual(self._post({"brightness": 30, "minutes": -5}).status_code, 400)

    def test_rejects_an_absurd_duration(self):
        self.assertEqual(self._post({"brightness": 30, "minutes": 600}).status_code, 400)

    def test_a_new_ramp_supersedes_the_previous_one(self):
        with patch.object(self.routes, "threading") as th:
            th.Thread.return_value.start = lambda: None
            self.client.post("/light/ramp", json={"brightness": 30, "minutes": 15})
            first = self.routes._ramp_generation
            self.client.post("/light/ramp", json={"brightness": 0, "minutes": 15})
            self.assertGreater(self.routes._ramp_generation, first)

    def test_the_ramp_thread_stops_when_superseded(self):
        device = MagicMock()
        device.get_brightness.return_value = 0
        with patch("app.sensors.light.routes.time.sleep"):
            self.routes._ramp_generation += 1
            # Claim a newer generation partway through, as a second request would.
            original = device.set_brightness

            def supersede(value):
                self.routes._ramp_generation += 1
                original(value)

            device.set_brightness.side_effect = supersede
            self.routes._run_ramp(device, 30, 15, generation=self.routes._ramp_generation - 1)
        # It must not have run to completion.
        self.assertNotEqual(device.set_brightness.call_count, 60)


if __name__ == "__main__":
    unittest.main()
