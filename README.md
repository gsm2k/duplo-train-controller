# DUPLO train keyboard controller

A small Python/Tkinter controller for the older Steam Train and newer DUPLO
train bases inspected in this project. Bluetooth uses [Bleak](https://bleak.readthedocs.io/en/latest/).
Packet details and hardware findings are in [TRAIN_BLE_REFERENCE.md](docs/TRAIN_BLE_REFERENCE.md).

## Repository layout

- `src/`: Python source; currently the unchanged `train_controller.py` module.
- `tests/`: automated tests, kept separate from the source.
- `docs/`: protocol reference and hardware investigation notes.
- `.venv/`: local virtual environment, excluded from Git.
- Root: README, license, requirements, and editor workspace configuration.

Run the commands below from the repository root. This source layout leaves room
for an installable library later; packaging metadata and package restructuring
are deferred.

## Setup and run

Requires Python 3.10+ with Tkinter and a desktop session. On Fedora, if
Tkinter is missing, install `python3-tkinter` with your package manager.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python src/train_controller.py
```

Switch on the train and close any LEGO app connected to it. Run this from
your laptop's normal desktop terminal so it has access to Bluetooth and
the display. The program scans for supported trains for eight seconds and
identifies the base generation from LEGO's manufacturer advertisement. It
pairs with the newer base if needed; the older base connects without pairing.
It sends an initial stop before enabling controls and displays the selected
base generation in the window.
If connection fails, close the window, switch the train on, and restart.
If multiple trains are found, select one explicitly:

```bash
# Older Steam Train:
python src/train_controller.py --address E0:7D:EA:07:7A:7F
# Newer base:
python src/train_controller.py --address 84:72:93:83:56:FA
```

Alternatively, `--profile old` or `--profile new` restricts discovery to that
generation. Supported profiles use motor/speed ports `00`/`13` for the older
base and `32`/`36` for the newer base. Other LEGO hub kinds are rejected.

## Keys

Click the controller window to focus it, then:

| Hold | Action |
| --- | --- |
| Up | Forward at 50% motor power |
| Down | Reverse at 50% motor power |
| Shift + Up | Forward at 100% motor power |
| Shift + Down | Reverse at 100% motor power |
| Left (press) | Sound the horn once |
| Right (press) | Toggle white headlights on/off |
| Both arrows | Stop |
| Neither arrow | Stop |

Pressing or releasing Shift while holding an arrow changes power immediately.
Left and Right trigger once per press, ignoring key auto-repeat, and work
while driving or stopped. The first Right press sets the lights to white;
later presses alternate off/on. The light indicator shows the last requested
state, not a sensor reading, and starts unknown on each connection. Closing
the app stops the motor but leaves the lights at the last requested setting.
Space or Escape stops and prevents auto-repeat from restarting the motor
until the arrow keys have been released. Switching away from the window
clears the held keys and sends a stop. Closing the window attempts a stop
before disconnecting. The window stays open while cleanup finishes.

Percentages set motor **power**, using the verified POWER-mode command;
they are not measured speed targets. Writes are serialized and the latest
power is refreshed every 250 ms. A GUI heartbeat that expires after 400 ms
requests zero power from the Bluetooth worker. A broken Bluetooth link or
terminated process can prevent a stop from reaching the train; the program
cannot guarantee a physical stop in those cases. Action tiles may also
start built-in movement sequences, as noted in the BLE reference.

## Battery and measured speed

The window shows battery percentage with a level bar, refreshed on connection
and every 30 seconds. Speed comes from the train's speed sensor on port
`0x13` (older) or `0x36` (newer), separately from the commanded motor power.
The older sensor uses 16-bit values; the newer uses 8-bit values. The program subscribes to
speed changes and also requests the current value about twice a second.

On connection, it requests the speed sensor's data format, raw/SI ranges, and
unit symbol. If the train supplies a usable scale and unit, speed is displayed
in that unit. Otherwise it is explicitly labeled `(raw)`, since physical speed
units have not yet been verified on these bases. The older base reports the
symbol `spd`; this is shown as reported, without assuming meters/second.
A plus/minus sign reflects the
sensor value; its relationship to travel direction still needs hardware
confirmation. Missing readings show `—`, including in dry-run mode and after
disconnection. Telemetry and motor writes share the same serialized worker.

## Test keys without the train

```bash
python src/train_controller.py --dry-run
# Exercise the older base's packet encoding:
python src/train_controller.py --dry-run --profile old
```

This needs Tkinter but no Bleak or Bluetooth connection. The window shows
the requested power and the terminal prints each change and its motor packet.
Check arrow releases, Shift changes, both arrows, Space/Escape, and switching
to another window before trying the real train. Left/Right also print the
profile-specific horn/light packets in dry-run mode; holding either key
should produce only one action until it is released and pressed again.

Run the automated input and Bluetooth-cleanup tests with:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

In PowerShell, run the tests with:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The source path is needed because the project is not yet installed as a package.
