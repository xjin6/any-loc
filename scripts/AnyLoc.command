#!/bin/bash
# ============================================================
#  AnyLoc — macOS 一键启动（从源码运行，无需打包）
#
#  双击这个文件即可。它会：
#    1. 启动本地网页服务及原生应用窗口；
#    2. 在界面选择 USB / Wi-Fi 和设备，然后点击连接。
#
#  想停止：直接关掉这个终端窗口。
#
#  （给最终用户的“装进 Application 就能用”的版本是 AnyLoc.app / AnyLoc.pkg，
#    用 ./build-mac.sh 生成。这个 .command 主要给开发/免打包快速使用。）
# ============================================================
cd "$(dirname "$0")/.."

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

if [ -z "$PY" ]; then
  echo "找不到 python3。请先安装 Python 3.10+（brew install python 或 python.org）。"
  read -r -p "按回车键关闭…" _
  exit 1
fi

echo "使用 Python: $PY"
echo "在窗口选择 USB 或 Wi-Fi 后连接。iOS 17.4+ 无需 root。"
echo "仅 iOS 17.0-17.3 的 USB 连接需要手动使用 sudo 启动。"
echo

# Modern USB/Wi-Fi sessions create their own in-process tunnel.
exec "$PY" src/launcher.py
