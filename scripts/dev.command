#!/bin/bash
# ============================================================
#  AnyLoc — 开发模式 (DEV) · macOS
#  改界面：改完 web/ 里的文件，浏览器自动刷新，无需打包。
#
#  直接双击可调试界面及 iOS 17.4+ 的 USB / Wi-Fi 连接。
#  仅 iOS 17.0-17.3 USB 的旧内核隧道需要 sudo。
# ============================================================
set -e
# this script lives in scripts/, so cd up to the project root first
cd "$(dirname "$0")/.."

echo "============================================================"
echo "  热重载已启用；在界面选择 USB 或 Wi-Fi 后连接设备。"
echo "  仅 iOS 17.0-17.3 的 USB 连接需要 sudo。"
echo "============================================================"
echo

# Prefer a project venv if present, else fall back to python3 on PATH.
if [ -x "platforms/mac/.venv/bin/python" ]; then
  PY="platforms/mac/.venv/bin/python"
elif [ -x ".venv-mac/bin/python" ]; then
  PY=".venv-mac/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
fi

echo "使用 Python: $PY"
exec "$PY" src/launcher.py --dev
