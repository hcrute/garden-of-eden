import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from app import create_app
from app.sensors.camera import camera


class CameraRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("default")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    @patch("app.sensors.camera.routes.camera.capture_upper")
    def test_capture_failure_returns_503(self, mock_capture):
        mock_capture.side_effect = FileNotFoundError("fswebcam missing")
        resp = self.client.get("/camera/upper")
        self.assertEqual(resp.status_code, 503)

    @patch("app.sensors.camera.routes.send_file")
    @patch("app.sensors.camera.routes.camera.capture_lower")
    def test_capture_success_serves_file(self, mock_capture, mock_send_file):
        with patch.object(config, "LOWER_CAMERA_ENABLED", True):
            mock_send_file.return_value = "IMG"
            resp = self.client.get("/camera/lower")
            mock_capture.assert_called_once()
            self.assertEqual(resp.status_code, 200)

    @patch.object(config, "LOWER_CAMERA_ENABLED", False)
    def test_disabled_lower_camera_returns_not_found(self):
        resp = self.client.get("/camera/lower")
        self.assertEqual(resp.status_code, 404)


class CameraCaptureCommandTestCase(unittest.TestCase):
    """The upper camera rotates at capture time via fswebcam --rotate."""

    @patch("app.sensors.camera.camera.subprocess.run")
    def test_upper_rotation_passed_to_fswebcam(self, mock_run):
        with patch.object(config, "UPPER_CAMERA_ROTATE", 90):
            camera.capture_upper()
        cmd = mock_run.call_args[0][0]
        self.assertIn("--rotate", cmd)
        self.assertEqual(cmd[cmd.index("--rotate") + 1], "90")

    @patch("app.sensors.camera.camera.subprocess.run")
    def test_upper_rotation_omitted_when_disabled(self, mock_run):
        with patch.object(config, "UPPER_CAMERA_ROTATE", 0):
            camera.capture_upper()
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("--rotate", cmd)

    @patch("app.sensors.camera.camera.subprocess.run")
    def test_lower_camera_is_not_rotated(self, mock_run):
        with patch.object(config, "UPPER_CAMERA_ROTATE", 90):
            camera.capture_lower()
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("--rotate", cmd)


class TimelapseBuildTestCase(unittest.TestCase):
    """Building a timelapse needs frames *and* ffmpeg; they fail differently."""

    def _with_frames(self, tmp):
        folder = Path(tmp) / "upper"
        folder.mkdir(parents=True)
        (folder / "20260101-000000.jpg").write_bytes(b"x")
        return folder

    def test_no_frames_reports_missing_archive(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(camera, "_frames_dir", return_value=str(Path(tmp) / "upper")):
                with self.assertRaises(FileNotFoundError) as ctx:
                    camera.generate_timelapse("upper")
        self.assertIn("no frames archived", str(ctx.exception))

    def test_missing_ffmpeg_is_reported_distinctly(self):
        # Both cases raise FileNotFoundError, so a missing ffmpeg reported as
        # "no frames archived" sends the reader to the wrong place entirely.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = self._with_frames(tmp)
            with (
                patch.object(camera, "_frames_dir", return_value=str(folder)),
                patch.object(camera.shutil, "which", return_value=None),
                patch("app.sensors.camera.camera.subprocess.run") as mock_run,
            ):
                with self.assertRaises(FileNotFoundError) as ctx:
                    camera.generate_timelapse("upper")
        mock_run.assert_not_called()
        self.assertIn("ffmpeg", str(ctx.exception))


class CaptureScriptTestCase(unittest.TestCase):
    """scripts/capture-frames.sh exists, parses, and setup.sh installs the timer."""

    def setUp(self):
        self.setup = (Path(__file__).resolve().parent.parent / "scripts" / "setup.sh").read_text()
        self.capture = Path(__file__).resolve().parent.parent / "scripts" / "capture-frames.sh"

    def test_capture_script_exists_and_parses(self):
        self.assertTrue(self.capture.exists(), "scripts/capture-frames.sh is missing")
        proc = subprocess.run(["bash", "-n", str(self.capture)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_capture_script_archives_via_the_library(self):
        # It must go through camera.capture/archive so rotation and the model
        # check (no lower camera on a 3.0) still apply.
        src = self.capture.read_text()
        self.assertIn("camera.capture_upper", src)
        self.assertIn("camera.archive_frame", src)
        self.assertIn("camera.CAMERAS", src)

    def test_setup_installs_the_timer(self):
        self.assertIn("setup_timelapse_timer", self.setup)
        self.assertIn("garden-timelapse.timer", self.setup)
        self.assertIn("garden-timelapse.service", self.setup)
        # ...and verification must know about it, or a missing timer is invisible.
        self.assertIn("garden-timelapse.timer", self.setup.split("verify_install")[0])


if __name__ == "__main__":
    unittest.main()
