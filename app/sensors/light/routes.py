import logging
import threading
import time
from functools import wraps

from flask import Blueprint, jsonify, request

import config
from app.lib import state as state_lib
from app.lib.guards import check_sensor_guard, parse_level
from app.lib.hardware import get_pin_factory

from .light import Light as LightControl

logger = logging.getLogger(__name__)

# Incremented per ramp so a running one can tell it has been superseded.
_ramp_generation = 0

light_blueprint = Blueprint("light", __name__)

try:
    light_control = LightControl(
        pin=config.LIGHT_PIN,
        frequency=config.LIGHT_FREQUENCY,
        pin_factory=get_pin_factory(),
    )
except Exception as exc:
    logger.error("Failed to initialize Light: %s", exc)
    light_control = None


def reclaim_light():
    """Re-create the Light so this process holds pigpiod's PWM claim again.

    gpiozero releases the pin when a Light is garbage-collected, and every
    process that imports the app builds its own. The 06:00 sunrise ramp is a
    short-lived process: when it exits it drops pigpiod's PWM claim on GPIO18,
    and this long-lived API -- which has held a Light since yesterday -- can
    then no longer write to the pin at all. It surfaces as every light request
    failing with 'GPIO is not in use for PWM' while the duty-cycle register
    still cheerfully reports the brightness it last held.

    Rebuilding the device re-registers the claim. Cheap, and only happens on
    the error path.
    """
    global light_control
    old = light_control
    try:
        if old is not None:
            old.close()
    except Exception as exc:  # noqa: BLE001 - closing a dead device is fine
        logger.debug("Ignoring error closing the old Light: %s", exc)
    try:
        light_control = LightControl(
            pin=config.LIGHT_PIN,
            frequency=config.LIGHT_FREQUENCY,
            pin_factory=get_pin_factory(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not re-create Light: %s", exc)
        light_control = None
        return False
    logger.warning("Re-claimed the light pin after losing pigpio's PWM claim")
    return light_control is not None


def with_reclaim(func):
    """Retry a light route once after re-claiming the pin.

    Only the lost-PWM-claim error is retried; anything else is a genuine
    hardware fault and should surface as a 503 as before.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if "not in use for PWM" not in str(exc):
                raise
            logger.warning("Light pin lost its PWM claim (%s); reclaiming", exc)
            if not reclaim_light():
                raise
            return func(*args, **kwargs)

    return wrapper


check_sensor = check_sensor_guard(sensor=light_control, sensor_name="Light")


@light_blueprint.route("/on", methods=["POST"])
@check_sensor
@with_reclaim
def turn_on():
    light_control.on()
    state_lib.save_state(light_on=True)
    return jsonify(message="Light turned on!"), 200


@light_blueprint.route("/off", methods=["POST"])
@check_sensor
@with_reclaim
def turn_off():
    light_control.off()
    state_lib.save_state(light_on=False)
    return jsonify(message="Light turned off!"), 200


@light_blueprint.route("/brightness", methods=["POST"])
@check_sensor
@with_reclaim
def set_brightness():
    data = request.get_json(silent=True) or {}
    brightness_value = parse_level(data, default=config.DEFAULT_BRIGHTNESS)
    light_control.set_brightness(brightness_value)
    state_lib.save_state(light_on=brightness_value > 0, brightness=brightness_value)
    return jsonify(message=f"Light adjusted to {brightness_value}%"), 200


@light_blueprint.route("/ramp", methods=["POST"])
@check_sensor
def start_ramp():
    """Fade to a target over a number of minutes, in the background.

    This exists so the sunrise and sunset can be driven by the API rather than
    by a short-lived CLI process. A CLI that builds its own Light releases the
    pin when it exits, and pigpiod drops the PWM claim with it -- which leaves
    this process, holding a Light since yesterday, unable to write to the pin
    at all. That happened at 06:15 this morning.

    Returns immediately with 202. A new ramp supersedes any ramp in flight,
    because two threads stepping the same pin toward different targets is
    worse than either one alone.
    """
    data = request.get_json(silent=True) or {}
    target = parse_level(data, key="brightness", default=None)
    minutes = data.get("minutes", 15)
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
        return jsonify(error="'minutes' must be a positive number"), 400
    if minutes > 120:
        return jsonify(error="'minutes' must be 120 or less"), 400

    global _ramp_generation
    _ramp_generation += 1  # supersedes any ramp in flight
    thread = threading.Thread(
        target=_run_ramp,
        args=(light_control, int(round(target)), float(minutes), _ramp_generation),
        name="light-ramp",
        daemon=True,
    )
    thread.start()
    return (
        jsonify(message=f"Ramping to {int(round(target))}% over {minutes:g} min"),
        202,
    )


def _run_ramp(device, target, minutes, generation):
    """Step the light toward ``target``. Runs on a daemon thread.

    Bails out between steps if a newer ramp has started, judged by comparing
    generations rather than a shared boolean -- two ramps crossing on one pin,
    each steering, would fight.
    """
    try:
        start = device.get_brightness()
        total = max(1, int(minutes * 60))
        steps = max(1, min(total, 60))  # at most ~1 update/sec
        delay = total / steps
        for i in range(1, steps + 1):
            if _ramp_generation != generation:
                logger.info("Ramp to %s%% superseded; stopping at %s%%", target, i * delay)
                return
            value = start + (target - start) * i / steps
            device.set_brightness(int(round(value)))
            if i < steps:
                time.sleep(delay)
        device.set_brightness(target)
        state_lib.save_state(light_on=target > 0, brightness=target)
        logger.info("Ramp finished at %s%%", target)
    except Exception as exc:  # noqa: BLE001 - a thread must not die silently
        logger.error("Light ramp failed: %s", exc)


@light_blueprint.route("/brightness", methods=["GET"])
@check_sensor
def get_brightness():
    brightness_value = light_control.get_brightness()
    return jsonify(value=brightness_value), 200
