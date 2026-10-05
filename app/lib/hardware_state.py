"""Read what the hardware is actually doing, rather than what we last asked.

Three separate processes drive the light and the pump: the REST API,
``mqtt.py``, and the cron scripts that ``build_cron_lines`` emits. Each holds
its own ``gpiozero`` device, and ``PWMLED.value`` is a *per-process cache* of
what that process last wrote. It is not a reading of the pin, and it cannot
observe the other two.

That gap is the whole reason a class of bugs looked like mysteries:

* the hourly timelapse capture zeroed the pin, and the API went on reporting
  the brightness it had set hours earlier
* a manual override from the web UI was invisible to mqtt.py, which restored
  its own remembered value on the next restart
* after a reboot the API reported a comfortable number for a light that was
  in fact dark

pigpiod holds the only shared truth, so this module goes straight to it and
answers "is the pin actually where we think it is?".

Scale note, because it is easy to get wrong: gpiozero's ``PWMLED`` writes a
0.0-1.0 value and pigpiod stores the duty cycle on its own configurable
range -- 10000 on this hardware, not the default 255. Measured on the Pi:

    set 50% via gpiozero  ->  get_PWM_dutycycle 5000 / get_PWM_range 10000

so the true fraction is always ``dutycycle / range``. Dividing by 255 is not a
39x error, it is meaningless.
"""

import logging

import config

logger = logging.getLogger(__name__)

# Anything under this is "off". Hardware never reports an exact zero because a
# PWM duty cycle is a real-world analogue level, not a bit.
OFF_EPSILON = 0.005


def duty_fraction(pin, pi=None):
    """The pin's PWM duty cycle as a 0.0-1.0 fraction, read from pigpiod.

    Returns None when pigpiod cannot be reached or the pin is not a PWM pin,
    so callers can tell "off" from "unknown" rather than reporting a fault as
    a dark light. Pass ``pi`` to reuse an existing connection.
    """
    own = pi is None
    if own:
        try:
            import pigpio

            pi = pigpio.pi()
        except Exception as exc:  # noqa: BLE001 - off-Pi, or pigpiod down
            logger.warning("Could not connect to pigpiod: %s", exc)
            return None
    try:
        if not getattr(pi, "connected", False):
            return None
        duty = pi.get_PWM_dutycycle(pin)
        rng = pi.get_PWM_range(pin)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read duty cycle for pin %s: %s", pin, exc)
        return None
    finally:
        if own:
            try:
                pi.stop()
            except Exception:  # noqa: BLE001
                pass
    if not rng:
        return None
    return max(0.0, min(1.0, float(duty) / float(rng)))


def light_actual(pin=None):
    """What the light pin is actually doing right now."""
    pin = config.LIGHT_PIN if pin is None else pin
    fraction = duty_fraction(pin)
    if fraction is None:
        return None
    return {"on": fraction > OFF_EPSILON, "brightness": round(fraction * 100, 1)}


def pump_actual(pin=None):
    """What the pump pin is actually doing right now."""
    pin = config.PUMP_PIN if pin is None else pin
    fraction = duty_fraction(pin)
    if fraction is None:
        return None
    return {"on": fraction > OFF_EPSILON, "speed": round(fraction * 100, 1)}


def verify_lights(schedule=None, now=None):
    """Compare what the schedule wants against what the pin is doing.

    Three independent numbers, deliberately not conflated:

    ``expected``  what the schedule says should happen right now
    ``commanded`` what the app last recorded writing (STATE_FILE)
    ``actual``    what pigpiod reports the pin is doing, read just now

    ``ok`` is True only when expected and actual agree. A mismatch between
    expected and commanded is normal after a manual override; a mismatch
    involving ``actual`` is the machine not doing what it was told.
    """
    from app.lib import state as state_lib
    from app.sensors.schedule import schedule as sched

    state = state_lib.load_state()
    expected = sched.light_state_now(schedule, now=now) if schedule is not None else None
    actual = light_actual()

    report = {
        "expected": expected,
        "commanded": {"on": state.get("light_on"), "brightness": state.get("brightness")},
        "actual": actual,
        "ok": None,
        "detail": None,
    }

    if expected is None:
        report["detail"] = "the schedule does not manage the lights"
        return report
    if actual is None:
        report["detail"] = "the light pin could not be read"
        return report

    if expected["on"] and not actual["on"]:
        report["ok"] = False
        report["detail"] = (
            f"schedule wants the lights on at {expected['brightness']}% but the pin is off"
        )
    elif not expected["on"] and actual["on"]:
        report["ok"] = False
        report["detail"] = (
            f"schedule wants the lights off but the pin is at {actual['brightness']}%"
        )
    else:
        report["ok"] = True
        if expected["on"]:
            report["detail"] = (
                f"lights on; pin reads {actual['brightness']}% "
                f"(schedule says {expected['brightness']}%)"
            )
        else:
            report["detail"] = "lights off, as scheduled"
    return report
