<img src="docs/_banner.svg" width="800px">

# Garden of Eden

Truly own that which is yours!

If you are interested in collaborating please review the [CONTRIBUTORS](CONTRIBUTORS.md) for commit styling guides.

## Video Tutorial for Gardyn of Eden and Homeassistant

Thanks to "Yong" for very well edited video tutorial.

[Video Tutorial](https://www.youtube.com/watch?v=gH5yu8JwS8Y)

## Project Status & Milestones

Work in progress. We should be picking up some steam here to give the DYI community the features you deserve.

[Milestones](https://github.com/iot-root/garden-of-eden/milestones)

![image](https://github.com/user-attachments/assets/403248f5-b7d4-4cb1-921a-0458f515f387)

## What's new

A set of changes closing out the open milestones:

- **Built-in web UI** — a self-contained control page served by the firmware at
  `http://gardyn.local:5000/` (controls, live sensors + pump power, cameras, grow
  cycle, full schedule editor, run-pump-for-N-seconds). Auto-starts as a service;
  no separate app required. See [`docs/access.md`](docs/access.md).
- **Headless-friendly** — `setup.sh` keeps **SSH on** and sets up mDNS so the unit
  is reachable at `gardyn.local` right after flashing.

> Installing on a Pi? Follow [`docs/INSTALL.md`](docs/INSTALL.md), a step-by-step,
> brick-safe install (dry-run, backups, uninstall).
- **Self-sufficient REST API** — camera, scheduling, grow-cycle, and system/model
  endpoints (see below), with optional API-key auth.
- **Home Assistant** — the physical button is now an HA `event` entity
  (single/double/long); example dashboard in
  [`docs/homeassistant/`](docs/homeassistant/lovelace-example.yaml).
- **Grow-cycle reminders** — thinning, root-check, harvest, and nutrient
  notifications via MQTT/REST.
- **Resilience** — shared pigpio connection, sensor auto-reprobe, power-loss
  state recovery, graceful shutdown.
- **Ops** — Docker/compose, CI (lint + tests), automated changelog, hardened
  setup with OS checks and camera udev rules.
- **Config** — all pins/addresses/thresholds live in `config.py` / `.env`.

See [`docs/design.md`](docs/design.md) for architecture, and
[`docs/maintenance.md`](docs/maintenance.md) for upkeep.

For contributor and coding-assistant context, see the [`docs/`](docs/)
documentation bank. Pi-only operational scripts and private SSH configuration are
covered in [`docs/pi-operations.md`](docs/pi-operations.md).

### REST API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/light/on` `/light/off` | toggle grow light |
| POST/GET | `/light/brightness` | set/get brightness (0–100) |
| POST | `/pump/on` `/pump/off` | toggle pump |
| POST/GET | `/pump/speed` | set/get pump speed |
| GET | `/pump/stats` | INA219 power data |
| GET | `/distance` `/distance/measure` | water-level distance (cm) |
| GET | `/temperature` `/humidity` `/pcb-temp` | environment sensors |
| GET | `/camera/upper` `/camera/lower` | capture a still (JPEG) |
| GET/POST | `/schedule` | lights/pump cron schedule |
| GET | `/grow` · POST `/grow/start` `/grow/stage` `/grow/acknowledge` | grow-cycle |
| GET | `/system` | identity, version, detected model/profile |

### Run with Docker

```
bash
cp .env-dist .env          # edit MQTT + identity
sudo pigpiod -p 8888       # pigpiod on the Pi host
docker compose up -d       # api (:5000) + mqtt + optional broker
```

For private remote access over HTTPS, use Tailscale Serve described in
[`docs/access.md`](docs/access.md). Do not expose port 5000 directly to the
internet.

See [`docs/integrations/`](docs/integrations/README.md) for Telegraf, ThingsBoard,
and Alexa.

### Test it without a Pi

A simulator runs the whole stack with fake hardware so you can try the web UI,
REST API, and Home Assistant discovery on your laptop:

```
bash
python -m venv .venv-dev && .venv-dev/scripts/pip install -r requirements-dev.txt
.venv-dev/scripts/python -m simulator.serve     # http://localhost:5000/
.venv-dev/scripts/python -m simulator.mqtt_sim  # MQTT for Home Assistant (needs a broker)
```

See [`docs/simulator.md`](docs/simulator.md).

## Table of Contents

- [Garden of Eden](#garden-of-eden)
  - [Project Status \& Milestones](#project-status--milestones)
  - [Table of Contents](#table-of-contents)
  - [Getting Started](#getting-started)
    - [Prerequisites](#prerequisites)
    - [Services and Scheduled Jobs](#services-and-scheduled-jobs)
  - [Usage](#usage)
    - [MQTT with HomeAssistant](#mqtt-with-homeassistant)
    - [Testing](#testing)
    - [Developing](#developing)
    - [Controlling Individual Sensors](#controlling-individual-sensors)
    - [REST API](#rest-api)
      - [Endpoints](#endpoints)
      - [Postman](#postman)
    - [Scheduled Jobs (cron)](#scheduled-jobs-cron)
  - [Hardware Overview](#hardware-overview)
    - [Air Temp \& Humidity Sensor](#air-temp--humidity-sensor)
    - [Pump Power Monitor](#pump-power-monitor)
    - [PCB Temp Sensor](#pcb-temp-sensor)
    - [Lights](#lights)
      - [Method](#method)
      - [Pins](#pins)
    - [Pump](#pump)
      - [Method](#method-1)
      - [Pins](#pins-1)
    - [Camera](#camera)
      - [Method](#method-2)
      - [Devices](#devices)
    - [Water Level Sensor](#water-level-sensor)
      - [Pins](#pins-2)
      - [Method](#method-3)
      - [References](#references)
    - [Momentary Button](#momentary-button)
    - [Electrical Diagrams](#electrical-diagrams)
      - [Sensors](#sensors)
      - [Power and Header](#power-and-header)
    - [Recommendations](#recommendations)
      - [Upgrading the Pi Zero 2](#upgrading-the-pi-zero-2)
  - [Design Decisions](#design-decisions)
    - [Python Version 3.9 \>=](#python-version-39-)
    - [Delays in Reading Temp/Humidity data](#delays-in-reading-temphumidity-data)
    - [GPIO](#gpio)
  - [Folder Structure](#folder-structure)

## Getting Started

### Prerequisites

Start with a clean install of Linux. Use the [RaspberryPi Imager](https://www.raspberrypi.com/software/). Ensure ssh and wifi is setup. Once the image is written, pop the SDcard into the pi and ssh into it.

```
bash
# clone repo
git clone git@github.com:iot-root/garden-of-eden.git
cd garden-of-eden 
```

Update the `.env` with mqtt broker info

```
cp .env-dist .env
nano .env
```

Install dependencies, and run services pigpiod, mqtt.service

```
./scripts/setup.sh`
```

Ensure the pigpiod daemon is running

```
sudo systemctl status pigpiod
sudo systemctl status mqtt.service
```

### Services and Scheduled Jobs

`./scripts/setup.sh` installs and enables everything the unit needs at boot:

| Unit | What it does |
|---|---|
| `pigpiod` | GPIO daemon; every driver talks to it |
| `mqtt.service` | `mqtt.py` — MQTT control, Home Assistant discovery, image publisher |
| `garden-api.service` | REST API + web UI, served by Waitress on port 5000 |
| `garden-autoupdate.timer` | nightly `scripts/update.sh` |

Confirm they are up:

```
bash
sudo systemctl status garden-api.service mqtt.service
curl -s http://localhost:5000/health      # {"status":"ok"}
sudo journalctl -u garden-api.service -n 40 --no-pager
```

The lights and pump schedule is **not** a service — it is cron, managed by the
Schedule card in the web UI. See [Scheduled Jobs (cron)](#scheduled-jobs-cron)
below. `setup.sh` also symlinks the two commands that schedule depends on into
`/usr/local/bin`; without those the schedule fails silently, so check them if
your lights and pump never run.

## Usage

## Quick Toggle Guide

> Ensure your press is quick and within the time frame for the action to register correctly. The press time window can be modified directly in the `mqtt.py` file.

- **One Press** (within 1 second): 
  - **Action**: Toggles the **Lights** on or off. 
  - **Description**: A single, swift press will illuminate or darken your space with ease.

- **Two Presses** (within 1 second): 
  - **Action**: Toggles the **Pump** on or off.
  - **Description**: Need to water the garden or fill up the pool? Double tap for action!


### MQTT with HomeAssistant

You need an MQTT broker - either on the Gardyn Pi or on your Home Assistant
host. Installing on the Pi:

```
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients
```

A broker on its own does nothing. `mqtt.service` is what publishes Home
Assistant discovery (25 entities), the camera image, and the periodic frame
capture:

```
sudo scripts/install-mqtt-service.sh
```

The unit embeds your username and checkout path, so it is generated at install
time rather than committed; `scripts/setup.sh` calls the same installer.

#### Local-only access (the default)

Out of the box mosquitto listens on `127.0.0.1` only. That is enough for
`mqtt.py` on the Pi to publish, and it is all the web UI and REST API need -
they do not use MQTT at all. Check it is working:

```
systemctl status mqtt.service
journalctl -u mqtt.service -n 30 --no-pager
```

Look for `Connected with result code Success`.

#### Allowing other machines to connect

To reach the broker from Home Assistant on another machine you must open the
port and require authentication. First give the account a real password in
`.env` - the shipped value is a placeholder:

```
openssl rand -base64 18
```

Then set `MQTT_USERNAME` and `MQTT_PASSWORD` in `.env` and run:

```
sudo scripts/install-mqtt-broker.sh
```

This writes `/etc/mosquitto/conf.d/20-gardyn-remote.conf` (a drop-in, so a
package upgrade cannot clobber it), sets `allow_anonymous false`, and creates
`/etc/mosquitto/passwd` from the credentials in `.env`. The password is piped
to `mosquitto_passwd` on stdin, so it never appears in `ps` or your shell
history. It refuses to run while `MQTT_PASSWORD` is still the example value.

Two `listener` lines are written, and **both are required**:

```
listener 1883 127.0.0.1     # mqtt.py on this machine
listener 1883 100.99.129.5  # remote clients
```

mosquitto only creates the implicit loopback listener when *no* `listener` is
defined. Define one and it disappears - so a single `listener 1883` line that
is remotely reachable also breaks `mqtt.py` on the Pi, and the symptom is a
reconnect loop that looks like a network fault rather than a config mistake.

**Which address to bind.** The script defaults to this machine's Tailscale
address, so the broker is reachable from your tailnet but *not* from the rest
of the LAN. Override it if you want the LAN instead:

```
MQTT_BIND=0.0.0.0 sudo -E scripts/install-mqtt-broker.sh
```

There is no firewall on a default Raspberry Pi OS install (INPUT policy is
`ACCEPT`), so no port needs opening - but that also means `0.0.0.0` exposes
the broker to every device on your Wi-Fi. Prefer Tailscale unless you have a
reason not to.

Verify from another machine:

```
mosquitto_sub -h 100.99.129.5 -p 1883 -u gardyn -P PASSWORD -t 'gardyn/#' -v
```

Home Assistant's MQTT integration wants broker set to that address, port
1883, and the same username and password.

To undo:

```
sudo rm /etc/mosquitto/conf.d/20-gardyn-remote.conf /etc/mosquitto/passwd
sudo systemctl restart mosquitto
```

`scripts/uninstall.sh` does this for you.

> Note: `config.py` reads `.env`, and `mqtt.py` takes its credentials from
> there. Keep `.env` as the single source of truth so the broker and the
> publisher cannot drift apart.

#### Using a broker on the Home Assistant host

Skip all of the above and set `BROKER` in `.env` to that host's address.

you just need to edit the `.env` with the mosquitto username and password created above in /etc/mosquitto/passwd.


Check the configuration works:

`sudo journalctl -xeu mosquitto.service`


If you havent already, run `./scripts/setup.sh`, this will install all OS dependencies, install the python libs, and run services pigpiod, mqtt.service

Ensure the pigpiod, mqtt, and broker daemon is running

```
sudo systemctl status pigpiod
sudo systemctl status mqtt.service
sudo systemctl status mosquitto
```

Go to your homeassistant instance:
If your broker is on the gardyn pi, make sure to install the service mqtt, go to settings->devices&services->mqtt and add your gardyn pi host, port, username and password.
The device should then appear in your homeassistant discovery settings.

To test locally on gardyn pi:

Light:

```
mosquitto_pub -t "gardyn/light/command" -m "ON" -u gardyn -P "somepassword"
mosquitto_pub -t "gardyn/light/command" -m "OFF" -u gardyn -P "somepassword"
```

Pump:

```
mosquitto_pub -t "gardyn/pump/command" -m "ON" -u gardyn -P "somepassword"
mosquitto_pub -t "gardyn/pump/command" -m "OFF" -u gardyn -P "somepassword"
```

Sensors:

Open two terminals on the gardyn pi, in one run:

`mosquitto_sub -t "gardyn/water/level" -u gardyn -P "somepassword"`

In the second gardyn pi terminal, run:

`mosquitto_pub -t "gardyn/water/level/get" -m ""-r  -u gardyn -P "somepassword"`

```

### Testing

Activate python venv `source venv/bin/activate`

Start the Flask REST API `python run.py`

Test options:

```
bash
# REST endpoints
./scripts/api-test.sh

# unit tests (the -t . -s tests form is required, see Developing below)
python -m unittest discover -t . -s tests -p 'test_*.py'

# one test module
python -m unittest tests.test_distance
```

### Developing

Short version of how to work on this without a Pi in front of you, and how to add something so it fits with the rest.

#### Set up once

```
bash
python -m venv .venv-dev
.venv-dev/scripts/pip install -r requirements-dev.txt
```

That's the pure-Python dev set. The Pi deps in `requirements.txt` (gpiozero, pigpio, the Adafruit libs) are not needed and won't install cleanly on a laptop anyway.

#### Run the checks

```
bash
.venv-dev/scripts/python -m unittest discover -t . -s tests -p 'test_*.py'
.venv-dev/scripts/ruff check .
.venv-dev/scripts/black --check .
```

CI runs exactly these three on every PR. The `-t . -s tests` part matters: `tests/__init__.py` installs fake hardware modules before anything under `app/` is imported, and plain `python -m unittest` skips that bootstrap and fails on the first `import gpiozero`.

#### How the fake hardware works

`tests/_hwstub.py` drops stand-ins for `board`, `busio`, `gpiozero`, `pigpio`, `smbus` and the `adafruit_*` modules into `sys.modules`, but only if the real ones aren't installed. On a Pi the real libraries win, so the same tests run against hardware. The stubs are deliberately dumb; a test that needs a specific reading patches the driver method it cares about.

#### Where things go

- **Pins, I2C addresses, thresholds, paths:** `config.py`, read from `.env`. Add the key there with a default, document it in `.env-dist`, and if it's a physical pin, add it to the sensor's "Pins" list under [Hardware Overview](#hardware-overview). Never hardcode a pin in a driver.
- **Drivers:** `app/sensors/<name>/<name>.py`. A class that takes `pin_factory=None` and defaults everything from `config`. Give it a `__main__` block with argparse so it can be run by hand on the Pi.
- **Routes:** `app/sensors/<name>/routes.py`. A Flask `Blueprint`, the driver built once at import inside `try/except` (so a missing sensor doesn't take the whole API down), and every route wrapped with `check_sensor_guard`. The guard gives you 400 if the driver never initialised, 503 if the hardware throws mid-request, and 400 on a `ValueError`, so raise `ValueError` for bad input and let it handle the response. For 0-100 inputs use `parse_level` from `app/lib/guards.py` instead of validating by hand.
- **Logging:** `logging.getLogger(__name__)` in modules, never `print`. Any new entry point (a script with a `__main__`, a service) calls `configure_logging()` from `app/lib/logging_config.py` once at startup; level comes from `LOG_LEVEL` in `.env`.
- **Tests:** `tests/test_<name>.py`. Import the driver or `create_app`, patch what you need, assert on the result.

#### Adding a feature, start to finish

1. Open an issue that says what's wrong or what you want. One issue per change.
2. Branch from `dev` named after it, e.g. `123-fix-pump-timeout`.
3. Write the test first if you can. It'll fail. That's the point.
4. Add the config key, then the driver change, then the route.
5. Run the three checks above until green.
6. Commit as `feat(scope): what it does` or `fix(scope): ...` with `Refs: #123` in the footer.
7. Open the PR against `dev`. Title under 50 characters, starting with `feat`, `fix`, `doc`, `test` or `ci` (the title check rejects anything else). Fill in the template for real.
8. Merge with a merge commit, not squash, so the history stays readable.

If it touches hardware behavior, say in the PR whether you ran it on a real unit and which model.


### Controlling Individual Sensors

Activate python venv `source venv/bin/activate`

Examples:

```
bash
python app/sensors/distance/distance.py
python app/sensors/humidity/humidity.py
python app/sensors/light/light.py [--on] [--off] [--brightness INT%]
python app/sensors/pcb_temp/pcb_temp.py
python app/sensors/pump/pump.py [--on] [--off] [--speed INT%] [--factory-host STR%] [--factory-port INT%]
python app/sensors/temperature/temperature.py
```

### REST API

Activate python venv `source venv/bin/activate`

Then Run `python run.py`, this will print the ip to send requests.

> **Note:** if run.py errors with: AttributeError: module 'dotenv' has no attribute 'find_dotenv'

```
pip uninstall python-dotenv
python run.py
```

#### Endpoints

```
[GET] http://<pi-ip>:5000/distance

[GET] http://<pi-ip>:5000/humidity

[POST] http://<pi-ip>:5000/light/on
[POST] http://<pi-ip>:5000/light/off
[POST] http://<pi-ip>:5000/light/brightness body:{"value": 50 }
[GET] http://<pi-ip>:5000/light/brightness

[GET] http://<pi-ip>:5000/temperature

[GET] http://<pi-ip>:5000/pcb-temp

[POST] http://<pi-ip>:5000/pump/on
[POST] http://<pi-ip>:5000/pump/off
[POST] http://<pi-ip>:5000/pump/speed body:{"value": 50 }
[GET] http://<pi-ip>:5000/pump/speed
[GET] http://<pi-ip>:5000/pump/stats
```

#### Postman

Export this [Postman collection](https://www.postman.com/orange-shadow-8689/workspace/garden-of-eden/collection/8244324-e9d8f79e-d3f2-423e-b0d1-a4ca5b1b08ca?action=share&creator=8244324&active-environment=8244324-861384b4-b4e3-48a3-8da1-181705bd2d8c), add to your private workspace, add the `pi-ip` env variable and you should be good to go.

### Scheduled Jobs (cron)

**Do not hand-write crontab entries for lights or pump.** The Schedule card in
the web UI (or `POST /schedule`) compiles your saved schedule into crontab lines
tagged with `# garden-of-eden` and rewrites only those, so it owns them from then
on. What it generates looks like this:

```
text
0 6 * * 1 /usr/local/bin/light ramp 30 15 # garden-of-eden
0 22 * * 1 /usr/local/bin/light ramp 0 15 # garden-of-eden
0 6 * * 1 /usr/local/bin/water 180 # garden-of-eden
0 12 * * 1 /usr/local/bin/water 180 # garden-of-eden
```

#### Saving a schedule also applies it now

Cron only ever acts at a window boundary. Editing the schedule at 16:00 would
otherwise leave the machine doing whatever it was doing before, so after an
unrelated fault -- a reboot, a manual override, the pin being zeroed -- the
lights could stay off for hours while the schedule said they should be on.

So `POST /schedule` also reconciles the lights with the schedule immediately:

| Schedule says | Hardware is | Result |
| --- | --- | --- |
| inside a light window | off | turned **on** at the scheduled brightness |
| inside a light window | already lit | left alone (a manual brightness is not overridden) |
| outside every window | on | turned **off** |
| outside every window | off | nothing |
| lights not scheduled | any | nothing -- no opinion is expressed |

The response says what happened, so the UI can show it rather than the save
silently changing the lights:

```json
{"lights_reconciled": true, "action": "turned on", "brightness": 30,
 "reason": "inside a light window scheduled for today"}
```

`GET /schedule` also returns `lights_now` -- what the schedule says the lights
should be doing at this instant -- so the UI can show a mismatch before you
change anything.

Two deliberate limits:

- **A schedule with lights disabled expresses no opinion**, so saving an
  unrelated change cannot switch the lights off on a machine that has never had
  a light schedule.
- **The pump is not reconciled.** Its runs are doses at fixed times, not a
  state to hold, so there is no "should be on now" to enforce.

A window that wraps past midnight (22:00-06:00) is handled, including the
early-morning half, which uses the *previous* day's brightness.

#### Checking the hardware actually matches

```
curl -H "X-API-Key: $GARDEN_ADMIN_PASSWORD" \
  http://localhost:5000/schedule/hardware
```

returns three numbers, deliberately kept apart:

| Field | Source | Trust |
| --- | --- | --- |
| `expected` | the schedule, evaluated now | what this machine *intends* |
| `commanded` | `STATE_FILE` | what it last recorded writing |
| `actual` | pigpiod, read from the pin right now | the only independent evidence |

```json
{"lights": {"expected": {"on": true, "brightness": 30},
            "commanded": {"on": true, "brightness": 30},
            "actual": {"on": true, "brightness": 30.0},
            "ok": true,
            "detail": "lights on; pin reads 30.0% (schedule says 30%)"}}
```

`ok` is true only when `expected` and `actual` agree. A difference between
`expected` and `commanded` is normal after a manual override; a difference
involving `actual` is the machine not doing what it was told.

This exists because of a specific blind spot. `gpiozero`'s `PWMLED.value` is a
**per-process cache of what that process last wrote**, not a reading of the
pin, and three processes drive the light: the REST API, `mqtt.py`, and the cron
scripts. When the hourly timelapse capture zeroed the pin, the API went on
reporting the brightness it had set hours earlier -- correct according to its
cache, wrong according to the hardware.

Note the PWM scale, which is easy to get wrong: `gpiozero` writes 0.0-1.0 and
pigpiod stores it on its own configurable range, **10000** on this hardware
rather than the default 255. Measured on the Pi:

```
set 50% via gpiozero  ->  get_PWM_dutycycle 5000 / get_PWM_range 10000
```

so the true level is always `dutycycle / range`. Dividing by 255 is not a 39x
error, it is meaningless.

`actual` is `null` when the pin cannot be read, rather than `0` -- reporting a
dead pigpiod as "the lights are off" would look exactly like the fault this
endpoint exists to detect.

#### The API owns the pins

Three processes used to drive GPIO18 and GPIO24: the API, the cron CLIs behind
`/usr/local/bin`, and anything that merely imported `app.*`. Each built its own
`gpiozero` device, and each **released the pin when it exited** — pigpiod drops
its PWM claim with it. Only the API is long-lived, so it was the one left
unable to write:

```
06:15:16  the 06:00 sunrise ramp process exits
06:15:24  ERROR 'GPIO is not in use for PWM'   every request, until a restart
```

The duty-cycle register is *not* released with the claim, so the light still
read 30% throughout. The plants had light; the control surface was dead.

Now there is one owner:

- **`light.sh` / `water.sh` ask the API** via `scripts/garden-api-call.sh`, and
  fall back to direct GPIO only when the API is down — the one case where
  there is no long-lived process to disturb.
- **`POST /light/ramp`** fades on a background thread inside the API and returns
  `202` immediately, so cron is not blocked for the length of the fade and the
  pin is never released. A new ramp supersedes one in flight.
- **`water <seconds>`** uses `POST /pump/run`, which arms the API's own auto-off
  timer, so the pump no longer has to be held by a sleeping process.
- **`app/__init__.py` no longer imports blueprints at module scope.** Importing
  a routes module builds its hardware, so *any* `import app.<anything>` used to
  open the GPIO pins. `scripts/capture-frames.sh` imports
  `app.sensors.camera.camera` only to archive a JPEG, and was zeroing the light
  pin on every hourly run.

Check who currently holds the pin:

```
curl -H "X-API-Key: $GARDEN_ADMIN_PASSWORD" http://localhost:5000/schedule/hardware
```

`pwm_claimed` is the field to look at. If it is `false`, something outside the
API is still touching the pins.

#### The symlinks cron calls

Those two commands are symlinks created by `scripts/setup.sh`:

```
bash
sudo ln -fs ~/garden-of-eden/scripts/light.sh /usr/local/bin/light
sudo ln -fs ~/garden-of-eden/scripts/water.sh /usr/local/bin/water
```

> **If those symlinks are missing, cron still fires every job and every one
> fails with `No such file or directory` — silently.** Nothing appears in the
> web UI and cron mails nobody, so the schedule keeps showing as saved and
> enabled while nothing actually runs. If your lights and pump never seem to
> fire, check this first:

```
bash
ls -la /usr/local/bin/light /usr/local/bin/water
journalctl -u cron | tail -20
```

The web UI reports a **"Schedule is not running"** banner when these commands
are missing, so this failure should now be visible rather than silent.

To confirm a job works before waiting a day for it, run it by hand. Note these
drive real hardware:

```
bash
/usr/local/bin/light 30      # set brightness to 30%
/usr/local/bin/water 180     # run the pump for 3 minutes
/usr/local/bin/light          # no argument prints usage and touches nothing
```

To remove the schedule, clear it in the web UI rather than deleting lines from
`crontab -e`, so the saved state and the crontab stay in agreement.

### Timelapse

The **Timelapse** card on the web UI plays a video assembled from archived
camera frames. Frames are captured by a systemd timer and stored under
`timelapse/`, pruned to `TIMELAPSE_MAX_FRAMES` (720 by default). Pressing
**Build** runs ffmpeg over whatever frames exist.

This needs two things, both installed by `scripts/setup.sh`:

```bash
sudo apt install -y ffmpeg      # to build the video
```

> **Frames only appear if the timer is installed and running.** Without it the
> archive stays empty and Build reports *"no frames archived yet"* -- the
> timelapse feature has no other source of frames.

#### Capture schedule

```bash
sudo scripts/install-timelapse-timer.sh                    # hourly
sudo scripts/install-timelapse-timer.sh --schedule daily   # once a day, 08:00
sudo scripts/install-timelapse-timer.sh --schedule every-30-min
sudo scripts/install-timelapse-timer.sh --list-presets
```

The timer uses systemd's `OnCalendar`, so the presets are conveniences and the
full calendar syntax is available:

| Preset | OnCalendar | Meaning |
| --- | --- | --- |
| `hourly` | `hourly` | every hour |
| `every-30-min` | `*:0/30` | twice an hour |
| `every-15-min` | `*:0/15` | four times an hour |
| `every-5-min` | `*:0/5` | twelve times an hour |
| `daily` | `*-*-* 08:00:00` | once a day at 8am |
| `daily-early` | `*-*-* 06:00:00` | once a day at 6am |
| `twice-daily` | `*-*-* 06,18:00:00` | morning and evening |
| `every-6-hours` | `*-*-* 00/6:00:00` | four times a day |

Anything that is not a preset name is passed straight to `OnCalendar`, so
`--schedule 'Mon..Fri *-*-* 07:30:00'` works. Check an expression without
installing anything:

```
scripts/install-timelapse-timer.sh --schedule daily --resolve
```

`daily` is deliberately 8am rather than midnight -- the lights are off then.

The unit files are generated rather than committed because they embed
`User=` and `WorkingDirectory=`, which are machine-specific. The timer sets
`Persistent=true`, so a daily capture whose Pi was off at 8am runs at next
boot instead of being skipped.

To capture one frame immediately, without waiting for the timer:

```bash
scripts/capture-frames.sh
```

#### Knobs

All of these go in `.env`; the timer ones are read when you re-run the
installer.

| Setting | Default | Meaning |
| --- | --- | --- |
| `TIMELAPSE_SCHEDULE` | `hourly` | preset name or `OnCalendar` expression |
| `TIMELAPSE_MAX_FRAMES` | `720` | frames retained before the oldest are pruned |
| `TIMELAPSE_FPS` | `12` | playback frame rate of the built video |
| `TIMELAPSE_DIR` | `<repo>/timelapse` | where frames and mp4s live |
| `TIMELAPSE_AUTO_BUILD` | `false` | rebuild the mp4 after each capture |

`TIMELAPSE_MAX_FRAMES` interacts with the schedule: at 720 frames, hourly
retains 30 days, but every 15 minutes only three. Raise it if you want longer.

`TIMELAPSE_AUTO_BUILD` is off by default because rebuilding re-encodes every
retained frame, which on a Pi is real CPU for a video most people watch once a
day.

#### Downloading the photos

The web UI has **Download upper photos** / **Download lower photos** buttons,
with an optional date range. They fetch:

```
GET /camera/timelapse/<cam>/frames.zip
GET /camera/timelapse/<cam>/frames.zip?from=2026-10-01&to=2026-10-04
```

which is the whole point over a tailnet: the browser on your phone or laptop
pulls the archive over Tailscale, with the same admin password as everything
else. From a shell:

```bash
curl -H "X-API-Key: $GARDEN_ADMIN_PASSWORD" \
  "http://gardengoblin:5000/camera/timelapse/upper/frames.zip?from=2026-10-01" \
  -o frames.zip
```

`from` and `to` are `YYYY-MM-DD` and both ends are inclusive. Omitting them
sends everything, which is bounded by `TIMELAPSE_MAX_FRAMES`. A range with no
frames returns 404 rather than an empty zip, since a zero-byte download looks
like a silent failure.

#### What the machine is actually doing

`GET /camera/timelapse-config` reports the configured schedule, the next fire
times, the frame count and whether the installed unit matches `.env`:

```bash
curl -H "X-API-Key: $GARDEN_ADMIN_PASSWORD" \
  http://localhost:5000/camera/timelapse-config
```

It compares `.env` against the generated unit and sets `pending_change` when
they disagree, so editing `.env` and forgetting to re-run the installer shows
up as "installer re-run needed to apply" in the web UI rather than silently
displaying a schedule that is not in effect.

## Hardware Overview

Depending on the system you have, here is a breakdown of the hardware.

Notes:

- GPIO num is different than pin number. See (<https://pinout.xyz/>)

### Air Temp & Humidity Sensor

- temp/humidity sensor AM2320 at address of `0x38`

### Pump Power Monitor

- motor power usage sensor INA219 at address of `0x40`

### PCB Temp Sensor

- pcb temp sensor PCT2075 at address `pf 0x48`

When you run `sudo i2cdetect -y 1`, you should see something like:

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- 38 -- -- -- -- -- -- --
40: 40 -- -- -- -- -- -- -- 48 -- -- -- -- -- -- --
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
70: -- -- -- -- -- -- -- --
```

### Lights

LED full spectrum lights.

#### Method

- Lights are driven by PWM duty and a frequency of 8 kHz.

#### Pins

- [GPIO-18 | PIN-12](https://pinout.xyz/pinout/pin12_gpio18/)

### Pump

#### Method

- The pump is driven by PWM with max duty of 30% and frequency of 50 Hz
- There is a current sensor to measure pump draw and a overtemp sensor to determine if board monitor PCB temp.

#### Pins

- [GPIO-24 | PIN-18](https://pinout.xyz/pinout/pin18_gpio24/)

Notes:

- Pump duty cycle is limited, likely full on is too much current draw for the system.

### Camera

One or two USB cameras, depending on the model. The **Home line** (1.0, 2.0,
3.0, 4.0) is the max-yield architecture: 3 towers, 2 full-spectrum LED light
bars, 2 cameras, 30 plant pods. The **Studio line** (Studio, Studio 2) is the
trimmed profile for compact spaces: 2 towers, 1 light bar, 1 camera, 16 pods.

The count comes from the detected model profile, so no configuration is needed.
Set `LOWER_CAMERA_ENABLED=true|false` in `.env` only for a unit that differs
from its profile. `POD_COUNT` and `POD_COLUMNS` follow the same rule: leave them
at `0` (unset) to take the model default, and set them only to override.

#### Method

- image capture with fswebcam
- the upper module sits sideways in the enclosure, so `UPPER_CAMERA_ROTATE` (in
  `.env`) rotates it at capture time with `fswebcam --rotate`. Right angles only
  (`0`, `90`, `180`, `270`). `90` is the default and rotates the image clockwise;
  `270` rotates counter-clockwise and `0` disables it. Because the rotation is
  baked into the JPEG, the web UI, MQTT image entity, and timelapse frames all
  stay consistently oriented.

#### Devices

- /dev/video0
- /dev/video1

### Water Level Sensor

Uses the ultrasonic distance sensor DYP-A01-V2.0.

#### Pins

- [GPIO-19 | PIN-35](https://pinout.xyz/pinout/pin35_gpio19/): water level in (trigger)
- [GPIO-26 | PIN-37](https://pinout.xyz/pinout/pin37_gpio26/): water level out (echo)

#### Method

- Uses time between the echo and response to deterine the distances.

#### References

- <https://www.google.com/search?q=DYP-A01-V2.0>
- <https://www.dypcn.com/uploads/A02-Datasheet.pdf>

### Momentary Button

`<section incomplete>`

### Electrical Diagrams

Incase you need to troubleshoot any problems with your system.

#### Sensors

<img src="docs/pcb1.png" width="800px">

#### Power and Header

<img src="docs/pcb2.png" width="800px">

### Recommendations

#### Upgrading the Pi Zero 2

For better performance, the Pi Zero can be replaced with a Pi Zero 2. This will enable the use of VS Code Remote Server to edit files and debug the python code remotely. The VS Code remote server uses OpenSSH and the minimum architecture is ARMv7.

> Buy one **without** a header, you will need to solder one on in the opposite direction.

## Design Decisions

### Python Version 3.9 >=

Minimum Python version is 3.9: it is what `pyproject.toml` and CI target and what the supported Raspberry Pi OS releases ship. (3.6 was the original floor for f-strings.)

### Delays in Reading Temp/Humidity data

Reading sensor values  with inherently long delays and responding to the REST API. To minimize the delay in subsequent readings the value is cached and given if another read occurs within two seconds.

### GPIO

Using `gpiozero` to leverage `pigpio` daemon which is hardware driven and more efficient.This ensures better accuracy of the distance sensor and is less cpu intensive when using PWMs.

## Folder Structure

```
text
<gardyn-of-eden>
├── run.py
├── app
│   ├── __init__.py
│   └── sensors
│       ├── config.py
│       ├── distance
│       │   ├── distance.py
│       │   ├── __init__.py
│       │   └── routes.py
│       ├── __init__.py
│       ├── light
│       │   ├── __init__.py
│       │   ├── light.py
│       │   └── routes.py
│       └── pump
│           ├── __init__.py
│           ├── pump.py
│           └── routes.py
└── tests
    ├── __init__.py
    ├── test_distance.py
    ├── test_light.py
    └── test_pump.py
```
