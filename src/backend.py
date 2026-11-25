#!/usr/bin/env python3
"""
any-loc backend
===============
A tiny local server that lets you spoof your iPhone/iPad OR Android GPS from a
Google-Maps-style web UI. Cross-platform (Windows + macOS).

How it works:
  - The web UI streams (lat, lon) updates; we apply the latest one on an open
    channel to the device. Keeping the channel open is what makes joystick
    movement smooth (reconnecting on every call is far too slow to drive).
  - The device-specific part lives behind a LocationBackend (see backend_base.py):
      * iOS     — pymobiledevice3 tunnel + Apple DVT LocationSimulation
                  (ios_backend.py). Needs elevation for the tunnel.
      * Android — bundled adb + the system `cmd location` test-provider
                  (android_backend.py). No elevation needed.
    DeviceWorker owns everything generic (asyncio loop, state machine, the
    latest-wins mover loop) and just calls the backend for the device touchpoints.

Everything here is Python stdlib; the device backends pull in their own deps.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import webbrowser
from urllib.parse import parse_qs, urlsplit
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend_base import LocationBackend

log = logging.getLogger("any-loc")

# Dev mode: enables the browser live-reload endpoint + injected reload script.
# Turned on by launcher.py --dev (via env var) or ANYLOC_DEV=1.
DEV_MODE = os.environ.get("ANYLOC_DEV") == "1"

# When frozen by PyInstaller, data files (web/) are unpacked to sys._MEIPASS.
# From source, the code lives in src/ but web/ sits at the repo root (one level
# up), so fall back to ../web when there's no web/ next to this file.
if getattr(sys, "frozen", False):
    _BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    WEB_DIR = os.path.join(_BASE_DIR, "web")
else:
    _SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    _cand = os.path.join(_SRC_DIR, "web")
    if os.path.isdir(_cand):
        WEB_DIR = _cand                                    # web/ beside this file
    else:
        WEB_DIR = os.path.join(os.path.dirname(_SRC_DIR), "web")  # repo-root web/



# =============================================================================
# Device worker: owns an asyncio loop on its own thread, holds the DVT channel
# open, and applies the latest requested coordinate.
# =============================================================================
class DeviceWorker:
    def __init__(self, backend: LocationBackend):
        self.backend = backend

        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

        # These asyncio primitives are loop-agnostic at construction in py3.10+.
        self._dirty = asyncio.Event()   # "there is a new target / clear request"
        self._stop = asyncio.Event()
        self._want_clear = False
        self._session_task = None
        self._control_lock = asyncio.Lock()
        self._requests = set()
        self._pending_lock = threading.Lock()
        self._pending_connections = 0
        self._closing = False
        self._clear_error = None

        # Signals that the session has fully finished tearing down. A
        # threading.Event (not asyncio) because shutdown() is called from
        # non-loop threads and must BLOCK on it. Starts SET: with no session
        # running there is nothing to wait for.
        self._done = threading.Event()
        self._done.set()

        # Public-ish state (plain attribute reads from HTTP threads; GIL-safe enough).
        self.state = "idle"             # idle | connecting | connected | error
        self.error = None
        self.device = {}                # neutral schema (see backend_base)
        self.target = None              # (lat, lon) desired
        self.applied = None             # (lat, lon) last applied on device
        self.devmode = {"state": "idle", "msg": ""}  # setup status (iOS DevMode / Android adb)


    # ---- lifecycle ----------------------------------------------------------
    def start(self):
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    # ---- called from HTTP threads ------------------------------------------
    def connect(self, connection=None, reconnect=False):
        """Serialize transport changes after the old device has been cleared."""
        if connection is not None:
            validate = getattr(self.backend, "validate_connection", None)
            if validate is None:
                raise ValueError("This device does not support iOS connection modes.")
            connection = validate(connection)
        if self._closing:
            raise RuntimeError("The application is shutting down.")
        with self._pending_lock:
            self._pending_connections += 1
            self._done.clear()

        def _schedule():
            if self._closing:
                with self._pending_lock:
                    self._pending_connections -= 1
                self._mark_done_if_idle()
                return
            task = self.loop.create_task(self._connect_serial(connection, reconnect))
            self._requests.add(task)
            task.add_done_callback(self._connection_finished)
        self.loop.call_soon_threadsafe(_schedule)

    def _mark_done_if_idle(self):
        with self._pending_lock:
            if not self._pending_connections and not self._requests and (self._session_task is None or self._session_task.done()):
                self._done.set()

    def _connection_finished(self, task):
        with self._pending_lock:
            self._pending_connections -= 1
        self._requests.discard(task)
        self._mark_done_if_idle()

    async def _stop_current(self):
        task = self._session_task
        if task is None or task.done():
            return
        self._clear_error = None
        self._want_clear = True
        self._stop.set()
        self._dirty.set()
        if self.state == "connecting":
            task.cancel()  # no location channel yet; cancel discovery/handshake
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=12)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
        except asyncio.TimeoutError:
            raise RuntimeError("旧连接尚未关闭，请稍后重试；未切换设备。") from None
        if self._clear_error:
            raise RuntimeError("无法确认旧设备已恢复真实定位；请重新连接旧设备并恢复定位后再切换。")

    async def _connect_serial(self, connection, reconnect):
        try:
            async with self._control_lock:
                if self._closing:
                    return
                active = self._session_task is not None and not self._session_task.done()
                current = getattr(self.backend, "connection", None)
                changed = connection is not None and connection != current
                if changed and self._clear_error:
                    raise RuntimeError("旧设备尚未确认恢复真实定位。请先重连旧设备并恢复定位，再切换连接。")
                if active and not (changed or reconnect):
                    return
                if active:
                    await self._stop_current()
                if self._closing:
                    return
                if changed or reconnect or self._clear_error:
                    # A newly selected device must never receive the previous target.
                    self.target = self.applied = None
                    self._want_clear = bool(self._clear_error)
                    self._dirty.clear()
                if connection is not None:
                    self.backend.configure_connection(connection)
                    self.devmode = {"state": "idle", "msg": ""}
                self.state, self.error = "connecting", None
                self._stop.clear()
                self._done.clear()
                self._session_task = self.loop.create_task(self._session())
                self._session_task.add_done_callback(lambda _: self._mark_done_if_idle())
        except Exception as e:
            self.state, self.error = "error", str(e)
            log.exception("Connection change failed")

    def ios_operation(self, operation, *args):
        """Run discovery/setup on the device loop, never concurrently with a switch."""
        if getattr(self.backend, "platform", None) != "ios":
            raise ValueError("iOS connection settings are unavailable in Android mode.")
        if self._closing:
            raise RuntimeError("The application is shutting down.")

        async def run():
            task = asyncio.current_task()
            self._requests.add(task)
            try:
                async with self._control_lock:
                    if self._closing:
                        raise RuntimeError("The application is shutting down.")
                    if operation == "enable_wifi":
                        await self._stop_current()
                        self.target = self.applied = None
                        self._want_clear = False
                        self.state, self.error = "idle", None
                    return await getattr(self.backend, operation)(*args)
            finally:
                self._requests.discard(task)
                self._mark_done_if_idle()

        self._done.clear()
        future = asyncio.run_coroutine_threadsafe(run(), self.loop)
        try:
            return future.result(timeout=45)
        except TimeoutError:
            future.cancel()
            raise RuntimeError("操作超时。请解锁 iPhone、检查连接后重试。") from None

    def set_location(self, lat: float, lon: float):
        """Store desired coordinate; applied by the mover on the open channel."""
        def _apply():
            # Once shutdown has been requested, ignore further movement: the UI
            # streams ~10 POSTs/second while the joystick is held, so one that
            # was already in flight when the window closed would otherwise land
            # here and clear _want_clear — cancelling the shutdown clear and
            # leaving the phone on the fake location.
            if self._stop.is_set():
                return
            self.target = (lat, lon)
            self._want_clear = False
            self._dirty.set()
        self.loop.call_soon_threadsafe(_apply)

    def clear(self):
        def _apply():
            self._want_clear = True
            self._dirty.set()
        self.loop.call_soon_threadsafe(_apply)

    def status(self):
        return {
            "state": self.state,
            "error": self.error,
            "device": self.device,
            "target": self.target,
            "applied": self.applied,
            "devmode": self.devmode,
            "platform": getattr(self.backend, "platform", "unknown"),
            "connection_pending": bool(self._pending_connections),
            **self.backend.status_extra(),
        }

    def shutdown(self, timeout: float = 8.0, clear_first: bool = True) -> bool:
        """
        Stop the session and BLOCK until the device has actually been released.

        This used to be fire-and-forget: it only did call_soon_threadsafe() and
        returned, so the interpreter exited and killed the daemon worker thread
        before the session's async `finally` ever ran. The result was a phone
        left frozen at the fake coordinate — Android's test providers were never
        removed, and iOS does not auto-clear when the DVT channel closes (its
        close() is a documented no-op). Closing the app therefore did NOT give
        you your real GPS back.

        So the contract is now: **when this returns True, the phone is back on
        real GPS.** Callers (the launcher's shutdown_all) depend on that, which
        is why they must call this FIRST, before anything else is torn down.

        clear_first — ask the session to clear the simulated location on its way
                      out (leave it True unless you deliberately want the fake
                      location to persist).
        timeout      — seconds to wait for the teardown. A real teardown takes
                       ~1.4-1.7s; the default leaves generous headroom.

        Returns False if the session did not finish in time (the caller should
        log it and carry on rather than hang forever — a wedged USB channel must
        not be able to keep the window open).
        """
        # Nothing running (or never started): there is nothing to release.
        self._closing = True
        if self._done.is_set() or not self._thread.is_alive():
            return True

        def _apply():
            if clear_first:
                self._want_clear = True
            self._stop.set()
            self._dirty.set()
            if self.state == "connecting" and self._session_task:
                self._session_task.cancel()
            for task in list(self._requests):
                task.cancel()

        try:
            self.loop.call_soon_threadsafe(_apply)
        except Exception:
            # The loop is already gone; nothing left to wait for.
            return True

        if not self._done.wait(timeout):
            log.warning("Worker shutdown timed out after %.1fs "
                        "(device may still be simulating).", timeout)
            return False
        return True

    # ---- the session (runs on the worker loop) ------------------------------
    async def _device_alive(self) -> bool:
        """Quick health check: is our device still present?"""
        try:
            return await self.backend.is_alive()
        except Exception:
            return False

    async def _session(self):
        try:
            async with self.backend.session() as sess:
                try:
                    await self._run_session(sess)
                finally:
                    # THE LAST-CHANCE CLEAR — this is the guarantee behind
                    # "closing the app restores your real GPS".
                    #
                    # _run_session() has SEVERAL exits: the `while` header
                    # re-check, a `break` on error, and two `return`s for a
                    # vanished device. Only one of them used to do the clear, so
                    # stopping the worker while the loop happened to be inside a
                    # health probe or an apply() skipped it entirely — and
                    # shutdown() still returned True, so the app logged "real GPS
                    # restored=True" with the phone still frozen at the fake
                    # coordinate. (Reproduced deterministically with a slow
                    # is_alive().)
                    #
                    # Doing it HERE, inside the `async with`, means every exit
                    # path runs it while the channel is still open — which iOS
                    # requires, since its DVT close() is a documented no-op.
                    if self._want_clear:
                        self._want_clear = False
                        try:
                            await sess.clear()
                            self.applied = None
                            self._clear_error = None
                            log.info("Cleared on shutdown (real GPS restored).")
                        except Exception as e:
                            self._clear_error = str(e)
                            log.exception("clear on shutdown failed")
        except Exception as e:
            self.state = "error"
            self.error = str(e)
            log.error("Session error: %s", e)
        finally:
            if self.state != "error":
                self.state = "idle"
            self.device = {}
            log.info("Session ended (state=%s).", self.state)
            # Release shutdown()'s waiter LAST: once this is set, the caller is
            # entitled to assume the device has been released and may start
            # tearing down the tunnel the session was talking through.
            # The task's done callback also accounts for queued connection changes.
            if self._session_task is not asyncio.current_task():
                self._mark_done_if_idle()

    async def _run_session(self, sess):
        """The mover loop. Runs inside _session()'s open channel."""
        self.device = self.backend.device_info()
        self.state = "connected"
        self.error = None
        log.info("Location channel open. Ready to spoof.")

        # If a clear was requested while we were (re)connecting — e.g. the
        # user hit "Reset real GPS" from an error state, which triggers a
        # reconnect — honour it right away so the button actually works.
        if self._want_clear:
            self._want_clear = False
            try:
                await sess.clear()
                self.applied = None
                self._clear_error = None
                log.info("Pending clear applied on connect (real GPS restored).")
            except Exception as e:
                self._clear_error = str(e)
                log.exception("pending clear failed")

        # Apply an initial target immediately if one is already queued.
        if self.target is not None:
            self._dirty.set()

        while not self._stop.is_set():
            # Wait for a new target, but wake up every ~3s to run a health
            # check so we notice the device being unplugged in real time.
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                # heartbeat: is the device still reachable?
                if not await self._device_alive():
                    self.state = "idle"
                    self.error = None
                    self.device = {}
                    log.info("Device disconnected. Back to idle.")
                    return
                continue
            self._dirty.clear()
            if self._stop.is_set():
                # The clear happens in _session()'s finally, which covers this
                # exit and every other one.
                break

            if self._want_clear:
                self._want_clear = False
                try:
                    await sess.clear()
                    self.applied = None
                    self._clear_error = None
                    log.info("Location simulation cleared (real GPS restored).")
                except Exception as e:
                    self._clear_error = str(e)
                    self.state = "error"
                    self.error = f"clear failed: {e}"
                    log.exception("clear failed")
                    break
                continue

            target = self.target
            if target is None:
                continue
            try:
                await sess.apply(float(target[0]), float(target[1]))
                self.applied = target
            except Exception as e:
                # A failed apply almost always means the device went away.
                if not await self._device_alive():
                    self.state = "idle"; self.error = None; self.device = {}
                    log.info("Device disconnected during set. Back to idle.")
                    return
                self.state = "error"
                self.error = "set failed"
                log.exception("set failed")
                break


# =============================================================================
# HTTP layer
# =============================================================================

# -----------------------------------------------------------------------------
# Approximate "where is this computer" via IP geolocation.
#
# Used ONLY to pick the map's opening view. It is never sent to the device: a
# coarse IP guess is a reasonable place to start panning from, and a terrible
# thing to silently spoof your phone to.
#
# Cached for the process lifetime so opening the app costs at most one lookup,
# and the whole thing is best-effort -- any failure just leaves the caller on
# its built-in default.
# -----------------------------------------------------------------------------
_IPLOC_CACHE = None
_IPLOC_LOCK = threading.Lock()


def _lookup_ip_location() -> dict:
    """Best-effort coarse location for this machine. {} when unavailable."""
    global _IPLOC_CACHE
    with _IPLOC_LOCK:
        if _IPLOC_CACHE is not None:
            return _IPLOC_CACHE

    import urllib.request
    result = {}
    # Two providers, tried in order: both are key-less and return lat/lon.
    # ipwho.is is materially more precise (city-level) than geojs, which has
    # been observed to answer with a point ~600km from the real one -- so it
    # goes first and geojs is only the fallback.
    providers = (
        ("https://ipwho.is/", lambda d: (d.get("latitude"), d.get("longitude"),
                                         d.get("city") or d.get("region") or "")),
        ("https://get.geojs.io/v1/ip/geo.json",
         lambda d: (d.get("latitude"), d.get("longitude"), d.get("city") or "")),
    )
    for url, pick in providers:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AnyLoc"})
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            lat, lon, city = pick(data)
            lat, lon = float(lat), float(lon)
            # Reject a nonsense answer rather than pointing the map at null island.
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                continue
            if lat == 0 and lon == 0:
                continue
            result = {"lat": lat, "lon": lon, "city": city or ""}
            log.info("Startup map view from IP geolocation: %s (%.4f, %.4f)",
                     city or "?", lat, lon)
            break
        except Exception as e:
            log.debug("ip geolocation via %s failed: %s: %s", url, type(e).__name__, e)

    if not result:
        log.info("IP geolocation unavailable; map opens on its default view.")
    with _IPLOC_LOCK:
        _IPLOC_CACHE = result
    return result


class Handler(BaseHTTPRequestHandler):
    worker: DeviceWorker = None  # injected

    # keep the console clean
    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---- helpers ----
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, relpath):
        # prevent path traversal
        safe = os.path.normpath(relpath).lstrip("\\/")
        full = os.path.join(WEB_DIR, safe)
        if not os.path.abspath(full).startswith(os.path.abspath(WEB_DIR)) \
                or not os.path.isfile(full):
            self.send_error(404, "Not found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        # In dev mode, inject a tiny live-reload script into index.html so the
        # browser auto-refreshes when you edit web/ files (no packaging needed).
        if DEV_MODE and full.lower().endswith("index.html"):
            snippet = (
                b"<script>(function(){let last=null;setInterval(async()=>{"
                b"try{const r=await fetch('/api/livereload');const j=await r.json();"
                b"if(last!==null&&j.mtime>last){location.reload();}last=j.mtime;"
                b"}catch(e){}},1000);})();</script>"
            )
            if b"</body>" in data:
                data = data.replace(b"</body>", snippet + b"</body>", 1)
            else:
                data = data + snippet
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if DEV_MODE:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _logs_payload(self):
        """
        Serve the in-memory log ring for the UI's log panel.

        This is the ONLY way to see what the app is doing in the windowed
        (console=False) build: there is no console, sys.stdout is None, and
        print()/logging output is silently discarded. So the ring is not a
        nicety — it is the diagnostic channel.

        `after` is a cursor, not an index: pass back the `next` from the previous
        response and you get only what has appeared since. Everything is parsed
        defensively (garbage query strings must not 500 the log viewer — that is
        precisely when you need it).
        """
        from urllib.parse import parse_qs, urlsplit

        qs = parse_qs(urlsplit(self.path).query)

        def _int(name, default, lo, hi):
            try:
                v = int(qs.get(name, [default])[0])
            except (TypeError, ValueError, IndexError):
                return default
            return max(lo, min(hi, v))

        after = _int("after", 0, 0, 2 ** 62)
        limit = _int("limit", 200, 1, 2000)

        try:
            import applog
            entries, nxt = applog.get_ring().snapshot(after=after, limit=limit)
        except Exception as e:
            # A broken log viewer must never take the app down with it.
            return {"entries": [], "next": after, "error": f"{type(e).__name__}: {e}"}
        return {"entries": list(entries), "next": nxt}

    # ---- routes ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/ios/devices":
            transport = parse_qs(urlsplit(self.path).query).get("transport", ["usb"])[0]
            try:
                devices = self.worker.ios_operation("discover", transport)
                return self._send_json({"ok": True, "devices": devices})
            except ValueError as e:
                return self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                log.exception("iOS discovery failed")
                return self._send_json({"ok": False, "error": str(e)}, 503)
        if path == "/" or path == "/index.html":
            return self._send_file("index.html")
        if path == "/api/status":
            return self._send_json(self.worker.status())
        if path == "/api/logs":
            return self._send_json(self._logs_payload())
        if path == "/api/iploc":
            # Coarse startup map view only -- never applied to the device.
            return self._send_json(_lookup_ip_location())
        if path == "/api/livereload":
            # Dev-only: report the newest mtime across web/ so the page can
            # auto-refresh when a file changes. Cheap to poll.
            newest = 0.0
            try:
                for root, _dirs, files in os.walk(WEB_DIR):
                    for fn in files:
                        try:
                            newest = max(newest, os.path.getmtime(os.path.join(root, fn)))
                        except OSError:
                            pass
            except Exception:
                pass
            return self._send_json({"mtime": newest, "dev": DEV_MODE})
        if path.startswith("/api/"):
            return self.send_error(404, "Unknown API")
        return self._send_file(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/connect":
            data = self._read_json()
            try:
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                reconnect = data.get("reconnect", False)
                if not isinstance(reconnect, bool):
                    raise ValueError("reconnect must be a boolean.")
                connection = None
                if "transport" in data or "device_id" in data:
                    connection = {"transport": data.get("transport", "usb"),
                                  "device_id": data.get("device_id")}
                self.worker.connect(connection, reconnect=reconnect)
            except (ValueError, RuntimeError) as e:
                return self._send_json({"ok": False, "error": str(e)}, 400)
            return self._send_json({"ok": True, **self.worker.status()})
        if path == "/api/ios/wifi/enable":
            data = self._read_json()
            try:
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                device_id = data.get("device_id")
                if device_id is not None and (not isinstance(device_id, str) or len(device_id) > 256):
                    raise ValueError("Invalid device_id.")
                result = self.worker.ios_operation("enable_wifi", device_id or None)
                return self._send_json({**result, "ok": True})
            except ValueError as e:
                return self._send_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                log.exception("Wi-Fi preparation failed")
                return self._send_json({"ok": False, "error": str(e)}, 503)
        if path == "/api/clear":
            self.worker.clear()
            return self._send_json({"ok": True})
        if path == "/api/set":
            data = self._read_json()
            try:
                lat = float(data["lat"])
                lon = float(data["lon"])
            except (KeyError, TypeError, ValueError):
                return self._send_json({"ok": False, "error": "lat/lon required"}, 400)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return self._send_json({"ok": False, "error": "out of range"}, 400)
            self.worker.set_location(lat, lon)
            return self._send_json({"ok": True})
        return self.send_error(404, "Unknown API")


def main():
    from ios_backend import IosBackend, TUNNELD_DEFAULT_ADDRESS

    ap = argparse.ArgumentParser(description="any-loc backend")
    ap.add_argument("--host", default="127.0.0.1", help="web UI bind host")
    ap.add_argument("--port", type=int, default=8765, help="web UI port")
    ap.add_argument("--tunneld-host", default=TUNNELD_DEFAULT_ADDRESS[0])
    ap.add_argument("--tunneld-port", type=int, default=TUNNELD_DEFAULT_ADDRESS[1])
    ap.add_argument("--no-browser", action="store_true", help="don't auto-open browser")
    ap.add_argument("--no-connect", action="store_true",
                    help="don't auto-connect to device on startup")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # quiet noisy libs unless -v
    if not args.verbose:
        for noisy in ("pymobiledevice3", "urllib3", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    worker = DeviceWorker(IosBackend(args.tunneld_host, args.tunneld_port))
    worker.start()
    if not args.no_connect:
        worker.connect()

    Handler.worker = worker
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)

    url = f"http://{args.host}:{args.port}/"
    print("\n" + "=" * 60)
    print("  any-loc is running")
    print(f"  Open:  {url}")
    print(f"  Tunneld: {args.tunneld_host}:{args.tunneld_port}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60 + "\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[any-loc] Shutting down ...")
    finally:
        worker.shutdown()
        httpd.shutdown()


if __name__ == "__main__":
    main()
