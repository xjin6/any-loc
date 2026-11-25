#!/usr/bin/env python3
"""iOS location simulation over an explicitly selected USB or Wi-Fi transport."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from backend_base import LocationBackend, LocationSession
from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import auto_mount
from pymobiledevice3.exceptions import AlreadyMountedError

import ios_transport

log = logging.getLogger("any-loc")


class _IosSession(LocationSession):
    def __init__(self, loc: LocationSimulation):
        self._loc = loc

    async def apply(self, lat: float, lon: float) -> None:
        await self._loc.set(float(lat), float(lon))

    async def clear(self) -> None:
        await self._loc.clear()


class IosBackend(LocationBackend):
    platform = "ios"

    def __init__(self, tunneld_host=None, tunneld_port=None, *, userspace=False):
        # Keep the old constructor callable by launcher/source integrations. Each
        # session now owns its transport; a background auto-tunneld is not used.
        self.connection = {"transport": "usb", "device_id": None}
        self._device = {}
        self._connection_handle = None

    @staticmethod
    def validate_connection(connection):
        transport = connection.get("transport", "usb")
        if transport not in ("usb", "wifi"):
            raise ValueError("仅支持有线 USB 和 Wi-Fi；iOS 定位调试暂不支持蓝牙连接。")
        device_id = connection.get("device_id")
        if device_id is not None:
            if not isinstance(device_id, str) or len(device_id) > 256:
                raise ValueError("Invalid device_id.")
            device_id = device_id.strip() or None
        return {"transport": transport, "device_id": device_id}

    def configure_connection(self, connection):
        if self._connection_handle is not None:
            raise RuntimeError("Close the current iOS session before changing transport.")
        self.connection = self.validate_connection(connection)

    async def discover(self, transport):
        self.validate_connection({"transport": transport})
        return await ios_transport.discover(transport)

    async def enable_wifi(self, device_id=None):
        return await ios_transport.enable_wifi(device_id)

    def device_info(self):
        return dict(self._device)

    def status_extra(self):
        return {"connection": dict(self.connection),
                "transport": self.connection["transport"],
                "connection_modes": ["usb", "wifi"]}

    async def is_alive(self):
        handle = self._connection_handle
        return handle is not None and await handle.is_alive()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[LocationSession]:
        selected = dict(self.connection)
        async with ios_transport.open_rsd(selected["transport"], selected["device_id"]) as handle:
            self._connection_handle = handle
            rsd = handle.rsd
            try:
                self._device = {
                    "platform": "ios", "id": getattr(rsd, "udid", ""),
                    "model": getattr(rsd, "product_type", ""),
                    "os_version": getattr(rsd, "product_version", ""),
                    "name": getattr(rsd, "name", "") or "",
                    "transport": selected["transport"],
                }
                log.info("Device: %s (iOS %s), transport=%s", self._device["model"],
                         self._device["os_version"], selected["transport"])
                try:
                    await auto_mount(rsd)
                    log.info("Developer Disk Image mounted.")
                except AlreadyMountedError:
                    pass
                except Exception as e:
                    log.warning("auto_mount failed (continuing): %s", e)
                async with DvtProvider(rsd) as dvt, LocationSimulation(dvt) as loc:
                    log.info("LocationSimulation channel open.")
                    yield _IosSession(loc)
            finally:
                self._device = {}
                self._connection_handle = None
