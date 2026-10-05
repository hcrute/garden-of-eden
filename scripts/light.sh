#!/usr/bin/env bash

# Script to control Gardyn lights
# Usage: light <brightness|on|off>
# "on" sets brightness to 70%, valid brightness range is 4%-100%

# -e exit immediately 
# -u undefined variables trigger error
# -o exit with first piped failure
set -euo pipefail

# Constants
readonly BRIGHTNESS_DEFAULT=70
readonly BRIGHTNESS_MIN=0
readonly BRIGHTNESS_MAX=100
readonly LIGHT_BY_DEFAULT=true   # Whether to default to 70% brightness on invalid input

RST=$(echo -e '\e[0m')
ITL=$(echo -e '\e[3m')

# Get Garden of Eden path from script location
GOE_PATH=$(realpath "$(dirname "$(readlink -e "${0}")")/..")
readonly API_CALL="${GOE_PATH}/scripts/garden-api-call.sh"

# Put the repo root on PYTHONPATH so the driver scripts can `import config`
# regardless of the caller's working directory (cron, systemd, etc.).
export PYTHONPATH="${GOE_PATH}${PYTHONPATH:+:${PYTHONPATH}}"

# Drive the light, preferring the API as the single owner of the pin.
#
# The API is long-lived and holds pigpiod's PWM claim. This script is not: a
# direct GPIO write from here would release the pin on exit and leave the API
# unable to control the light. See scripts/garden-api-call.sh.
# Direct GPIO remains the fallback for when the API is down.

# Turn off the light
turn_off_light() {
    "${API_CALL}" POST /light/off >/dev/null 2>&1 \
        && return 0
    "${GOE_PATH}/venv/bin/python" "${GOE_PATH}/app/sensors/light/light.py" --off
}

# Turn on the light with specified brightness
turn_on_light() {
    local brightness="$1"
    "${API_CALL}" POST /light/brightness "{\"value\": ${brightness}}" >/dev/null 2>&1 \
        && return 0
    "${GOE_PATH}/venv/bin/python" "${GOE_PATH}/app/sensors/light/light.py" --on --brightness "${brightness}"
}

# Print usage information
usage() {
    cat << EOF
Usage: light <off|on|${ITL}brightness${RST}>
Brightness value must be ${BRIGHTNESS_MIN}-${BRIGHTNESS_MAX}; "on" defaults to ${BRIGHTNESS_DEFAULT}.
Example: light 65
EOF
}

# Validate brightness
validate_brightness() {
    local brightness="$1"
    if [[ "${brightness}" -ge "${BRIGHTNESS_MIN}" && "${brightness}" -le "${BRIGHTNESS_MAX}" ]]; then
        return 0
    else
        return 1
    fi
}

# Main logic
main() {
    if [[ $# -eq 0 ]]; then
        echo "ERROR: No arguments provided"
        usage
        exit 1
    fi

    local brightness

    case "$1" in
        ramp)
            # ramp <brightness> <minutes> — sunrise/sunset gradual change.
            # Delegated so the API owns the pin for the whole fade. The API
            # ramps on a background thread and returns immediately, so cron is
            # not blocked for the length of the fade either.
            local ramp_brightness="${2:-${BRIGHTNESS_DEFAULT}}"
            local ramp_minutes="${3:-0}"
            if [ "$ramp_minutes" -gt 0 ] 2>/dev/null; then
                "${API_CALL}" POST /light/ramp \
                    "{\"brightness\": ${ramp_brightness}, \"minutes\": ${ramp_minutes}}" \
                    >/dev/null 2>&1 && exit 0
            fi
            "${GOE_PATH}/venv/bin/python" "${GOE_PATH}/app/sensors/light/light.py" \
                --on --brightness "${ramp_brightness}" --ramp-minutes "${ramp_minutes}"
            exit 0
            ;;
        off)
            turn_off_light
            exit 0
            ;;
        on)
            brightness="${BRIGHTNESS_DEFAULT}"
            ;;
        ''|*[!0-9]*)
            echo "ERROR: Unrecognized input format"
            usage
            exit 1
            ;;
        *)
            brightness="$1"
            ;;
    esac

    if validate_brightness "${brightness}"; then
        turn_on_light "${brightness}"
    elif [[ "${LIGHT_BY_DEFAULT}" == true ]]; then
        turn_on_light "${BRIGHTNESS_DEFAULT}"
    else
        echo "ERROR: Brightness must be between ${BRIGHTNESS_MIN}-${BRIGHTNESS_MAX}"
        usage
        exit 1
    fi
}

# Run main function
main "$@"
