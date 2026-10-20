# Development notes

See the [README](../README.md) for the project overview and test-program controls,
and the [BLE reference](TRAIN_BLE_REFERENCE.md) for packet layouts and hardware
observations.

## Repository layout

- `src/train_controller.py`: controller module and GUI test program.
- `tests/`: automated tests.
- `docs/`: protocol reference and development notes.
- `.venv/`: local virtual environment, excluded from Git.

The project currently runs directly from source. Packaging metadata and an
installable library interface are planned.

## Environment setup

Requires Python 3.10+ and a desktop session. Create a virtual environment from
the repository root:

```bash
python -m venv .venv
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

Or in PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the Bluetooth dependency:

```bash
python -m pip install -r requirements.txt
```

Tkinter is supplied by Python or the OS. On Fedora, install `python3-tkinter`
if it is missing. Run the GUI from a desktop terminal with access to Bluetooth
and the display.

## Automated tests

The source directory must be on Python's import path because the project is
not yet installed as a package.

Linux/macOS:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

These tests cover discovery, input handling, telemetry decoding, packet encoding,
and Bluetooth-session cleanup using simulated clients. They do not establish
physical compatibility with every retail set or firmware version.

## Discovery and connection

The program scans for eight seconds and identifies the base generation from
LEGO manufacturer advertisement data. It requests pairing for the newer base;
the older base connects without requesting pairing. Other hub kinds are rejected.

If connection fails, close the window, switch the train on, and restart.
Use `--address` to select one train when several are available, or
`--profile old` / `--profile new` to restrict discovery.

## Control and cleanup behavior

An initial motor stop is sent before controls are enabled. All Bluetooth writes
are serialized. Requested motor power is refreshed every 250 ms; a GUI heartbeat
that expires after 400 ms requests zero power from the Bluetooth worker.

Horn and light actions trigger once per key press, ignoring key auto-repeat.
The first light toggle requests white; subsequent toggles alternate off/on.
The light indicator reflects the requested state, not a sensor reading.
Closing the program attempts a motor stop and leaves the lights at their last
requested setting.

A broken Bluetooth link or terminated process can prevent a stop from reaching
the train. Action tiles can also start built-in movement sequences; see the
BLE reference for observations.

## Telemetry

Battery percentage is requested on connection and every 30 seconds. Speed
notifications are subscribed to, and the current value is also polled about
twice a second.

The controller queries the speed sensor's data format, raw/SI ranges, and unit
symbol. A usable scale and symbol produce a reading in the reported unit;
otherwise the display labels it `(raw)`. The older base reports `spd`, which
does not establish a physical unit. The sign's relationship to physical travel
direction still needs confirmation.

Dry-run mode needs Tkinter but no Bleak or Bluetooth connection. It prints
motor, horn, and light packets to the terminal. Battery and speed remain
unavailable in that mode.
