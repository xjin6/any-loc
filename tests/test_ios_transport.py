"""Transport contract tests. All device discovery and connections are mocked."""
import asyncio
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ios_transport as transport


def lockdown(version="18.0"):
    return SimpleNamespace(
        udid="phone-a", all_values={"DeviceName": "Test phone", "ProductVersion": version},
        close=AsyncMock(), set_enable_wifi_connections=AsyncMock(),
        get_enable_wifi_connections=AsyncMock(return_value=True),
    )


def rsd():
    return SimpleNamespace(service=SimpleNamespace(ping=AsyncMock()),
                           connect=AsyncMock(), close=AsyncMock())


class Browse:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.aclose = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.answers)
        except StopIteration:
            raise StopAsyncIteration


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_bluetooth_is_rejected_before_discovery(self):
        with patch.object(transport, "_usb_devices", new_callable=AsyncMock) as usb:
            with self.assertRaisesRegex(transport.TransportError, "蓝牙"):
                await transport.discover("bluetooth")
            with self.assertRaisesRegex(transport.TransportError, "蓝牙"):
                async with transport.open_rsd("bluetooth"):
                    self.fail("unsupported transport opened")
            usb.assert_not_called()

    async def test_usb_discovery_excludes_network_and_never_pairs(self):
        client = lockdown()
        devices = [SimpleNamespace(serial="phone-a", is_usb=True),
                   SimpleNamespace(serial="network-only", is_usb=False)]
        with patch.object(transport.usbmux, "list_devices", AsyncMock(return_value=devices)), \
                patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)) as create:
            found = await transport.discover("usb")
        self.assertEqual(found, [{"id": "phone-a", "name": "Test phone", "os_version": "18.0"}])
        create.assert_awaited_once_with(serial="phone-a", connection_type="USB", autopair=False)
        client.close.assert_awaited_once()

    async def test_untrusted_usb_remains_selectable(self):
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "a", "name": "a"}])), \
                patch.object(transport, "create_using_usbmux", AsyncMock(side_effect=OSError("locked"))):
            self.assertEqual(await transport.discover("usb"), [{"id": "a", "name": "a"}])

    async def test_multiple_usb_requires_selection(self):
        devices = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=devices)), \
                patch.object(transport, "create_using_usbmux", new_callable=AsyncMock) as create:
            with self.assertRaisesRegex(transport.TransportError, "多个"):
                await transport.enable_wifi()
            with self.assertRaisesRegex(transport.TransportError, "多个"):
                async with transport.open_rsd("usb"):
                    self.fail("ambiguous selection opened")
        create.assert_not_called()

    def test_selection_never_substitutes_another_device(self):
        with self.assertRaisesRegex(transport.TransportError, "所选"):
            transport._select([{"id": "other"}], "selected", "USB")
        self.assertEqual(transport._select([{"id": "ABC-123"}], "abc123", "USB")["id"], "ABC-123")

    async def test_wifi_discovery_authenticates_advertisements_and_closes_browser(self):
        known = SimpleNamespace(properties={"identifier": "opaque", "authTag": "good"},
                                addresses=[SimpleNamespace(full_ip="192.0.2.1")],
                                host="test-phone.local", port=1234)
        foreign = SimpleNamespace(properties={"identifier": "foreign", "authTag": "bad"},
                                  addresses=known.addresses, host="other.local", port=1234)
        browser = Browse([known, foreign])
        with patch.object(transport, "_remote_identities", return_value={"record-a": b"a" * 16}), \
                patch.object(transport, "iter_browse_remotepairing", return_value=browser), \
                patch.object(transport, "validate_auth_tag", side_effect=lambda _, identifier, tag: tag == "good"), \
                patch.object(transport.usbmux, "list_devices", new_callable=AsyncMock) as usb:
            self.assertEqual(await transport.discover("wifi"), [{"id": "record-a", "name": "test-phone.local"}])
        browser.aclose.assert_awaited_once()
        usb.assert_not_called()

    async def test_wifi_discovery_cancel_closes_browser(self):
        started = asyncio.Event()

        class WaitingBrowse(Browse):
            async def __anext__(self):
                started.set()
                await asyncio.Future()

        browser = WaitingBrowse([])
        with patch.object(transport, "_remote_identities", return_value={"record-a": b"a" * 16}), \
                patch.object(transport, "iter_browse_remotepairing", return_value=browser):
            task = asyncio.create_task(transport.discover("wifi"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        browser.aclose.assert_awaited_once()

    async def test_enable_wifi_pairs_only_selected_usb_and_closes_everything(self):
        client = lockdown()
        service = SimpleNamespace(connect=AsyncMock(side_effect=transport.RemotePairingCompletedError()),
                                  close=AsyncMock())
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a", "name": "A"}])), \
                patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)) as create, \
                patch.object(transport.RemotePairingLockdownService, "create", AsyncMock(return_value=service)), \
                patch.object(transport, "_remote_identities", return_value={"phone-a": b"a" * 16}):
            result = await transport.enable_wifi("phone-a")
        create.assert_awaited_once_with(serial="phone-a", connection_type="USB", autopair=True, pair_timeout=8)
        client.set_enable_wifi_connections.assert_awaited_once_with(True)
        service.connect.assert_awaited_once_with(autopair=True)
        self.assertEqual(result["remote_record_id"], "phone-a")
        self.assertTrue(result["wifi_enabled"])
        service.close.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_enable_wifi_failure_is_not_reported_as_ready(self):
        client = lockdown()
        client.get_enable_wifi_connections.return_value = False
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a"}])), \
                patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)), \
                patch.object(transport.RemotePairingLockdownService, "create", new_callable=AsyncMock) as create:
            with self.assertRaisesRegex(transport.TransportError, "未启用"):
                await transport.enable_wifi("phone-a")
        create.assert_not_called()
        client.close.assert_awaited_once()

    async def test_enable_wifi_cancel_closes_pairing_and_lockdown(self):
        started = asyncio.Event()
        client = lockdown()

        async def wait(**kwargs):
            started.set()
            await asyncio.Future()

        service = SimpleNamespace(connect=AsyncMock(side_effect=wait), close=AsyncMock())
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a"}])), \
                patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)), \
                patch.object(transport.RemotePairingLockdownService, "create", AsyncMock(return_value=service)):
            task = asyncio.create_task(transport.enable_wifi("phone-a"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        service.close.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_usb_provider_is_strict_and_transfers_ownership(self):
        client = lockdown()
        service = SimpleNamespace(close=AsyncMock())
        with patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)) as create, \
                patch.object(transport.CoreDeviceTunnelProxy, "create", AsyncMock(return_value=service)):
            provider = await transport._usb_provider("phone-a")
        create.assert_awaited_once_with(serial="phone-a", connection_type="USB", autopair=True, pair_timeout=8)
        self.assertIs(provider.lockdown, client)
        self.assertIs(provider.service, service)
        client.close.assert_not_called()

    async def test_usb_provider_failure_closes_lockdown(self):
        client = lockdown()
        with patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)), \
                patch.object(transport.CoreDeviceTunnelProxy, "create", AsyncMock(side_effect=OSError("failure"))):
            with self.assertRaises(OSError):
                await transport._usb_provider("phone-a")
        client.close.assert_awaited_once()

    async def test_legacy_usb_is_requested_only_for_known_old_version(self):
        client = lockdown("17.3")
        with patch.object(transport, "create_using_usbmux", AsyncMock(return_value=client)), \
                patch.object(transport.CoreDeviceTunnelProxy, "create", new_callable=AsyncMock) as create:
            with self.assertRaises(transport._LegacyUsbRequired):
                await transport._usb_provider("phone-a")
        client.close.assert_awaited_once()
        create.assert_not_called()

    async def test_wifi_provider_closes_failed_address_and_never_autopairs(self):
        failed = SimpleNamespace(connect=AsyncMock(side_effect=OSError("unreachable")), close=AsyncMock())
        good = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), encryption_key=b"test-only")
        device = {"id": "record", "addresses": ["192.0.2.1", "192.0.2.2"], "port": 1234}
        with patch.object(transport, "RemotePairingTunnelService", side_effect=[failed, good]), \
                patch.object(transport, "create_using_usbmux", new_callable=AsyncMock) as usb:
            provider = await transport._wifi_provider(device)
        self.assertIs(provider.service, good)
        self.assertIsNone(provider.lockdown)
        failed.close.assert_awaited_once()
        good.connect.assert_awaited_once_with(autopair=False)
        good.close.assert_not_called()
        usb.assert_not_called()

    async def test_open_context_closes_tunnel_when_consumer_fails(self):
        remote = rsd()
        tunnel = SimpleNamespace(aopen=AsyncMock(return_value=remote), aclose=AsyncMock(), rsd=remote)
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a"}])), \
                patch.object(transport, "UserspaceRsdTunnel", return_value=tunnel) as factory:
            with self.assertRaisesRegex(ValueError, "consumer"):
                async with transport.open_rsd("usb", "phone-a") as connection:
                    self.assertIs(connection.rsd, remote)
                    raise ValueError("consumer")
        self.assertTrue(connection.closed)
        self.assertIn("provider_factory", factory.call_args.kwargs)
        tunnel.aclose.assert_awaited_once()

    async def test_open_timeout_closes_tunnel_and_releases_session_guard(self):
        async def wait():
            await asyncio.Future()

        tunnel = SimpleNamespace(aopen=AsyncMock(side_effect=wait), aclose=AsyncMock())
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a"}])), \
                patch.object(transport, "UserspaceRsdTunnel", return_value=tunnel), \
                patch.object(transport, "OPEN_TIMEOUT", 0.01):
            with self.assertRaisesRegex(transport.TransportError, "超时"):
                async with transport.open_rsd("usb"):
                    self.fail("timed out connection opened")
        tunnel.aclose.assert_awaited_once()
        self.assertTrue(transport._session_guard.acquire(blocking=False))
        transport._session_guard.release()

    async def test_wifi_connection_uses_remote_record_without_usb_probe(self):
        selected = {"id": "remote-record", "addresses": ["192.0.2.1"], "port": 1234}
        remote = rsd()
        tunnel = SimpleNamespace(aopen=AsyncMock(return_value=remote), aclose=AsyncMock(), rsd=remote)
        with patch.object(transport, "_wifi_devices", AsyncMock(return_value=[selected])), \
                patch.object(transport, "_usb_devices", new_callable=AsyncMock) as usb, \
                patch.object(transport, "_wifi_provider", AsyncMock(return_value="provider")) as provider, \
                patch.object(transport, "UserspaceRsdTunnel", return_value=tunnel) as create:
            async with transport.open_rsd("wifi") as connection:
                self.assertEqual(connection.device_id, "remote-record")
                self.assertEqual(await create.call_args.kwargs["provider_factory"](), "provider")
        provider.assert_awaited_once_with(selected)
        usb.assert_not_called()
        tunnel.aclose.assert_awaited_once()

    async def test_heartbeat_uses_rsd_ping_and_detects_closed_transport(self):
        remote = rsd()
        handle = SimpleNamespace(rsd=remote)
        connection = transport.RsdConnection(remote, "wifi", "record", handle)
        with patch.object(transport.usbmux, "list_devices", new_callable=AsyncMock) as usb:
            self.assertTrue(await connection.is_alive())
            remote.service.ping.side_effect = OSError("gone")
            self.assertFalse(await connection.is_alive())
            handle.rsd = None
            self.assertFalse(await connection.is_alive())
        self.assertEqual(remote.service.ping.await_count, 2)
        usb.assert_not_called()

    async def test_legacy_fallback_begins_after_userspace_cleanup(self):
        order = []
        tunnel = SimpleNamespace(
            aopen=AsyncMock(side_effect=transport._LegacyUsbRequired()),
            aclose=AsyncMock(side_effect=lambda: order.append("closed-userspace")),
        )

        @asynccontextmanager
        async def legacy(device_id):
            self.assertEqual(device_id, "phone-a")
            order.append("opened-kernel")
            yield transport.RsdConnection(rsd(), "usb", device_id)

        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a"}])), \
                patch.object(transport, "UserspaceRsdTunnel", return_value=tunnel), \
                patch.object(transport, "_legacy_usb", legacy):
            async with transport.open_rsd("usb"):
                pass
        self.assertEqual(order, ["closed-userspace", "opened-kernel"])

    async def test_legacy_usb_without_admin_does_not_probe_or_elevate(self):
        with patch.object(transport, "_is_admin", return_value=False), \
                patch.object(transport, "get_core_device_tunnel_services", new_callable=AsyncMock) as discover:
            with self.assertRaisesRegex(transport.TransportError, "管理员"):
                async with transport._legacy_usb("phone-a"):
                    self.fail("unprivileged kernel tunnel opened")
        discover.assert_not_called()

    async def test_kernel_failure_closes_rsd_context_and_every_discovered_service(self):
        selected = SimpleNamespace(rsd=SimpleNamespace(udid="phone-a"), close=AsyncMock())
        unused = SimpleNamespace(rsd=SimpleNamespace(udid="phone-a"), close=AsyncMock())
        remote = rsd()
        remote.connect.side_effect = OSError("RSD failed")
        exits = []

        @asynccontextmanager
        async def kernel(service, protocol):
            self.assertIs(service, selected)
            self.assertEqual(protocol, transport.TunnelProtocol.TCP)
            try:
                yield SimpleNamespace(address="fd00::1", port=58783)
            finally:
                exits.append("closed")

        with patch.object(transport, "_is_admin", return_value=True), \
                patch.object(transport, "get_core_device_tunnel_services", AsyncMock(return_value=[selected, unused])) as find, \
                patch.object(transport, "start_tunnel", kernel), \
                patch.object(transport, "RemoteServiceDiscoveryService", return_value=remote):
            with self.assertRaisesRegex(OSError, "RSD failed"):
                async with transport._legacy_usb("phone-a"):
                    self.fail("failed RSD yielded")
        find.assert_awaited_once_with(udid="phone-a", bonjour_timeout=transport.DISCOVERY_TIMEOUT)
        remote.close.assert_awaited_once()
        selected.close.assert_awaited_once()
        unused.close.assert_awaited_once()
        self.assertEqual(exits, ["closed"])

    async def test_open_rejects_overlapping_sessions(self):
        remote = rsd()
        tunnel = SimpleNamespace(aopen=AsyncMock(return_value=remote), aclose=AsyncMock(), rsd=remote)
        with patch.object(transport, "_usb_devices", AsyncMock(return_value=[{"id": "phone-a"}])), \
                patch.object(transport, "UserspaceRsdTunnel", return_value=tunnel):
            async with transport.open_rsd("usb"):
                with self.assertRaisesRegex(transport.TransportError, "尚未关闭"):
                    async with transport.open_rsd("wifi"):
                        self.fail("overlapping userspace tunnel opened")
        tunnel.aopen.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
