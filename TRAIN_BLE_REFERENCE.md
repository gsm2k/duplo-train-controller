# DUPLO train base BLE reference

Recorded from hardware tests on **2026-10-18**, using Linux BlueZ and
`bluetoothctl`. The user confirmed motor movement in both directions, light
color changes, and the horn sound.

This base uses the newer DUPLO train interface. Its exact retail set number
was not established. Do not substitute the older train's port assignments or
color numbers.

An older Steam Train base was also inspected on **2026-10-19**. See the
[comparison and older-base readings](#older-steam-train-comparison-2026-10-19)
below. The main reference above that section describes the newer base.

## Connection

| Item | Value |
| --- | --- |
| Advertised name | `DUPLO Train` |
| Resolved public address after bonding | `84:72:93:83:56:FA` |
| Manufacturer ID | `0x0397` (LEGO) |
| Observed manufacturer payload | `00 21 02 FF 01 00` |
| LEGO service UUID | `00001623-1212-efde-1623-785feabcd123` |
| Command/notification characteristic UUID | `00001624-1212-efde-1623-785feabcd123` |

The base initially advertised with random addresses (`5D:CC:9B:48:B8:BD`, then
`49:7D:10:78:DC:67`). Discover by name/service instead of hard-coding those
addresses. BlueZ resolved the public address during pairing. At the end of
testing, BlueZ reported `Paired: yes`, `Bonded: yes`, and `Trusted: yes`.
Pairing occurred automatically during GATT interaction; an unpaired BLE
connection alone was not sufficient evidence that controls worked.

```text
bluetoothctl
scan le
# Wait for DUPLO Train, then use its current address:
connect <address>
# Wait for connection success and ServicesResolved: yes.
scan off
menu gatt
select-attribute 00001624-1212-efde-1623-785feabcd123
notify on
# Wait for notifications to start before sending commands.
```

Send one write at a time and wait for its result. Back-to-back writes without
waiting produced `org.bluez.Error.InProgress` during testing.

**Prefix every byte with `0x` in bluetoothctl.** Bare `21` is parsed as decimal
21 (`0x15`), not hexadecimal `0x21`. For example:

```text
write "0x05 0x00 0x01 0x06 0x05"
```

All packet examples below are hexadecimal bytes. They need the `0x` prefixes
shown above when entered into `bluetoothctl`.

## Packet framing

For the short packets used here:

```text
[total byte length] [hub ID = 00] [message type] [payload...]
```

Multi-byte integers are little-endian. Negative motor power uses signed
8-bit two's complement. Notifications on the same characteristic carry
replies, port announcements, sensor values, and command feedback.

## Reported ports and modes

These values were queried from this base, rather than inferred from an older
train's documentation.

| Port (hex / decimal) | Reported I/O type | Reported modes | Verified interpretation |
| --- | --- | --- | --- |
| `32` / 50 | `0029` | 0: `POWER`; 1: `SPEED` | Drive motor; mode 0 tested |
| `33` / 51 | `005B` | 0: `TAG` | Tile-related notifications observed; color/symbol mapping below |
| `34` / 52 | `005A` | 0: `VERS`; 1: `EVENTS`; 2: `DEBUG` | Mode 1 controls lights and horn |
| `35` / 53 | `0014` | 0: `VLT L`; 1: `VLT S` | Voltage sensor; voltage conversion not tested |
| `36` / 54 | `002C` | 0: `SPEED`; 1: `COUNT`; 2: `VELO` | Mode 0 readable; other modes not tested |

The port-information replies were:

```text
0B 00 43 32 01 00 02 00 00 00 00
0B 00 43 33 01 01 01 00 00 01 00
0B 00 43 34 01 01 03 00 00 07 00
0B 00 43 35 01 02 02 03 00 00 00
0B 00 43 36 01 02 03 07 00 00 00
```

Format: `0B 00 43 <port> 01 <capabilities> <mode count>
<input mask: uint16> <output mask: uint16>`.
Capability bits are output `01`, input `02`, combinable `04`, synchronizable
`08`. This motor reports capability and mode masks of zero despite accepting
motor commands, so discovery metadata alone must not disable motor support.

## Motor commands — physically verified

```text
08 00 81 32 11 51 00 PP
```

| Field | Meaning |
| --- | --- |
| `81` | Port output command |
| `32` | Motor port |
| `11` | Execute immediately and request command feedback |
| `51` | WriteDirectModeData |
| `00` | POWER mode |
| `PP` | Signed motor power; zero stops |

| Action | Packet |
| --- | --- |
| +40% power | `08 00 81 32 11 51 00 28` |
| −40% power | `08 00 81 32 11 51 00 D8` |
| Stop | `08 00 81 32 11 51 00 00` |

Both directions were run briefly with explicit stops after each. The broader
power range is documented by the matching community implementation as
−100…100; only ±40 and zero were tested here. The physical forward direction
depends on how the base is oriented.

Example stop in `bluetoothctl`:

```text
write "0x08 0x00 0x81 0x32 0x11 0x51 0x00 0x00"
```

Motor completion feedback means the power command was applied, not that the
motor has stopped. Schedule a stop explicitly and attempt it during error
cleanup before disconnecting. Motor mode 1 (`SPEED`) remains untested.

## Light commands — color changes physically verified

Lights use the EVENTS port, not the older train's RGB LED port:

```text
0B 00 81 34 11 51 01 04 01 CC 00
```

`34` is the port, `01` after `51` selects EVENTS mode, `04` is the light
subcommand, and `CC` selects a color. Preserve the remaining bytes as shown.

| Color | `CC` | Status |
| --- | --- | --- |
| Off | `00` | Sent successfully at cleanup |
| White | `01` | Sent in the confirmed color-change test |
| Green | `07` | Sent in the color test |
| Yellow | `08` | Community mapping; not tested here |
| Light blue | `09` | Community mapping; not tested here |
| Dark blue | `0F` | Sent in the color test |
| Purple | `0A` | Community mapping; not tested here |
| Purple-pink | `0E` | Community mapping; not tested here |
| Light pink | `0B` | Community mapping; not tested here |
| Red-pink | `0D` | Community mapping; not tested here |
| Red | `0C` | Sent in the confirmed color-change test |

The user confirmed color changes overall, not the identity of each individual
color. Values `02` and `05` were also sent during initial exploration; their
colors were not established. They should not be labeled green/blue.

Example red:

```text
write "0x0b 0x00 0x81 0x34 0x11 0x51 0x01 0x04 0x01 0x0c 0x00"
```

## Horn — physically verified

```text
0B 00 81 34 11 51 01 07 01 00 00
```

This uses EVENTS mode with subcommand `07`:

```text
write "0x0b 0x00 0x81 0x34 0x11 0x51 0x01 0x07 0x01 0x00 0x00"
```

Additional sounds remain unverified. A port-information request to the older
speaker port `01` returned `05 00 05 21 06` (invalid use/parameters). Do not
assume older train sound commands or sound IDs work on this base. Other
EVENTS payloads and the full `TAG` value mapping need further investigation.

## Tile sensor investigation

Subscriptions to both of these were accepted:

```text
TAG mode 0:    0A 00 41 33 00 01 00 00 00 01
EVENTS mode 1: 0A 00 41 34 01 01 00 00 00 01
```

Each returned a matching `47` input-configuration confirmation. Brief +40%
motor runs with tiles presented to the underside sensor produced:

| Tile identified by user | Notification | Little-endian TAG value | Decimal |
| --- | --- | --- | --- |
| Red, stop symbol | `06 00 45 33 92 01` | `0x0192` | 402 |
| Purple, star symbol | `06 00 45 33 44 01` | `0x0144` | 324 |
| Blue, water-drop symbol | `06 00 45 33 41 01` | `0x0141` | 321 |
| Bright green, lightning symbol | `06 00 45 33 25 00` | `0x0025` | 37 |
| Light orange, spanner symbol | `06 00 45 33 BF 00` | `0x00BF` | 191 |

The user also identified these available tiles for a future session. They
were **not tested**, and their TAG values and behavior remain unknown:

| Tile | Symbol | TAG value |
| --- | --- | --- |
| Dark orange | Clock | Unknown; not tested |
| Light green | Leaf | Unknown; not tested |
| White | Lightbulb | Unknown; not tested |

Symbol names describe the tiles' markings, not a verified interpretation of
their actions. Testing ended after the five readings above; the train was
disconnected. Resume with the three untested tiles and repeats of the
existing readings when convenient.

The first reading was initially labeled purple, then corrected to red by the
user before the actual purple-tile run. The table reflects that correction.

These are single-mode values from TAG port `33`. Record them as observations
with these particular tiles, not yet universal color codes: each is one
observation, and the value could identify a particular action-tile pattern.
Repeat with the same tiles before using the mapping as stable.

The purple run also produced an additional EVENTS notification:

```text
08 00 45 34 01 A0 A9 00
```

Its meaning is not established; it may relate to the tile-triggered action.

The bright green lightning-tile run produced this EVENTS sequence after the
initial `01 A0 8C 00` payload:

```text
08 00 45 34 01 A0 A9 00
08 00 45 34 01 A0 98 00
08 00 45 34 01 A0 87 00
08 00 45 34 01 A0 75 00
08 00 45 34 01 A0 64 00
```

The `A9` event also appeared with purple, so it is not a unique color ID.
This sequence's meaning and the lightning tile's physical effect have not
been confirmed. The explicit motor stop command returned completed/idle.

The blue run also produced EVENTS packets ending in `01 A0 32 00` and
`01 A0 96 00`, interspersed with `01 A0 64 00`. Some arrived after the
explicit motor stop command. Their meaning is unconfirmed; tile-triggered
behavior may continue beyond the immediate motor command. A further stop
was sent after the sequence.

The light orange spanner-tile reading was captured after resetting the train,
reconnecting, and restoring both TAG/EVENTS subscriptions. It also produced
EVENTS payloads `01 A0 4B 00`, `01 A0 64 00`, and `01 A0 7D 00`, including
packets after the first stop command. Their meaning remains unconfirmed.
A further stop was sent after the sequence and acknowledged.

No tile notification was observed in the preceding stationary attempt. This
does not prove that motor operation is always required; placement, timing,
or a detection transition may matter. A controlled repeat is still needed.

The following EVENTS packets appeared during forward runs before and during
both tile tests, so they are not specific evidence of either color:

```text
08 00 45 34 01 A0 8C 00
08 00 45 34 01 A0 64 00
```

Their payload meaning is unconfirmed. Do not label them color readings.

## Battery and speed readings

### Battery percentage — verified

```text
Request:  05 00 01 06 05
Observed: 06 00 01 06 06 64
```

This is hub property `06` (battery voltage percentage), operation `05`
(request update). Reply operation `06` carries the value; `64` = 100%.
Battery percentage is separate from the voltage sensor on port `35`.

### Speed sensor — subscription and stationary read verified

Subscribe to port `36`, mode 0, with change threshold 1:

```text
Request:  0A 00 41 36 00 01 00 00 00 01
Observed: 0A 00 47 36 00 01 00 00 00 01
```

The four bytes after the mode are a uint32 change threshold, not a sampling
period. The final byte enables (`01`) or disables (`00`) notifications.

```text
Read current value: 05 00 21 36 00
Observed reply:    05 00 45 36 00
Unsubscribe:       0A 00 41 36 00 01 00 00 00 00
```

The observed mode-0 value-format reply was
`0A 00 44 36 00 80 01 00 05 00`: one dataset, 8-bit integer format. The
stationary value was zero. Units, scaling, signed motion behavior, and
nonzero readings were not validated. Do not assume a 16-bit speed value.

## Capability discovery requests

| Query | Packet | Reply type |
| --- | --- | --- |
| Port modes/capabilities | `05 00 21 PP 01` | `43` |
| Current port value | `05 00 21 PP 00` | `45` |
| Mode name | `06 00 22 PP MM 00` | `44` |
| Raw value range | `06 00 22 PP MM 01` | `44` |
| Percent range | `06 00 22 PP MM 02` | `44` |
| SI range | `06 00 22 PP MM 03` | `44` |
| Unit symbol | `06 00 22 PP MM 04` | `44` |
| Value format | `06 00 22 PP MM 80` | `44` |

`PP` is the port; `MM` is the mode. Name, raw-range, and value-format requests
were exercised. The other query types come from the LEGO protocol docs.

Example: request the motor's POWER mode name:

```text
Request: 06 00 22 32 00 00
Reply:   0B 00 44 32 00 00 50 4F 57 45 52
                                       P  O  W  E  R
```

## Incoming events and replies

| Type | Meaning | Relevant format after the common header |
| --- | --- | --- |
| `01` | Hub property update | `<property> <operation> <value...>` |
| `04` | Attached/detached I/O announcement | `<port> <event> <type uint16> <HW revision 4 bytes> <SW revision 4 bytes>` for attachment |
| `05` | Generic protocol error | `<request message type> <error code>` |
| `43` | Port information | See reported-port section |
| `44` | Mode information | `<port> <mode> <information type> <data...>` |
| `45` | Single-mode port value | `<port> <value bytes...>` interpreted using its mode's value format |
| `47` | Input configuration confirmation | `<port> <mode> <threshold uint32> <notifications enabled>` |
| `82` | Output command feedback | One or more `<port> <status bitmask>` pairs |

Port announcements were received after enabling GATT notifications. Example:

```text
0F 00 04 32 01 29 00 01 00 00 00 01 00 00 00
```

This announces attached event `01`, port `32`, I/O type `0029`, and hardware
and software revision bytes `01 00 00 00`. Detach event `00` omits the type
and revision fields. These are protocol I/O events; the payload semantics of
the port named EVENTS are a separate, partly undocumented interface.

### Output feedback

```text
05 00 82 32 0A    # Motor command completed, port idle
05 00 82 34 0A    # EVENTS command completed, port idle
```

Both replies were observed. Status bits:

| Bit | Meaning |
| --- | --- |
| `01` | Command in progress, buffer empty |
| `02` | Command completed, buffer empty |
| `04` | Command discarded |
| `08` | Idle |
| `10` | Busy/full |

`0A = 02 | 08`. Feedback confirms protocol execution; it is not a physical
sensor reading or proof of a particular color/sound. Physical operation was
confirmed separately by the user.

### Errors

Observed rejection of a query for the older speaker port:

```text
05 00 05 21 06
```

This identifies request type `21` and error `06` (invalid use/parameters).
Other documented error codes include `03` buffer overflow, `04` timeout,
`05` unrecognized command, `07` overcurrent, and `08` internal error.
BlueZ errors such as `org.bluez.Error.InProgress` are host errors, not these
LEGO notification packets.

## Session cleanup

1. Send the motor stop packet and wait for feedback.
2. Turn the light off if desired.
3. Disable sensor notifications if enabled.
4. Return to the main bluetoothctl menu and disconnect using the resolved
   address, then quit:

```text
back
disconnect 84:72:93:83:56:FA
quit
```

If the original random address becomes unavailable after pairing, use
`devices Connected` to find the resolved address. The actuator tests were
performed with the train upside down so its drive wheels could turn freely.

## Sources and remaining work

- [Local LEGO Wireless Protocol documentation](../lego-ble-wireless-protocol-docs/docs/index.html): common framing, message types, discovery, sensor configuration, and feedback. This is an older general protocol reference; its device list does not fully describe this base.
- [Matching newer-train command implementation](https://github.com/micschr0/duplo-train-10427-ble2mqtt/blob/main/src/protocol/commands.rs): motor and EVENTS packet layouts.
- [Newer-train color mapping](https://github.com/micschr0/duplo-train-10427-ble2mqtt/blob/main/src/types.rs): named color IDs, including entries not individually tested here.

Next investigations: exact model/firmware identification; nonzero speed
readings and units; voltage scaling; motor SPEED mode; repeat observed tile
detections and map other TAG values; additional sounds; other action-brick
events. No firmware update,
calibration, or custom sound upload was attempted.

## Older Steam Train comparison (2026-10-19)

Connected successfully with `bluetoothctl`. These results are hardware
discovery and sensor queries only; no motor, light, or sound output command
was sent to the older base. The user identified it as an older Steam Train;
its retail set number was not independently verified.

| Feature | Older base | Newer base |
| --- | --- | --- |
| Advertised name | `Train Base` | `DUPLO Train` |
| Public address | `E0:7D:EA:07:7A:7F` | `84:72:93:83:56:FA` after bonding |
| Manufacturer payload | `00 20 02 FE 41 00` | `00 21 02 FF 01 00` |
| Hub system type | `20`, queried | `21`, from advertisement |
| Pairing state during successful queries | Paired/bonded/trusted: all no | Paired/bonded/trusted: all yes after earlier tests |
| Motor | Port `00`, I/O type `0029` | Port `32`, I/O type `0029` |
| Sound | Port `01`, I/O type `002A` | Horn verified through EVENTS port `34` |
| Lights | Port `11`, I/O type `0017` | Light colors verified through EVENTS port `34` |
| Tile/color sensing | Port `12`, I/O type `002B` | TAG port `33`, I/O type `005B` |
| Speed sensor | Port `13`, I/O type `002C`, int16 in mode 0 | Port `36`, I/O type `002C`, int8 in mode 0 |
| Voltage sensor | Port `14`, I/O type `0014` | Port `35`, I/O type `0014` |

Both expose the same LEGO service `00001623-1212-efde-1623-785feabcd123`
and command/notification characteristic `00001624-1212-efde-1623-785feabcd123`.
On the older base, the characteristic's observed BlueZ object path was:

```text
/org/bluez/hci0/dev_E0_7D_EA_07_7A_7F/service0009/char000a
```

When multiple LEGO hubs are cached, `bluetoothctl select-attribute <UUID>`
may select another hub's attribute. Inspect `list-attributes` for the intended
device and select its full object path. Object handles/paths are observed
values, not portable protocol constants. Bleak resolves the UUID through
the selected client's services instead.

### Older-base modes queried

| Port | Mode names reported by the older base |
| --- | --- |
| `00` motor | 0: `T MOT`; 1: `ONSEC` |
| `01` sound | 0: `TONE`; 1: `SOUND`; 2: `UI SND` |
| `11` lights | 0: `COL O`; 1: `RGB O` |
| `12` color sensor | 0: `COLOR`; 1: `C TAG`; 2: `REFLT`; 3: `RGB I`; 4: `CALIB` |
| `13` speed sensor | 0: `SPEED`; 1: `COUNT`; 2: `CALIB` |
| `14` voltage sensor | Two modes reported; names not queried |

Mode names describe available interfaces, not verified actuator behavior.
No calibration mode was enabled.

### Red stop tile: stationary detection verified

The user placed the red stop tile directly under the older base's underside
sensor. COLOR-mode subscription and current-value polling:

```text
Subscribe:  0A 00 41 12 00 01 00 00 00 01
Confirmed:  0A 00 47 12 00 01 00 00 00 01
Read:       05 00 21 12 00
Red reply:  05 00 45 12 09
Unsubscribe: 0A 00 41 12 00 01 00 00 00 00
```

Red was observed repeatedly as `09` (9), including in the final polled read.
Brief `FF` and `07` readings occurred during placement; their physical colors
were not independently checked in this session. The initial reading was
`00` before the tile was directly under the sensor. COLOR mode reports one
8-bit dataset (`0A 00 44 12 00 80 01 00 03 00`). Motor movement was not
needed for these readings.

This differs from the newer base's red stop tile notification
`06 00 45 33 92 01`: a two-byte TAG value `0x0192`. Do not reuse the newer
TAG mapping for older COLOR mode. Older C TAG, reflection, and RGB modes
were discovered but not sampled.

### Other older-base readings

- Battery reply: `06 00 01 06 06 3E` = **62%** at inspection time.
- Firmware-version reply: `09 00 01 03 06 00 00 00 12`.
- Hardware-version reply: `09 00 01 04 06 00 00 00 10`.
- LWP-version reply: `07 00 01 0A 06 00 03`.
- System-type reply: `06 00 01 0B 06 20`.
- Speed mode-0 value format: `0A 00 44 13 00 80 01 01 05 00` = one int16 dataset.
- Speed raw range: −300…300; SI range: −10…10; symbol: `spd`.
  These were queried, not validated against movement or physical units.
  The reported scaling would map raw 300 to SI 10, but `spd` does not
  establish meters/second or another physical unit.

The Python controller now has separate older/newer profiles, selected from
the LEGO manufacturer advertisement (`20` or `21`). The older profile uses
motor port `00`, speed port `13`, and an int16 speed decoder; the newer uses
ports `32`/`36` and int8 speed values. `--address` selects a specific base,
and `--profile old`/`new` optionally filters discovery. The older profile
does not request pairing. See the README for launch examples. The older
base was unsubscribed and disconnected after the discovery inspection.
