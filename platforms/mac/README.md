# platforms/mac

macOS release output. Build dependencies and work files are created under the
system temporary directory and removed automatically, so they never enter the
OneDrive repository.

- `dist/` — final `AnyLoc-<version>.pkg` only

Build with:  `./scripts/build-mac.sh`  (or `./scripts/build-mac.sh test`)
