"""Connection transitions with fake devices; no phone, pairing or network access."""
import asyncio
from contextlib import asynccontextmanager
import io
import json
import logging
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend import DeviceWorker, Handler


class FakeSession:
    def __init__(self, backend, selected):
        self.backend = backend
        self.selected = selected

    async def apply(self, lat, lon):
        self.backend.events.append(("apply", self.selected, (lat, lon)))

    async def clear(self):
        self.backend.events.append(("clear-start", self.selected))
        self.backend.clear_started.set()
        if self.backend.clear_gate is not None:
            await self.backend.clear_gate.wait()
        if self.backend.clear_fails:
            raise RuntimeError("Fake device refused to clear")
        self.backend.events.append(("clear", self.selected))


class FakeBackend:
    platform = "ios"

    def __init__(self):
        self.connection = {"transport": "usb", "device_id": None}
        self.events = []
        self.active_sessions = 0
        self.open_gate = None
        self.clear_gate = None
        self.clear_started = threading.Event()
        self.enable_gate = None
        self.enable_started = threading.Event()
        self.clear_fails = False

    @staticmethod
    def validate_connection(connection):
        if connection.get("transport") not in ("usb", "wifi"):
            raise ValueError("Unsupported transport")
        device_id = connection.get("device_id")
        if device_id is not None and not isinstance(device_id, str):
            raise ValueError("Invalid device_id")
        return {"transport": connection["transport"], "device_id": device_id}

    def configure_connection(self, connection):
        if self.active_sessions:
            raise AssertionError("Reconfigured before closing the old session")
        self.connection = dict(connection)
        self.events.append(("configure", self.selected()))

    def selected(self):
        return self.connection["transport"], self.connection["device_id"]

    def device_info(self):
        return {"id": self.connection["device_id"], "platform": "ios"}

    def status_extra(self):
        return {"connection": dict(self.connection)}

    async def is_alive(self):
        return True

    async def discover(self, transport):
        self.events.append(("discover", transport))
        return [{"id": "found-phone", "name": "Test phone"}]

    async def enable_wifi(self, device_id):
        if self.active_sessions:
            raise AssertionError("Wireless preparation ran before old session closed")
        self.events.append(("enable-wifi", device_id))
        self.enable_started.set()
        try:
            if self.enable_gate is not None:
                await self.enable_gate.wait()
            return {"device_id": device_id, "enabled": True}
        finally:
            self.events.append(("enable-closed", device_id))

    @asynccontextmanager
    async def session(self):
        selected = self.selected()
        self.events.append(("opening", selected))
        self.active_sessions += 1
        try:
            if self.open_gate is not None:
                await self.open_gate.wait()
            self.events.append(("open", selected))
            yield FakeSession(self, selected)
        finally:
            self.active_sessions -= 1
            self.events.append(("close", selected))


class WorkerConnectionTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.worker = DeviceWorker(self.backend)
        self.worker.start()

    def tearDown(self):
        self.backend.clear_fails = False
        for gate in (self.backend.clear_gate, self.backend.open_gate, self.backend.enable_gate):
            if gate is not None:
                self.worker.loop.call_soon_threadsafe(gate.set)
        self.assertTrue(self.worker.shutdown(timeout=3), "Worker failed to shut down")
        self.worker.loop.call_soon_threadsafe(self.worker.loop.stop)
        self.worker._thread.join(timeout=3)
        self.assertFalse(self.worker._thread.is_alive())

    def wait_for(self, predicate, message="Expected state was not reached"):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(f"{message}: state={self.worker.state}, events={self.backend.events}")
            time.sleep(0.005)

    def settle_requests(self):
        async def settled():
            await asyncio.sleep(0)
            while self.worker._requests:
                await asyncio.sleep(0.005)
        asyncio.run_coroutine_threadsafe(settled(), self.worker.loop).result(timeout=3)

    def connect_to(self, transport="usb", device_id="phone-a"):
        self.worker.connect({"transport": transport, "device_id": device_id})
        self.settle_requests()
        self.wait_for(lambda: self.worker.state == "connected")

    def test_transport_switch_clears_and_closes_old_device_before_opening_new(self):
        self.connect_to()
        self.worker.set_location(31.2, 121.5)
        self.wait_for(lambda: self.worker.applied == (31.2, 121.5))

        self.worker.connect({"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
        self.settle_requests()
        self.wait_for(lambda: self.worker.state == "connected")

        events = self.backend.events
        old = ("usb", "phone-a")
        new = ("wifi", "remote-a")
        self.assertLess(events.index(("clear", old)), events.index(("close", old)))
        self.assertLess(events.index(("close", old)), events.index(("configure", new)))
        self.assertLess(events.index(("configure", new)), events.index(("open", new)))
        self.assertIsNone(self.worker.target)
        self.assertIsNone(self.worker.applied)
        self.assertFalse(any(e[0] == "apply" and e[1] == new for e in events))

    def test_clear_failure_prevents_switch_and_is_not_bypassed_by_retry(self):
        self.connect_to()
        self.worker.set_location(1, 2)
        self.wait_for(lambda: self.worker.applied == (1, 2))
        self.backend.clear_fails = True
        for _ in range(2):
            self.worker.connect({"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
            self.settle_requests()
            self.assertEqual(self.worker.state, "error")
            self.assertEqual(self.backend.selected(), ("usb", "phone-a"))
            self.assertNotIn(("open", ("wifi", "remote-a")), self.backend.events)

    def test_reconnect_cancels_incomplete_handshake_and_closes_before_new_open(self):
        gate = self.backend.open_gate = asyncio.Event()
        self.worker.connect({"transport": "usb", "device_id": "phone-a"})
        self.wait_for(lambda: ("opening", ("usb", "phone-a")) in self.backend.events)
        self.assertEqual(self.worker.state, "connecting")

        self.backend.open_gate = None
        self.worker.connect({"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
        self.settle_requests()
        self.wait_for(lambda: self.worker.state == "connected")
        self.assertLess(self.backend.events.index(("close", ("usb", "phone-a"))),
                        self.backend.events.index(("open", ("wifi", "remote-a"))))
        self.assertFalse(gate.is_set(), "The old handshake should have been cancelled")
        self.assertEqual(self.backend.active_sessions, 1)

    def test_successful_old_device_recovery_allows_later_transport_switch(self):
        self.connect_to()
        self.worker.set_location(1, 2)
        self.wait_for(lambda: self.worker.applied == (1, 2))
        self.backend.clear_fails = True
        self.worker.connect({"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
        self.settle_requests()
        self.assertEqual(self.worker.state, "error")

        self.backend.clear_fails = False
        event_start = len(self.backend.events)
        self.worker.connect(reconnect=True)
        self.settle_requests()
        self.wait_for(lambda: self.worker.state == "connected" and self.worker._clear_error is None)
        self.assertIn(("clear", ("usb", "phone-a")), self.backend.events[event_start:])
        self.assertFalse(any(e[0] == "apply" for e in self.backend.events[event_start:]))

        self.connect_to("wifi", "remote-a")
        self.assertEqual(self.backend.selected(), ("wifi", "remote-a"))

    def test_implicit_reset_reconnect_clears_failed_old_device_without_reapplying_target(self):
        self.connect_to()
        self.worker.set_location(1, 2)
        self.wait_for(lambda: self.worker.applied == (1, 2))
        self.backend.clear_fails = True
        self.worker.connect({"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
        self.settle_requests()
        self.assertEqual(self.worker.state, "error")

        self.backend.clear_fails = False
        event_start = len(self.backend.events)
        self.worker.connect()  # the Reset GPS button reconnects without a body
        self.settle_requests()
        self.wait_for(lambda: self.worker.state == "connected")
        self.assertIn(("clear", ("usb", "phone-a")), self.backend.events[event_start:])
        self.assertFalse(any(e[0] == "apply" for e in self.backend.events[event_start:]))
        self.assertIsNone(self.worker._clear_error)

    def test_implicit_connect_reuses_active_transport_and_device(self):
        self.connect_to("wifi", "remote-a")
        before = list(self.backend.events)
        self.worker.connect()
        self.settle_requests()
        self.assertEqual(self.backend.events, before)
        self.assertEqual(self.backend.selected(), ("wifi", "remote-a"))

    def test_explicit_reconnect_reopens_same_device_without_old_target(self):
        self.connect_to("wifi", "remote-a")
        self.worker.set_location(10, 20)
        self.wait_for(lambda: self.worker.applied == (10, 20))
        self.worker.connect(reconnect=True)
        self.settle_requests()
        self.wait_for(lambda: self.worker.state == "connected")
        self.assertEqual(self.backend.events.count(("open", ("wifi", "remote-a"))), 2)
        self.assertIsNone(self.worker.target)
        self.assertIsNone(self.worker.applied)

    def test_pending_is_visible_in_http_reply_until_old_clear_finishes(self):
        self.connect_to("wifi", "remote-a")
        self.backend.clear_gate = asyncio.Event()
        body = json.dumps({"transport": "wifi", "device_id": "remote-a",
                           "reconnect": True}).encode("utf-8")
        handler = Handler.__new__(Handler)
        handler.path = "/api/connect"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.worker = self.worker
        handler._send_json = Mock()

        handler.do_POST()
        reply = handler._send_json.call_args.args[0]
        self.assertTrue(reply["ok"])
        self.assertTrue(reply["connection_pending"], "The synchronous HTTP reply must mark the request pending")
        self.assertTrue(self.backend.clear_started.wait(timeout=3))
        # The old device is still reported connected while its clear is blocked;
        # pending prevents the UI from mistaking this for the new connection.
        self.assertEqual(self.worker.status()["state"], "connected")
        self.assertTrue(self.worker.status()["connection_pending"])
        self.assertEqual(self.backend.events.count(("open", ("wifi", "remote-a"))), 1)

        self.worker.loop.call_soon_threadsafe(self.backend.clear_gate.set)
        self.settle_requests()
        self.wait_for(lambda: self.backend.events.count(("open", ("wifi", "remote-a"))) == 2
                      and not self.worker.status()["connection_pending"])
        self.assertEqual(self.worker.state, "connected")
        self.assertEqual(self.worker._pending_connections, 0)

    def test_enable_wifi_waits_for_clear_and_close(self):
        self.connect_to()
        result = self.worker.ios_operation("enable_wifi", "phone-a")
        self.assertTrue(result["enabled"])
        events = self.backend.events
        self.assertLess(events.index(("clear", ("usb", "phone-a"))),
                        events.index(("close", ("usb", "phone-a"))))
        self.assertLess(events.index(("close", ("usb", "phone-a"))),
                        events.index(("enable-wifi", "phone-a")))
        self.assertEqual(self.worker.state, "idle")

    def test_discovery_keeps_active_location_session(self):
        self.connect_to()
        result = self.worker.ios_operation("discover", "wifi")
        self.assertEqual(result[0]["id"], "found-phone")
        self.assertEqual(self.backend.active_sessions, 1)
        self.assertNotIn(("close", ("usb", "phone-a")), self.backend.events)

    def test_shutdown_does_not_start_queued_connection_after_old_clear(self):
        self.connect_to()
        self.backend.clear_gate = asyncio.Event()
        self.worker.connect({"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
        self.assertTrue(self.backend.clear_started.wait(timeout=3))
        self.worker.connect({"transport": "usb", "device_id": "phone-b"}, reconnect=True)

        result = []
        thread = threading.Thread(target=lambda: result.append(self.worker.shutdown(timeout=3)))
        thread.start()
        self.wait_for(lambda: self.worker._closing)
        self.worker.loop.call_soon_threadsafe(self.backend.clear_gate.set)
        thread.join(timeout=3)
        self.assertEqual(result, [True])
        self.assertFalse(self.worker.status()["connection_pending"])
        self.assertEqual(self.worker._pending_connections, 0)
        self.assertEqual([e for e in self.backend.events if e[0] == "open"],
                         [("open", ("usb", "phone-a"))])
        with self.assertRaises(RuntimeError):
            self.worker.connect()

    def test_invalid_configuration_is_rejected_before_scheduling(self):
        for connection in ({"transport": "bluetooth"},
                           {"transport": "wifi", "device_id": ["phone-a"]}):
            with self.assertRaises(ValueError):
                self.worker.connect(connection)
        self.assertEqual(self.worker.state, "idle")
        self.assertTrue(self.worker._done.is_set())
        self.assertEqual(self.worker._pending_connections, 0)
        self.assertEqual(self.backend.events, [])

    def test_shutdown_cancels_running_wireless_preparation_and_waits_for_cleanup(self):
        self.backend.enable_gate = asyncio.Event()
        outcomes = []

        def prepare():
            try:
                outcomes.append(self.worker.ios_operation("enable_wifi", "phone-a"))
            except BaseException as error:
                outcomes.append(error)

        thread = threading.Thread(target=prepare)
        thread.start()
        self.assertTrue(self.backend.enable_started.wait(timeout=3))
        self.assertFalse(self.worker._done.is_set())
        self.assertTrue(self.worker.shutdown(timeout=3))
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], BaseException)
        self.assertIn(("enable-closed", "phone-a"), self.backend.events)


class ConnectionApiTests(unittest.TestCase):
    """Exercise request parsing without opening a socket or contacting a device."""

    def make_handler(self, path, body=None):
        handler = Handler.__new__(Handler)
        handler.path = path
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.worker = Mock()
        handler.worker.status.return_value = {"state": "idle"}
        handler._send_json = Mock()
        return handler

    def test_connect_passes_transport_device_and_reconnect(self):
        handler = self.make_handler("/api/connect", {
            "transport": "wifi", "device_id": "remote-a", "reconnect": True,
        })
        handler.do_POST()
        handler.worker.connect.assert_called_once_with(
            {"transport": "wifi", "device_id": "remote-a"}, reconnect=True)
        self.assertTrue(handler._send_json.call_args.args[0]["ok"])

    def test_empty_connect_keeps_current_configuration(self):
        handler = self.make_handler("/api/connect")
        handler.do_POST()
        handler.worker.connect.assert_called_once_with(None, reconnect=False)

    def test_non_boolean_reconnect_does_not_reach_worker(self):
        handler = self.make_handler("/api/connect", {"transport": "wifi", "reconnect": "true"})
        handler.do_POST()
        handler.worker.connect.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_wireless_preparation_rejects_non_string_identifier(self):
        handler = self.make_handler("/api/ios/wifi/enable", {"device_id": {"id": "phone-a"}})
        handler.do_POST()
        handler.worker.ios_operation.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1], 400)

    def test_wireless_discovery_uses_requested_transport(self):
        handler = self.make_handler("/api/ios/devices?transport=wifi")
        handler.worker.ios_operation.return_value = [{"id": "remote-a", "name": "Test phone"}]
        handler.do_GET()
        handler.worker.ios_operation.assert_called_once_with("discover", "wifi")
        self.assertEqual(handler._send_json.call_args.args[0]["devices"][0]["id"], "remote-a")


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
