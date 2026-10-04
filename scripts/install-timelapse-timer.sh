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
# Schedule: TIMELAPSE_SCHEDULE, default "hourly". Either a preset name (see
# config.TIMELAPSE_SCHEDULE_PRESETS, or --list-presets here) or a raw systemd
# OnCalendar expression such as "*-*-* 06:00:00".
#
# TIMELAPSE_INTERVAL (seconds) still works and is converted to a matching
# schedule with a deprecation warning; it cannot express anything under a
# minute, which OnCalendar does not support either.
#
# Usage: sudo scripts/install-timelapse-timer.sh [--schedule NAME|EXPR] [--list-presets]

set -euo pipefail

GOE_PATH="$(realpath "$(dirname "$(readlink -e "$0")")/..")"
INSTALL_USER="${SUDO_USER:-$USER}"
RUN_USER="${INSTALL_USER}"
SCHEDULE="${TIMELAPSE_SCHEDULE:-}"

# Back-compat: TIMELAPSE_INTERVAL (seconds) predates OnCalendar support.
# OnCalendar cannot express a sub-minute period, so anything under 60s is
# rejected rather than silently rounded up to every minute.
if [ -z "$SCHEDULE" ]; then
    if [ -n "${TIMELAPSE_INTERVAL:-}" ]; then
        if [ "$TIMELAPSE_INTERVAL" -lt 60 ]; then
            echo "TIMELAPSE_INTERVAL=$TIMELAPSE_INTERVAL is under a minute;" >&2
            echo "OnCalendar cannot schedule that. Use TIMELAPSE_SCHEDULE instead." >&2
            exit 1
        fi
        MINUTES=$((TIMELAPSE_INTERVAL / 60))
        SCHEDULE="*:0/${MINUTES}"
        echo "TIMELAPSE_INTERVAL is deprecated; using schedule '$SCHEDULE'." >&2
        echo "Set TIMELAPSE_SCHEDULE in .env to make this permanent." >&2
    else
        SCHEDULE="hourly"
    fi
fi

list_presets() {
    "$GOE_PATH/venv/bin/python" - <<'PYEOF'
import config
for name, expr in sorted(config.TIMELAPSE_SCHEDULE_PRESETS.items()):
    print(f"  {name:<16} {expr}")
PYEOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --schedule)
            SCHEDULE="${2:?--schedule needs a value}"
            shift 2
            ;;
        --schedule=*) SCHEDULE="${1#*=}"; shift ;;
        --list-presets)
            list_presets
            exit 0
            ;;
        -h|--help)
            sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

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

# Resolve the schedule to a systemd calendar expression. A preset name maps to
# its expression; anything else is passed through so the full systemd calendar
# syntax stays available. An empty or nonsense expression is rejected here
# rather than producing a timer that silently never fires.
read -r ON_CALENDAR IS_PRESET <<EOF
$("$GOE_PATH/venv/bin/python" - <<PYEOF
import config
name = ${SCHEDULE@Q}
presets = config.TIMELAPSE_SCHEDULE_PRESETS
expr = presets.get(name, name)
print(expr, "preset" if name in presets else "custom")
PYEOF
)
EOF

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
OnCalendar=$ON_CALENDAR
# Persistent: if the Pi was off when the schedule came round -- which is the
# normal case for a once-a-day capture -- systemd runs it at next boot rather
# than skipping that day entirely.
Persistent=true

[Install]
WantedBy=timers.target
EOF

install -m 644 "$service_file" /etc/systemd/system/garden-timelapse.service
install -m 644 "$timer_file" /etc/systemd/system/garden-timelapse.timer

systemctl daemon-reload
systemctl enable --now garden-timelapse.timer

echo
if [ "$IS_PRESET" = "preset" ]; then
    echo "Installed. Schedule '$SCHEDULE' -> OnCalendar=$ON_CALENDAR"
else
    echo "Installed. Schedule '$SCHEDULE' (custom OnCalendar expression)"
fi
echo "Frames land in ${GOE_PATH}/timelapse/"
echo
# If this does not resolve, the expression is wrong and the timer will never
# fire, so say so loudly rather than reporting a successful install.
if ! systemctl show garden-timelapse.timer -p TimersCalendar --value | grep -q .; then
    echo "WARNING: systemd did not accept OnCalendar=$ON_CALENDAR." >&2
    echo "         Run --list-presets, or check the syntax with:" >&2
    echo "           systemd-analyze calendar '$ON_CALENDAR'" >&2
fi
systemctl list-timers garden-timelapse.timer --no-pager
