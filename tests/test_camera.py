import datetime
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
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
        # setup.sh delegates to the installer rather than inlining the units,
        # so it only needs to reference the timer by name and the script.
        self.assertIn("setup_timelapse_timer", self.setup)
        self.assertIn("garden-timelapse.timer", self.setup)
        self.assertIn("install-timelapse-timer.sh", self.setup)
        # ...and verification must know about it, or a missing timer is invisible.
        self.assertIn("garden-timelapse.timer", self.setup.split("verify_install")[0])


class TimelapseTimerInstallerTestCase(unittest.TestCase):
    """scripts/install-timelapse-timer.sh is the documented way to add the timer.

    The units embed User= and WorkingDirectory=, so they are generated rather
    than committed. setup.sh delegates here so the unit definitions have one
    source of truth.
    """

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.installer = root / "scripts" / "install-timelapse-timer.sh"
        self.setup = (root / "scripts" / "setup.sh").read_text()
        self.src = self.installer.read_text() if self.installer.exists() else ""

    def test_installer_exists_and_parses(self):
        self.assertTrue(self.installer.exists(), "install-timelapse-timer.sh is missing")
        proc = subprocess.run(["bash", "-n", str(self.installer)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_installer_generates_both_units(self):
        self.assertIn("garden-timelapse.service", self.src)
        self.assertIn("garden-timelapse.timer", self.src)
        self.assertIn("systemctl enable --now garden-timelapse.timer", self.src)

    def test_installer_is_referenced_by_the_docs(self):
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        self.assertIn("scripts/install-timelapse-timer.sh", readme)

    def test_setup_delegates_to_the_installer(self):
        # One definition of the units, not two that can drift.
        self.assertIn("install-timelapse-timer.sh", self.setup)
        # setup.sh must no longer inline the unit bodies.
        body = self.setup.split("setup_timelapse_timer {")[1].split("\n}")[0]
        self.assertNotIn("[Timer]", body, "setup.sh should delegate, not redefine the unit")

    def test_units_are_not_committed(self):
        # Machine-specific User=/WorkingDirectory= must not live in the repo.
        for stray in (
            "services/etc/systemd/system/garden-timelapse.service",
            "services/etc/systemd/system/garden-timelapse.timer",
        ):
            self.assertFalse(
                (Path(__file__).resolve().parent.parent / stray).exists(),
                f"{stray} should be generated, not committed",
            )

    def test_timer_uses_on_calendar_not_a_fixed_interval(self):
        """OnCalendar is what makes "every 30 min" or "daily at 08:00" possible.

        OnUnitActiveSec can express neither a time of day nor a calendar, so
        the presets would be impossible with it.
        """
        self.assertIn("OnCalendar=$ON_CALENDAR", self.src)
        self.assertNotIn("OnUnitActiveSec", self.src)

    def test_accepts_a_schedule_argument_and_lists_presets(self):
        self.assertIn("--schedule", self.src)
        self.assertIn("--list-presets", self.src)

    def test_resolves_presets_through_config_not_a_second_copy(self):
        # One definition of the presets, so config and the installer cannot
        # disagree about what "every-30-min" means.
        self.assertIn("config.TIMELAPSE_SCHEDULE_PRESETS", self.src)

    def test_legacy_interval_still_works(self):
        self.assertIn("TIMELAPSE_INTERVAL", self.src)
        self.assertIn("deprecated", self.src)

    def test_persistent_catches_a_missed_run(self):
        # A once-a-day capture whose Pi was off at 08:00 is skipped entirely
        # without this, which quietly loses a day of the archive.
        self.assertIn("Persistent=true", self.src)

    def test_warns_when_systemd_rejects_the_expression(self):
        self.assertIn("TimersCalendar", self.src)
        self.assertIn("WARNING", self.src)

    def _resolve(self, *args, env=None):
        """Run the installer in its no-op --resolve mode.

        This is the regression test for a real bug: resolving into two shell
        variables off one line of output truncated every expression containing
        a space, so "daily" installed OnCalendar=*-*-* and fired 288 times a
        day at nothing.
        """
        full = dict(os.environ)
        full["TIMELAPSE_PYTHON"] = sys.executable
        full.update(env or {})
        proc = subprocess.run(
            ["bash", str(self.installer), *args, "--resolve"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env=full,
            timeout=60,
        )
        return proc

    def test_resolve_preserves_the_full_expression_including_spaces(self):
        for name, expr in config.TIMELAPSE_SCHEDULE_PRESETS.items():
            with self.subTest(preset=name):
                proc = self._resolve("--schedule", name)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), expr)

    def test_resolve_passes_a_custom_expression_through_untouched(self):
        proc = self._resolve("--schedule", "Mon..Fri *-*-* 07:30:00")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "Mon..Fri *-*-* 07:30:00")

    def test_resolve_rejects_an_expression_systemd_would_not_accept(self):
        # Otherwise this installs a timer that loads cleanly and never fires.
        proc = self._resolve("--schedule", "not a calendar")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Refusing", proc.stderr)

    def test_legacy_interval_is_converted_with_a_warning(self):
        proc = self._resolve(env={"TIMELAPSE_INTERVAL": "1800"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "*:0/30")
        self.assertIn("deprecated", proc.stderr)

    def test_sub_minute_interval_is_rejected_not_rounded(self):
        proc = self._resolve(env={"TIMELAPSE_INTERVAL": "30"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("under a minute", proc.stderr)

    def _run_installer(self, *args):
        full = dict(os.environ)
        full["TIMELAPSE_PYTHON"] = sys.executable
        return subprocess.run(
            ["bash", str(self.installer), *args],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env=full,
            timeout=60,
        )

    def test_dry_run_generates_both_units_without_root(self):
        # `set -u` means any variable that is referenced before it is assigned
        # aborts the script. That is invisible to `bash -n` and to the
        # assertIn-style tests above -- two real bugs (a deleted mktemp pair,
        # then a CAPTURE used by the heredoc before its assignment) reached a
        # user's terminal this way. Running the generation path is the only
        # thing that actually catches them.
        proc = self._run_installer("--schedule", "daily", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("=== garden-timelapse.service ===", proc.stdout)
        self.assertIn("=== garden-timelapse.timer ===", proc.stdout)

    def test_dry_run_emits_the_resolved_oncalendar_for_every_preset(self):
        for name, expr in config.TIMELAPSE_SCHEDULE_PRESETS.items():
            with self.subTest(preset=name):
                proc = self._run_installer("--schedule", name, "--dry-run")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f"OnCalendar={expr}", proc.stdout)

    def test_generated_service_points_at_the_capture_script(self):
        proc = self._run_installer("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ExecStart=", proc.stdout)
        self.assertIn("scripts/capture-frames.sh", proc.stdout)
        self.assertIn("Type=oneshot", proc.stdout)

    def test_dry_run_writes_nothing_outside_tmp(self):
        # It must be safe on a live machine: no units written, no systemctl.
        proc = self._run_installer("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Installed.", proc.stdout)


class TimelapseFrameExportTestCase(unittest.TestCase):
    """frame_files / zip_frames back the download endpoint."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "upper"
        self.folder.mkdir()
        for stamp in (
            "20261001-080000",
            "20261002-080000",
            "20261003-080000",
            "20261004-080000",
        ):
            (self.folder / f"{stamp}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        self._dir = config.TIMELAPSE_DIR
        config.TIMELAPSE_DIR = str(self.root)
        self.addCleanup(setattr, config, "TIMELAPSE_DIR", self._dir)

    def test_frame_files_returns_all_by_default(self):
        self.assertEqual(len(camera.frame_files("upper")), 4)

    def test_date_range_is_inclusive_on_both_ends(self):
        got = camera.frame_files("upper", datetime.date(2026, 10, 2), datetime.date(2026, 10, 3))
        self.assertEqual(
            [os.path.basename(p) for p in got], ["20261002-080000.jpg", "20261003-080000.jpg"]
        )

    def test_open_ended_range(self):
        self.assertEqual(len(camera.frame_files("upper", start=datetime.date(2026, 10, 3))), 2)
        self.assertEqual(len(camera.frame_files("upper", end=datetime.date(2026, 10, 2))), 2)

    def test_files_not_named_like_ours_are_ignored(self):
        # A stray file must not crash the parser or land in the zip.
        (self.folder / "notes.txt").write_text("hello")
        self.assertEqual(len(camera.frame_files("upper")), 4)

    def test_zip_contains_only_the_selected_frames(self):
        buf, count = camera.zip_frames(
            "upper", datetime.date(2026, 10, 4), datetime.date(2026, 10, 4)
        )
        self.assertEqual(count, 1)
        with zipfile.ZipFile(buf) as zf:
            self.assertEqual(zf.namelist(), ["upper/20261004-080000.jpg"])

    def test_zip_is_stored_not_deflated(self):
        # JPEG does not compress further; spending CPU here would be the
        # slowest part of an export that is otherwise a file copy.
        buf, _ = camera.zip_frames("upper")
        with zipfile.ZipFile(buf) as zf:
            for info in zf.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)

    def test_range_is_read_from_the_filename_not_the_mtime(self):
        # Re-touching a frame must not move it into the range being exported.
        target = self.folder / "20261001-080000.jpg"
        os.utime(target, (0, 0))
        got = camera.frame_files("upper", datetime.date(2026, 10, 4), datetime.date(2026, 10, 4))
        self.assertNotIn(str(target), got)


class TimelapseDownloadRouteTestCase(unittest.TestCase):
    """GET /camera/timelapse/<cam>/frames.zip"""

    def setUp(self):
        self.app = create_app("default")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / "upper"
        folder.mkdir()
        for stamp in ("20261003-080000", "20261004-080000"):
            (folder / f"{stamp}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        self._dir = config.TIMELAPSE_DIR
        config.TIMELAPSE_DIR = str(self.root)
        self.addCleanup(setattr, config, "TIMELAPSE_DIR", self._dir)

    def test_returns_a_zip(self):
        resp = self.client.get("/camera/timelapse/upper/frames.zip")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("zip", resp.headers["Content-Type"])
        self.assertIn("attachment", resp.headers["Content-Disposition"])

    def test_date_range_narrows_the_archive(self):
        resp = self.client.get("/camera/timelapse/upper/frames.zip?from=2026-10-04&to=2026-10-04")
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertEqual(zf.namelist(), ["upper/20261004-080000.jpg"])

    def test_malformed_date_is_rejected(self):
        resp = self.client.get("/camera/timelapse/upper/frames.zip?from=yesterday")
        self.assertEqual(resp.status_code, 400)

    def test_inverted_range_is_rejected(self):
        resp = self.client.get("/camera/timelapse/upper/frames.zip?from=2026-10-04&to=2026-10-01")
        self.assertEqual(resp.status_code, 400)

    def test_empty_range_is_404_not_an_empty_zip(self):
        # An empty zip downloads as a 0-byte file that looks like a failure.
        resp = self.client.get("/camera/timelapse/upper/frames.zip?from=2020-01-01&to=2020-01-02")
        self.assertEqual(resp.status_code, 404)

    def test_unknown_camera_is_rejected(self):
        self.assertEqual(self.client.get("/camera/timelapse/sideways/frames.zip").status_code, 400)

    def test_does_not_collide_with_the_timelapse_video_route(self):
        # /timelapse/<cam> also matches "timelapse-config" as a <cam>; make sure
        # each URL still reaches the handler it was written for.
        self.assertEqual(self.client.get("/camera/timelapse/upper").status_code in (200, 404), True)
        self.assertEqual(self.client.get("/camera/timelapse-config").status_code, 200)


class TimelapseConfigRouteTestCase(unittest.TestCase):
    """GET /camera/timelapse-config -- the schedule knobs, readable without sudo."""

    def setUp(self):
        self.app = create_app("default")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_reports_the_configured_schedule_and_presets(self):
        body = self.client.get("/camera/timelapse-config").get_json()
        self.assertIn("schedule", body)
        self.assertIn("hourly", body["presets"])
        self.assertIn("daily", body["presets"])
        self.assertIsInstance(body["presets"], list)

    def test_survives_a_missing_timer_unit(self):
        # The unit lives outside the repo and is absent on a dev box; status
        # display must degrade, not 500.
        body = self.client.get("/camera/timelapse-config").get_json()
        self.assertIn("installed_on_calendar", body)

    def test_every_preset_is_valid_systemd_syntax(self):
        """A typo in a preset produces a timer that silently never fires.

        Checked against systemd-analyze itself rather than a regex, so this
        keeps working as systemd's calendar syntax evolves.
        """
        import shutil

        if shutil.which("systemd-analyze") is None:
            self.skipTest("systemd-analyze not available on this host")
        for name, expr in config.TIMELAPSE_SCHEDULE_PRESETS.items():
            with self.subTest(preset=name):
                proc = subprocess.run(
                    ["systemd-analyze", "calendar", expr],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(
                    proc.returncode, 0, f"{name}: systemd rejected {expr!r}: {proc.stderr}"
                )

    def test_pending_change_when_the_unit_has_no_oncalthar_at_all(self):
        """A unit predating OnCalendar support says nothing about the schedule.

        Reporting pending_change=False there is worse than useless: it looks
        like .env and the unit agree when the unit is running on a completely
        different, now-unreported cadence.
        """
        import builtins

        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if str(path).endswith("garden-timelapse.timer"):
                return io.StringIO("[Timer]\nOnBootSec=2min\nOnUnitActiveSec=3600s\n")
            return real_open(path, *a, **kw)

        with patch("builtins.open", fake_open):
            body = self.client.get("/camera/timelapse-config").get_json()
        self.assertIsNone(body["installed_on_calendar"])
        self.assertTrue(body["pending_change"])

    def test_no_pending_change_when_the_unit_matches_env(self):
        import builtins

        real_open = builtins.open
        expression = config.TIMELAPSE_SCHEDULE_PRESETS.get(
            config.TIMELAPSE_SCHEDULE, config.TIMELAPSE_SCHEDULE
        )

        def fake_open(path, *a, **kw):
            if str(path).endswith("garden-timelapse.timer"):
                return io.StringIO(f"[Timer]\nOnCalendar={expression}\n")
            return real_open(path, *a, **kw)

        with patch("builtins.open", fake_open):
            body = self.client.get("/camera/timelapse-config").get_json()
        self.assertFalse(body["pending_change"])

    def test_next_run_keeps_the_time_not_just_the_date(self):
        """list-timers NEXT is four tokens; the first two drop the clock time."""
        import subprocess as sp

        class Result:
            returncode = 0

            def __init__(self, out):
                self.stdout = out

        def fake_run(cmd, **kw):
            if "show" in cmd:
                return Result("\n")  # realtime empty: a monotonic timer
            return Result(
                "Sun 2026-10-04 12:18:17 PDT 12min Sun 2026-10-04 11:16:52 PDT 48min ago garden-timelapse.timer garden-timelapse.service\n"
            )

        with patch.object(sp, "run", fake_run):
            body = self.client.get("/camera/timelapse-config").get_json()
        self.assertEqual(body["next_runs"], ["Sun 2026-10-04 12:18:17 PDT"])

    def test_prefers_the_exact_systemd_timestamp_when_available(self):
        import subprocess as sp

        class Result:
            returncode = 0
            stdout = "Sun 2026-10-04 12:18:17 PDT\n"

        with patch.object(sp, "run", lambda cmd, **kw: Result()):
            body = self.client.get("/camera/timelapse-config").get_json()
        self.assertEqual(body["next_runs"], ["Sun 2026-10-04 12:18:17 PDT"])


if __name__ == "__main__":
    unittest.main()
