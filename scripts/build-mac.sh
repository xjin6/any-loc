#!/bin/bash
# =============================================================================
#  build-mac.sh — 在 macOS 上把 AnyLoc 打包成 .app 和 .pkg
# =============================================================================
#  产物（输出到 platforms/mac/dist/）：
#    platforms/mac/dist/AnyLoc-1.1.0.pkg    —— 安装包，双击装进 /Applications
#
#  用法：
#    ./build-mac.sh              # 正式版（带 root 提权流程）
#    ./build-mac.sh test         # 测试版（AnyLocTest.app，可 --selftest 验证）
#
#  依赖：Python 3.10+。脚本在系统临时目录中创建一次性 venv，
#  安装打包依赖，并在成功或失败后自动清理，不写入 OneDrive 仓库。
# =============================================================================
set -euo pipefail
# this script lives in scripts/, so cd up to the project root first
cd "$(dirname "$0")/.."

VARIANT="${1:-final}"
# Version comes from the VERSION file at the repo root — the same file
# AnyLoc.spec reads for CFBundleVersion. Keeping one source means the .pkg
# filename and the version inside the .app can never disagree.
VERSION="$(tr -d '[:space:]' < VERSION 2>/dev/null || true)"
if [ -z "$VERSION" ]; then
  echo "!! 读不到 VERSION 文件（仓库根目录），无法确定版本号。"
  exit 1
fi

# Only the final installer lives in the repo. Venv, work files and the temporary
# .app all live outside OneDrive and are removed on exit.
MAC_DIR="platforms/mac"
DIST_DIR="$MAC_DIR/dist"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/anyloc-mac-build.XXXXXX")"
VENV_DIR="$TEMP_ROOT/venv"
WORK_DIR="$TEMP_ROOT/work"
TEMP_DIST="$TEMP_ROOT/dist"
trap 'rm -rf "$TEMP_ROOT"' EXIT INT TERM

if [ "$VARIANT" = "test" ]; then
  APP_NAME="AnyLocTest"
  BUNDLE_ID="com.anyloc.app.test"
else
  APP_NAME="AnyLoc"
  BUNDLE_ID="com.anyloc.app"
fi

# ---- create an isolated, disposable build environment ----------------------
BOOT_PY="$(command -v python3 || true)"
if [ -z "$BOOT_PY" ]; then
  echo "!! 找不到 Python 3.10+。"
  exit 1
fi
echo "==> 在临时目录创建一次性构建环境：$TEMP_ROOT"
"$BOOT_PY" -m venv "$VENV_DIR"
PY="$VENV_DIR/bin/python"
"$PY" -m pip install -U pip >/dev/null
"$PY" -m pip install -U -r requirements.txt pyinstaller pywebview
"$PY" -c "import PyInstaller, webview"

# The .app icon is generated, not committed as a binary blob. Warn (don't fail)
# so a missing Pillow doesn't block a build.
if [ ! -f "web/icon.icns" ]; then
  echo "!! 提示：缺少 web/icon.icns，App 将没有图标。"
  echo "   生成： $PY scripts/make_icon.py   （需要 Pillow）"
fi

# ---- 1) build the .app with PyInstaller ------------------------------------
echo "==> [1/3] PyInstaller 打包 ($VARIANT) → 临时目录 ..."
ANYLOC_VARIANT="$VARIANT" "$PY" -m PyInstaller --clean --noconfirm \
  --distpath "$TEMP_DIST" --workpath "$WORK_DIR" \
  AnyLoc.spec

APP_PATH="$TEMP_DIST/${APP_NAME}.app"
if [ ! -d "$APP_PATH" ]; then
  echo "!! 打包失败：找不到 $APP_PATH"
  exit 1
fi
echo "    生成： $APP_PATH"

# ---- 1.5) make the inner CLI executable ad-hoc code-signed -----------------
# Unsigned apps on Apple Silicon must at least be ad-hoc signed to run.
#
# Deliberately NOT `--deep`: Apple has formally deprecated it for signing, and it
# would re-sign the BUNDLED adb too — replacing Google's valid Developer ID
# signature (TeamIdentifier EQHXZ8M8AV, hardened runtime) with an ad-hoc one.
# Correct practice is inside-out: sign nested code first, then the bundle, and
# leave already-validly-signed third-party binaries alone.
echo "==> 对 .app 做 ad-hoc 代码签名（避免 Gatekeeper 直接拒绝）..."
# Strip extended attributes FIRST. codesign refuses a bundle carrying resource
# forks / Finder info ("resource fork, Finder information, or similar detritus
# not allowed"), and macOS attaches com.apple.provenance (and OneDrive/iCloud
# add their own) to files as they are written — so a build from a synced folder
# fails to sign for reasons that have nothing to do with the code.
xattr -cr "$APP_PATH" 2>/dev/null || true
codesign --force --sign - "$APP_PATH" 2>/dev/null || \
  echo "    (codesign 失败，忽略——首次打开时右键→打开即可)"

# Prove the signature is actually valid rather than assuming it took.
if codesign --verify --strict "$APP_PATH" 2>/dev/null; then
  echo "    签名校验通过 ✓"
else
  echo "    !! 签名校验未通过（首次打开时右键→打开即可）"
fi

# ---- 2) build a component .pkg that installs into /Applications ------------
echo "==> [2/3] 生成安装包 .pkg ..."
PKG_ROOT="$TEMP_ROOT/pkg-root"
COMPONENT_DIR="$TEMP_ROOT/pkg-component"
mkdir -p "$PKG_ROOT" "$COMPONENT_DIR" "$DIST_DIR"
COMPONENT_PKG="$COMPONENT_DIR/component.pkg"
COMPONENT_PLIST="$COMPONENT_DIR/component.plist"

mkdir -p "$PKG_ROOT/Applications"
cp -R "$APP_PATH" "$PKG_ROOT/Applications/"

# Force a fixed install location: without a component plist, pkgbuild marks the
# app "relocatable by bundle id", so if a same-id app already exists elsewhere
# the installer may drop it THERE instead of /Applications. Pin it down.
PLIST_ARGS=()
pkgbuild --analyze --root "$PKG_ROOT" "$COMPONENT_PLIST" >/dev/null 2>&1 || true
if [ -f "$COMPONENT_PLIST" ]; then
  /usr/libexec/PlistBuddy -c "Set :0:BundleIsRelocatable false" "$COMPONENT_PLIST" 2>/dev/null || true
  PLIST_ARGS=(--component-plist "$COMPONENT_PLIST")
fi

pkgbuild \
  --root "$PKG_ROOT" \
  --install-location "/" \
  "${PLIST_ARGS[@]+"${PLIST_ARGS[@]}"}" \
  --identifier "$BUNDLE_ID" \
  --version "$VERSION" \
  "$COMPONENT_PKG"

OUT_PKG="$DIST_DIR/${APP_NAME}-${VERSION}.pkg"
# productbuild wraps the component into a friendlier distribution installer.
productbuild \
  --package "$COMPONENT_PKG" \
  "$OUT_PKG"

echo
echo "============================================================"
echo "  ✓ 完成！"
echo "    Pkg:  $OUT_PKG"
echo "    临时 venv / work / App 已自动清理，仓库只保留 Pkg。"
echo
echo "  把 $OUT_PKG 发给用户，双击安装 → 应用进 /Applications。"
echo "  首次打开若被拦截：右键 AnyLoc.app → 打开 → 打开。"
echo "============================================================"
