#!/usr/bin/env python3
"""
android_backend.py — the Android device backend for any-loc (bundled adb).

Unlike iOS (Apple's DVT channel via pymobiledevice3), Android needs no on-device
app and no elevation. From Android 11 the system ships `cmd location`, a shell
command that manages "test providers" — exactly the mock-location mechanism, but
driven straight from adb's shell user (uid 2000) once it's granted the
MOCK_LOCATION app-op.

The command sequence (verified on a real Huawei JLN-AL00, Android 12 / EMUI 14):

    adb shell cmd location set-location-enabled true            # some OEMs (Huawei) default OFF
    adb shell appops set --uid 2000 android:mock_location allow # Huawei needs --uid, not `set shell`
    adb shell appops set shell android:mock_location allow      # stock Android form (harmless extra)
    adb shell cmd location providers add-test-provider <p>
    adb shell cmd location providers set-test-provider-enabled <p> true
    adb shell cmd location providers set-test-provider-location <p> --location <lat>,<lon>
  clear:
    adb shell cmd location providers set-test-provider-enabled <p> false
  release:
    adb shell cmd location providers remove-test-provider <p>

We register three providers (gps, network, fused) so apps that read any of them —
including Google Play "fused" location — follow the spoof.

Requires: Developer Options + USB debugging ON, and the PC authorized on the phone
("Allow USB debugging"). Android 11+ (older devices lack `cmd location`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from backend_base import LocationBackend, LocationSession

log = logging.getLogger("any-loc")

# Providers we drive. gps+network+fused maximizes app compatibility.
PROVIDERS = ("gps", "network", "fused")

# adb's shell user is uid 2000 on all Android devices.
SHELL_UID = "2000"


# ---------------------------------------------------------------------------
# Locate the bundled adb (adb.exe on Windows, adb on macOS). When frozen by
# PyInstaller it's unpacked next to web/ at sys._MEIPASS; from source it's in
# vendor/<os>/ in the project root.
# ---------------------------------------------------------------------------
def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def find_adb() -> str:
    base = _base_dir()
    is_win = os.name == "nt"
    exe = "adb.exe" if is_win else "adb"
    candidates = [
        os.path.join(base, exe),                         # frozen: _MEIPASS root
        os.path.join(base, "vendor", "win" if is_win else "mac", exe),  # from source
    ]
    for c in candidates:
        if os.path.isfile(c):
            if not is_win:
                try:
                    os.chmod(c, 0o755)  # _MEIPASS may drop the exec bit
                except OSError:
                    pass
            return c
    # Last resort: hope it's on PATH.
    return exe


class AdbError(RuntimeError):
    pass


class _AndroidSession(LocationSession):
    """An open mock-location session: providers registered + enabled."""

    def __init__(self, backend: "AndroidBackend"):
        self._b = backend

    async def apply(self, lat: float, lon: float) -> None:
        # latest-wins: push the newest coord to every provider.
        loc = f"{float(lat)},{float(lon)}"
        for p in PROVIDERS:
            await self._b._adb_shell(
                "cmd", "location", "providers",
                "set-test-provider-location", p, "--location", loc,
            )

    async def clear(self) -> None:
        # Disable (but keep) the providers so the phone falls back to real GPS.
        for p in PROVIDERS:
            try:
                await self._b._adb_shell(
                    "cmd", "location", "providers",
                    "set-test-provider-enabled", p, "false",
                )
            except AdbError:
                pass


class AndroidBackend(LocationBackend):
    platform = "android"

    def __init__(self, serial: Optional[str] = None, adb_path: Optional[str] = None):
        # Optional explicit device serial (when several are attached).
        self._serial = serial
        self._adb_path = adb_path or find_adb()
        self._device: dict = {}

    # ---- low-level adb helpers ---------------------------------------------
    def _argv(self, *args: str) -> list:
        base = [self._adb_path]
        if self._serial:
            base += ["-s", self._serial]
        return base + list(args)

    async def _adb(self, *args: str, check: bool = True) -> str:
        """Run `adb <args>` and return stdout (stripped). Raise AdbError on fail."""
        argv = self._argv(*args)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        so = (out or b"").decode("utf-8", "replace").strip()
        se = (err or b"").decode("utf-8", "replace").strip()
        if check and proc.returncode != 0:
            raise AdbError(f"adb {' '.join(args)} failed: {se or so}")
        # `cmd location` prints its Java stack trace to STDOUT with rc 0 on the
        # SecurityException path, so treat that as an error too.
        if check and ("SecurityException" in so or "Exception occurred" in so):
            raise AdbError(so.splitlines()[0] if so else "adb command exception")
        return so

    async def _adb_shell(self, *args: str, check: bool = True) -> str:
        return await self._adb("shell", *args, check=check)

    async def _getprop(self, name: str) -> str:
        try:
            return await self._adb_shell("getprop", name, check=False)
        except Exception:
            return ""

    # ---- device discovery ---------------------------------------------------
    async def list_devices(self) -> list:
        """Return [(serial, state), ...] from `adb devices`."""
        out = await self._adb("devices", check=False)
        devs = []
        for line in out.splitlines()[1:]:  # skip "List of devices attached"
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            devs.append((serial.strip(), state.strip()))
        return devs

    # ---- LocationBackend interface -----------------------------------------
    def device_info(self) -> dict:
        return self._device

    async def is_alive(self) -> bool:
        try:
            devs = await self.list_devices()
        except Exception:
            return False
        ready = [s for s, st in devs if st == "device"]
        if not ready:
            return False
        if self._serial:
            return self._serial in ready
        return True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[LocationSession]:
        # 1) Confirm a ready, authorized device.
        devs = await self.list_devices()
        ready = [s for s, st in devs if st == "device"]
        unauth = [s for s, st in devs if st == "unauthorized"]
        if not ready:
            if unauth:
                raise RuntimeError(
                    "Android device found but not authorized. On the phone, tap "
                    "'Allow USB debugging' (check 'Always allow from this computer')."
                )
            raise RuntimeError(
                "No Android device found. Plug it in with a USB cable, enable "
                "Developer Options > USB debugging, and authorize this computer."
            )
        if not self._serial:
            self._serial = ready[0]

        # 2) Read device identity for the UI.
        model = await self._getprop("ro.product.model")
        brand = await self._getprop("ro.product.brand")
        release = await self._getprop("ro.build.version.release")
        sdk = await self._getprop("ro.build.version.sdk")
        name = (f"{brand} {model}".strip() if brand else model) or self._serial
        self._device = {
            "platform": "android",
            "id": self._serial,
            "model": model or self._serial,
            "os_version": release or "?",
            "name": name,
        }
        log.info("Android device: %s (Android %s, SDK %s)", name, release, sdk)

        # 3) Sanity: `cmd location` must exist (Android 11+).
        try:
            sdk_n = int(sdk) if sdk.isdigit() else 0
        except Exception:
            sdk_n = 0
        if sdk_n and sdk_n < 30:
            raise RuntimeError(
                f"This Android version (SDK {sdk_n}) is too old — AnyLoc needs "
                "Android 11+ (which has the system 'cmd location' command)."
            )

        # 4) Turn the master location switch on (some OEMs default it OFF).
        await self._adb_shell("cmd", "location", "set-location-enabled", "true", check=False)

        # 5) Grant MOCK_LOCATION to the adb shell user. Send BOTH forms:
        #    --uid (required on Huawei/EMUI) and the package form (stock Android).
        granted = False
        for opargs in (["--uid", SHELL_UID], ["shell"]):
            try:
                await self._adb_shell("appops", "set", *opargs,
                                      "android:mock_location", "allow", check=True)
                granted = True
            except AdbError as e:
                log.debug("appops set %s failed: %s", opargs, e)
        if not granted:
            raise RuntimeError(
                "Could not grant mock-location permission via adb. Your ROM may "
                "restrict this; check Developer Options for a 'Select mock location "
                "app' or 'USB debugging (Security settings)' toggle."
            )

        # 6) Register + enable all three test providers.
        registered = []
        try:
            for p in PROVIDERS:
                try:
                    await self._adb_shell("cmd", "location", "providers",
                                          "add-test-provider", p)
                    await self._adb_shell("cmd", "location", "providers",
                                          "set-test-provider-enabled", p, "true")
                    registered.append(p)
                except AdbError as e:
                    log.warning("provider %s not available (skipping): %s", p, e)
            if not registered:
                raise RuntimeError(
                    "Could not register any location test provider. The device "
                    "rejected mock location (some locked-down ROMs do this)."
                )
            log.info("Mock providers active: %s", ", ".join(registered))
            yield _AndroidSession(self)
        finally:
            # Tear down: disable + remove every provider we registered.
            for p in registered:
                for sub in (["set-test-provider-enabled", p, "false"],
                            ["remove-test-provider", p]):
                    try:
                        await self._adb_shell("cmd", "location", "providers", *sub,
                                              check=False)
                    except Exception:
                        pass
            self._device = {}
            log.info("Android mock providers removed (real GPS restored).")
