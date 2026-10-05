import logging

from flask import Blueprint, jsonify, request

from app.lib import hardware_state as hw_state

from . import schedule as sched

logger = logging.getLogger(__name__)

schedule_blueprint = Blueprint("schedule", __name__)


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
        # Read the pin, not light_control.get_brightness(). That returns
        # gpiozero's per-process cache of what *this* process last wrote, so it
        # would report the lights as on while another process (the timelapse
        # capture, mqtt.py) had in fact zeroed the pin -- and reconciliation
        # would conclude there was nothing to do.
        actual = hw_state.light_actual()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read light pin: %s", exc)
        return None
    if actual is None:
        return None

    try:
        if target["on"]:
            if actual["on"]:
                return None  # already lit; leave a manual brightness alone
            light_control.set_brightness(target["brightness"])
            return {
                "lights_reconciled": True,
                "action": "turned on",
                "brightness": target["brightness"],
                "reason": target["reason"],
            }
        if not actual["on"]:
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


@schedule_blueprint.route("/hardware", methods=["GET"])
def hardware_state():
    """What the schedule wants, what we last commanded, and what the pin is
    actually doing.

    The three are reported separately and never conflated. ``expected`` comes
    from the schedule, ``commanded`` from the persisted state file, and
    ``actual`` is read from pigpiod right now. Only ``actual`` is independent
    evidence: the other two are things this machine believes.

    This exists because the failure mode it catches is invisible otherwise. A
    second process (the timelapse capture, mqtt.py) zeroing the light pin left
    the API confidently reporting the brightness it had set hours earlier,
    because gpiozero's value is a per-process cache rather than a reading.
    """
    schedule = sched.load_schedule()
    return jsonify(
        {
            "lights": hw_state.verify_lights(schedule),
            "pump_actual": hw_state.pump_actual(),
            "scripts_missing": sched.missing_scripts(),
        }
    )


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
