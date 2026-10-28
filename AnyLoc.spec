# -*- mode: python ; coding: utf-8 -*-
r"""
Cross-platform build spec for any-loc (Windows single .exe + macOS .app bundle).

Parameterized via the environment variable ANYLOC_VARIANT:
  - "test"  -> name=AnyLocTest,  no admin manifest  (validatable without elevation)
  - "final" -> name=AnyLoc,      admin manifest on Windows  (the shippable app)

Packaging shape differs per OS:
  - Windows: ONEFILE (a single AnyLoc.exe). Everything is packed into the exe and
    unpacked at launch. Easiest for end users — just one file to double-click, no
    folder to keep together. See WIN_RUNTIME_TMPDIR below for where it unpacks and
    why that is NOT the temp dir.
  - macOS:   ONEDIR (COLLECT) wrapped in a proper AnyLoc.app bundle so users can
    drop it into /Applications. dylibs run from inside the .app.

Set ANYLOC_ONEDIR=1 to force a Windows onedir (folder) build instead. It is a
supported escape hatch, not the default — see the WIN_RUNTIME_TMPDIR note.

WINDOW MODEL (the reason console= differs per OS):
  - Windows: console=False. This is a real GUI app — the UI is a native window
    (Edge WebView2 via pywebview), and a black console box next to it is exactly
    what this build is meant to get rid of. console=False makes the PE subsystem
    GUI, so Windows never allocates a console for it. The cost is that
    sys.stdout/stderr/stdin are all None at runtime: applog.install_null_streams()
    puts writable stand-ins back, and applog.setup_logging() writes the real log
    to %LOCALAPPDATA%\AnyLoc\logs\anyloc.log. Nothing may rely on print().
  - macOS:   console=True, deliberately. The mac app elevates by re-launching
    itself under `sudo` in Terminal, and sudo needs a TTY to prompt for the
    password. Take the console away and there is nowhere to type it.

Elevation model (handled at runtime by launcher.py, NOT here):
  - Windows: uac_admin=True bakes an "requireAdministrator" manifest -> UAC prompt.
    console=False and requireAdministrator coexist fine — the elevated process is
    still a GUI process, it just has no console.
  - macOS:   the app re-launches itself under `sudo` in Terminal (no manifest needed).
"""
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# App version — SINGLE SOURCE OF TRUTH is the VERSION file at the repo root.
# Read it rather than hardcoding, because this number is used in three places
# that must agree: the macOS .app bundle (CFBundleVersion), the .pkg filename
# (scripts/build-mac.sh reads the same file), and WIN_RUNTIME_TMPDIR below.
# Bumping one and forgetting another used to yield a .pkg named for the wrong
# version, or a Windows unpack dir that release notes tell users to delete but
# which does not exist. Bump VERSION, and every consumer follows.
def _read_version(default="0.0.0"):
    # Anchor on SPECPATH (PyInstaller injects it = the directory holding this
    # .spec = the repo root), NOT os.getcwd(): the build can legitimately be
    # invoked from elsewhere, and a cwd-relative read would then silently fall
    # back to the default version instead of the real one.
    root = globals().get("SPECPATH") or os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as fh:
            v = fh.read().strip()
            return v or default
    except OSError:
        # Never fail the build over this: a missing VERSION yields a visibly
        # wrong number rather than a traceback in the middle of packaging.
        print("[spec] warn: VERSION not found — falling back to " + default)
        return default


APP_VERSION = _read_version()

VARIANT = os.environ.get("ANYLOC_VARIANT", "final")
if VARIANT == "test":
    APP_NAME, UAC = "AnyLocTest", False
    BUNDLE_ID = "com.anyloc.app.test"
else:
    APP_NAME, UAC = "AnyLoc", True
    BUNDLE_ID = "com.anyloc.app"

# Windows ships as a single .exe by default (easiest for end users). Set
# ANYLOC_ONEDIR=1 to fall back to a folder build.
WIN_ONEFILE = IS_WINDOWS and os.environ.get("ANYLOC_ONEDIR", "") not in ("1", "true", "yes")

# WHERE THE ONEFILE EXE UNPACKS ITSELF — do not drop this.
#
# By default the bootloader creates its _MEIxxxxxx folder under %TEMP%. On a
# machine with WDAC / Application Control enforced, loading python314.dll (and
# every other bundled DLL) out of %TEMP% is BLOCKED by policy, so the exe dies
# during bootstrap before a single line of our Python runs — no window, no log,
# no error the user can act on. Pointing runtime_tmpdir at a folder under
# LOCALAPPDATA moves the unpack out of the blocked location and the same exe
# starts working. (%LOCALAPPDATA% is also UAC-stable: it resolves to the same
# real path elevated and non-elevated, so the elevated relaunch reuses the
# unpack instead of doing it a second time somewhere else.)
#
# PyInstaller's Windows bootloader DOES expand environment variables in this
# string, so %LOCALAPPDATA% is resolved on the user's machine, not ours.
#
# The version is IN THE PATH on purpose. Each run unpacks into a fresh
# _MEIxxxxxx subfolder and deletes it on exit, but a crash (or a file still
# locked by another process — bundled adb.exe is the usual culprit) can strand
# one. Namespacing by version keeps a stranded 1.1.0 folder from being confused
# with a 1.2.0 one and makes "delete the old version's leftovers" a safe,
# obvious operation.
WIN_RUNTIME_TMPDIR = r"%LOCALAPPDATA%\AnyLoc\runtime-" + APP_VERSION


# App icon for the Windows exe (generated by scripts/make_icon.py). None on macOS.
EXE_ICON = os.path.join("web", "icon.ico") if IS_WINDOWS and os.path.exists(os.path.join("web", "icon.ico")) else None

# App icon for the macOS .app bundle. Without this the bundle shows the generic
# blank-document icon in Finder, the Dock and Launchpad. Regenerate with
# `python scripts/make_icon.py` (needs Pillow; uses Apple's iconutil).
_icns = os.path.join("web", "icon.icns")
APP_ICON = _icns if IS_MACOS and os.path.exists(_icns) else None
if IS_MACOS and APP_ICON is None:
    print("[spec] warn: web/icon.icns missing — the .app will have no icon "
          "(run: python scripts/make_icon.py)")

datas, binaries, hiddenimports = [], [], []
datas += [("web", "web")]

# Bundle adb (Android Debug Bridge) so Android mode works with no separate SDK
# install. android_backend.find_adb() looks for it at the _MEIPASS root when
# frozen, so we ship it there (dest "."). Windows adb needs its two helper DLLs
# next to it. Use `datas` (verbatim copy) rather than `binaries`: adb is a
# standalone exe that doesn't want PyInstaller's shared-lib dependency analysis
# (which silently drops it). Skipped gracefully if the vendor files aren't present.
_adb_files = []
if IS_WINDOWS:
    _adb_files = [os.path.join("vendor", "win", f)
                  for f in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")]
elif IS_MACOS:
    _adb_files = [os.path.join("vendor", "mac", "adb")]
for _p in _adb_files:
    if os.path.exists(_p):
        datas += [(_p, ".")]
    else:
        print(f"[spec] warn: {_p} missing — Android mode won't work in this build")

# Packages to fully collect. `apple_compress` is macOS-only (pyimg4 pulls it in
# on Darwin for LZFSE); harmless to list — collect_all just warns if absent.
_collect_pkgs = [
    "pymobiledevice3", "pytun_pmd3", "developer_disk_image", "ipsw_parser",
    "qh3", "uvicorn", "construct", "opack2", "bpylist2", "pyimg4",
    "remotezip2", "pykdebugparser",
    # pymobiledevice3 10+: the no-root userspace tunnel runs on these.
    "pmd_pytcp", "pmd_net_addr",
]
if IS_MACOS:
    _collect_pkgs.append("apple_compress")

# Fully collect data + binaries + submodules for the runtime-dynamic packages.
for pkg in _collect_pkgs:
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        print(f"[spec] warn collect_all({pkg!r}): {e}")

# Ship .dist-info metadata for packages that read their own version at import
# time (importlib.metadata.version(...)). Missing metadata = import crash.
# NB: apple_compress does exactly this on macOS -> it MUST be here for Darwin.
_meta_pkgs = [
    "pyimg4", "ipsw_parser", "developer_disk_image", "pymobiledevice3",
    "remotezip2", "construct", "qh3", "uvicorn", "pytun_pmd3", "opack2",
    "bpylist2", "pykdebugparser", "cryptography", "pyusb",
    # These read their own version via importlib.metadata at import time with no
    # fallback; bundling their metadata prevents PackageNotFoundError crashes if
    # any code path reaches them in the frozen app. (Cheap insurance.)
    "prompt_toolkit", "readchar",
]
if IS_WINDOWS:
    # NOTE the name: the DISTRIBUTION is "pywebview", the IMPORT is "webview".
    # copy_metadata() wants the distribution name — "webview" raises
    # PackageNotFoundError. webview/_version.py reads its own version through
    # importlib.metadata at import time, so without this the frozen app fails
    # the moment gui.py imports it.
    #
    # This is the ONLY thing pywebview needs from us. Do NOT add hiddenimports
    # or binaries for webview / clr / pythonnet: pywebview and pythonnet each
    # register a PyInstaller hook directory via the "pyinstaller40" entry point,
    # and those hooks already collect webview's lib/ + js/ data and every native
    # DLL. Hand-adding them fights the hooks instead of helping.
    _meta_pkgs.append("pywebview")
if IS_MACOS:
    _meta_pkgs.append("apple_compress")
    # macOS now gets the native window too (WKWebView via pywebview's Cocoa
    # backend), so it needs pywebview's metadata for exactly the same reason
    # Windows does — webview/_version.py reads it at import time.
    _meta_pkgs.append("pywebview")
    # The Cocoa backend is reached through pyobjc; PyInstaller's static analysis
    # does not follow pywebview's runtime backend selection, so name the
    # frameworks we actually use. (pyobjc-core itself is pulled in as a
    # dependency of these.)
    hiddenimports += [
        "webview.platforms.cocoa",
        "objc", "Foundation", "AppKit", "WebKit", "Quartz",
        "Security", "UniformTypeIdentifiers",
    ]

for pkg in _meta_pkgs:
    try:
        datas += copy_metadata(pkg)
    except Exception as e:
        print(f"[spec] warn copy_metadata({pkg!r}): {e}")

hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.loops.asyncio",
]

# Our own modules are imported lazily (inside functions) in a few places, so make
# sure PyInstaller bundles them even if static analysis misses a path.
#   applog  — imported at the top of launcher.py AND lazily inside backend.py's
#             /api/logs handler, which static analysis does not follow.
#   gui     — imported lazily inside run_ui_blocking() so that --dev and
#             --selftest never need pywebview at all. That laziness is the whole
#             reason it must be listed here.
hiddenimports += ["backend", "backend_base", "ios_backend", "android_backend",
                  "applog", "gui"]

# Tunnel-service modules: names have shifted across pymobiledevice3 versions
# (e.g. `core_device_tunnel_service` was folded into `tunnel_service` around
# v9.33). Only add the ones that actually import, so the spec stays clean and
# version-proof on both Windows and macOS instead of logging hard ERRORs.
import importlib.util as _ilu
for _mod in (
    "pymobiledevice3.remote.tunnel_service",
    "pymobiledevice3.remote.core_device_tunnel_service",
    "pymobiledevice3.remote.userspace_tunnel",
):
    try:
        if _ilu.find_spec(_mod) is not None:
            hiddenimports.append(_mod)
    except Exception:
        pass

a = Analysis(
    ["src/launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "matplotlib", "pytest",
        # Heavy libs present in this dev env but unused by any-loc.
        # (IPython is intentionally NOT excluded — pmd3 hard-imports it.)
        "torch", "numpy", "pandas", "scipy", "numba", "llvmlite", "av",
        "notebook", "sympy", "sklearn", "cv2", "PIL",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

# Console window, per OS. See the WINDOW MODEL note at the top of this file:
# Windows is a real windowed GUI app (no console box); macOS keeps its Terminal
# because that is where sudo prompts for the password.
CONSOLE = not IS_WINDOWS

if WIN_ONEFILE:
    # Single-file Windows build: bundle EVERYTHING (scripts + libs + datas) into
    # one AnyLoc.exe. At launch the bootloader unpacks to runtime_tmpdir and runs
    # from there. No COLLECT folder, no .app — just the one file the user
    # double-clicks.
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=APP_NAME,
        console=CONSOLE,            # False on Windows: native window, no console
        uac_admin=UAC,              # requireAdministrator manifest -> UAC prompt
        icon=EXE_ICON,
        upx=False,
        # NOT %TEMP% — WDAC blocks loading DLLs from there. See WIN_RUNTIME_TMPDIR.
        runtime_tmpdir=WIN_RUNTIME_TMPDIR,
    )
else:
    # Onedir build: exe holds only the bootstrap; the rest lives beside it in the
    # COLLECT folder. Used for macOS (.app) and available on Windows via
    # ANYLOC_ONEDIR=1.
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=APP_NAME,
        console=CONSOLE,            # False on Windows, True on macOS (sudo needs the TTY)
        uac_admin=UAC,              # Windows-only manifest; ignored on macOS
        icon=EXE_ICON,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

    # On macOS, wrap the onedir COLLECT output in a real .app bundle so users can
    # drop it into /Applications and double-click it. launcher.py does the sudo
    # elevation.
    if IS_MACOS:
        app = BUNDLE(
            coll,
            name=f"{APP_NAME}.app",
            icon=APP_ICON,
            bundle_identifier=BUNDLE_ID,
            version=APP_VERSION,
            info_plist={
                "CFBundleName": APP_NAME,
                "CFBundleDisplayName": APP_NAME,
                "CFBundleShortVersionString": APP_VERSION,
                "CFBundleVersion": APP_VERSION,
                "NSHighResolutionCapable": True,
                "LSMinimumSystemVersion": "11.0",
                # Not a background-only agent: we want it to appear/activate
                # normally and be allowed to talk to the local network + spawn
                # Terminal.
                "LSBackgroundOnly": False,
                # REQUIRED for the elevation prompt. Since macOS 10.14 an app
                # that sends Apple Events — which is how we show the native
                # password sheet (`osascript ... with administrator privileges`)
                # and how we fall back to driving Terminal.app — must declare a
                # usage string. WITHOUT THIS macOS denies the event outright
                # (errAEEventNotPermitted, -1743) instead of prompting, so the
                # elevated path silently fails on a Finder launch. This string is
                # what the user reads in that one-time consent dialog.
                "NSAppleEventsUsageDescription":
                    "AnyLoc needs your permission to ask for the administrator "
                    "password required to create the tunnel to your iPhone.",
                # The UI is served from 127.0.0.1; declaring local networking
                # keeps the app well-behaved under the modern privacy prompts.
                "NSLocalNetworkUsageDescription":
                    "AnyLoc serves its map interface on your computer only "
                    "(127.0.0.1). Nothing is sent anywhere else.",
            },
        )
