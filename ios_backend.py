#!/usr/bin/env python3
"""
ios_backend.py — the iOS device backend for any-loc (pymobiledevice3).

This is the original iOS logic, lifted verbatim out of backend.py's DeviceWorker
and wrapped behind the LocationBackend interface. Nothing about the iOS behavior
changes: same tunneld query, same Developer Disk Image auto-mount, same long-lived
DVT `LocationSimulation` channel, same latest-wins `loc.set` / `loc.clear`.

iOS flow (iOS 17+):
  1. `pymobiledevice3 remote tunneld` (elevated) exposes the device on
     127.0.0.1:49151.
  2. Ask tunneld for the device, mount the DDI if needed, then open Apple's DVT
     LocationSimulation channel and KEEP IT OPEN.
  3. Apply the latest (lat, lon) on the open channel.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from backend_base import LocationBackend, LocationSession

# ---- pymobiledevice3 (verified against v9.32.0) -----------------------------
from pymobiledevice3.tunneld.api import (
    TUNNELD_DEFAULT_ADDRESS,
    get_tunneld_devices,
)
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import (
    LocationSimulation,
)
from pymobiledevice3.services.mobile_image_mounter import auto_mount
from pymobiledevice3.exceptions import (
    AlreadyMountedError,
    TunneldConnectionError,
)

log = logging.getLogger("any-loc")


class _IosSession(LocationSession):
    """Wraps an open DVT LocationSimulation channel."""

    def __init__(self, loc: LocationSimulation):
        self._loc = loc

    async def apply(self, lat: float, lon: float) -> None:
        await self._loc.set(float(lat), float(lon))

    async def clear(self) -> None:
        await self._loc.clear()


class IosBackend(LocationBackend):
    platform = "ios"

    def __init__(self, tunneld_host: str, tunneld_port: int):
        self.tunneld_addr = (tunneld_host, tunneld_port)
        self._device: dict = {}

    # ---- LocationBackend interface -----------------------------------------
    def device_info(self) -> dict:
        return self._device

    def status_extra(self) -> dict:
        return {"tunneld": f"{self.tunneld_addr[0]}:{self.tunneld_addr[1]}"}

    async def is_alive(self) -> bool:
        """Quick health check: is our device still present via tunneld?"""
        try:
            rsds = await get_tunneld_devices(self.tunneld_addr)
        except Exception:
            return False
        my = (self._device or {}).get("id")
        if not rsds:
            return False
        if not my:
            return True
        return any(getattr(r, "udid", None) == my for r in rsds)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[LocationSession]:
        log.info("Querying tunneld at %s:%s ...", *self.tunneld_addr)
        try:
            rsds = await get_tunneld_devices(self.tunneld_addr)
        except TunneldConnectionError:
            raise RuntimeError(
                "Cannot reach tunneld. Start AnyLoc with elevated rights "
                "(Windows: allow the UAC prompt; macOS: enter your password "
                "for sudo) and make sure the iPhone is plugged in and trusted."
            )
        if not rsds:
            raise RuntimeError(
                "Tunnel is up but no device found. Unlock the iPhone, tap 'Trust', "
                "and enable Developer Mode (Settings > Privacy & Security > Developer Mode)."
            )

        rsd = rsds[0]
        self._device = {
            "platform": "ios",
            "id": getattr(rsd, "udid", ""),
            "model": getattr(rsd, "product_type", ""),
            "os_version": getattr(rsd, "product_version", ""),
            "name": getattr(rsd, "name", "") or "",
        }
        log.info("Device: %s (iOS %s)", self._device["model"],
                 self._device["os_version"])

        # Mount the Developer Disk Image (idempotent). Non-fatal if it fails,
        # because it may already be mounted from a previous run.
        try:
            log.info("Ensuring Developer Disk Image is mounted ...")
            await auto_mount(rsd)
            log.info("DDI mounted.")
        except AlreadyMountedError:
            log.info("DDI already mounted.")
        except Exception as e:
            log.warning("auto_mount failed (continuing anyway): %s", e)

        # Open the DVT location channel and keep it open for the session.
        async with DvtProvider(rsd) as dvt, LocationSimulation(dvt) as loc:
            log.info("LocationSimulation channel open. Ready to spoof.")
            try:
                yield _IosSession(loc)
            finally:
                self._device = {}
