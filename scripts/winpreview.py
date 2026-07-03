#!/usr/bin/env python3
"""
winpreview.py — 临时原型：把 AnyLoc 的网页装进一个原生窗口看看效果。
不影响 launcher / backend。指向当前 dev 服务器 (8766)。
运行： python scripts/winpreview.py
"""
import os
import sys
import webview

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/"
# this script lives in scripts/, so web/ is one level up (project root)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "web", "icon.ico")

if __name__ == "__main__":
    # 创建一个原生窗口（Windows 上用 Edge WebView2 内核渲染）
    webview.create_window(
        "AnyLoc",           # 窗口标题（任务栏也显示这个）
        url=URL,
        width=1180,
        height=800,
        min_size=(900, 620),
        confirm_close=False,
    )
    # gui=None 让 pywebview 自动挑后端（Windows -> edgechromium / WebView2）
    # icon 让窗口 + 任务栏用 AnyLoc 图标（部分 pywebview 版本支持 icon 参数）
    try:
        webview.start(icon=ICON)
    except TypeError:
        # 老版本 start() 不接受 icon 参数，退回不带图标
        webview.start()

