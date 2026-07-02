# -*- mode: python ; coding: utf-8 -*-
"""
Cross-platform build spec for any-loc (Windows .exe folder + macOS .app bundle).

Parameterized via the environment variable ANYLOC_VARIANT:
  - "test"  -> name=AnyLocTest,  no admin manifest  (validatable without elevation)
  - "final" -> name=AnyLoc,      admin manifest on Windows  (the shippable app)

Both are ONEDIR (COLLECT) builds so DLLs/dylibs run from the app folder, not %TEMP%
(required to pass WDAC/Application Control on managed Windows machines). On macOS the
COLLECT folder is additionally wrapped in a proper AnyLoc.app bundle via BUNDLE.

Elevation model (handled at runtime by launcher.py, NOT here):
  - Windows: uac_admin=True bakes an "requireAdministrator" manifest -> UAC prompt.
  - macOS:   the app re-launches itself under `sudo` in Terminal (no manifest needed).
"""
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

VARIANT = os.environ.get("ANYLOC_VARIANT", "final")
if VARIANT == "test":
    APP_NAME, UAC = "AnyLocTest", False
    BUNDLE_ID = "com.anyloc.app.test"
else:
    APP_NAME, UAC = "AnyLoc", True
    BUNDLE_ID = "com.anyloc.app"

datas, binaries, hiddenimports = [], [], []
datas += [("web", "web")]

# Packages to fully collect. `apple_compress` is macOS-only (pyimg4 pulls it in
# on Darwin for LZFSE); harmless to list — collect_all just warns if absent.
_collect_pkgs = [
    "pymobiledevice3", "pytun_pmd3", "developer_disk_image", "ipsw_parser",
    "qh3", "uvicorn", "construct", "opack2", "bpylist2", "pyimg4",
    "remotezip2", "pykdebugparser",
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
if IS_MACOS:
    _meta_pkgs.append("apple_compress")

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
    ["launcher.py"],
    pathex=["."],
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

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=True,       # keep logs visible (Windows console / macOS Terminal)
    uac_admin=UAC,      # Windows-only manifest; ignored on macOS
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

# On macOS, wrap the onedir COLLECT output in a real .app bundle so users can drop
# it into /Applications and double-click it. launcher.py does the sudo elevation.
if IS_MACOS:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier=BUNDLE_ID,
        version="1.0.0",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # Not a background-only agent: we want it to appear/activate normally
            # and be allowed to talk to the local network + spawn Terminal.
            "LSBackgroundOnly": False,
        },
    )
