import asyncio
from queue import SimpleQueue
import struct
import threading
import time
from types import SimpleNamespace
import unittest

from train_controller import (
    BATTERY_REQUEST, SPEED_REQUEST, SPEED_SUBSCRIBE, ControlState, Keys,
    Telemetry, WATCHDOG_SECONDS, motor_packet, motor_session, OLDER, NEWER, select_train,
    horn_packets, light_packets,
)


def advertisement(address, kind):
    return (SimpleNamespace(address=address),
            SimpleNamespace(manufacturer_data={0x0397: bytes([0, kind, 2, 0, 0, 0])}))


class DiscoveryTests(unittest.TestCase):
    def test_detects_both_profiles(self):
        for profile in (OLDER, NEWER):
            device, selected = select_train({"train": advertisement("AA:BB", profile.hub_kind)})
            self.assertEqual(device.address, "AA:BB")
            self.assertEqual(selected, profile)

    def test_requires_selection_when_both_are_on(self):
        devices = {"old": advertisement("AA:BB", 0x20), "new": advertisement("CC:DD", 0x21)}
        with self.assertRaisesRegex(ConnectionError, "Multiple trains"):
            select_train(devices)
        self.assertEqual(select_train(devices, address="aa:bb")[1], OLDER)
        self.assertEqual(select_train(devices, profile_name="new")[1], NEWER)

    def test_unrecognized_hub_is_not_controlled(self):
        with self.assertRaisesRegex(ConnectionError, "No matching"):
            select_train({"other": advertisement("AA:BB", 0x40)})


class TelemetryTests(unittest.TestCase):
    def test_older_speed_uses_its_port_and_int16_default(self):
        telemetry = Telemetry(OLDER)
        self.assertEqual(telemetry.decode(bytes.fromhex("06 00 45 13 D4 FE")),
                         ("speed", "-300 (raw)"))
        self.assertIsNone(telemetry.decode(bytes.fromhex("05 00 45 36 32")))
        for info, limits in ((1, (-300, 300)), (3, (-10, 10))):
            telemetry.decode(bytes([14, 0, 0x44, 0x13, 0, info]) + struct.pack("<ff", *limits))
        self.assertEqual(telemetry.decode(bytes.fromhex("0A 00 44 13 00 04 73 70 64 00")),
                         ("speed", "-10.0 spd"))

    def test_battery_percentage_and_invalid_values(self):
        telemetry = Telemetry()
        self.assertEqual(telemetry.decode(bytes.fromhex("06 00 01 06 06 64")), ("battery", 100))
        self.assertIsNone(telemetry.decode(bytes.fromhex("06 00 01 06 06 FF")))

    def test_speed_uses_observed_int8_format_without_assuming_units(self):
        telemetry = Telemetry()
        self.assertEqual(telemetry.decode(bytes.fromhex("05 00 45 36 CE")), ("speed", "-50 (raw)"))
        self.assertEqual(telemetry.decode(bytes.fromhex("05 00 45 36 00")), ("speed", "+0 (raw)"))

    def test_reported_scale_and_symbol_update_existing_reading(self):
        telemetry = Telemetry()
        telemetry.decode(bytes.fromhex("05 00 45 36 32"))
        for info, limits in ((1, (-100, 100)), (3, (-20, 20))):
            packet = bytes([14, 0, 0x44, 0x36, 0, info]) + struct.pack("<ff", *limits)
            telemetry.decode(packet)
        self.assertEqual(
            telemetry.decode(bytes.fromhex("0A 00 44 36 00 04 63 6D 2F 73")),
            ("speed", "+10.0 cm/s"),
        )

    def test_value_format_can_select_signed_int16(self):
        telemetry = Telemetry()
        telemetry.decode(bytes.fromhex("0A 00 44 36 00 80 01 01 05 00"))
        self.assertEqual(
            telemetry.decode(bytes.fromhex("06 00 45 36 D4 FE")),
            ("speed", "-300 (raw)"),
        )

    def test_unrelated_truncated_and_unsupported_readings_are_ignored(self):
        telemetry = Telemetry()
        for packet in ("", "05 00 45 36", "06 00 45 33 41 01", "05 00 45 35 00"):
            self.assertIsNone(telemetry.decode(bytes.fromhex(packet)))
        telemetry.decode(bytes.fromhex("0A 00 44 36 00 80 02 00 05 00"))
        self.assertIsNone(telemetry.decode(bytes.fromhex("05 00 45 36 01")))


class InputTests(unittest.TestCase):
    def test_auxiliary_keys_trigger_once_until_released(self):
        keys = Keys()
        keys.press("Up")
        for key, action in (("Left", "horn"), ("Right", "lights")):
            self.assertEqual(keys.press(key), action)
            self.assertIsNone(keys.press(key))
            self.assertEqual(keys.power, 50)
            keys.release(key)
            self.assertEqual(keys.press(key), action)
            keys.release(key)

    def test_stale_or_cleared_actions_are_not_replayed(self):
        state = ControlState()
        state.publish(0)
        state.enqueue_action("horn")
        state.enqueue_action("lights")
        self.assertEqual(state.pop_action(), "horn")
        state.clear_actions()
        self.assertIsNone(state.pop_action())
        state.enqueue_action("horn")
        state.publish(0, now=time.monotonic() - WATCHDOG_SECONDS - 1)
        self.assertIsNone(state.pop_action())

    def test_profile_specific_horn_and_light_packets(self):
        self.assertEqual(horn_packets(NEWER)[-1],
                         bytes.fromhex("0B 00 81 34 11 51 01 07 01 00 00"))
        self.assertEqual(horn_packets(OLDER)[-1], bytes.fromhex("08 00 81 01 11 51 01 09"))
        self.assertEqual(light_packets(NEWER, True)[-1],
                         bytes.fromhex("0B 00 81 34 11 51 01 04 01 01 00"))
        self.assertEqual(light_packets(OLDER, True)[-1],
                         bytes.fromhex("08 00 81 11 11 51 00 0A"))
        self.assertEqual(light_packets(OLDER, False)[-1][-1], 0)
        self.assertEqual(light_packets(NEWER, False)[-1][-2:], bytes([0, 0]))

    def test_directions_shift_and_release(self):
        keys = Keys()
        keys.press("Up")
        self.assertEqual(keys.power, 50)
        keys.press("Shift_L")
        self.assertEqual(keys.power, 100)
        keys.release("Shift_L")
        self.assertEqual(keys.power, 50)
        keys.release("Up")
        self.assertEqual(keys.power, 0)
        keys.press("Down")
        self.assertEqual(keys.power, -50)
        keys.press("Shift_R")
        self.assertEqual(keys.power, -100)
        keys.clear()
        self.assertEqual(keys.power, 0)

    def test_opposing_arrows_stop(self):
        keys = Keys()
        keys.press("Up")
        keys.press("Down")
        self.assertEqual(keys.power, 0)
        keys.release("Up")
        self.assertEqual(keys.power, -50)

    def test_emergency_stop_ignores_repeat_until_release(self):
        for stop in ("space", "Escape"):
            keys = Keys()
            keys.press("Up")
            keys.press(stop)
            keys.press("Up")  # OS key auto-repeat must not restart motion.
            keys.release(stop)
            self.assertEqual(keys.power, 0)
            keys.release("Up")
            keys.press("Up")
            self.assertEqual(keys.power, 50)

    def test_gui_heartbeat_expires(self):
        state = ControlState()
        state.publish(100, now=10)
        self.assertEqual(state.requested(now=10.1), 100)
        self.assertEqual(state.requested(now=10 + WATCHDOG_SECONDS + 0.01), 0)

    def test_reverse_motor_encoding(self):
        self.assertEqual(motor_packet(-50), bytes.fromhex("08 00 81 32 11 51 00 CE"))
        self.assertEqual(motor_packet(100)[-1], 100)
        with self.assertRaises(ValueError):
            motor_packet(101)


class FakeClient:
    is_connected = True

    def __init__(self, state, shutdown, failure=False):
        self.state, self.shutdown, self.failure = state, shutdown, failure
        self.writes = []

    async def start_notify(self, characteristic, callback):
        self.notification = callback

    async def write_gatt_char(self, characteristic, data, response):
        self.writes.append(bytes(data))
        if len(self.writes) == 1:
            # motor_session clears stale input after its initial stop.
            asyncio.get_running_loop().call_later(0.01, self.state.publish, 50)
        elif data[-1] == 50:
            if self.failure:
                raise OSError("Simulated write failure")
            self.shutdown.set()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_horn_and_light_toggles_are_serialized_for_each_base(self):
        for profile in (OLDER, NEWER):
            with self.subTest(profile=profile.name):
                state, shutdown, messages = ControlState(), threading.Event(), SimpleQueue()
                client = FakeClient(state, shutdown)

                def enqueue():
                    state.publish(0)
                    for action in ("horn", "lights", "lights"):
                        state.enqueue_action(action)

                async def write(characteristic, data, response):
                    client.writes.append(bytes(data))
                    if len(client.writes) == 1:
                        asyncio.get_running_loop().call_later(0.01, enqueue)
                    if data == light_packets(profile, False)[-1]:
                        shutdown.set()

                client.write_gatt_char = write
                await asyncio.wait_for(motor_session(client, state, shutdown, messages, profile), 1)
                expected = [*horn_packets(profile), *light_packets(profile, True),
                            *light_packets(profile, False)]
                # Look at the final action transaction followed by motor cleanup.
                self.assertEqual(client.writes[-len(expected)-1:-1], expected)
                self.assertEqual(client.writes[-1], motor_packet(0, profile))
                events = []
                while not messages.empty():
                    events.append(messages.get_nowait())
                self.assertIn(("lights", True), events)
                self.assertIn(("lights", False), events)

    async def test_older_session_uses_correct_motor_and_speed_ports(self):
        state, shutdown = ControlState(), threading.Event()
        client = FakeClient(state, shutdown)
        await asyncio.wait_for(motor_session(client, state, shutdown, SimpleQueue(), OLDER), 1)
        motors = [p for p in client.writes if p[2] == 0x81]
        self.assertEqual(motors, [motor_packet(0, OLDER), motor_packet(50, OLDER),
                                  motor_packet(0, OLDER)])
        self.assertEqual(motor_packet(-50, OLDER), bytes.fromhex("08 00 81 00 11 51 00 CE"))
        self.assertIn(bytes.fromhex("0A 00 41 13 00 01 00 00 00 01"), client.writes)
        self.assertIn(bytes.fromhex("05 00 21 13 00"), client.writes)
        self.assertFalse(any(p[3] == 0x36 for p in client.writes if p[2] in (0x21, 0x22, 0x41)))

    async def test_older_motor_rejection_stops_the_session(self):
        state, shutdown = ControlState(), threading.Event()
        client = FakeClient(state, shutdown)

        async def write(characteristic, data, response):
            client.writes.append(bytes(data))
            if len(client.writes) == 1:
                asyncio.get_running_loop().call_later(
                    0.01, client.notification, None, bytes.fromhex("05 00 82 00 04")
                )

        client.write_gatt_char = write
        await asyncio.wait_for(motor_session(client, state, shutdown, SimpleQueue(), OLDER), 1)
        self.assertTrue(shutdown.is_set())
        self.assertEqual(client.writes[-1], motor_packet(0, OLDER))

    async def test_initial_and_final_stop_surround_motion(self):
        state, shutdown = ControlState(), threading.Event()
        client = FakeClient(state, shutdown)
        await asyncio.wait_for(motor_session(client, state, shutdown, SimpleQueue()), 1)
        self.assertEqual([p[-1] for p in client.writes if p[2] == 0x81], [0, 50, 0])
        for request in (BATTERY_REQUEST, SPEED_REQUEST, SPEED_SUBSCRIBE):
            self.assertIn(request, client.writes)

    async def test_write_failure_attempts_stop(self):
        state, shutdown = ControlState(), threading.Event()
        client = FakeClient(state, shutdown, failure=True)
        with self.assertRaisesRegex(OSError, "Simulated"):
            await asyncio.wait_for(motor_session(client, state, shutdown, SimpleQueue()), 1)
        self.assertEqual([p[-1] for p in client.writes if p[2] == 0x81], [0, 50, 0])

    async def test_cancellation_attempts_stop(self):
        state, shutdown = ControlState(), threading.Event()
        client = FakeClient(state, shutdown)
        # Keep the session alive until cancellation instead of its normal fake shutdown.
        async def write(characteristic, data, response):
            client.writes.append(bytes(data))
        client.write_gatt_char = write
        task = asyncio.create_task(motor_session(client, state, shutdown, SimpleQueue()))
        await asyncio.sleep(0.04)
        state.publish(-100)
        await asyncio.sleep(0.04)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn(motor_packet(-100), client.writes)
        self.assertEqual(client.writes[-1], motor_packet(0))

    async def test_protocol_rejection_stops_session(self):
        state, shutdown = ControlState(), threading.Event()
        client = FakeClient(state, shutdown)
        async def write(characteristic, data, response):
            client.writes.append(bytes(data))
            if len(client.writes) == 1:
                asyncio.get_running_loop().call_later(
                    0.01, client.notification, None, bytes.fromhex("05 00 82 32 04")
                )
        client.write_gatt_char = write
        await asyncio.wait_for(motor_session(client, state, shutdown, SimpleQueue()), 1)
        self.assertTrue(shutdown.is_set())
        self.assertEqual(client.writes[-1], motor_packet(0))

    async def test_battery_and_speed_notifications_reach_gui_queue(self):
        state, shutdown, messages = ControlState(), threading.Event(), SimpleQueue()
        client = FakeClient(state, shutdown)
        original_write = client.write_gatt_char

        async def write(characteristic, data, response):
            await original_write(characteristic, data, response)
            if data == BATTERY_REQUEST:
                client.notification(None, bytes.fromhex("06 00 01 06 06 55"))
            elif data == SPEED_REQUEST:
                client.notification(None, bytes.fromhex("05 00 45 36 00"))

        client.write_gatt_char = write
        await asyncio.wait_for(motor_session(client, state, shutdown, messages), 1)
        events = []
        while not messages.empty():
            events.append(messages.get_nowait())
        self.assertIn(("battery", 85), events)
        self.assertIn(("speed", "+0 (raw)"), events)


if __name__ == "__main__":
    unittest.main()
