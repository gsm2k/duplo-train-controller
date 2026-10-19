#!/usr/bin/env python3
"""Hold-to-drive keyboard controller for older and newer DUPLO train bases."""

import argparse
import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
import math
from queue import Empty, SimpleQueue
import struct
import threading
import time


CHARACTERISTIC = "00001624-1212-efde-1623-785feabcd123"
WATCHDOG_SECONDS = 0.4
REFRESH_SECONDS = 0.25
BATTERY_REQUEST = bytes.fromhex("05 00 01 06 05")


@dataclass(frozen=True)
class TrainProfile:
    name: str
    label: str
    hub_kind: int
    motor_port: int
    speed_port: int
    speed_format: str
    pair: bool

    @property
    def speed_request(self):
        return bytes([5, 0, 0x21, self.speed_port, 0])

    @property
    def speed_subscribe(self):
        return bytes([10, 0, 0x41, self.speed_port, 0, 1, 0, 0, 0, 1])


NEWER = TrainProfile("new", "Newer DUPLO Train", 0x21, 0x32, 0x36, "<b", True)
OLDER = TrainProfile("old", "Older Steam Train", 0x20, 0x00, 0x13, "<h", False)
PROFILES = (OLDER, NEWER)
# Kept as aliases for the newer base's recorded packets.
SPEED_REQUEST = NEWER.speed_request
SPEED_SUBSCRIBE = NEWER.speed_subscribe


def select_train(advertisements, address=None, profile_name="auto"):
    """Select a supported LEGO hub from its advertised manufacturer data."""
    candidates = []
    for device, advertisement in advertisements.values():
        if address and device.address.lower() != address.lower():
            continue
        manufacturer = advertisement.manufacturer_data.get(0x0397, b"")
        if len(manufacturer) < 2:
            continue
        for profile in PROFILES:
            if manufacturer[1] == profile.hub_kind and profile_name in ("auto", profile.name):
                candidates.append((device, profile))
    if not candidates:
        raise ConnectionError("No matching DUPLO train found. Switch it on and restart.")
    if len(candidates) > 1:
        choices = ", ".join(f"{p.label}: {d.address}" for d, p in candidates)
        raise ConnectionError(f"Multiple trains found ({choices}). Restart with --address ADDRESS.")
    return candidates[0]


class Telemetry:
    """Decode battery and speed; only show physical units reported by the hub."""

    def __init__(self, profile=NEWER):
        self.speed_port = profile.speed_port
        self.speed_format = profile.speed_format
        self.raw_range = None
        self.si_range = None
        self.symbol = ""
        self.speed_data = None

    def decode(self, data):
        if len(data) < 3 or data[0] != len(data) or data[1] != 0:
            return None
        if len(data) == 6 and data[2:5] == bytes([1, 6, 6]):
            return ("battery", data[5]) if data[5] <= 100 else None
        if len(data) >= 6 and data[2:5] == bytes([0x44, self.speed_port, 0]):
            info, payload = data[5], data[6:]
            if info in (1, 3) and len(payload) == 8:
                limits = struct.unpack("<ff", payload)
                if all(math.isfinite(value) for value in limits):
                    if info == 1:
                        self.raw_range = limits
                    else:
                        self.si_range = limits
            elif info == 4:
                self.symbol = bytes(payload).decode("ascii", errors="replace").rstrip("\0 ")
            elif info == 0x80 and len(payload) == 4:
                formats = {0: "<b", 1: "<h", 2: "<i", 3: "<f"}
                self.speed_format = formats.get(payload[1]) if payload[0] == 1 else None
        elif len(data) >= 5 and data[2:4] == bytes([0x45, self.speed_port]):
            self.speed_data = bytes(data[4:])
        else:
            return None
        if not self.speed_format or self.speed_data is None:
            return None
        if len(self.speed_data) != struct.calcsize(self.speed_format):
            return None
        value = struct.unpack(self.speed_format, self.speed_data)[0]
        if not math.isfinite(value):
            return None
        if self.raw_range and self.si_range and self.symbol:
            low, high = self.raw_range
            if high != low:
                si_low, si_high = self.si_range
                value = si_low + (value - low) * (si_high - si_low) / (high - low)
                return "speed", f"{value:+.1f} {self.symbol}"
        return "speed", f"{value:+g} (raw)"


def motor_packet(power, profile=NEWER):
    if not -100 <= power <= 100:
        raise ValueError("Motor power must be between -100 and 100")
    return bytes([8, 0, 0x81, profile.motor_port, 0x11, 0x51, 0, power & 0xFF])


def horn_packets(profile):
    if profile == OLDER:
        # Select SOUND mode before requesting the built-in horn (sound 9).
        return (bytes.fromhex("0A 00 41 01 01 01 00 00 00 00"),
                bytes.fromhex("08 00 81 01 11 51 01 09"))
    return (bytes.fromhex("0B 00 81 34 11 51 01 07 01 00 00"),)


def light_packets(profile, enabled):
    if profile == OLDER:
        return (bytes.fromhex("0A 00 41 11 00 01 00 00 00 00"),
                bytes([8, 0, 0x81, 0x11, 0x11, 0x51, 0, 10 if enabled else 0]))
    return (bytes([11, 0, 0x81, 0x34, 0x11, 0x51, 1, 4, 1, int(enabled), 0]),)


class Keys:
    """Track held keys; opposing arrows and an emergency stop mean zero."""

    def __init__(self):
        self.held = set()
        self.stopped = False

    def press(self, key):
        repeated = key in self.held
        if key in ("space", "Escape"):
            self.stopped = True
        else:
            self.held.add(key)
        if not repeated:
            return {"Left": "horn", "Right": "lights"}.get(key)

    def release(self, key):
        self.held.discard(key)
        if not self.held.intersection({"Up", "Down"}):
            self.stopped = False

    def clear(self):
        self.held.clear()
        self.stopped = False

    @property
    def power(self):
        up, down = "Up" in self.held, "Down" in self.held
        if self.stopped or up == down:
            return 0
        magnitude = 100 if self.held.intersection({"Shift_L", "Shift_R"}) else 50
        return magnitude if up else -magnitude


class ControlState:
    """Latest input plus a GUI heartbeat, shared with the Bluetooth thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.power = 0
        self.updated = 0.0
        self.actions = deque(maxlen=8)

    def enqueue_action(self, action):
        if action not in ("horn", "lights"):
            raise ValueError(f"Unknown action: {action}")
        with self.lock:
            self.actions.append(action)

    def clear_actions(self):
        with self.lock:
            self.actions.clear()

    def pop_action(self):
        with self.lock:
            if time.monotonic() - self.updated > WATCHDOG_SECONDS:
                self.actions.clear()
            return self.actions.popleft() if self.actions else None

    def publish(self, power, now=None):
        with self.lock:
            self.power = power
            self.updated = time.monotonic() if now is None else now

    def requested(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            return self.power if now - self.updated <= WATCHDOG_SECONDS else 0


async def motor_session(client, state, shutdown, messages, profile=NEWER):
    """All writes are serialized here, including a stop on exit or failure."""

    telemetry = Telemetry(profile)

    async def send(packet):
        await asyncio.wait_for(
            client.write_gatt_char(CHARACTERISTIC, packet, response=True),
            timeout=2,
        )

    async def write(power):
        await send(motor_packet(power, profile))

    def notification(_sender, data):
        reading = telemetry.decode(data)
        if reading:
            messages.put(reading)
        if len(data) >= 5 and data[2] == 0x05:
            if data[3] in (1, 0x21, 0x22, 0x41):
                messages.put(("telemetry_error", "Some battery/speed data is unavailable."))
                return
            messages.put(("error", f"Train protocol error: {bytes(data).hex(' ')}"))
            shutdown.set()
        elif len(data) >= 5 and data[2:4] == bytes([0x82, profile.motor_port]):
            if data[4] & 0x14:  # Discarded or busy/full.
                messages.put(("error", "Train rejected the motor command."))
                shutdown.set()

    try:
        await asyncio.wait_for(client.start_notify(CHARACTERISTIC, notification), 5)
        await write(0)
        state.publish(0)
        state.clear_actions()
        # Query sensor encoding and scale before enabling the controls.
        for info in (0x80, 1, 3, 4):
            await send(bytes([6, 0, 0x22, profile.speed_port, 0, info]))
        await send(profile.speed_subscribe)
        await send(BATTERY_REQUEST)
        await send(profile.speed_request)
        messages.put(("ready", f"{profile.label} connected — hold an arrow key to drive"))
        previous, last_write = 0, time.monotonic()
        lights_on = False  # First toggle explicitly sets white; do not assume the hub's light state.
        next_speed, next_battery = last_write + 0.5, last_write + 30
        while not shutdown.is_set():
            if not client.is_connected:
                raise ConnectionError("Bluetooth disconnected. Restart the program to reconnect.")
            now = time.monotonic()
            power = state.requested(now)
            if power != previous:
                await write(power)
                previous, last_write = power, time.monotonic()
                messages.put(("power", power))
            elif (action := state.pop_action()) is not None:
                packets = horn_packets(profile) if action == "horn" else light_packets(profile, not lights_on)
                for packet in packets:
                    await send(packet)
                if action == "lights":
                    lights_on = not lights_on
                    messages.put(("lights", lights_on))
            elif now - last_write >= REFRESH_SECONDS:
                await write(power)
                last_write = time.monotonic()
            elif now >= next_battery:
                await send(BATTERY_REQUEST)
                next_battery = time.monotonic() + 30
            elif now >= next_speed:
                # Poll as well as subscribe: a stopped sensor may emit no changes.
                await send(profile.speed_request)
                next_speed = time.monotonic() + 0.5
            await asyncio.sleep(0.02)
    finally:
        state.publish(0)
        state.clear_actions()
        if client.is_connected:
            try:
                await write(0)
                messages.put(("stopped", None))
            except Exception as exc:
                messages.put(("error", f"Could not send stop: {exc}. Switch the train off."))


async def connect_and_drive(address, state, shutdown, messages, profile_name="auto"):
    from bleak import BleakClient, BleakScanner

    messages.put(("status", "Looking for the train — switch it on"))
    advertisements = await BleakScanner.discover(timeout=8, return_adv=True)
    device, profile = select_train(advertisements, address, profile_name)
    messages.put(("status", f"Connecting to {profile.label} ({device.address})…"))
    async with BleakClient(device, pair=profile.pair, timeout=30) as client:
        await motor_session(client, state, shutdown, messages, profile)


async def dry_run(state, shutdown, messages, profile=NEWER):
    messages.put(("ready", f"Dry run: {profile.label} — Bluetooth is disabled"))
    previous = None
    lights_on = False
    while not shutdown.is_set():
        power = state.requested()
        if power != previous:
            print(f"Power {power:+4d}%  |  {motor_packet(power, profile).hex(' ')}", flush=True)
            messages.put(("power", power))
            previous = power
        if (action := state.pop_action()) is not None:
            packets = horn_packets(profile) if action == "horn" else light_packets(profile, not lights_on)
            for packet in packets:
                print(f"{action.title()}: {packet.hex(' ')}", flush=True)
            if action == "lights":
                lights_on = not lights_on
                messages.put(("lights", lights_on))
        await asyncio.sleep(0.02)
    print("Power   +0%  |  dry run ended", flush=True)


def worker(address, simulate, state, shutdown, messages, profile_name="auto"):
    async def run():
        task = asyncio.create_task(
            dry_run(state, shutdown, messages, OLDER if profile_name == "old" else NEWER) if simulate
            else connect_and_drive(address, state, shutdown, messages, profile_name)
        )
        try:
            while not task.done() and not shutdown.is_set():
                await asyncio.sleep(0.05)
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    try:
        asyncio.run(run())
    except Exception as exc:
        messages.put(("error", str(exc)))
    finally:
        messages.put(("finished", None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="Select a specific train Bluetooth address")
    parser.add_argument("--profile", choices=("auto", "old", "new"), default="auto",
                        help="Limit discovery to a base generation (default: auto)")
    parser.add_argument("--dry-run", action="store_true", help="Test keys without Bluetooth")
    args = parser.parse_args()
    if not args.dry_run:
        try:
            import bleak  # noqa: F401
        except ImportError:
            parser.error("Install dependencies first: python -m pip install -r requirements.txt")

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("DUPLO train controller")
    root.geometry("580x420")
    root.minsize(560, 410)
    keys, state, shutdown, messages = Keys(), ControlState(), threading.Event(), SimpleQueue()
    status = tk.StringVar(value="Starting…")
    power_label = tk.StringVar(value="Stopped")
    battery_label = tk.StringVar(value="Battery: —")
    battery_level = tk.DoubleVar(value=0)
    speed_label = tk.StringVar(value="Speed: —")
    lights_label = tk.StringVar(value="Lights: —")
    ready, closing, finished = False, False, False
    releases = {}

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="DUPLO train", font=("sans", 22, "bold")).pack()
    ttk.Label(frame, text="↑ Forward   •   ↓ Reverse   •   Shift = 100% power").pack(pady=12)
    ttk.Label(frame, text="← Horn   •   → Toggle lights").pack(pady=(0, 6))
    ttk.Label(frame, text="Release to stop. Both arrows together also stop.").pack()
    ttk.Label(frame, text="Space / Esc: stop   •   Keep this window focused").pack(pady=6)
    ttk.Label(frame, textvariable=power_label, font=("sans", 18)).pack(pady=10)
    readings = ttk.Frame(frame)
    readings.pack(fill="x", pady=(0, 12))
    ttk.Label(readings, textvariable=battery_label).pack(side="left")
    ttk.Progressbar(readings, variable=battery_level, maximum=100, length=110).pack(
        side="left", padx=10
    )
    ttk.Label(readings, textvariable=speed_label).pack(side="right")
    ttk.Label(frame, textvariable=lights_label).pack(pady=(0, 8))
    ttk.Label(frame, textvariable=status, wraplength=490).pack()

    def publish():
        state.publish(keys.power if ready and not closing else 0)

    def press(event):
        if event.keysym not in {"Up", "Down", "Left", "Right", "Shift_L", "Shift_R", "space", "Escape"}:
            return
        pending = releases.pop(event.keysym, None)
        if pending:
            root.after_cancel(pending)
        if ready and not closing:
            action = keys.press(event.keysym)
            if event.keysym in ("space", "Escape"):
                state.clear_actions()
            elif action:
                state.enqueue_action(action)
        publish()
        return "break"

    def release(event):
        # X11 auto-repeat can synthesize release/press pairs. Cancel that
        # release if the matching press is already queued in this event cycle.
        def apply_release():
            releases.pop(event.keysym, None)
            keys.release(event.keysym)
            publish()

        old = releases.pop(event.keysym, None)
        if old:
            root.after_cancel(old)
        releases[event.keysym] = root.after_idle(apply_release)

    def clear(_event=None):
        for callback in releases.values():
            root.after_cancel(callback)
        releases.clear()
        keys.clear()
        state.clear_actions()
        state.publish(0)

    def close():
        nonlocal closing, ready
        closing, ready = True, False
        clear()
        shutdown.set()
        status.set("Stopping and disconnecting…")
        if finished:
            root.destroy()

    def tick():
        nonlocal ready, finished
        while True:
            try:
                kind, value = messages.get_nowait()
            except Empty:
                break
            if kind == "ready":
                clear()
                ready = not closing
                status.set(value)
            elif kind == "power":
                direction = "Forward" if value > 0 else "Reverse"
                power_label.set(f"{direction} {abs(value)}%" if value else "Stopped")
            elif kind == "stopped":
                power_label.set("Stopped")
            elif kind == "battery":
                battery_label.set(f"Battery: {value}%")
                battery_level.set(value)
            elif kind == "speed":
                speed_label.set(f"Speed: {value}")
            elif kind == "lights":
                lights_label.set("Lights: on" if value else "Lights: off")
            elif kind == "telemetry_error":
                status.set(value)
            elif kind in ("status", "error"):
                status.set(value)
                if kind == "error":
                    ready = False
                    clear()
            elif kind == "finished":
                finished, ready = True, False
                clear()
                power_label.set("Controls inactive")
                battery_label.set("Battery: —")
                battery_level.set(0)
                speed_label.set("Speed: —")
                lights_label.set("Lights: —")
        if closing and finished:
            root.destroy()
            return
        publish()  # Heartbeat: a stalled GUI expires to zero in the worker.
        root.after(20, tick)

    root.bind("<KeyPress>", press)
    root.bind("<KeyRelease>", release)
    root.bind("<FocusOut>", clear)
    root.protocol("WM_DELETE_WINDOW", close)
    thread = threading.Thread(
        target=worker, args=(args.address, args.dry_run, state, shutdown, messages, args.profile),
        daemon=True
    )
    thread.start()
    root.after(20, tick)
    root.after(100, root.focus_set)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        state.publish(0)
        shutdown.set()
        thread.join(timeout=8)


if __name__ == "__main__":
    main()
