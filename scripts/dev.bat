@echo off
REM ============================================================
REM  AnyLoc — 开发模式 (DEV)
REM  改界面：改完 web/ 里的文件，浏览器自动刷新，无需打包。
REM
REM  普通双击可调试界面及 iOS 17.4+ 的 USB / Wi-Fi 连接。
REM  iOS 17.0-17.3 的 USB 连接需要右键“以管理员身份运行”。
REM
REM  这里【故意】保留控制台窗口。打包出来的 AnyLoc.exe 是无控制台的窗口程序
REM  （日志在 %LOCALAPPDATA%\AnyLoc\logs\anyloc.log），但开发模式要的就是
REM  能直接看到日志、能按 Ctrl+C 停。--dev 走的是浏览器 + 热重载，根本不加载
REM  原生窗口那套（gui.py / pywebview），所以别把这里也改成无控制台。
REM ============================================================
title AnyLoc DEV
REM this script lives in scripts/, so cd up to the project root first
cd /d "%~dp0.."

echo 热重载已启用；在界面选择 USB 或 Wi-Fi 后连接设备。
echo 仅 iOS 17.0-17.3 的 USB 连接需要管理员权限。
echo.
python src\launcher.py --dev
pause
