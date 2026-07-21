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
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend_base import LocationBackend

log = logging.getLogger("any-loc")

# Dev mode: enables the browser live-reload endpoint + injected reload script.
# Turned on by launcher.py --dev (via env var) or ANYLOC_DEV=1.
DEV_MODE = os.environ.get("ANYLOC_DEV") == "1"

# When frozen by PyInstaller, data files (web/) are unpacked to sys._MEIPASS.
if getattr(sys, "frozen", False):
    _BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(_BASE_DIR, "web")



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
    def connect(self):
        """Kick off (or reuse) a device session. Non-blocking."""
        def _schedule():
            if self.state in ("connecting", "connected") and self._session_task \
                    and not self._session_task.done():
                return
            self.state = "connecting"
            self.error = None
            self._stop.clear()
            self._session_task = self.loop.create_task(self._session())
        self.loop.call_soon_threadsafe(_schedule)

    def set_location(self, lat: float, lon: float):
        """Store desired coordinate; applied by the mover on the open channel."""
        def _apply():
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
            **self.backend.status_extra(),
        }

    def shutdown(self):
        def _apply():
            self._stop.set()
            self._dirty.set()
        try:
            self.loop.call_soon_threadsafe(_apply)
        except Exception:
            pass

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
                        log.info("Pending clear applied on connect (real GPS restored).")
                    except Exception:
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
                        break

                    if self._want_clear:
                        self._want_clear = False
                        try:
                            await sess.clear()
                            self.applied = None
                            log.info("Location simulation cleared (real GPS restored).")
                        except Exception as e:
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

        except Exception as e:
            self.state = "error"
            self.error = str(e)
            log.error("Session error: %s", e)
        finally:
            if self.state != "error":
                self.state = "idle"
            self.device = {}
            log.info("Session ended (state=%s).", self.state)


# =============================================================================
# HTTP layer
# =============================================================================
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

    # ---- routes ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            return self._send_file("index.html")
        if path == "/api/status":
            return self._send_json(self.worker.status())
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
            self.worker.connect()
            return self._send_json({"ok": True, **self.worker.status()})
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
