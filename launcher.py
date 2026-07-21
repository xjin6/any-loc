#!/usr/bin/env python3
"""
any-loc all-in-one launcher (becomes AnyLoc.exe on Windows / AnyLoc.app on macOS)
=================================================================================
Double-click behaviour:
  1. Re-launch itself with elevated rights if not already privileged — the
     tunnel needs Administrator (Windows) / root (macOS).
       * Windows -> UAC prompt (ShellExecuteW "runas").
       * macOS   -> opens Terminal and runs itself under `sudo` (inline password
                    prompt); if already in a terminal, just re-execs via sudo.
  2. Start the web UI server + device worker on a background thread.
  3. Open the browser.
  4. Run pymobiledevice3's tunneld in the MAIN thread (it blocks on uvicorn.run).

One window (which you can minimize), one elevation prompt. No batch/shell files.
"""
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
import webbrowser

# ---- platform ---------------------------------------------------------------
# The core app is byte-for-byte identical on both OSes; only *elevation* and a
# couple of native dialogs differ (UAC on Windows, sudo/root on macOS).
IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

# ctypes.windll only exists on Windows; import it lazily where needed so the
# module imports cleanly on macOS/Linux.
if IS_WINDOWS:
    import ctypes

# The bundled console defaults to the legacy code page (cp1252 on many Windows
# installs), which crashes on Chinese status/log text. Force UTF-8 on the
# standard streams before anything prints.
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---- import the web server + worker from backend.py -------------------------
# (backend.py lives next to this file / is bundled by PyInstaller)
try:
    import backend
except Exception:
    # When frozen, ensure the bundle dir is importable
    sys.path.insert(0, os.path.dirname(os.path.abspath(sys.executable)))
    import backend

from http.server import ThreadingHTTPServer

WEB_HOST = "127.0.0.1"
WEB_PORT = 8765


# =============================================================================
# Elevation (cross-platform)
#   Windows -> Administrator via UAC.
#   macOS   -> root via sudo (the tunnel creates a TUN interface, which needs
#              root, exactly like admin is needed on Windows).
# =============================================================================
def is_admin() -> bool:
    """True if we already have the elevated rights the tunnel needs."""
    if IS_WINDOWS:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    # POSIX (macOS): root is uid 0.
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _native_error(message: str, title: str = "AnyLoc") -> None:
    """Show a native, blocking error dialog (best-effort; falls back to print)."""
    if IS_WINDOWS:
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
            return
        except Exception:
            pass
    elif IS_MACOS:
        try:
            script = (
                f'display dialog {json_str(message)} with title {json_str(title)} '
                f'buttons {{"OK"}} default button "OK" with icon stop'
            )
            subprocess.run(["osascript", "-e", script], check=False)
            return
        except Exception:
            pass
    print(f"\n[{title}] {message}\n", file=sys.stderr)


def json_str(s: str) -> str:
    """
    Quote a Python string as an AppleScript string literal.

    ensure_ascii=False is REQUIRED: AppleScript's `do script`/`display dialog`
    reject Python's \\uXXXX escapes, so a non-ASCII path (e.g. a Chinese folder
    name — this project can live under exactly such a path) would otherwise make
    the Terminal-elevation osascript fail with a syntax error and silently break
    the whole macOS launch. Emitting the raw UTF-8 characters works correctly.
    """
    import json
    return json.dumps(s, ensure_ascii=False)


def relaunch_as_admin() -> None:
    """Re-run this program elevated, then exit the current (non-elevated) copy."""
    if IS_WINDOWS:
        return _relaunch_windows_uac()
    if IS_MACOS:
        return _relaunch_macos_sudo()
    # Other POSIX: best-effort sudo re-exec in place.
    _relaunch_posix_sudo()


def _relaunch_windows_uac() -> None:
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in [os.path.abspath(__file__)] + sys.argv[1:])

    # ShellExecuteW with "runas" triggers the UAC prompt
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if rc <= 32:
        # user declined UAC, or it failed
        _native_error(
            "AnyLoc needs Administrator rights to create the tunnel to your iPhone.\n\n"
            "Please allow the prompt (or right-click the app > Run as administrator).",
            "AnyLoc",
        )
    sys.exit(0)


def _elevation_argv() -> list:
    """The command that re-runs *this* program, frozen (.app/.exe) or as .py."""
    # Finder sometimes passes a "-psn_0_12345" process-serial-number arg to an
    # .app; strip it so it isn't forwarded through the sudo re-launch.
    passthru = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if getattr(sys, "frozen", False):
        return [sys.executable] + passthru
    return [sys.executable, os.path.abspath(__file__)] + passthru


def _relaunch_posix_sudo() -> None:
    """Re-exec in place under sudo (works when a controlling terminal exists)."""
    argv = _elevation_argv()
    os.execvp("sudo", ["sudo", "-E", "--"] + argv)


def _relaunch_macos_sudo() -> None:
    """
    Get root on macOS.

    Two situations:
      * Launched from a Terminal (there's a TTY) -> just re-exec via `sudo`;
        the password prompt appears right here in the same window.
      * Double-clicked in Finder (the .app has NO terminal) -> open Terminal.app
        and run ourselves under `sudo` there, so the user gets a visible window
        with an inline password prompt and the live log — the macOS analog of
        the Windows console window you keep open while spoofing.
    """
    argv = _elevation_argv()

    # Has a controlling terminal? Then elevate in place — simplest and cleanest.
    if sys.stdin and sys.stdin.isatty():
        try:
            os.execvp("sudo", ["sudo", "-E", "--"] + argv)
        except Exception as e:
            _native_error(f"Could not run sudo: {e}", "AnyLoc")
            sys.exit(1)
        return

    # No terminal (Finder double-click): open Terminal.app running `sudo <us>`.
    cmd = " ".join(shlex.quote(a) for a in argv)
    # `exec sudo ...` so the tunnel process replaces the shell (closing the
    # Terminal window then cleanly stops AnyLoc — mirrors the Windows UX).
    shell_line = f"clear; echo '--- AnyLoc (macOS) ---'; exec sudo {cmd}"
    osa = (
        'tell application "Terminal"\n'
        f"  do script {json_str(shell_line)}\n"
        "  activate\n"
        "end tell\n"
    )
    try:
        subprocess.run(["osascript", "-e", osa], check=True)
    except Exception as e:
        _native_error(
            "AnyLoc needs root to create the tunnel to your iPhone, and could not "
            f"open Terminal automatically.\n\n{e}\n\n"
            "You can start it manually in Terminal with:\n"
            f"   sudo {cmd}",
            "AnyLoc",
        )
    sys.exit(0)


# =============================================================================
# Web UI server thread (reuses backend.DeviceWorker + backend.Handler)
# =============================================================================
def start_web_server(worker: "backend.DeviceWorker") -> ThreadingHTTPServer:
    backend.Handler.worker = worker
    httpd = ThreadingHTTPServer((WEB_HOST, WEB_PORT), backend.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name="web-server")
    t.start()
    return httpd


def open_browser(url: str) -> None:
    """
    Open the default browser at `url`.

    On macOS we usually run elevated via `sudo`, and asking root to open the
    browser tends to spawn a root-owned browser (or nothing) instead of using
    the logged-in user's session. When we detect that case, drop back to the
    original user ($SUDO_USER) via `open` so the page appears in *their* browser.
    """
    if IS_MACOS:
        sudo_user = os.environ.get("SUDO_USER")
        try:
            euid_root = os.geteuid() == 0
        except AttributeError:
            euid_root = False
        if euid_root and sudo_user and sudo_user != "root":
            try:
                subprocess.Popen(["sudo", "-u", sudo_user, "open", url])
                return
            except Exception:
                pass  # fall through to webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass


# =============================================================================
# Main
# =============================================================================
def run_dev(log) -> None:
    """
    Developer mode — fast UI iteration with live-reload (edit web/ → browser
    auto-refreshes in ~1s), NO packaging needed.

    Two ways to run:
      * normal        -> serves the UI only (perfect for pure front-end work,
                         but the tunnel is skipped so you can't reach a device)
      * elevated      -> UI live-reload PLUS the real tunnel + Developer Mode
                         watcher, so you can test spoofing on a real iPhone
    To get the full flow:
      * Windows: right-click dev.bat > Run as administrator
      * macOS:   run `sudo ./dev.command` (or `sudo python3 launcher.py --dev`)
    """
    os.environ["ANYLOC_DEV"] = "1"
    # re-evaluate backend's DEV_MODE now that the env var is set
    backend.DEV_MODE = True

    dev_host, dev_port = "127.0.0.1", 8766
    admin = is_admin()

    # Platform-specific hint for how to get the real-device flow in dev mode.
    if IS_WINDOWS:
        _elevate_hint = "关掉重来，右键 dev.bat > 以管理员身份运行"
    else:
        _elevate_hint = "关掉重来，改用 sudo ./dev.command（或 sudo python3 launcher.py --dev）"

    print("\n" + "=" * 62)
    print("  AnyLoc — 开发模式 (DEV)")
    print(f"  网页:      http://{dev_host}:{dev_port}/")
    print("  热重载:    改 web/ 里的文件 → 浏览器自动刷新（~1秒）")
    if admin:
        print("  真机隧道:  ✓ 已启用 —— 可连 iPhone 实测定位")
    else:
        print("  真机隧道:  ✗ 未启用（当前非管理员/非 root）")
        print(f"             要实测定位：{_elevate_hint}")
    print("  改 Python 后端: Ctrl+C 停，再重新运行开发脚本（dev.bat / dev.command）")
    print("  按 Ctrl+C 停止。")
    print("=" * 62 + "\n")

    worker = _ios_worker()
    worker.start()

    # bind the web server on the dev port
    backend.Handler.worker = worker
    try:
        httpd = ThreadingHTTPServer((dev_host, dev_port), backend.Handler)
    except OSError as e:
        log.error("无法启动开发服务器 %s:%s (%s)。可能已在运行？", dev_host, dev_port, e)
        return
    threading.Thread(target=httpd.serve_forever, daemon=True, name="web-dev").start()

    url = f"http://{dev_host}:{dev_port}/"
    threading.Timer(0.8, lambda: open_browser(url)).start()

    if admin:
        # full flow available — web server (with live-reload) is already running
        # above; now bring up the device worker, dev-mode watcher, and tunnel.
        worker.connect()
        DevModeWatcher(worker).start()
        log.info("以管理员/root 运行：启动隧道（连接 iPhone）…")
        from pymobiledevice3.tunneld.server import TunneldRunner
        from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS
        try:
            TunneldRunner.create(TUNNELD_DEFAULT_ADDRESS[0], TUNNELD_DEFAULT_ADDRESS[1])
        except KeyboardInterrupt:
            pass
        finally:
            worker.shutdown(); httpd.shutdown()
    else:
        # UI-only: just keep serving until Ctrl+C
        log.info("界面已就绪（仅网页）。做前端改动 → 浏览器会自动刷新。Ctrl+C 退出。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("退出开发模式。")
            worker.shutdown(); httpd.shutdown()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("pymobiledevice3", "urllib3", "asyncio", "uvicorn", "uvicorn.error",
                  "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("any-loc")

    # --dev: developer mode. Fast iteration without packaging.
    #   * no elevation (so it starts instantly; tunnel/devmode only run if you
    #     happen to already be admin/root)
    #   * separate port (8766) so it never clashes with a running release build
    #   * live-reload: edit anything in web/ and the browser auto-refreshes
    # Use this while working on the UI. Package to .exe/.app only for release.
    if "--dev" in sys.argv:
        return run_dev(log)

    # --devmode-test: run only the DevModeWatcher against a connected device for
    # ~12s and print its status transitions, then exit. Proves the frozen bundle
    # can actually drive Developer Mode setup. Needs no elevation/tunnel.
    if "--devmode-test" in sys.argv:
        import time
        class _W:
            devmode = {"state": "idle", "msg": ""}
        w = _W()
        dm = DevModeWatcher(w)
        dm.start()
        last = None
        for _ in range(12):
            time.sleep(1)
            if w.devmode != last:
                print(f"[devmode-test] {w.devmode['state']}: {w.devmode['msg']}")
                last = dict(w.devmode)
            if w.devmode["state"] in ("enabled", "error"):
                break
        print("[devmode-test] FINAL:", w.devmode["state"])
        sys.exit(0 if w.devmode["state"] in ("enabled", "waiting", "trust", "reveal_done") else 1)

    # --selftest: boot the web stack WITHOUT elevation or tunnel, prove the
    # frozen import graph + web server work, then exit. Used to validate builds.
    if "--selftest" in sys.argv:
        print("[selftest] booting web stack (no elevation, no tunnel) ...")
        worker = _ios_worker()
        worker.start()
        try:
            httpd = start_web_server(worker)
        except OSError as e:
            print(f"[selftest] FAIL: web server: {e}")
            sys.exit(2)
        import urllib.request
        ok = True
        try:
            for path in ("/", "/app.js", "/api/status"):
                with urllib.request.urlopen(f"http://{WEB_HOST}:{WEB_PORT}{path}", timeout=5) as r:
                    code = r.getcode()
                    print(f"[selftest] GET {path} -> {code}")
                    ok = ok and (code == 200)
            # verify tunnel components import (the risky frozen bit)
            from pymobiledevice3.remote.module_imports import verify_tunnel_imports
            from pymobiledevice3.tunneld.server import TunneldRunner  # noqa
            print(f"[selftest] verify_tunnel_imports -> {verify_tunnel_imports()}")
        except Exception as e:
            print(f"[selftest] FAIL: {type(e).__name__}: {e}")
            ok = False
        finally:
            try:
                worker.shutdown(); httpd.shutdown()
            except Exception:
                pass
        print("[selftest] RESULT:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)

    # 1) elevate — the tunnel needs Administrator (Windows) / root (macOS).
    if not is_admin():
        if IS_WINDOWS:
            log.info("Requesting administrator rights (needed for the tunnel) ...")
        else:
            log.info("Requesting root via sudo (needed for the tunnel) ...")
        relaunch_as_admin()
        return

    _priv = "administrator" if IS_WINDOWS else "root"
    print("\n" + "=" * 62)
    print(f"  AnyLoc  —  running as {_priv}")
    print("  This one window runs everything. You can MINIMIZE it,")
    print("  but keep it open while spoofing. Close it to stop.")
    print("=" * 62 + "\n")

    # 2) tunnel import sanity
    from pymobiledevice3.remote.module_imports import verify_tunnel_imports
    if not verify_tunnel_imports():
        _native_error(
            "The tunnel components failed to load. Try reinstalling:\n"
            "   pip install -U pymobiledevice3",
            "AnyLoc")
        return

    # 3) start web UI + device worker (background)
    worker = _ios_worker()
    worker.start()
    worker.connect()  # will keep retrying via the UI's Connect button too
    try:
        httpd = start_web_server(worker)
    except OSError as e:
        _native_error(
            f"Could not start the web UI on {WEB_HOST}:{WEB_PORT}.\n{e}\n\n"
            "Is AnyLoc already running?", "AnyLoc")
        return

    url = f"http://{WEB_HOST}:{WEB_PORT}/"
    log.info("Web UI:  %s", url)
    threading.Timer(1.2, lambda: open_browser(url)).start()

    # 3.5) Continuously watch for a device and set up Developer Mode in the
    #      background. NOT one-shot: a new phone may be plugged in / unlocked /
    #      trusted long after launch, and the Trust dialog only appears once we
    #      try to pair — so we keep retrying instead of checking a single time.
    dm = DevModeWatcher(worker)
    dm.start()

    # 4) run tunneld in the MAIN thread (blocks). It powers get_tunneld_devices().
    log.info("Starting tunnel (this is what talks to the iPhone) ...")
    from pymobiledevice3.tunneld.server import TunneldRunner
    from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS
    try:
        TunneldRunner.create(TUNNELD_DEFAULT_ADDRESS[0], TUNNELD_DEFAULT_ADDRESS[1])
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Tunnel stopped. Shutting down.")
        try:
            worker.shutdown()
            httpd.shutdown()
        except Exception:
            pass


class DevModeWatcher:
    """
    Background watcher that keeps trying to make Developer Mode *available* over
    plain USB (no tunnel). Runs on its own thread + event loop, polling every few
    seconds.

    Why a loop and not a single check: on a NEW phone the user must (a) plug in,
    (b) unlock, and (c) tap "Trust" — and the Trust dialog only pops when we try
    to pair. That dance usually happens *after* launch, so a one-shot check almost
    always misses it. We retry until the device is paired, then REVEAL the
    Developer Mode option in Settings.

    Design decision: we only REVEAL the option (make it appear). We deliberately
    do NOT auto-enable it — enabling forces a device reboot, which is alarming and
    confusing when a PC app triggers it silently. Turning it on is left to the
    user via the Settings toggle: safe, visible, in their control. Once the user
    enables it (and reboots), we detect that and report "enabled".

    Reports human-readable progress into worker.devmode so the web UI can show it.
    """

    def __init__(self, worker):
        self.worker = worker
        self.log = logging.getLogger("any-loc.devmode")
        self._loop = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="devmode-watch")
        self._stop = False           # set True to end the watch loop
        self._revealed = False       # have we already revealed the option?
        self._set("waiting", "等待设备… 请插上 iPhone 并解锁")

    def _set(self, state, msg):
        # state: waiting | trust | revealing | reveal_done | enabled | error
        try:
            self.worker.devmode = {"state": state, "msg": msg}
        except Exception:
            pass
        self.log.info("[devmode] %s: %s", state, msg)

    def start(self):
        self._thread.start()

    def _run(self):
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._watch())
        except Exception as e:
            self._set("error", f"检查开发者模式出错: {e}")

    async def _watch(self):
        import asyncio
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.amfi import AmfiService
        from pymobiledevice3.usbmux import list_devices

        announced_trust = False
        # Poll forever so the status reflects REALITY in real time: unplug the
        # phone -> back to "waiting"; enable dev mode -> "enabled". Never latch.
        while not self._stop:
            try:
                devs = await list_devices()
            except Exception:
                devs = []

            if not devs:
                self._set("waiting", "等待设备… 请用数据线插上 iPhone 并解锁")
                announced_trust = False
                self._revealed = False
                await asyncio.sleep(2)
                continue

            # A device is present — try to open a paired lockdown session.
            try:
                lockdown = await create_using_usbmux(autopair=True, pair_timeout=8)
            except Exception:
                if not announced_trust:
                    self._set("trust", "请在 iPhone 上点「信任这台电脑」并输入密码")
                    announced_trust = True
                await asyncio.sleep(2)
                continue

            # Paired. Check developer-mode status.
            try:
                already = await lockdown.get_developer_mode_status()
            except Exception:
                already = False

            if already:
                self._set("enabled", "开发者模式已开启 ✓ 可以连接了")
                self._revealed = False
                await asyncio.sleep(3)   # keep re-checking; do NOT latch/stop
                continue

            # OFF -> reveal the Settings row once per connection.
            if not self._revealed:
                self._set("revealing", "正在让「开发者模式」选项出现在设置里…")
                try:
                    amfi = AmfiService(lockdown)
                    await amfi.reveal_developer_mode_option_in_ui()
                    self._revealed = True
                except Exception as e:
                    self._set("error", f"无法自动显示开发者模式选项：{e}")
                    await asyncio.sleep(3)
                    continue

            # Revealed — now it's the user's move. Clear, non-scary guidance.
            self._set("reveal_done",
                      "已让「开发者模式」出现在设置里。请在 iPhone 上手动打开："
                      "设置 > 隐私与安全性 > 开发者模式 > 打开开关"
                      "（手机会重启，重启后在锁屏点「打开」确认）。开启后这里会自动继续。")

            # Keep polling: once the user enables + reboots, the check above flips
            # to "enabled". Also detects unplug -> "waiting".
            await asyncio.sleep(3)


def __tunneld_addr():
    from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS
    return TUNNELD_DEFAULT_ADDRESS


def _ios_worker():
    """Build a DeviceWorker wired to the iOS (pymobiledevice3) backend."""
    from ios_backend import IosBackend
    return backend.DeviceWorker(IosBackend(*__tunneld_addr()))


if __name__ == "__main__":
    main()
