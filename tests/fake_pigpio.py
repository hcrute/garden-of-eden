"""A model of the pigpiod behaviours that have actually caused faults.

Real pigpiod has two properties that no amount of mocking a PWMLED can
reproduce, and both have cost real time on a Pi:

1. **PWM is owned per connection.** Only the client that set the pin up as PWM
   may write the duty cycle. Another client gets "GPIO is not in use for PWM".

2. **The duty-cycle register outlives the claim.** Releasing PWM on disconnect
   leaves the register holding its last value, so the pin still *reads* a
   plausible brightness while driving nothing. That is why the light read 30%
   for hours while every write was being rejected -- and why the endpoint
   added to be independent evidence reported all clear.

This models exactly those, and nothing else, so a test can reproduce the
06:15 failure and assert it stays fixed.
"""


class PigpioError(Exception):
    """Mirrors pigpio.error, including the message text the code matches on."""


class _FakeConnection:
    """One client's view of the daemon."""

    def __init__(self, daemon, name):
        self._daemon = daemon
        self._name = name
        self.connected = True

    # -- reads ---------------------------------------------------------------
    def get_mode(self, pin):
        self._daemon._require_connected(self)
        return self._daemon.mode(pin)

    def get_PWM_dutycycle(self, pin):
        self._daemon._require_connected(self)
        if self._daemon.pwm_owner(pin) is None:
            raise PigpioError("GPIO is not in use for PWM")
        return self._daemon.duty(pin)

    def get_PWM_range(self, pin):
        self._daemon._require_connected(self)
        return self._daemon.rng(pin)

    # -- writes --------------------------------------------------------------
    def set_PWM_range(self, pin, value):
        self._daemon._require_connected(self)
        self._daemon.ranges[pin] = value

    def set_PWM_frequency(self, pin, value):
        self._daemon._require_connected(self)
        self._daemon.freqs[pin] = value
        # Claiming PWM, as pigpiod does on first use of the pin as PWM.
        self._daemon.owners[pin] = self._name
        self._daemon.modes[pin] = 1

    def set_PWM_dutycycle(self, pin, value):
        self._daemon._require_connected(self)
        owner = self._daemon.owners.get(pin)
        if owner is None:
            raise PigpioError("GPIO is not in use for PWM")
        if owner != self._name:
            raise PigpioError("GPIO is not in use for PWM")
        self._daemon.duties[pin] = value

    def read(self, pin):
        self._daemon._require_connected(self)
        return 1 if self._daemon.duty(pin) > 0 else 0

    def stop(self):
        """Disconnect -- and release this client's PWM claims, as pigpiod does.

        gpiozero calls this at process teardown, which is the entire mechanism
        behind the 06:15 failure.
        """
        if not self.connected:
            return
        self.connected = False
        self._daemon.release(self._name)

    # pigpio.pi() exposes .connected
    @property
    def connected_ok(self):
        return self.connected


class FakePigpiod:
    """Shared daemon state. One per test, handed to each fake connection."""

    def __init__(self, default_range=10000, default_freq=8000):
        self.modes = {}
        self.duties = {}
        self.ranges = {}
        self.freqs = {}
        self.owners = {}
        self._default_range = default_range
        self._default_freq = default_freq
        self._serial = 0

    # -- accessors used by the code under test ------------------------------
    def mode(self, pin):
        return self.modes.get(pin, 0)

    def duty(self, pin):
        return self.duties.get(pin, 0)

    def rng(self, pin):
        return self.ranges.get(pin, self._default_range)

    def pwm_owner(self, pin):
        return self.owners.get(pin)

    def release(self, client_name):
        """Drop every PWM claim held by ``client_name``; the register survives."""
        for pin, owner in list(self.owners.items()):
            if owner == client_name:
                del self.owners[pin]
                self.modes[pin] = 0  # gpiozero puts the pin back to input

    def connect(self, name=None):
        self._serial += 1
        return _FakeConnection(self, name or f"client{self._serial}")

    def _require_connected(self, conn):
        if not conn.connected:
            raise PigpioError("socket is not connected")

    # -- assertions ---------------------------------------------------------
    def fraction(self, pin):
        rng = self.rng(pin)
        return 0.0 if not rng else self.duty(pin) / rng
