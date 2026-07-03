@echo off
REM ============================================================
REM  AnyLoc — 开发模式 (DEV)
REM  改界面：改完 web/ 里的文件，浏览器自动刷新，无需打包。
REM
REM  两种用法：
REM   * 普通双击        = 只开网页，快速调界面（连不了真机）
REM   * 右键“以管理员身份运行” = 网页热重载 + 真机隧道，能实测定位
REM ============================================================
title AnyLoc DEV
REM this script lives in scripts/, so cd up to the project root first
cd /d "%~dp0.."

REM 检测是否管理员
net session >nul 2>&1
if %errorlevel%==0 (
  echo [管理员模式] 热重载 + 真机隧道，可实测定位。
) else (
  echo [普通模式] 只开网页，改界面用。
  echo.
  echo   想连真机实测定位？请关掉这个窗口，
  echo   改为【右键 dev.bat ^> 以管理员身份运行】。
)
echo.
python launcher.py --dev
pause
