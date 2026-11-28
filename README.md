# any-loc

A tiny, self-hosted **iOS & Android GPS spoofer for Windows and macOS** with a
Google-Maps-style web UI. Click the map to teleport your iPhone/iPad or Android
phone, or drive around with a joystick / WASD keys.

This is a DIY, local-only take on tools like AnyGo / iToolab. Everything runs on your
own machine; nothing is uploaded anywhere.

AnyLoc **auto-detects** what you plug in: an Android phone (over adb) takes the
Android path; otherwise it opens the iOS interface. For iOS, choose **USB** or
**Wi-Fi**, refresh the device list, select a device, and click **Connect**.
The new iOS transport selection is **experimental**: development on a DevBox has
covered mocked tests and software self-tests, not a physical iPhone connection.

```
   Browser (map + joystick)  ──HTTP──►  backend.py ──┬─ iOS:     DVT LocationSimulation  ──► iPhone
                                                      │          (session-owned USB / Wi-Fi tunnel)
                                                      └─ Android: adb `cmd location`     ──► Android phone
                                                                 (bundled adb, no root)
```

The exact same Python code runs on both OSes, and both get the same **native app
window**. Platform-specific requirements and packaging:

| | Windows | macOS |
|---|---|---|
| **Elevation** | The release `.exe` still requests **UAC** via its manifest. Source/test builds need no admin for the iOS 17.4+ userspace paths; iOS 17.0–17.3 USB needs admin | iOS 17.4+ uses userspace tunnels without root. iOS 17.0–17.3 USB needs a manually elevated launch; the app does not automatically prompt or relaunch |
| **USB driver** for the iPhone | needs **iTunes / Apple Mobile Device Support** | built into macOS (**nothing to install**) |
| **Ships as** | a single `AnyLoc.exe` | `AnyLoc.app` + `AnyLoc.pkg` |
| **What you see** | one **app window**, no console | one **app window** — no Terminal, no browser tab |
| **Renderer** | Edge **WebView2** | system **WebKit** (nothing to install) |

---

## Requirements

**Both platforms**
- **Python 3.10+** (only needed if you run from source or build; end users of the
  packaged app don't need Python). **Python 3.12** was used for the current DevBox checks.
- The Python dependencies: `pip install -U -r requirements.txt`
  (`pymobiledevice3>=11.63.1,<12` for explicit USB providers and RemotePairing
  discovery, plus `pywebview` for the native window). The new transport code does
  not support the older 9.x/10.x dependency range.
- Building the app on **either** OS uses the same `requirements.txt`, and
  `scripts/build-win.ps1` / `scripts/build-mac.sh` install it into the build venv
  for you. They refuse to build without `pywebview`, because PyInstaller only
  bundles what it can see. On macOS it pulls in `pyobjc` automatically.

**Windows — the app window**
- AnyLoc draws its UI in a native window using the **Edge WebView2 Runtime**. It ships
  with Windows 11 and with current Windows 10, so on essentially every machine it is
  already there and there is nothing to do.
- If it is missing (a trimmed LTSC/Server image, or it was removed), AnyLoc notices,
  writes it to the log, and opens the UI in an **Edge/Chrome app window** instead —
  same app, same features, just not a native window. Installing the free
  [Evergreen WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
  restores the real window.

### To spoof an iPhone/iPad

- An **iPhone/iPad running iOS 17+**, with a Lightning/USB-C data cable for USB
  use and the initial Wi-Fi preparation.
- On the device: **Developer Mode ON**
  (`Settings > Privacy & Security > Developer Mode` → toggle on → reboot).
- **Windows:** **iTunes / Apple Mobile Device Support** installed (provides the USB
  driver Windows needs to see the iPhone). Installing Apple's iTunes from apple.com
  is the easy way.
- **macOS:** nothing extra — macOS already talks to iPhones over USB (`usbmuxd` is
  built in).
- **Experimental Wi-Fi:** the phone and computer must be on the same LAN, and
  the phone must advertise a RemotePairing service compatible with the saved
  pairing record. Keep the phone awake/unlocked during setup and connection.
  Wi-Fi preparation can fail on devices that do not expose the required service;
  USB remains available. See the setup steps below.
- **Bluetooth is not supported** by this implementation. Pairing the phone in
  Windows Bluetooth settings does not create an iOS developer location channel.

### To spoof an Android phone
- **Android 11 or newer** (AnyLoc uses the system `cmd location` command, added in
  Android 11). Pure-HarmonyOS-NEXT Huawei devices (no adb) are not supported;
  HarmonyOS 4 / EMUI and below still work.
- **Developer Options** on, and **USB debugging** on
  (`Settings > About phone` → tap *Build number* 7×, then `Settings > System >
  Developer options > USB debugging`). On some phones (Xiaomi, Huawei) also enable
  **USB debugging (Security settings)**.
- Plug in with a USB cable and tap **Allow USB debugging** when the phone asks.
- **No app to install, no root, and no Android-specific admin requirement** — `adb` is bundled with AnyLoc,
  and it grants itself mock-location over adb. macOS talks to Android over USB with
  nothing extra. The Windows release executable still has its administrator manifest.

**macOS — architecture note:** the packaged `AnyLoc.app` matches the Mac it was
**built** on (Apple Silicon build won't run on Intel and vice-versa). Running from
source works on any Mac with Python.


---

## Usage

### iOS USB / Wi-Fi selection (experimental)

USB is the default. Refresh the USB device list, select your phone, and click
**Connect**. A USB selection only uses a physical USB transport; it does not
silently choose the same phone over the network. When several devices are
available, choose one explicitly.

To prepare Wi-Fi for the first time:

1. Connect the phone by USB, unlock it, accept **Trust This Computer**, and enable
   **Developer Mode**. Connect the phone and computer to the same LAN.
2. Select **Wi-Fi** in AnyLoc and expand **First use: enable Wi-Fi over USB**.
   Refresh its USB device list and select the phone to prepare.
3. Click **Enable Wi-Fi for this device**. This enables wireless lockdown and
   prepares a RemotePairing record over the trusted USB connection. Wait for
   success; an error means the preparation is incomplete.
4. Unplug the USB cable. Refresh the wireless device list, select the phone, and
   click **Connect**. Discovery matches RemotePairing advertisements to existing
   pairing records; it does not pair unknown devices automatically.
5. After connecting, test a location change, **Reset GPS**, and closing the app.
   A green connection indicator alone does not verify that the phone's location
   changed or was restored.

Wi-Fi is currently conditional RemotePairing support, not a guarantee for every
iOS version or network. An empty list can mean an unavailable service, missing
or stale pairing information, a sleeping phone, or blocked LAN discovery. The
new USB/Wi-Fi paths, disconnection handling, and GPS restoration still require
verification with a physical iPhone; the DevBox tests cannot establish those outcomes.

### Windows

1. Download **`AnyLoc.exe`** and double-click it. It's a single self-contained
   file — no install, nothing to unzip.
2. Allow the **UAC** prompt requested by the release executable's manifest.
   The launcher does not force an additional elevation; iOS 17.4+ sessions use
   userspace tunnels on Windows as well.
3. The **AnyLoc window** opens — a normal app window with the map in it. No
   console, no browser tab, nothing else to keep open.
   (First launch takes a few extra seconds — the single exe unpacks itself once.)
4. For iOS, choose USB or Wi-Fi, refresh and select your device, then click
   **Connect**. When the dot turns green, the location channel is connected.
5. **Click anywhere on the map** to teleport, or use the **joystick / WASD / arrows**
   to move. Pick a speed (Walk / Run / Bike / Drive); hold **Shift** for 3×.
6. Press **Reset GPS** to stop spoofing and restore the real location.
   **Closing the AnyLoc window** also requests GPS restoration and waits for
   session cleanup. Verify this on the phone when testing the new transports.

Minimizing the window is fine. Just don't close it while you want the fake
location to stick.

**The map is drawn by the Edge WebView2 Runtime**, which ships with Windows 11 and
with current Windows 10 — on almost every machine it is simply already there, and
you never think about it. If it is missing (a stripped LTSC/Server image, or it was
removed), AnyLoc says so in the log and falls back to opening the UI in an
**Edge/Chrome app window** instead. Everything works the same; it just isn't a
native window. To get the real window back, install the free
[Evergreen WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
from Microsoft.

**Where the log is.** There is no console window any more, so if something goes
wrong the log file is the place to look:

```
%LOCALAPPDATA%\AnyLoc\logs\anyloc.log
```

(that expands to `C:\Users\<you>\AppData\Local\AnyLoc\logs\anyloc.log` — paste the
`%LOCALAPPDATA%` form into Explorer's address bar and it will take you there). It
rotates at 1 MB and keeps 3 old copies. The same lines are visible live inside the
app: expand the **Logs** panel at the bottom of the side panel. If AnyLoc hits a
fatal error it shows a message box with the exception and this path.

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
3. The **AnyLoc window** opens — a normal Mac app window with the map in it. No
   Terminal, no browser tab, nothing else to keep open.
   - **No password on iOS 17.4+ or Android.** AnyLoc builds the tunnel inside its
     own process, which needs no root.
   - **iOS 17.0–17.3 USB only:** these sessions require the privileged kernel
     fallback. An ordinary launch reports that elevated permissions are needed;
     it does not automatically show a password sheet or relaunch itself.
4. For iOS, choose the transport and device, then click **Connect**. From here it's
   identical to Windows: click the map / joystick / WASD → **Reset GPS** to restore.
5. To stop: **close the AnyLoc window** or quit with ⌘Q. The app requests GPS
   restoration before releasing the session; verify the result on the phone
   when testing the new transports.

Minimizing the window is fine. Just don't close it while you want the fake
location to stick.

**Where the log is** (macOS):

```
~/Library/Logs/AnyLoc/anyloc.log
```

The same lines are visible live inside the app: expand the **Logs** panel at the
bottom of the side panel.

---

## Notes, limits, gotchas

- **iOS 17 / 18+** use the RemoteXPC "tunnel" path (what this project targets). The tunnel
  must stay running the whole time — it lives inside the app, so that means keeping the
  **AnyLoc window** open.
- **USB** needs the cable throughout the session. **Wi-Fi** needs the initial USB
  preparation described above, then uses RemotePairing over the LAN. This path is
  experimental and has not yet been validated with a physical phone.
- On the very first connect, `backend.py` **auto-mounts the Developer Disk Image** for your
  iOS version. This needs internet (it fetches the DDI) and can take a minute once.
- If **Connect** errors:
  - **Windows release executable:** allow its **UAC** prompt. Source/test builds
    on iOS 17.4+ do not need admin for the userspace tunnel. The iOS 17.0–17.3 USB
    fallback needs a manually elevated launch on either OS.
  - Unlock the phone; re-accept **Trust**; confirm **Developer Mode** is on.
  - For USB, replug the cable or try a direct USB port. For Wi-Fi, confirm USB
    preparation succeeded, refresh the list while the phone is awake, and check
    that LAN discovery is allowed. Bluetooth cannot be used as a fallback.
  - Open the **Logs** panel in the app, or read the log file — with no console
    window, that is where the real error message is:
    Windows `%LOCALAPPDATA%\AnyLoc\logs\anyloc.log`,
    macOS `~/Library/Logs/AnyLoc/anyloc.log`.
- **Only one AnyLoc at a time.** A second launch notices the first (a named mutex on
  Windows, a file lock on macOS) and tells you to switch to the window that's already
  open, rather than quietly fighting it for the phone.
- Movement is integrated client-side and streamed at ~10 Hz; the backend keeps Apple's DVT
  `LocationSimulation` channel **open** and applies the latest coordinate (latest-wins), which
  is what makes the joystick feel smooth instead of teleporting in jumps.

### Command-line options (optional)
```bash
python src/backend.py --port 8765 --no-browser --no-connect -v
```
- These options belong to the standalone HTTP backend. For the normal native
  window and explicit iOS connection flow, use `python src/launcher.py`.
- `--port` web UI port (default 8765)
- `--no-connect` don't auto-connect on startup (click Connect yourself)
- The old `--tunneld-host` / `--tunneld-port` arguments remain parseable for
  compatibility; the new iOS sessions own their tunnels and do not use them.
- `-v` verbose logging (shows pymobiledevice3 detail)

---

## Project layout

```
any-loc/
├── src/               all Python source
│   ├── launcher.py       starts the server + idle iOS worker + native window
│   ├── gui.py            the native app window (WebView2 on Windows, WebKit on macOS) + browser fallback
│   ├── applog.py         file/ring logging + the null-stream shim the windowed build needs
│   ├── backend.py        device worker + static server + JSON API (stdlib only)
│   ├── backend_base.py   LocationBackend abstraction (shared by iOS + Android)
│   ├── ios_backend.py    iOS backend (pymobiledevice3 tunnel + DVT)
│   ├── ios_transport.py  explicit USB / experimental Wi-Fi discovery, pairing preparation and session tunnels
│   └── android_backend.py Android backend (bundled adb + `cmd location`)
├── AnyLoc.spec        PyInstaller build spec (Windows .exe / macOS .app)
├── VERSION            the one place the version number lives (spec + build-mac.sh read it)
├── requirements.txt   Python dependencies
├── web/               the UI (index.html, app.js, i18n.js, config.js, icons)
│   └── vendor/           Leaflet, vendored so the map works with no internet
├── vendor/            bundled adb (vendor/win/adb.exe, vendor/mac/adb)
├── scripts/           launch & build helpers
│   ├── dev.bat / dev.command    dev mode (browser hot-reload, explicit device connection)
│   ├── AnyLoc.command           run-from-source launcher (macOS, no packaging)
│   ├── build-win.ps1            build AnyLoc.exe (creates the venv, checks the PE header)
│   ├── build-mac.sh             build AnyLoc.app + AnyLoc.pkg
│   └── make_icon.py             regenerate web/icon.ico + icon.icns + icon-256.png
└── platforms/         per-OS build workspace (generated, git-ignored)
    ├── mac/           .venv/ + dist/ (AnyLoc.app, AnyLoc.pkg) + build/
    └── windows/       .venv/ + dist/ (AnyLoc.exe) + build/
```

The Python source lives in `src/`; `web/`, `vendor/`, and `AnyLoc.spec` stay at the
repo root. `import backend` works because all `src/*.py` sit together (and the spec
puts `src/` on `pathex`). The `scripts/` helpers `cd` back to the root and run
`src/launcher.py`.

Each OS gets its own build workspace under `platforms/<os>/` — its build venv
(`.venv/`), PyInstaller work dir (`build/`), and output (`dist/`). All of it is
generated and git-ignored; only the two `platforms/*/README.md` placeholders are
tracked so the structure survives a clone.

---

## Building from source

The same `AnyLoc.spec` builds on both OSes; `ANYLOC_VARIANT` picks test vs. shippable.
Each OS builds into its own `platforms/<os>/` workspace.

### Windows → a single `AnyLoc.exe`
```powershell
.\scripts\build-win.ps1 test     # test build → platforms\windows\dist\AnyLocTest.exe
.\scripts\build-win.ps1          # shippable  → platforms\windows\dist\AnyLoc.exe

# validate the test exe without UAC:
platforms\windows\dist\AnyLocTest.exe --selftest        # expect RESULT: PASS
```
`scripts/build-win.ps1` is the Windows counterpart of `build-mac.sh`: it creates (or
reuses) `platforms\windows\.venv`, installs `requirements.txt` + `pyinstaller` +
**`pywebview`**, runs PyInstaller into `platforms\windows\dist`, and then checks the
PE header of the result to prove the exe really is a **GUI** binary and not a console
one. Useful flags: `-OneDir` (folder build instead of one file), `-SkipDeps` (skip pip
when nothing changed).

> `pywebview` must be installed **in that venv** — PyInstaller only bundles what it
> can see. Build without it and you get an exe that silently falls back to a browser
> window instead of the native one. The script checks and refuses to continue.

Prefer the script, but the raw commands still work if you want them:
```powershell
$env:ANYLOC_VARIANT="test"   # or "final"
pyinstaller --clean --noconfirm --distpath platforms\windows\dist --workpath platforms\windows\build AnyLoc.spec
```

**Two things the Windows build does deliberately:**

- **`console=False`** — the exe is a GUI binary (PE subsystem = GUI), so Windows never
  gives it a console window. The consequence is that `sys.stdout`/`stderr` are `None`
  at runtime and `print()` goes nowhere: everything diagnostic must go through
  `applog`, which writes `%LOCALAPPDATA%\AnyLoc\logs\anyloc.log`. macOS keeps
  `console=True`; the current launcher does not automatically invoke `sudo`.
- **`runtime_tmpdir`** — the onefile exe unpacks to
  `%LOCALAPPDATA%\AnyLoc\runtime-<version>\` instead of `%TEMP%`. Machines with WDAC /
  Application Control commonly forbid loading DLLs out of `%TEMP%`, which kills a
  default onefile build during bootstrap — before any of our code runs, so there is no
  error message to go on. Unpacking under `LOCALAPPDATA` avoids that policy. Each run
  uses a fresh subfolder and cleans it up on exit; the version in the path keeps one
  version's stranded leftovers (from a crash) distinguishable from another's.

Onefile is the default because it is the thing you hand to a user: one file, no folder
to keep together. `-OneDir` remains available if you want the unpacked form (faster
startup, easier to inspect).

### macOS → `AnyLoc.app` + `AnyLoc.pkg`
```bash
# one-time: make the build venv
python3 -m venv platforms/mac/.venv
platforms/mac/.venv/bin/pip install -U -r requirements.txt pyinstaller pywebview

./scripts/build-mac.sh          # shippable: platforms/mac/dist/AnyLoc-<version>.pkg
./scripts/build-mac.sh test     # test: platforms/mac/dist/AnyLocTest.app

# validate the test app without root:
platforms/mac/dist/AnyLocTest.app/Contents/MacOS/AnyLocTest --selftest   # expect RESULT: PASS
```
`scripts/build-mac.sh` auto-uses `platforms/mac/.venv`, checks that `pywebview` is
present (without it the app would silently degrade to a browser window), runs
PyInstaller (output → `platforms/mac/dist`), ad-hoc code-signs the `.app` and verifies
the signature, then wraps it into a `.pkg` that installs into `/Applications`.

The `.app` icon comes from `web/icon.icns`; regenerate it (and the Windows `.ico`) with
`python scripts/make_icon.py`, which uses Apple's `iconutil`.

### Dev mode (fast UI iteration, no packaging)

Run `python src/launcher.py --dev` after installing the requirements. It opens a
browser at `http://127.0.0.1:8766`; changes under `web/` refresh in about one second.
The iOS worker starts idle and uses the same explicit USB/Wi-Fi connection flow
as the native app. It creates a session's tunnel only when you click **Connect**.
iOS 17.4+ does not require elevation for these userspace paths. Only the older
iOS 17.0–17.3 USB fallback needs admin/root; no automatic tunneld monitor is started.

### 实体 Windows 机器验证步骤（待执行）

当前 DevBox 只验证了 mock 测试和软件 self-test，没有连接实体 iPhone。
以下步骤用于之后在有手机的 Windows 机器上验证，不能视为已通过的测试：

1. 安装 Python 3.12 和 iTunes / Apple Mobile Device Support。在仓库目录运行：

   ```powershell
   git pull
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -U pip
   .\.venv\Scripts\python.exe -m pip install -U -r requirements.txt
   .\.venv\Scripts\python.exe src\launcher.py
   ```

   如需测试打包版本，先退出源码应用，再运行 `.\scripts\build-win.ps1`，
   然后打开 `platforms\windows\dist\AnyLoc.exe`；正式 EXE 仍会弹 UAC。
2. 手机通过 USB 连接，解锁、信任电脑并开启开发者模式。先选择有线 USB，
   刷新并选择手机，点击连接；检查位置修改和“恢复真实定位”。
3. 切换 Wi-Fi，展开“首次使用：通过 USB 启用 Wi-Fi”，刷新 USB 设备列表，
   选中手机并点击“启用此设备的 Wi-Fi 连接”，等待成功。
4. 确认手机与电脑在同一局域网，**拔掉数据线**，保持手机解锁；刷新无线设备，
   选择手机后点击连接。核实此时确实没有 USB 连接。
5. 在手机上检查位置修改、恢复定位，以及断网、重新连接、关闭 AnyLoc 后的行为。
   保留日志和 iOS 版本；Wi-Fi 准备或发现失败时记录错误并继续使用 USB。

蓝牙不在本次实现范围内；没有可执行的蓝牙定位连接流程。

### App icon
The icon is generated from code — no binary editing. Regenerate `web/icon.ico`
(Windows), `web/icon.icns` (macOS) + `web/icon-256.png` with:
```bash
python scripts/make_icon.py     # needs Pillow: pip install pillow
```
The `.icns` step runs only on macOS (it shells out to Apple's `iconutil`).

---

## How it works (for the curious)

`backend.py` serves the static UI and JSON API, including device discovery,
explicit connection selection and USB Wi-Fi preparation. Its worker runs device
operations on a dedicated asyncio thread.

`ios_transport.py` owns each selected session's tunnel and RSD connection:

- USB on iOS 17.4+ uses `CoreDeviceTunnelProxy` through a lockdown client restricted
  to the `USB` connection type. Wi-Fi uses an authenticated RemotePairing service;
  it never silently switches to USB. Both feed an in-process `UserspaceRsdTunnel`.
- iOS 17.0–17.3 USB falls back to an explicit kernel tunnel for the selected USB
  device, only when the process is already elevated. No automatic tunnel monitors
  or background `tunneld` are started.
- Each context closes its RSD, services and tunnel before another session opens.
  An HTTP/2 PING checks the active RSD connection, including for Wi-Fi devices
  that are absent from usbmux.

`ios_backend.py` mounts the Developer Disk Image with `auto_mount(rsd)`, then
holds `DvtProvider(rsd)` and `LocationSimulation(dvt)` open for `loc.set(lat, lon)`
and `loc.clear()`.

`launcher.py` is the all-in-one entry point (what becomes `AnyLoc.exe` / `AnyLoc.app`).
It starts the web server + device worker on background threads and gives the **main
thread to the app window** — pywebview refuses to start anywhere else. The iOS
worker waits for the user's connection selection. Closing the window fires a
`closing` handler that asks the session to restore GPS, closes its tunnel, and
stops the web server before the process exits. POSIX signal handlers (`SIGHUP`/`SIGTERM`/
`SIGINT`) run that same teardown, because `atexit` does **not** run when a process is
killed by a signal — without them, quitting could leave the phone on the fake location.

The iOS 17.4+ userspace paths do not require admin/root on either OS. The Windows
release executable still requests elevation through its `requireAdministrator`
manifest. Source/test launches do not forcibly elevate; the old USB kernel
fallback reports a clear permissions error when admin/root is unavailable.

`gui.py` owns the window, and the check differs per OS because the failure modes do.
On **Windows** it probes the registry for the **Edge WebView2 Runtime**, because a
missing runtime raises no exception — pywebview silently falls back to the ancient
MSHTML engine, which would break the map while looking like it worked. If the runtime
really is absent, AnyLoc opens the UI in a dedicated Edge/Chrome app window instead and
watches that process for the close signal. On **macOS** the renderer is WKWebView from
the system WebKit framework: there is no separate runtime to install or miss, so it
simply imports the Cocoa backend and lets that answer the question.

`applog.py` exists because `console=False` means `sys.stdout` is `None` and `print()`
output is discarded without error. It installs harmless stand-in streams (several
libraries, uvicorn included, call `sys.stdout.isatty()` during setup and would crash on
`None`), writes the rotating log under `%LOCALAPPDATA%\AnyLoc\logs\` (Windows) /
`~/Library/Logs/AnyLoc/` (macOS), and keeps the last few hundred lines in memory for the
in-app **Logs** panel to read via `/api/logs`. Both path helpers resolve the *real*
user's home even when running under `sudo`, so an elevated run and an ordinary one write
to the same file instead of stranding it in root's home.

The current transport integration targets **pymobiledevice3 11.63.1**. DevBox
validation covers mocked transport behavior and software self-tests only;
physical USB/Wi-Fi location changes and GPS restoration remain to be tested.

---

## Legal / ethical

Location spoofing has legitimate uses (development, testing, privacy). Using it to defeat
another service's rules (games, check-in/attendance, dating, etc.) may violate their terms
and, in some cases (e.g. faking work attendance), the law. You are responsible for how you
use this. Provided as-is, for learning and legitimate testing.
