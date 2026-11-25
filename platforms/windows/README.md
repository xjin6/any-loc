# platforms/windows

Windows release output. Build dependencies and work files are created under
`%TEMP%` and removed automatically, so they never enter the OneDrive repository.

- `dist/` — final `AnyLoc.exe`

Build on a Windows machine with:
```powershell
.\scripts\build-win.ps1
```
(PyInstaller cannot cross-build a Windows .exe from macOS — build this on Windows.)
