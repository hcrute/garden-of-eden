"""The light.py / pump.py CLIs must persist what they actually do.

Cron drives the sunrise and the sunset through these CLIs. If they move the
pin without updating STATE_FILE, the file goes stale, and every process that
later builds a Light or Pump seeds from it -- which is every process that
imports the app, including the hourly timelapse capture.

The concrete failure this prevents: a UI toggle leaves light_on true, the
22:00 sunset fades the pin to zero, the file still says true, and the next
capture re-applies 30% and the lights come back on for the rest of the night.
"""

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.sensors.light import light as light_mod
from app.sensors.pump import pump as pump_mod


class LightCliStateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file = f"{self.tmp.name}/state.json"

        self.light = MagicMock()
        self.saved = []
        patcher = patch.object(light_mod, "Light", return_value=self.light)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.state_patch = patch("app.lib.state.save_state", side_effect=self._save)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def _save(self, **changes):
        self.saved.append(changes)

    def test_off_records_that_the_lights_are_off(self):
        light_mod.main(["--off"])
        self.light.off.assert_called_once()
        self.assertEqual(self.saved, [{"light_on": False}])

    def test_on_with_brightness_records_both(self):
        light_mod.main(["--on", "--brightness", "45"])
        self.assertEqual(self.saved, [{"light_on": True, "brightness": 45}])

    def test_brightness_records_the_level(self):
        light_mod.main(["--brightness", "60"])
        self.assertEqual(self.saved, [{"light_on": True, "brightness": 60}])

    def test_brightness_zero_is_recorded_as_off(self):
        light_mod.main(["--brightness", "0"])
        self.assertEqual(self.saved, [{"light_on": False, "brightness": 0}])

    def test_ramp_records_its_destination_not_each_step(self):
        # Mid-fade the pin is neither the old nor the new value; what matters
        # is where it lands, or the next process re-applies the old one.
        with patch.object(light_mod, "ramp_to") as ramp:
            light_mod.main(["--on", "--brightness", "0", "--ramp-minutes", "15"])
        ramp.assert_called_once()
        self.assertEqual(self.saved, [{"light_on": False, "brightness": 0}])

    def test_out_of_range_brightness_is_clamped_not_rejected(self):
        light_mod.main(["--on", "--brightness", "150"])
        self.assertEqual(self.saved, [{"light_on": True, "brightness": 100}])

    def test_a_failing_state_write_does_not_stop_the_lights(self):
        # The actuator is the important thing; the state file is bookkeeping.
        with patch("app.lib.state.save_state", side_effect=OSError("disk full")):
            light_mod.main(["--off"])
        self.light.off.assert_called_once()

    def test_the_hardware_is_moved_before_the_state_is_written(self):
        order = []
        self.light.off.side_effect = lambda: order.append("off")
        self.saved = []
        patcher = patch("app.lib.state.save_state", side_effect=lambda **k: order.append("save"))
        patcher.start()
        try:
            light_mod.main(["--off"])
        finally:
            patcher.stop()
        self.assertEqual(
            order, ["off", "save"], "must not claim success for a light it never moved"
        )


class PumpCliStateTestCase(unittest.TestCase):
    def setUp(self):
        self.pump = MagicMock()
        self.saved = []
        patcher = patch.object(pump_mod, "Pump", return_value=self.pump)
        patcher.start()
        self.addCleanup(patcher.stop)
        sp = patch("app.lib.state.save_state", side_effect=lambda **k: self.saved.append(k))
        sp.start()
        self.addCleanup(sp.stop)

    def test_off_records_that_the_pump_is_off(self):
        pump_mod.main(["--off"])
        self.pump.off.assert_called_once()
        self.assertEqual(self.saved, [{"pump_on": False}])

    def test_on_with_speed_records_both(self):
        pump_mod.main(["--on", "--speed", "30"])
        self.assertEqual(self.saved, [{"pump_on": True, "speed": 30}])

    def test_speed_zero_is_recorded_as_off(self):
        pump_mod.main(["--speed", "0"])
        self.assertEqual(self.saved, [{"pump_on": False, "speed": 0}])

    def test_a_failing_state_write_does_not_stop_the_pump(self):
        with patch("app.lib.state.save_state", side_effect=OSError("disk full")):
            pump_mod.main(["--off"])
        self.pump.off.assert_called_once()


if __name__ == "__main__":
    unittest.main()
