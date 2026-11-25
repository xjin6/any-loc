"""Explicit USB / Wi-Fi transports for iOS developer services.

Discovery never enables wireless access or pairs a device. ``enable_wifi`` is
the separate, user-requested USB preparation step. One open context owns the
entire tunnel; neither transport starts an automatic tunneld monitor.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from pymobiledevice3 import usbmux
from pymobiledevice3.bonjour import iter_browse_remotepairing
from pymobiledevice3.exceptions import InvalidServiceError, RemotePairingCompletedError
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier
from pymobiledevice3.remote.common import TunnelProtocol
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.remote.tunnel_service import (
    PEER_ALT_IRK_KEY,
    CoreDeviceTunnelProxy,
    RemotePairingLockdownService,
    RemotePairingTunnelService,
    get_core_device_tunnel_services,
    start_tunnel,
    validate_auth_tag,
)
from pymobiledevice3.remote.userspace_tunnel import TunnelProvider, UserspaceRsdTunnel

log = logging.getLogger("any-loc.transport")
DISCOVERY_TIMEOUT = 3.0
USB_TIMEOUT = 10.0
OPEN_TIMEOUT = 25.0
CLOSE_TIMEOUT = 8.0
PING_TIMEOUT = 3.0
_session_guard = threading.Lock()


class TransportError(RuntimeError):
    """An actionable connection error suitable for the app's status panel."""


class _LegacyUsbRequired(Exception):
    pass


def _transport(value: str) -> str:
    if value == "bluetooth":
        raise TransportError("当前 iOS 开发者定位协议不支持蓝牙连接，请选择 USB 或 Wi-Fi。")
    if value not in ("usb", "wifi"):
        raise TransportError("连接方式必须是 USB 或 Wi-Fi。")
    return value


def _same_id(left: str, right: str) -> bool:
    return left.replace("-", "").lower() == right.replace("-", "").lower()


def _select(devices: list[dict], device_id: str | None, label: str) -> dict:
    if device_id:
        for device in devices:
            if _same_id(device["id"], device_id):
                return device
        raise TransportError(f"未找到所选的 {label} 设备，请刷新设备列表后重试。")
    if not devices:
        if label == "Wi-Fi":
            raise TransportError("未发现已配对的 Wi-Fi 设备。请先用 USB 准备无线连接，并让手机与电脑处于同一局域网、保持手机解锁。")
        raise TransportError("未发现 USB 设备。请接上数据线、解锁手机并信任这台电脑。")
    if len(devices) != 1:
        raise TransportError(f"发现多个 {label} 设备，请先选择要连接的设备。")
    return devices[0]


async def _close(resource: Any) -> None:
    """Finish bounded cleanup even when the owning operation is cancelled."""
    close = getattr(resource, "aclose", None) or resource.close
    task = asyncio.create_task(asyncio.wait_for(close(), CLOSE_TIMEOUT))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
    except Exception:
        log.warning("Closing iOS transport resource failed", exc_info=True)


async def _usb_devices() -> list[dict]:
    try:
        devices = await asyncio.wait_for(usbmux.list_devices(), USB_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise TransportError("读取 USB 设备超时，请检查数据线和 Apple Mobile Device 服务。") from exc
    except Exception as exc:
        raise TransportError("无法读取 USB 设备；Windows 请安装或启动 Apple Mobile Device Support。") from exc
    return list({d.serial: {"id": d.serial, "name": d.serial}
                 for d in devices if d.is_usb}.values())


async def _usb_details(device: dict) -> dict:
    lockdown = None
    try:
        lockdown = await asyncio.wait_for(create_using_usbmux(
            serial=device["id"], connection_type="USB", autopair=False,
        ), USB_TIMEOUT)
        values = lockdown.all_values
        return {**device, "name": values.get("DeviceName") or device["name"],
                "os_version": values.get("ProductVersion", "")}
    except Exception:
        # An untrusted/locked phone should still be selectable so Connect can
        # perform the normal trust flow. Discovery itself never auto-pairs.
        return device
    finally:
        if lockdown is not None:
            await _close(lockdown)


def _remote_identities() -> dict[str, bytes]:
    return {identifier: record[PEER_ALT_IRK_KEY]
            for identifier, _, record in iter_remote_pair_records_by_identifier()
            if isinstance(record.get(PEER_ALT_IRK_KEY), bytes)
            and len(record[PEER_ALT_IRK_KEY]) == 16}


async def _wifi_devices() -> list[dict]:
    identities = _remote_identities()
    if not identities:
        return []
    devices: dict[str, dict] = {}
    answers = iter_browse_remotepairing(timeout=DISCOVERY_TIMEOUT)

    async def collect() -> None:
        async for answer in answers:
            opaque_id = answer.properties.get("identifier")
            auth_tag = answer.properties.get("authTag")
            if not opaque_id or not auth_tag or not answer.addresses:
                continue
            for identifier, key in identities.items():
                try:
                    matched = validate_auth_tag(key, opaque_id, auth_tag)
                except (ValueError, TypeError):
                    matched = False
                if matched:
                    devices[identifier] = {
                        "id": identifier,
                        "name": answer.properties.get("name") or answer.host or identifier,
                        "addresses": [address.full_ip for address in answer.addresses],
                        "port": answer.port,
                    }
                    break

    try:
        await asyncio.wait_for(collect(), DISCOVERY_TIMEOUT + 2)
    except asyncio.TimeoutError:
        # A bounded scan may still have received useful advertisements.
        pass
    finally:
        await _close(answers)
    return list(devices.values())


async def discover(transport: str) -> list[dict]:
    """List USB devices or authenticated Wi-Fi advertisements, without pairing."""
    if _transport(transport) == "usb":
        devices = await _usb_devices()
        return list(await asyncio.gather(*(_usb_details(device) for device in devices)))
    return [{"id": device["id"], "name": device["name"]}
            for device in await _wifi_devices()]


async def enable_wifi(device_id: str | None = None) -> dict:
    """Enable wireless lockdown and create its RemotePairing record over USB."""
    selected = _select(await _usb_devices(), device_id, "USB")
    lockdown = service = None
    try:
        lockdown = await asyncio.wait_for(create_using_usbmux(
            serial=selected["id"], connection_type="USB", autopair=True,
            pair_timeout=8,
        ), USB_TIMEOUT)
        await asyncio.wait_for(lockdown.set_enable_wifi_connections(True), USB_TIMEOUT)
        if not await asyncio.wait_for(lockdown.get_enable_wifi_connections(), USB_TIMEOUT):
            raise TransportError("手机未启用无线连接，请保持解锁后重试。")
        service = await asyncio.wait_for(RemotePairingLockdownService.create(lockdown), USB_TIMEOUT)
        try:
            await asyncio.wait_for(service.connect(autopair=True), OPEN_TIMEOUT)
        except RemotePairingCompletedError:
            # The phone closes the control stream after successfully saving the
            # record. No developer tunnel needs to be opened for preparation.
            pass
        identifier = lockdown.udid or selected["id"]
        if identifier not in _remote_identities():
            raise TransportError("已启用无线连接，但远程配对记录缺少设备识别信息，尚不能连接 Wi-Fi。请重新配对后再试。")
        return {"id": identifier, "device_id": identifier,
                "remote_record_id": identifier, "wifi_enabled": True,
                "name": lockdown.all_values.get("DeviceName") or identifier}
    except InvalidServiceError as exc:
        raise TransportError("已启用无线连接，但此 iOS 未提供 USB 远程配对服务；请继续使用 USB。") from exc
    except asyncio.TimeoutError as exc:
        raise TransportError("准备无线连接超时，请保持 USB 连接、解锁手机并确认信任提示。") from exc
    finally:
        if service is not None:
            await _close(service)
        if lockdown is not None:
            await _close(lockdown)


async def _usb_provider(device_id: str) -> TunnelProvider:
    lockdown = await asyncio.wait_for(create_using_usbmux(
        serial=device_id, connection_type="USB", autopair=True, pair_timeout=8,
    ), USB_TIMEOUT)
    try:
        version = str(lockdown.all_values.get("ProductVersion", ""))
        match = re.match(r"(\d+)\.(\d+)", version)
        parsed = tuple(map(int, match.groups())) if match else ()
        if parsed and parsed < (17, 0):
            raise TransportError("当前定位后端要求 iOS 17 或更新版本。")
        if parsed and parsed < (17, 4):
            raise _LegacyUsbRequired()
        service = await asyncio.wait_for(CoreDeviceTunnelProxy.create(lockdown), USB_TIMEOUT)
        return TunnelProvider(service, lockdown)
    except BaseException:
        await _close(lockdown)
        raise


async def _wifi_provider(device: dict) -> TunnelProvider:
    last_error = None
    for address in device["addresses"]:
        service = RemotePairingTunnelService(
            device["id"], address, device["port"], connect_timeout=5,
        )
        try:
            await asyncio.wait_for(service.connect(autopair=False), USB_TIMEOUT)
            if service.encryption_key is None:
                raise TransportError("手机已不再信任此无线配对，请接上 USB 重新准备 Wi-Fi。")
            return TunnelProvider(service)
        except asyncio.CancelledError:
            await _close(service)
            raise
        except Exception as exc:
            await _close(service)
            last_error = exc
    raise TransportError("无法建立 Wi-Fi 配对连接，请保持手机解锁并检查局域网连接。") from last_error


def _is_admin() -> bool:
    if os.name == "nt":
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


@dataclass
class RsdConnection:
    rsd: Any
    transport: str
    device_id: str
    handle: Any = None
    closed: bool = False

    async def is_alive(self) -> bool:
        if self.closed or (self.handle is not None and self.handle.rsd is None):
            return False
        try:
            await asyncio.wait_for(self.rsd.service.ping(), PING_TIMEOUT)
            return True
        except Exception:
            return False


@asynccontextmanager
async def _legacy_usb(device_id: str):
    if not _is_admin():
        raise TransportError("iOS 17.0–17.3 的 USB 连接需要管理员权限，请以管理员身份重新打开 AnyLoc。")
    services = []
    connection = None
    stack = AsyncExitStack()
    try:
        # This explicitly targets the selected, physically attached USB device;
        # no Wi-Fi or usbmux-network monitor is running alongside it.
        services = await asyncio.wait_for(get_core_device_tunnel_services(
            udid=device_id, bonjour_timeout=DISCOVERY_TIMEOUT,
        ), OPEN_TIMEOUT)
        matching = [s for s in services if _same_id(s.rsd.udid, device_id)]
        if not matching:
            raise TransportError("没有找到所选手机的 USB 开发者隧道，请确认信任及开发者模式后重插数据线。")
        result = await asyncio.wait_for(stack.enter_async_context(start_tunnel(
            matching[0], protocol=TunnelProtocol.TCP,
        )), OPEN_TIMEOUT)
        rsd = RemoteServiceDiscoveryService((result.address, result.port))
        stack.push_async_callback(_close, rsd)
        await asyncio.wait_for(rsd.connect(), USB_TIMEOUT)
        connection = RsdConnection(rsd, "usb", device_id)
        yield connection
    finally:
        if connection is not None:
            connection.closed = True
        await _close(stack)
        for service in services:
            await _close(service)


@asynccontextmanager
async def open_rsd(transport: str, device_id: str | None = None):
    """Own exactly one selected transport until its location session ends."""
    mode = _transport(transport)
    if not _session_guard.acquire(blocking=False):
        raise TransportError("另一个 iOS 连接尚未关闭，请先断开后再切换连接方式。")
    try:
        selected = _select(await (_usb_devices() if mode == "usb" else _wifi_devices()),
                           device_id, "USB" if mode == "usb" else "Wi-Fi")
        provider = (lambda: _usb_provider(selected["id"])) if mode == "usb" else (lambda: _wifi_provider(selected))
        tunnel = UserspaceRsdTunnel(provider_factory=provider)
        connection = None
        legacy = False
        try:
            try:
                rsd = await asyncio.wait_for(tunnel.aopen(), OPEN_TIMEOUT)
            except _LegacyUsbRequired:
                legacy = True
            else:
                connection = RsdConnection(rsd, mode, selected["id"], tunnel)
                yield connection
        finally:
            if connection is not None:
                connection.closed = True
            await _close(tunnel)
        # Userspace cleanup resets the global tunnel factory before the legacy
        # kernel path is entered. These two kinds of tunnel never overlap.
        if legacy:
            async with _legacy_usb(selected["id"]) as connection:
                yield connection
    except asyncio.TimeoutError as exc:
        raise TransportError("连接 iOS 设备超时，请检查手机解锁状态、信任和所选连接方式。") from exc
    finally:
        _session_guard.release()
