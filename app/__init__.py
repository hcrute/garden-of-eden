import logging
import os

from flask import Flask, jsonify, request
from flask_cors import CORS

import config

logger = logging.getLogger(__name__)

# (module path, blueprint attribute, url prefix). Imported inside create_app rather
# than at module scope on purpose: importing a routes module builds its
# hardware, so an import here meant that ANY `import app.<anything>` opened the
# GPIO pins. scripts/capture-frames.sh imports app.sensors.camera.camera only
# to archive a JPEG, and was silently zeroing the light pin on every run.
BLUEPRINTS = (
    (".sensors.light.routes", "light_blueprint", "/light"),
    (".sensors.pump.routes", "pump_blueprint", "/pump"),
    (".sensors.distance.routes", "distance_blueprint", "/distance"),
    (".sensors.temperature.routes", "temperature_blueprint", "/temperature"),
    (".sensors.humidity.routes", "humidity_blueprint", "/humidity"),
    (".sensors.pcb_temp.routes", "pcb_temp_blueprint", "/pcb-temp"),
    (".sensors.camera.routes", "camera_blueprint", "/camera"),
    (".sensors.schedule.routes", "schedule_blueprint", "/schedule"),
    (".sensors.grow.routes", "grow_blueprint", "/grow"),
    (".sensors.pods.routes", "pods_blueprint", "/pods"),
    (".sensors.system.routes", "system_blueprint", "/system"),
    (".sensors.advice.routes", "advice_blueprint", "/advice"),
    # The web UI is not a sensor, so it does not live under sensors/.
    (".web.routes", "web_blueprint", None),
)


def create_app(config_name=None):
    app = Flask(__name__)

    # CORS lives here (not run.py) so the test client and any WSGI host get
    # identical behavior.
    CORS(app)

    _register_auth(app)

    # Register blueprints. See BLUEPRINTS for why this is not at module scope.
    import importlib

    for module_path, attr, prefix in BLUEPRINTS:
        module = importlib.import_module(module_path, __name__)
        blueprint = getattr(module, attr)
        app.register_blueprint(blueprint, **({"url_prefix": prefix} if prefix else {}))

    @app.route("/health")
    def health():
        return jsonify(status="ok"), 200

    return app


def _register_auth(app):
    """Optional admin-password auth (issue #7).

    Enabled only when GARDEN_ADMIN_PASSWORD (or its deprecated alias
    GARDEN_API_KEY) is set. Localhost requests (e.g. the Pi's own cron jobs)
    and CORS preflight bypass the check so automation keeps working without a
    password.
    """
    api_key = (config.GARDEN_ADMIN_PASSWORD or "").strip()
    if not api_key:
        return

    if not os.getenv("GARDEN_ADMIN_PASSWORD") and os.getenv("GARDEN_API_KEY"):
        logger.warning("GARDEN_API_KEY is deprecated; rename it to GARDEN_ADMIN_PASSWORD")

    # The UI shell and static assets load without a key so the page can prompt
    # for one; API calls it makes still carry the key.
    @app.before_request
    def check_auth():
        if request.method == "OPTIONS":
            return None
        if request.remote_addr in ("127.0.0.1", "::1"):
            return None
        if request.path in ("/", "/health") or request.path.startswith("/static"):
            return None
        if request.headers.get("X-API-Key", "").strip() != api_key:
            return jsonify(error="Unauthorized"), 401
        return None
