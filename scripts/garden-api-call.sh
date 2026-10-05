#!/usr/bin/env bash

# Ask the running API to act, instead of touching the GPIO pin directly.
#
# Why this exists: gpiozero releases a pin when its device object is collected,
# and pigpiod drops the PWM claim with it. Cron runs the sunrise and sunset as
# short-lived processes, so when one exits the long-lived API -- which has held
# a Light since the day before -- can no longer write to the pin at all. Every
# light request then fails with "GPIO is not in use for PWM" while the
# duty-cycle register still cheerfully reports the brightness it last held.
#
# The API is the single owner of the actuators; everything else asks it.
# Direct GPIO remains the fallback for when the API is down, which is the one
# case where there is no long-lived process to disturb.
#
# Usage: garden-api-call.sh <METHOD> <PATH> [JSON_BODY]
# Prints the API's response. Exit 0 on success, 1 if the API is unreachable.

set -uo pipefail

GOE_PATH="$(realpath "$(dirname "$(readlink -e "$0")")/..")"

METHOD="${1:?usage: garden-api-call.sh METHOD PATH [BODY]}"
API_PATH="${2:?usage: garden-api-call.sh METHOD PATH [BODY]}"
BODY="${3:-}"

# Read the admin password from .env without sourcing it: sourcing a .env is how
# quoted values and stray spaces become shell syntax errors at 3am from cron.
API_KEY=""
if [ -f "${GOE_PATH}/.env" ]; then
    API_KEY="$(grep -E '^[[:space:]]*GARDEN_ADMIN_PASSWORD=' "${GOE_PATH}/.env" \
        | tail -n1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')"
fi

BASE_URL="${GARDEN_BASE_URL:-http://127.0.0.1:${PORT:-5000}}"

args=(-s -m 15 -X "$METHOD" "${BASE_URL}${API_PATH}" -w '\n%{http_code}')
[ -n "$API_KEY" ] && args+=(-H "X-API-Key: ${API_KEY}")
if [ -n "$BODY" ]; then
    args+=(-H 'Content-Type: application/json' -d "$BODY")
fi

out="$(curl "${args[@]}" 2>/dev/null)" || exit 1
code="${out##*$'\n'}"
body_out="${out%$'\n'*}"

# 2xx is success. A 401/503 means the API is up but refused -- report it rather
# than silently falling back to direct GPIO, because the caller needs to know
# its command did NOT happen where it thinks it did.
case "$code" in
    2*) printf '%s\n' "$body_out"; exit 0 ;;
    000) exit 1 ;;                       # unreachable
    *) printf 'API %s: %s\n' "$code" "$body_out" >&2; exit 1 ;;
esac