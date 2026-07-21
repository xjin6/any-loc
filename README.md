# any-loc

A tiny, self-hosted **iOS & Android GPS spoofer for Windows and macOS** with a
Google-Maps-style web UI. Click the map to teleport your iPhone/iPad or Android
phone, or drive around with a joystick / WASD keys.

This is a DIY, local-only take on tools like AnyGo / iToolab. Everything runs on your
own machine; nothing is uploaded anywhere.

AnyLoc **auto-detects** what you plug in: an Android phone (over adb) takes the
Android path — no admin needed; otherwise it drives an iPhone/iPad over Apple's
tunnel (which needs elevation). Same web UI either way.

```
   Browser (map + joystick)  ──HTTP──►  backend.py ──┬─ iOS:     DVT LocationSimulation  ──► iPhone
                                                      │          (pymobiledevice3 tunneld)
                                                      └─ Android: adb `cmd location`     ──► Android phone
                                                                 (bundled adb, no root)
```

The exact same Python code runs on both OSes. Only two things differ, and both are
handled automatically:

| | Windows | macOS |
|---|---|---|
| **Elevation** (the tunnel needs it) | Administrator via a **UAC** prompt | root via **sudo** (login password) |
| **USB driver** for the iPhone | needs **iTunes / Apple Mobile Device Support** | built into macOS (**nothing to install**) |
| **Ships as** | a single `AnyLoc.exe` | `AnyLoc.app` + `AnyLoc.pkg` |

---

## Requirements

**Both platforms**
- **Python 3.10+** (only needed if you run from source or build; end users of the
  packaged app don't need Python).
- The one Python dependency: `pip install -U pymobiledevice3`

### To spoof an iPhone/iPad
- An **iPhone/iPad** with a Lightning/USB-C cable.
- On the device: **Developer Mode ON**
  (`Settings > Privacy & Security > Developer Mode` → toggle on → reboot).
- **Windows:** **iTunes / Apple Mobile Device Support** installed (provides the USB
  driver Windows needs to see the iPhone). Installing Apple's iTunes from apple.com
  is the easy way.
- **macOS:** nothing extra — macOS already talks to iPhones over USB (`usbmuxd` is
  built in).

### To spoof an Android phone
- **Android 11 or newer** (AnyLoc uses the system `cmd location` command, added in
  Android 11). Pure-HarmonyOS-NEXT Huawei devices (no adb) are not supported;
  HarmonyOS 4 / EMUI and below still work.
- **Developer Options** on, and **USB debugging** on
  (`Settings > About phone` → tap *Build number* 7×, then `Settings > System >
  Developer options > USB debugging`). On some phones (Xiaomi, Huawei) also enable
  **USB debugging (Security settings)**.
- Plug in with a USB cable and tap **Allow USB debugging** when the phone asks.
- **No app to install, no root, and no admin/UAC** — `adb` is bundled with AnyLoc,
  and it grants itself mock-location over adb. macOS talks to Android over USB with
  nothing extra.

**macOS — architecture note:** the packaged `AnyLoc.app` matches the Mac it was
**built** on (Apple Silicon build won't run on Intel and vice-versa). Running from
source works on any Mac with Python.


---

## Usage

### Windows

1. Download **`AnyLoc.exe`** and double-click it. It's a single self-contained
   file — no install, nothing to unzip.
2. Allow the **UAC** prompt (the tunnel needs Administrator).
3. A console window opens (leave it open; you can minimize it) and your browser
   opens `http://127.0.0.1:8765/`.
   (First launch takes a few extra seconds — the single exe unpacks itself once.)
4. Click **Connect**. When the dot turns green, you're live.
5. **Click anywhere on the map** to teleport, or use the **joystick / WASD / arrows**
   to move. Pick a speed (Walk / Run / Bike / Drive); hold **Shift** for 3×.
6. Press **Reset GPS** to stop spoofing and restore the real location.
   (Closing the console window, or rebooting the phone, also restores real GPS.)

### macOS

1. Install **`AnyLoc.pkg`** (double-click → installs `AnyLoc.app` into `/Applications`),
   or just drag `AnyLoc.app` into **Applications**.
   - The `.pkg` and `.app` are **ad-hoc signed, not notarized** (no paid Apple
     Developer ID). After downloading/AirDropping, macOS Gatekeeper may block the
     first open. If the installer or app is blocked with *"Apple cannot check it
     for malicious software"*: **right-click it → Open → Open** (once), or run
     `xattr -dr com.apple.quarantine AnyLoc.pkg` / `AnyLoc.app` to clear the flag.
2. Launch **AnyLoc** from Applications / Launchpad / Spotlight.
   - First launch of the app may also need the same **right-click → Open** once.
3. A **Terminal window** opens and asks for your **login password** — this is `sudo`
   (the tunnel needs root, the macOS equivalent of Administrator). Type it and press Return.
   (You won't see the characters as you type — that's normal.)
   - macOS may show a one-time *"AnyLoc wants to control Terminal"* prompt — click **OK**.
4. Your browser opens `http://127.0.0.1:8765/`. From here it's identical to Windows:
   **Connect** → click the map / joystick / WASD → **Reset GPS** to restore.
5. To stop: **close that Terminal window** (or reboot the phone).

---

## Notes, limits, gotchas

- **iOS 17 / 18+** use the RemoteXPC "tunnel" path (what this project targets). The tunnel
  must stay running the whole time — that's the console (Windows) / Terminal (macOS) window.
- The tunnel requires a **USB cable**. Going fully wireless is possible but needs an initial
  wired pairing plus extra setup; not included here to keep it simple and reliable.
- On the very first connect, `backend.py` **auto-mounts the Developer Disk Image** for your
  iOS version. This needs internet (it fetches the DDI) and can take a minute once.
- If **Connect** errors:
  - Make sure the app is running **elevated** — Windows: you allowed UAC; macOS: you
    entered your password for sudo and the Terminal window is still open.
  - Unlock the phone; re-accept **Trust**; confirm **Developer Mode** is on.
  - Unplug/replug the cable; try a different USB port (a direct port is often more
    reliable than a hub).
- Movement is integrated client-side and streamed at ~10 Hz; the backend keeps Apple's DVT
  `LocationSimulation` channel **open** and applies the latest coordinate (latest-wins), which
  is what makes the joystick feel smooth instead of teleporting in jumps.

### Command-line options (optional)
```bash
python backend.py --port 8765 --no-browser -v
```
- `--port` web UI port (default 8765)
- `--no-connect` don't auto-connect on startup (click Connect yourself)
- `--tunneld-port` if you ran tunneld on a non-default port
- `-v` verbose logging (shows pymobiledevice3 detail)

---

## Project layout

```
any-loc/
├── launcher.py       all-in-one entry point (self-elevates, starts server + tunnel)
├── backend.py        device worker + static server + JSON API (stdlib only)
├── AnyLoc.spec       PyInstaller build spec (Windows .exe / macOS .app)
├── requirements.txt  Python dependencies
├── web/              the UI (index.html, app.js, i18n.js, config.js, icons)
└── scripts/          launch & build helpers
    ├── dev.bat / dev.command    dev mode (UI hot-reload, optional real tunnel)
    ├── AnyLoc.command           run-from-source launcher (macOS, no packaging)
    ├── build-mac.sh             build AnyLoc.app + AnyLoc.pkg
    ├── make_icon.py             regenerate web/icon.ico + icon-256.png
    └── winpreview.py            experimental native-window preview (pywebview)
```

`launcher.py`, `backend.py`, `AnyLoc.spec`, and `web/` must stay at the repo root —
PyInstaller and `import backend` assume they sit together. The `scripts/` helpers
`cd` back to the root before running.

---

## Building from source

The same `AnyLoc.spec` builds on both OSes; `ANYLOC_VARIANT` picks test vs. shippable.

### Windows → a single `AnyLoc.exe`
```powershell
# test build (no admin manifest, so you can --selftest without UAC)
$env:ANYLOC_VARIANT="test";  pyinstaller --clean --noconfirm AnyLoc.spec
dist\AnyLocTest.exe --selftest                 # expect RESULT: PASS

# shippable build (bakes the requireAdministrator manifest → UAC)
$env:ANYLOC_VARIANT="final"; pyinstaller --clean --noconfirm AnyLoc.spec
# result is a single file: dist\AnyLoc.exe
```
Windows builds as **one self-contained `AnyLoc.exe`** (onefile). On a locked-down
machine where WDAC / Application Control blocks running unpacked code from `%TEMP%`,
build the folder form instead: set `$env:ANYLOC_ONEDIR="1"` before running
PyInstaller — you'll get a `dist\AnyLoc\` folder (keep it together, run the exe inside).

### macOS → `AnyLoc.app` + `AnyLoc.pkg`
```bash
./scripts/build-mac.sh          # shippable: dist/AnyLoc.app + dist/AnyLoc-1.0.0.pkg
./scripts/build-mac.sh test     # test: dist/AnyLocTest.app (validate with --selftest)

# validate the test app without root:
dist/AnyLocTest.app/Contents/MacOS/AnyLocTest --selftest   # expect RESULT: PASS
```
`scripts/build-mac.sh` runs PyInstaller, ad-hoc code-signs the `.app` (so Gatekeeper lets it
run at all), then wraps it into a `.pkg` that installs into `/Applications`.

### Dev mode (fast UI iteration, no packaging)
Edit anything under `web/` and the browser auto-refreshes in ~1s.
- **Windows:** double-click `scripts/dev.bat` (UI only), or right-click → **Run as administrator**
  (adds the real tunnel so you can test on a device).
- **macOS:** `./scripts/dev.command` (UI only), or `sudo ./scripts/dev.command` (adds the real tunnel).

### App icon
The icon is generated from code — no binary editing. Regenerate `web/icon.ico` +
`web/icon-256.png` with:
```bash
python scripts/make_icon.py     # needs Pillow: pip install pillow
```

---

## How it works (for the curious)

`backend.py`:
- Talks to `pymobiledevice3 remote tunneld` on `127.0.0.1:49151` via `get_tunneld_devices()`
  to obtain a connected `RemoteServiceDiscoveryService` for the phone.
- `auto_mount(rsd)` mounts the Developer Disk Image if needed.
- Opens `DvtProvider(rsd)` → `LocationSimulation(dvt)` as a long-lived async context manager
  on a dedicated event-loop thread, and calls `loc.set(lat, lon)` / `loc.clear()`.
- Serves the static UI and a small JSON API (`/api/connect`, `/api/set`, `/api/clear`,
  `/api/status`) using only the Python standard library — no web framework.

`launcher.py` is the all-in-one entry point (what becomes `AnyLoc.exe` / `AnyLoc.app`).
It self-elevates (UAC on Windows, sudo-in-Terminal on macOS), starts the web server +
device worker, opens the browser, and runs `tunneld` in the main thread.

All pymobiledevice3 calls were verified against **v9.3x**.

---

## Legal / ethical

Location spoofing has legitimate uses (development, testing, privacy). Using it to defeat
another service's rules (games, check-in/attendance, dating, etc.) may violate their terms
and, in some cases (e.g. faking work attendance), the law. You are responsible for how you
use this. Provided as-is, for learning and legitimate testing.
