"""Capture stills from the Gardyn USB cameras via fswebcam.

Shared by the REST camera endpoints and the MQTT image publisher so capture
behavior lives in one place.
"""

import datetime
import glob
import logging
import os
import shutil
import subprocess

import config
from app.lib.hardware import lower_camera_enabled

logger = logging.getLogger(__name__)


def capture(device, output_path, resolution=None, rotate=None):
    """Capture a single frame from ``device`` to ``output_path``.

    ``rotate`` is passed to fswebcam as ``--rotate`` (right angles only). It is
    applied at capture time so every consumer of the JPEG -- the REST endpoints,
    the MQTT image publisher, and the timelapse frames -- sees the same
    orientation.

    Returns the output path on success, or raises CalledProcessError/OSError.
    """
    resolution = resolution or config.CAMERA_RESOLUTION
    cmd = [
        "fswebcam",
        "-d",
        device,
        "--no-banner",
        "-r",
        resolution,
        "-S",
        "2",  # skip initial frames so exposure settles
    ]
    if rotate:
        cmd += ["--rotate", str(rotate)]
    cmd.append(output_path)
    logger.info("Capturing image from %s -> %s", device, output_path)
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def capture_upper():
    return capture(
        config.UPPER_CAMERA_DEVICE,
        config.UPPER_IMAGE_PATH,
        rotate=config.UPPER_CAMERA_ROTATE,
    )


def capture_lower():
    return capture(config.LOWER_CAMERA_DEVICE, config.LOWER_IMAGE_PATH)


# --- Timelapse: archive frames over time, assemble into mp4 with ffmpeg --------

# Timelapse cameras available on this unit (the Studio line has no lower camera).
CAMERAS = ("upper", "lower") if lower_camera_enabled() else ("upper",)


def _frames_dir(cam):
    path = os.path.join(config.TIMELAPSE_DIR, cam)
    os.makedirs(path, exist_ok=True)
    return path


def timelapse_path(cam):
    """Path to the assembled mp4 for ``cam`` (may not exist yet)."""
    return os.path.join(config.TIMELAPSE_DIR, f"{cam}.mp4")


def archive_frame(src_path, cam):
    """Save a timestamped copy of ``src_path`` into the timelapse archive and
    prune to TIMELAPSE_MAX_FRAMES. Best-effort: never raises."""
    try:
        folder = _frames_dir(cam)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(src_path, os.path.join(folder, f"{stamp}.jpg"))
        frames = sorted(glob.glob(os.path.join(folder, "*.jpg")))
        for stale in frames[: max(0, len(frames) - config.TIMELAPSE_MAX_FRAMES)]:
            os.remove(stale)
    except Exception as exc:  # noqa: BLE001 - archiving must never break capture
        logger.error("Timelapse archive failed for %s: %s", cam, exc)


def frame_files(cam, start=None, end=None):
    """Archived frames for ``cam``, optionally limited to a date range.

    ``start`` and ``end`` are ``datetime.date`` or ``datetime.datetime``; the
    range is inclusive of both ends. They are compared against the timestamp
    encoded in each filename rather than the file mtime, so re-capturing or
    copying a frame does not move it into the wrong day.

    Returns a list of paths, oldest first.
    """
    pattern = os.path.join(_frames_dir(cam), "*.jpg")
    out = []
    for path in sorted(glob.glob(pattern)):
        stamp = os.path.basename(path)[:15]
        try:
            when = datetime.datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        except ValueError:
            continue  # not one of ours; leave it alone
        if start is not None and when.date() < _as_date(start):
            continue
        if end is not None and when.date() > _as_date(end):
            continue
        out.append(path)
    return out


def _as_date(value):
    """Normalize a date or datetime to a date."""
    if isinstance(value, datetime.datetime):
        return value.date()
    return value


def zip_frames(cam, start=None, end=None):
    """Zip the archived frames for ``cam`` and return it as bytes.

    Used by the download endpoint so a phone or laptop on the tailnet can pull
    the raw JPEGs without needing shell access to the Pi. Frames are stored
    uncompressed: JPEG does not compress further, and spending CPU here would
    be the slowest part of an export that is otherwise just a file copy.
    """
    import io
    import zipfile

    paths = frame_files(cam, start, end)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for path in paths:
            zf.write(path, arcname=os.path.join(cam, os.path.basename(path)))
    buf.seek(0)
    return buf, len(paths)


def generate_timelapse(cam):
    """Assemble the archived frames for ``cam`` into an mp4. Raises
    FileNotFoundError if no frames have been archived yet, or if ffmpeg is not
    installed on this host."""
    folder = _frames_dir(cam)
    if not glob.glob(os.path.join(folder, "*.jpg")):
        raise FileNotFoundError("no frames archived yet")
    # Distinguish "nothing to build" from "cannot build". Both surface as
    # FileNotFoundError, and reporting a missing ffmpeg as a missing frame
    # archive sends the reader looking in entirely the wrong place.
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError(
            "ffmpeg is not installed on this host; install it to build timelapses"
        )
    out = timelapse_path(cam)
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(config.TIMELAPSE_FPS),
        "-pattern_type",
        "glob",
        "-i",
        os.path.join(folder, "*.jpg"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        out,
    ]
    logger.info("Assembling timelapse for %s -> %s", cam, out)
    subprocess.run(cmd, capture_output=True, check=True)
    return out
