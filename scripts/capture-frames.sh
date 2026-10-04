#!/usr/bin/env bash

# Capture one frame per configured camera and archive it for timelapse.
#
# Run from garden-timelapse.timer on a schedule. Historically frames were only
# archived by the MQTT publisher, so timelapse silently stayed empty on any unit
# without mqtt.service installed. This does just the capture, so the timelapse
# feature does not depend on a broker.
#
# Failures are logged and swallowed: a camera unplugged should not make a timer
# unit flap or block the rest of the run.

set -uo pipefail

GOE_PATH="$(realpath "$(dirname "$(readlink -e "$0")")/..")"
export PYTHONPATH="${GOE_PATH}${PYTHONPATH:+:${PYTHONPATH}}"

"${GOE_PATH}/venv/bin/python" - <<'PY'
import logging

import config
from app.sensors.camera import camera

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("timelapse-capture")

# Honor the model: a Gardyn 3.0 has no lower camera, so do not fail on it.
for name in camera.CAMERAS:
    capture = camera.capture_upper if name == "upper" else camera.capture_lower
    try:
        path = capture()
    except Exception as exc:
        log.error("%s camera capture failed: %s", name, exc)
        continue
    camera.archive_frame(path, name)
    log.info("archived %s frame -> %s", name, config.TIMELAPSE_DIR)

print(
    "frames kept: %d/%d, playback %d fps"
    % (
        len(__import__("glob").glob(config.TIMELAPSE_DIR + "/*/*.jpg")),
        config.TIMELAPSE_MAX_FRAMES,
        config.TIMELAPSE_FPS,
    )
)
PY