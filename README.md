# AnyLoc

Local GPS simulation for iPhone, iPad, and Android on Windows and macOS.
Choose a device, click the map to teleport, or move with the joystick, WASD,
and road-following routes.

**[Product website](https://xjin6.github.io/any-loc/)** ·
**[Download the latest release](https://github.com/xjin6/any-loc/releases/latest)**

Device control and simulated coordinates stay on your computer. Map, search, and
route providers receive the normal network requests needed to serve those features.

## Features

- iOS 17+ over USB or experimental Wi-Fi
- Android 11+ over USB with bundled `adb`
- Click-to-teleport, joystick, WASD / arrow movement, and route walking
- Walk, run, bike, and drive speeds
- Native app windows on Windows and macOS
- Explicit device and transport selection
- Real-GPS restoration on reset, disconnect, and normal shutdown
- English, Chinese, and additional UI languages

## Download

| Platform | File | Notes |
|---|---|---|
| Windows 10/11 x64 | [`AnyLoc.exe`](https://github.com/xjin6/any-loc/releases/latest/download/AnyLoc.exe) | Single-file app; allow the UAC prompt |
| macOS Apple Silicon | [`AnyLoc-1.2.0.pkg`](https://github.com/xjin6/any-loc/releases/latest/download/AnyLoc-1.2.0.pkg) | Installs into `/Applications` |

The macOS build is ad-hoc signed and is not notarized. If Gatekeeper blocks the
first launch, right-click **AnyLoc** and choose **Open**. Intel Mac users can run
from source or build locally.

## Device requirements

### iPhone / iPad

- iOS 17 or newer
- **Developer Mode** enabled under `Settings > Privacy & Security`
- A data-capable USB cable for USB use and initial Wi-Fi setup
- On Windows, **Apple Mobile Device Support / iTunes** for the USB driver
- On macOS, no additional USB driver is required

### Android

- Android 11 or newer
- **Developer options** and **USB debugging** enabled
- Accept the **Allow USB debugging** prompt after connecting
- No root or phone-side app is required

Pure HarmonyOS NEXT devices without `adb` are not supported.

## Quick start

1. Open AnyLoc and connect the phone.
2. Select the connection type and device, then click **Connect**.
3. Click the map to change location, or use the joystick / keyboard controls.
4. Click **Reset real GPS** before disconnecting. Closing AnyLoc also requests
   restoration and waits for session cleanup.

### iOS over USB

Connect and unlock the iPhone, accept **Trust This Computer**, select **USB** in
AnyLoc, refresh the device list, and connect. Keep the cable attached during the
session.

### iOS over Wi-Fi

Wi-Fi needs one initial USB preparation:

1. Connect and unlock the iPhone by USB, trust the computer, and place the phone
   and computer on the same LAN.
2. Select **Wi-Fi** in AnyLoc. If the phone is not listed, expand
   **Can’t find your iPhone?**.
3. Refresh the USB device, select it, and click **Enable Wi-Fi**.
4. Unplug USB, keep the phone awake, refresh the iPhone list, and connect.

Wi-Fi uses Apple RemotePairing and remains experimental across iOS versions and
network configurations. USB remains available when wireless discovery fails.
Bluetooth is not supported for iOS location simulation.

### Android over USB

Connect the phone, accept the USB-debugging prompt, and click **Connect**.
AnyLoc detects Android automatically and uses the bundled `adb` to configure the
mock-location provider.

The first iOS connection may download and mount an Apple Developer Disk Image.
That step needs internet access and can take about a minute.

## Run from source

Python 3.10 or newer is required.

```bash
git clone https://github.com/xjin6/any-loc.git
cd any-loc
python3 -m venv .venv
.venv/bin/python -m pip install -U pip -r requirements.txt
.venv/bin/python src/launcher.py
```

On Windows, use `py -3.12 -m venv .venv` and
`.venv\Scripts\python.exe` in place of `.venv/bin/python`.

For UI development with browser refresh:

```bash
.venv/bin/python src/launcher.py --dev
```

## Build releases

The build scripts create disposable environments outside the repository and
leave only the final artifact under `platforms/<os>/dist/`.

### Windows

```powershell
.\scripts\build-win.ps1
# platforms\windows\dist\AnyLoc.exe
```

The script verifies that the result is a Windows GUI executable. The repository
also includes a manually triggered GitHub Actions workflow at
`.github/workflows/build-windows.yml`.

### macOS

```bash
./scripts/build-mac.sh
# platforms/mac/dist/AnyLoc-<version>.pkg
```

The script builds the app, ad-hoc signs it, verifies the signature, and creates
an installer for `/Applications`. `VERSION` is the release version source for
both platforms.

## Tests

The transport and connection tests use mock devices and do not change a real
phone:

```bash
python -m unittest tests.test_ios_transport tests.test_worker_connections
```

Packaged test builds support `--selftest` to verify the web stack and frozen
imports without opening a device tunnel.

## Troubleshooting

- **Phone not listed:** unlock it, accept Trust / USB debugging, and refresh.
- **Wi-Fi phone not listed:** keep it awake, confirm both devices are on the same
  LAN, repeat the one-time USB setup, or use USB.
- **First iOS connection is slow:** wait for the Developer Disk Image download.
- **Windows window does not open:** install the Microsoft Edge WebView2 Runtime.
- **Only run one AnyLoc instance:** multiple processes cannot safely own the same
  phone session.

Logs:

```text
Windows: %LOCALAPPDATA%\AnyLoc\logs\anyloc.log
macOS:   ~/Library/Logs/AnyLoc/anyloc.log
```

## Project structure

```text
src/          Python launcher, device backends, transports, and logging
web/          Native-window web UI and vendored Leaflet assets
vendor/       Bundled Android platform tools
scripts/      Development and release build helpers
tests/        Mocked iOS transport and connection tests
AnyLoc.spec   Shared PyInstaller specification
VERSION       Release version
```

The app window talks to a local HTTP backend. iOS sessions use
`pymobiledevice3` and Apple DVT `LocationSimulation`; Android sessions use
`adb cmd location`. Each iOS session owns and closes its USB or Wi-Fi tunnel.

## Product website

The product site is published from the independent `gh-pages` branch at
**https://xjin6.github.io/any-loc/**. Application source and releases remain on
`main`; updating either branch does not overwrite the other.

## Responsible use

AnyLoc is intended for development, testing, geofence validation, route
simulation, and other legitimate uses. Using location simulation to deceive a
service or violate its terms is your responsibility.
