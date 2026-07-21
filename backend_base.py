#!/usr/bin/env python3
"""
backend_base.py — the device-backend abstraction for any-loc.

any-loc drives one thing: "open a channel to a device, keep it open, and push the
latest (lat, lon) at it; clear to restore the real GPS." That shape is identical
for iOS (Apple DVT LocationSimulation over a RemoteXPC tunnel) and Android (the
system `cmd location` test-provider over adb). Only the device-specific calls
differ.

`DeviceWorker` (in backend.py) owns everything generic — the asyncio loop on its
own thread, the state machine, the latest-wins mover loop — and talks to a
`LocationBackend` for the ~8 device touchpoints. Two implementations plug in:

  - IosBackend      (ios_backend.py)      — pymobiledevice3 / tunneld / DVT
  - AndroidBackend  (android_backend.py)  — bundled adb / cmd location

A backend exposes an async `session()` context manager. Entering it connects to
the device and yields a `LocationSession` with `apply()` / `clear()`; the channel
stays open for the whole `async with` block (that's what makes the joystick
smooth). Leaving it tears the channel down.
"""
from __future__ import annotations

import abc
from contextlib import asynccontextmanager
from typing import AsyncIterator


class LocationSession(abc.ABC):
    """An open, live channel to one device. Lives for one `session()` block."""

    @abc.abstractmethod
    async def apply(self, lat: float, lon: float) -> None:
        """Push a coordinate to the device (latest-wins; called at ~10 Hz)."""

    @abc.abstractmethod
    async def clear(self) -> None:
        """Stop simulating and restore the device's real GPS."""


class LocationBackend(abc.ABC):
    """
    Device-family driver. One instance per run; `DeviceWorker` calls into it.

    Contract:
      - `platform` is a stable id ("ios" | "android") surfaced to the UI.
      - `session()` is an async context manager. On enter it must connect and
        populate `device_info()`; it yields a `LocationSession`. Raise a plain
        `RuntimeError` with a human-readable message if the device isn't ready
        (DeviceWorker shows it as the error state).
      - `is_alive()` is a cheap health probe used as a heartbeat while connected.
      - `status_extra()` adds backend-specific fields to the /api/status payload.
    """

    #: "ios" | "android"
    platform: str = "unknown"

    @asynccontextmanager
    async def session(self) -> AsyncIterator[LocationSession]:  # pragma: no cover
        """Connect, yield a live LocationSession, tear down on exit.

        Subclasses override this with an `@asynccontextmanager` implementation.
        """
        raise NotImplementedError
        yield  # pragma: no cover  (marks this as an async generator)

    @abc.abstractmethod
    def device_info(self) -> dict:
        """Current device as a neutral dict. Populated during `session()` enter.

        Neutral schema (both backends fill these):
            platform     "ios" | "android"
            id           udid (iOS) / serial (Android)
            model        product_type (iOS) / ro.product.model (Android)
            os_version   iOS version / Android version
            name         friendly device name (may be "")
        Empty dict when not connected.
        """

    @abc.abstractmethod
    async def is_alive(self) -> bool:
        """True if the previously-connected device is still reachable."""

    def status_extra(self) -> dict:
        """Extra fields merged into /api/status (e.g. iOS 'tunneld'). Default none."""
        return {}
