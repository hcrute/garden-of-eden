#!/usr/bin/env bash

# Install the MQTT service.
#
# Generated rather than committed for the same reason as the timelapse units:
# User= and WorkingDirectory= are machine-specific (CLAUDE.md). setup.sh calls
# this too, so the unit definition has a single source of truth.
#
# mqtt.py publishes Home Assistant discovery, the camera image entity, and the
# periodic frame capture. It needs a broker to talk to; BROKER/PORT come from
# .env and default to localhost:1883.
#
# Usage: sudo scripts/install-mqtt-service.sh

set -euo pipefail

GOE_PATH="$(realpath "$(dirname "$(readlink -e "$0")")/..")"
RUN_USER="${SUDO_USER:-$USER}"

if [ "$(id -u)" -ne 0 ]; then
    echo "This installs a system unit and needs sudo." >&2
    echo "Usage: sudo $0" >&2
    exit 1
fi

if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    echo "User '$RUN_USER' does not exist. Run under the account that owns the checkout." >&2
    exit 1
fi

PYTHON="${GOE_PATH}/venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "Missing runtime: $PYTHON (run scripts/setup.sh first)" >&2
    exit 1
fi
if [ ! -f "${GOE_PATH}/mqtt.py" ]; then
    echo "Missing ${GOE_PATH}/mqtt.py" >&2
    exit 1
fi

unit_file=$(mktemp)
trap 'rm -f "$unit_file"' EXIT

cat > "$unit_file" <<EOF
[Unit]
Description=MQTT Service
Requires=pigpiod.service
After=network-online.target pigpiod.service
Wants=network-online.target
# The service retries rather than giving up: without StartLimitIntervalSec=0
# systemd stops after a burst of failures and the unit then looks "failed"
# even though a broker may simply not be up yet.
StartLimitIntervalSec=0

[Service]
User=$RUN_USER
WorkingDirectory=$GOE_PATH
# Wait (up to 60s) for pigpiod before starting, so the service does not
# crash-restart during the boot race.
ExecStartPre=/bin/bash -c 'for i in \$(seq 1 60); do (echo > /dev/tcp/127.0.0.1/8888) >/dev/null 2>&1 && exit 0; sleep 1; done; exit 0'
ExecStart=$PYTHON $GOE_PATH/mqtt.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

install -m 644 "$unit_file" /etc/systemd/system/mqtt.service

systemctl daemon-reload
systemctl enable mqtt.service
systemctl restart mqtt.service

echo
if systemctl is-active --quiet mqtt.service; then
    echo "mqtt.service installed and running."
else
    echo "mqtt.service installed but NOT running. Most likely no broker is listening." >&2
    journalctl -u mqtt.service -n 20 --no-pager >&2
fi

echo
echo "Broker check (BROKER/PORT come from .env, default localhost:1883):"
BROKER="${MQTT_BROKER:-localhost}"
PORT="${MQTT_PORT:-1883}"
if (exec 3<>"/dev/tcp/${BROKER}/${PORT}") 2>/dev/null; then
    echo "  ${BROKER}:${PORT} reachable."
else
    echo "  ${BROKER}:${PORT} NOT reachable -- mqtt.py will retry until it is." >&2
    echo "  Start a local broker:   sudo apt install -y mosquitto && sudo systemctl enable --now mosquitto" >&2
    echo "  Or point BROKER at your Home Assistant broker in .env." >&2
fi