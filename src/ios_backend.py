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

# ---- pymobiledevice3 (verified against v11.45.0, iOS 27.2) -------------------
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

    def __init__(self, tunneld_host: str, tunneld_port: int,
                 userspace: bool = False):
        self.tunneld_addr = (tunneld_host, tunneld_port)
        self._device: dict = {}
        # When True, establish the tunnel IN THIS PROCESS with no root at all
        # (pymobiledevice3's userspace PyTCP stack) instead of talking to a
        # privileged tunneld. See _userspace_rsd() for the trade-offs.
        self.userspace = userspace
        self._userspace_tunnel = None

    # ---- LocationBackend interface -----------------------------------------
    def device_info(self) -> dict:
        return self._device

    def status_extra(self) -> dict:
        if self.userspace:
            return {"tunnel_mode": "userspace"}
        return {"tunneld": f"{self.tunneld_addr[0]}:{self.tunneld_addr[1]}",
                "tunnel_mode": "tunneld"}

    async def is_alive(self) -> bool:
        """Quick health check: is our device still present?"""
        if self.userspace:
            # The in-process tunnel has no registry to query, and "our handle is
            # not None" would be true for the whole session — the heartbeat could
            # then NEVER fire, so unplugging the phone would leave the UI pinned
            # at "connected". Ask usbmuxd whether the device is still on the bus
            # instead; that is the same question, needs no privileges, and is
            # cheap enough for a 3s poll.
            if self._userspace_tunnel is None:
                return False
            try:
                from pymobiledevice3 import usbmux
                devices = await usbmux.list_devices()
            except Exception:
                # usbmuxd unreachable: report alive rather than tearing down a
                # working session over a transient probe failure.
                return True
            if not devices:
                return False
            my = (self._device or {}).get("id")
            if not my:
                return True
            return any(getattr(d, "serial", None) == my for d in devices)
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
    async def _userspace_rsd(self):
        """
        Establish the tunnel in-process, with NO root.

        pymobiledevice3 10.x can build the tunnel's network interface as a pure
        Python (PyTCP) userspace stack instead of a kernel `utun` device. The
        kernel device is the ONLY reason this app ever needed Administrator/root,
        so this path removes the elevation prompt entirely.

        Requirements and limits (from pymobiledevice3's own docs):
          * iOS 17.4+ over USB — uses the CoreDeviceProxy lockdown service.
            iOS 17.0-17.3 has no such service and must use the kernel tunnel; we
            pass remotepairing_fallback=False so those devices fail fast here and
            the caller can fall back to tunneld rather than silently landing on
            the Wi-Fi-only bonjour path (which races `remoted` on macOS).
          * One tunnel per process — PyTCP's stack is a process-global singleton.
          * The device address is in-process only; no external tool can use it.
            That is fine: WE are the only consumer.
        """
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

        log.info("Establishing the in-process tunnel (no root needed) ...")
        tunnel = UserspaceRsdTunnel(autopair=True, remotepairing_fallback=False)
        rsd = await tunnel.aopen()
        self._userspace_tunnel = tunnel
        try:
            yield rsd
        finally:
            self._userspace_tunnel = None
            try:
                await tunnel.aclose()
            except Exception as e:
                log.warning("userspace tunnel close failed: %s: %s",
                            type(e).__name__, e)

    @asynccontextmanager
    async def _tunneld_rsd(self):
        """Get the device from a privileged `tunneld` (the original path)."""
        log.info("Querying tunneld at %s:%s ...", *self.tunneld_addr)
        try:
            rsds = await get_tunneld_devices(self.tunneld_addr)
        except TunneldConnectionError:
            raise RuntimeError(
                "Cannot reach tunneld. Make sure AnyLoc is running with the "
                "rights the tunnel needs, and that the iPhone is plugged in "
                "and trusted."
            )
        if not rsds:
            raise RuntimeError(
                "Tunnel is up but no device found. Unlock the iPhone, tap 'Trust', "
                "and enable Developer Mode (Settings > Privacy & Security > Developer Mode)."
            )
        yield rsds[0]

    @asynccontextmanager
    async def session(self) -> AsyncIterator[LocationSession]:
        # Two ways to reach the phone; everything after this is identical.
        source = self._userspace_rsd() if self.userspace else self._tunneld_rsd()
        async with source as rsd:
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
