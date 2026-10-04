import datetime
import glob
import logging
import os
import re
import subprocess

from flask import Blueprint, jsonify, request, send_file

import config
from app.lib.hardware import lower_camera_enabled

from . import camera

logger = logging.getLogger(__name__)

camera_blueprint = Blueprint("camera", __name__)


def _serve(capture_fn, path):
    try:
        capture_fn()
    except FileNotFoundError:
        return jsonify(error="fswebcam not installed on this host"), 503
    except Exception as exc:
        logger.error("Camera capture failed: %s", exc)
        return jsonify(error=f"camera capture failed: {exc}"), 503
    return send_file(path, mimetype="image/jpeg")


@camera_blueprint.route("/upper", methods=["GET"])
def upper():
    return _serve(camera.capture_upper, config.UPPER_IMAGE_PATH)


@camera_blueprint.route("/lower", methods=["GET"])
def lower():
    if not lower_camera_enabled():
        return jsonify(error="lower camera is disabled"), 404
    return _serve(camera.capture_lower, config.LOWER_IMAGE_PATH)


@camera_blueprint.route("/timelapse/<cam>", methods=["GET"])
def get_timelapse(cam):
    if cam not in camera.CAMERAS:
        return jsonify(error="unknown camera"), 400
    path = camera.timelapse_path(cam)
    if not os.path.exists(path):
        return jsonify(error="no timelapse yet — generate one"), 404
    return send_file(path, mimetype="video/mp4")


@camera_blueprint.route("/timelapse/<cam>/frames.zip", methods=["GET"])
def download_frames(cam):
    """Zip the archived frames, optionally limited to a date range.

    Query params ``from`` and ``to`` are YYYY-MM-DD and both ends are
    inclusive. Omitting both sends every retained frame, which is fine: the
    archive is capped at TIMELAPSE_MAX_FRAMES, so it cannot grow without
    bound.
    """
    if cam not in camera.CAMERAS:
        return jsonify(error="unknown camera"), 400

    def _parse(name):
        raw = request.args.get(name)
        if not raw:
            return None
        try:
            return datetime.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return raw  # signal bad input to the caller

    start, end = _parse("from"), _parse("to")
    for value, name in ((start, "from"), (end, "to")):
        if isinstance(value, str):
            return jsonify(error=f"'{name}' must be YYYY-MM-DD"), 400
    if start and end and start > end:
        return jsonify(error="'from' is after 'to'"), 400

    buf, count = camera.zip_frames(cam, start, end)
    if not count:
        return jsonify(error="no frames in that range"), 404
    stamp = datetime.date.today().isoformat()
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"gardyn-{cam}-frames-{stamp}.zip",
    )


@camera_blueprint.route("/timelapse-config", methods=["GET"])
def timelapse_config():
    """What the capture timer is configured to do, without needing sudo.

    Reads the generated unit rather than .env, because .env is the input and
    the unit is what systemd actually runs: showing the source of truth is the
    only way this can be trusted after someone edits one and forgets the other.
    """
    schedule = config.TIMELAPSE_SCHEDULE
    expression = config.TIMELAPSE_SCHEDULE_PRESETS.get(schedule, schedule)
    is_preset = schedule in config.TIMELAPSE_SCHEDULE_PRESETS

    installed = None
    try:
        with open("/etc/systemd/system/garden-timelapse.timer", encoding="utf-8") as fh:
            found = re.search(r"^OnCalendar=(.+)$", fh.read(), re.M)
            if found:
                installed = found.group(1).strip()
    except OSError:
        pass

    # NextElapseUSecRealtime is exact and already formatted, but it is only
    # populated for wall-clock (OnCalendar) timers -- an OnUnitActiveSec timer
    # is monotonic and leaves it empty. Fall back to list-timers, whose NEXT
    # column is four tokens (weekday, date, time, zone); taking the first two
    # silently drops the time and leaves "Sun 2026-10-04".
    next_runs = []
    try:
        out = subprocess.run(
            [
                "systemctl",
                "show",
                "garden-timelapse.timer",
                "-p",
                "NextElapseUSecRealtime",
                "--value",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        stamp = out.stdout.strip() if out.returncode == 0 else ""
        if stamp and stamp != "n/a":
            next_runs = [stamp]
        else:
            out = subprocess.run(
                ["systemctl", "list-timers", "garden-timelapse.timer", "--no-pager", "--no-legend"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                next_runs = [
                    " ".join(line.split()[:4])
                    for line in out.stdout.strip().splitlines()
                    if line.split()
                ]
    except Exception:  # noqa: BLE001 - status display must never 500
        next_runs = []

    frames = sum(
        len(glob.glob(os.path.join(config.TIMELAPSE_DIR, c, "*.jpg"))) for c in camera.CAMERAS
    )
    return jsonify(
        {
            "schedule": schedule,
            "on_calendar": expression,
            "is_preset": is_preset,
            "installed_on_calendar": installed,
            "next_runs": next_runs,
            # True whenever the unit does not say what .env says, including
            # when it predates OnCalendar support and says nothing at all.
            "pending_change": installed != expression,
            "presets": sorted(config.TIMELAPSE_SCHEDULE_PRESETS),
            "cameras": list(camera.CAMERAS),
            "frames_archived": frames,
            "max_frames": config.TIMELAPSE_MAX_FRAMES,
            "fps": config.TIMELAPSE_FPS,
            "auto_build": config.TIMELAPSE_AUTO_BUILD,
        }
    )


@camera_blueprint.route("/timelapse/<cam>", methods=["POST"])
def make_timelapse(cam):
    if cam not in camera.CAMERAS:
        return jsonify(error="unknown camera"), 400
    try:
        camera.generate_timelapse(cam)
    except FileNotFoundError as exc:
        return jsonify(error=str(exc)), 404
    except Exception as exc:
        logger.error("Timelapse generation failed: %s", exc)
        return jsonify(error=f"generation failed: {exc}"), 503
    return jsonify(message=f"{cam} timelapse generated"), 200
