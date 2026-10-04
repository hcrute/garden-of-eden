import logging

from flask import Blueprint, jsonify, request

from . import schedule as sched

logger = logging.getLogger(__name__)

schedule_blueprint = Blueprint("schedule", __name__)

# A threshold for "the light is actually off". gpiozero reports tiny residual
# duty cycles, so comparing to exactly 0 would make every save rewrite the pin.
_OFF_EPSILON = 0.5


def reconcile_lights(applied, now=None):
    """Make the hardware agree with the schedule right now.

    Cron only ever acts at a window boundary, so editing a schedule mid-window
    left the machine doing whatever it was doing before -- which, after an
    unrelated fault (a stray capture zeroing the pin, a reboot, a manual
    override), means the lights stay off for hours while the schedule says
    they should be on.

    Returns a small report dict for the API response, or None when the schedule
    has no opinion or nothing needed doing. Never raises: a schedule save must
    succeed even if the GPIO is unavailable.
    """
    target = sched.light_state_now(applied, now=now)
    if target is None:
        return None

    try:
        # Imported lazily: the light module constructs real hardware at import,
        # and a schedule save should not depend on that succeeding.
        from app.sensors.light.routes import light_control
    except Exception as exc:  # noqa: BLE001 - never fail a schedule save
        logger.warning("Could not reconcile lights: %s", exc)
        return None
    if light_control is None:
        return None

    try:
        current = light_control.get_brightness()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read light brightness: %s", exc)
        return None

    try:
        if target["on"]:
            if current > _OFF_EPSILON:
                return None  # already lit; leave a manual brightness alone
            light_control.set_brightness(target["brightness"])
            return {
                "lights_reconciled": True,
                "action": "turned on",
                "brightness": target["brightness"],
                "reason": target["reason"],
            }
        if current <= _OFF_EPSILON:
            return None  # already off
        light_control.off()
        return {
            "lights_reconciled": True,
            "action": "turned off",
            "brightness": 0,
            "reason": target["reason"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not apply light state: %s", exc)
        return None


@schedule_blueprint.route("", methods=["GET"])
def get_schedule():
    payload = sched.load_schedule()
    # Cron fails silently when the light/water symlinks are absent, so report
    # them alongside the schedule rather than letting it look healthy.
    missing = sched.missing_scripts()
    payload["scripts_missing"] = missing
    payload["scripts_ready"] = not missing
    payload["lights_now"] = sched.light_state_now(payload)
    return jsonify(payload)


@schedule_blueprint.route("", methods=["POST"])
def set_schedule():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="expected a JSON schedule object"), 400
    try:
        applied = sched.apply_schedule(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except FileNotFoundError:
        # crontab binary missing (e.g. off-Pi) — schedule is still saved.
        return jsonify(error="crontab not available on this host"), 503

    # Catch up to the schedule immediately rather than waiting for the next
    # window boundary.
    report = reconcile_lights(applied)
    payload = dict(applied)
    payload["lights_now"] = sched.light_state_now(applied)
    if report:
        payload.update(report)
    return jsonify(payload)
