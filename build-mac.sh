#!/bin/bash
# =============================================================================
#  build-mac.sh — 在 macOS 上把 AnyLoc 打包成 .app 和 .pkg
# =============================================================================
#  产物：
#    dist/AnyLoc.app          —— 可直接拖进「应用程序」双击运行
#    dist/AnyLoc-1.0.0.pkg    —— 安装包，双击后自动装进 /Applications
#
#  用法：
#    ./build-mac.sh              # 正式版（带 root 提权流程）
#    ./build-mac.sh test         # 测试版（AnyLocTest.app，可 --selftest 验证）
#
#  依赖：Python 3.10+，并已安装 pymobiledevice3 与 pyinstaller。
#  脚本会优先使用项目里的 .venv-mac / .venv 虚拟环境。
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

VARIANT="${1:-final}"
VERSION="1.0.0"

if [ "$VARIANT" = "test" ]; then
  APP_NAME="AnyLocTest"
  BUNDLE_ID="com.anyloc.app.test"
else
  APP_NAME="AnyLoc"
  BUNDLE_ID="com.anyloc.app"
fi

# ---- pick a Python interpreter ---------------------------------------------
if [ -x ".venv-mac/bin/python" ]; then
  PY=".venv-mac/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
fi
echo "==> 使用 Python: $PY  ($("$PY" --version 2>&1))"

if ! "$PY" -c "import PyInstaller" 2>/dev/null; then
  echo "!! 没装 PyInstaller。请先： $PY -m pip install -U pyinstaller pymobiledevice3"
  exit 1
fi

# ---- 1) build the .app with PyInstaller ------------------------------------
echo "==> [1/3] PyInstaller 打包 ($VARIANT) ..."
ANYLOC_VARIANT="$VARIANT" "$PY" -m PyInstaller --clean --noconfirm AnyLoc.spec

APP_PATH="dist/${APP_NAME}.app"
if [ ! -d "$APP_PATH" ]; then
  echo "!! 打包失败：找不到 $APP_PATH"
  exit 1
fi
echo "    生成： $APP_PATH"

# ---- 1.5) make the inner CLI executable ad-hoc code-signed -----------------
# Unsigned apps on Apple Silicon must at least be ad-hoc signed to run.
echo "==> 对 .app 做 ad-hoc 代码签名（避免 Gatekeeper 直接拒绝）..."
codesign --force --deep --sign - "$APP_PATH" 2>/dev/null || \
  echo "    (codesign 失败，忽略——首次打开时右键→打开即可)"

# ---- 2) build a component .pkg that installs into /Applications ------------
echo "==> [2/3] 生成安装包 .pkg ..."
PKG_ROOT="$(mktemp -d)"
COMPONENT_DIR="$(mktemp -d)"
COMPONENT_PKG="$COMPONENT_DIR/component.pkg"
COMPONENT_PLIST="$COMPONENT_DIR/component.plist"

# Clean up temp trees no matter how we exit (success or failure).
trap 'rm -rf "$PKG_ROOT" "$COMPONENT_DIR"' EXIT

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

OUT_PKG="dist/${APP_NAME}-${VERSION}.pkg"
# productbuild wraps the component into a friendlier distribution installer.
productbuild \
  --package "$COMPONENT_PKG" \
  "$OUT_PKG"

echo
echo "============================================================"
echo "  ✓ 完成！"
echo "    App:  $APP_PATH"
echo "    Pkg:  $OUT_PKG"
echo
echo "  把 $OUT_PKG 发给用户，双击安装 → 应用进 /Applications。"
echo "  首次打开若被拦截：右键 AnyLoc.app → 打开 → 打开。"
echo "============================================================"
