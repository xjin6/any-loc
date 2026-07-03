#!/bin/bash
# ============================================================
#  AnyLoc — macOS 一键启动（从源码运行，无需打包）
#
#  双击这个文件即可。它会：
#    1. 用 sudo 请求 root（连接 iPhone 的隧道需要），会让你输入开机密码；
#    2. 启动本地网页服务 + 隧道；
#    3. 自动打开浏览器 http://127.0.0.1:8765 。
#
#  想停止：直接关掉这个终端窗口。
#
#  （给最终用户的“装进 Application 就能用”的版本是 AnyLoc.app / AnyLoc.pkg，
#    用 ./build-mac.sh 生成。这个 .command 主要给开发/免打包快速使用。）
# ============================================================
cd "$(dirname "$0")/.."

# Prefer a project venv if present, else fall back to python3 on PATH.
if [ -x ".venv-mac/bin/python" ]; then
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
echo "AnyLoc 需要 root 来创建到 iPhone 的隧道，接下来会让你输入开机密码。"
echo

# launcher.py itself will re-exec under sudo when it detects it's not root and
# it's attached to a TTY (this window). Running it directly is enough.
exec "$PY" launcher.py
