#!/bin/bash
# ============================================================
#  AnyLoc — 开发模式 (DEV) · macOS
#  改界面：改完 web/ 里的文件，浏览器自动刷新，无需打包。
#
#  两种用法：
#   * 直接双击 / ./dev.command   = 只开网页，快速调界面（连不了真机）
#   * sudo ./dev.command          = 网页热重载 + 真机隧道，能实测定位
# ============================================================
set -e
# this script lives in scripts/, so cd up to the project root first
cd "$(dirname "$0")/.."

echo "============================================================"
if [ "$(id -u)" = "0" ]; then
  echo "  [root 模式] 热重载 + 真机隧道，可实测定位。"
else
  echo "  [普通模式] 只开网页，改界面用。"
  echo
  echo "  想连真机实测定位？请关掉，改用：  sudo ./dev.command"
fi
echo "============================================================"
echo

# Prefer a project venv if present, else fall back to python3 on PATH.
if [ -x ".venv-mac/bin/python" ]; then
  PY=".venv-mac/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
fi

echo "使用 Python: $PY"
exec "$PY" launcher.py --dev
