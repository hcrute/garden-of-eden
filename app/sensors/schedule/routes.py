import logging

from flask import Blueprint, jsonify, request

from . import schedule as sched

logger = logging.getLogger(__name__)

schedule_blueprint = Blueprint("schedule", __name__)


@schedule_blueprint.route("", methods=["GET"])
def get_schedule():
    payload = sched.load_schedule()
    # Cron fails silently when the light/water symlinks are absent, so report
    # them alongside the schedule rather than letting it look healthy.
    missing = sched.missing_scripts()
    payload["scripts_missing"] = missing
    payload["scripts_ready"] = not missing
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
    return jsonify(applied)
