#!/usr/bin/env bash

# Install the garden-timelapse timer.
#
# The unit files are generated here rather than committed, for the same reason
# setup.sh generates its other units: they contain User= and WorkingDirectory=,
# which are machine-specific and must not live in the repository (CLAUDE.md).
#
# Timelapse needs a periodic frame capture, not a long-running service, so this
# is a oneshot service driven by a timer. Use this script when you want the
# timer without a full scripts/setup.sh run -- setup.sh calls it too, so the unit
# definitions have a single source of truth.
#
# Interval: TIMELAPSE_INTERVAL (seconds), default 3600 (hourly).
#
# Usage: sudo scripts/install-timelapse-timer.sh

set -euo pipefail

GOE_PATH="$(realpath "$(dirname "$(readlink -e "$0")")/..")"
INSTALL_USER="${SUDO_USER:-$USER}"
INTERVAL="${TIMELAPSE_INTERVAL:-3600}"
RUN_USER="${INSTALL_USER}"

if [ "$(id -u)" -ne 0 ]; then
    echo "This installs system units and needs sudo." >&2
    echo "Usage: sudo $0" >&2
    exit 1
fi

# The service runs unprivileged as the repo owner, not root, so the GPIO and
# pigpio permissions from setup.sh's group membership apply.
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    echo "User '$RUN_USER' does not exist. Run under the account that owns the checkout." >&2
    exit 1
fi

CAPTURE="${GOE_PATH}/scripts/capture-frames.sh"
if [ ! -x "$CAPTURE" ]; then
    echo "Missing or not executable: $CAPTURE" >&2
    exit 1
fi

service_file=$(mktemp)
timer_file=$(mktemp)
trap 'rm -f "$service_file" "$timer_file"' EXIT

cat > "$service_file" <<EOF
[Unit]
Description=Garden of Eden timelapse frame capture
After=network.target pigpiod.service
Wants=pigpiod.service

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$GOE_PATH
ExecStart=$CAPTURE
EOF

cat > "$timer_file" <<EOF
[Unit]
Description=Capture Garden of Eden timelapse frames

[Timer]
# First frame shortly after boot so a fresh install has something to build.
OnBootSec=2min
OnUnitActiveSec=${INTERVAL}s
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
EOF

install -m 644 "$service_file" /etc/systemd/system/garden-timelapse.service
install -m 644 "$timer_file" /etc/systemd/system/garden-timelapse.timer

systemctl daemon-reload
systemctl enable --now garden-timelapse.timer

echo
echo "Installed. Frames land in ${GOE_PATH}/timelapse/ every ${INTERVAL}s."
systemctl list-timers garden-timelapse.timer --no-pager
