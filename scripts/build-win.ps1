# =============================================================================
#  build-win.ps1 — 在 Windows 上把 AnyLoc 打包成单个 .exe
# =============================================================================
#  产物（输出到 platforms\windows\dist\）：
#    platforms\windows\dist\AnyLoc.exe       —— 双击运行（会弹 UAC 提权）
#    platforms\windows\dist\AnyLocTest.exe   —— 测试版，不提权，可 --selftest
#
#  用法（PowerShell）：
#    .\scripts\build-win.ps1              # 正式版（带 requireAdministrator）
#    .\scripts\build-win.ps1 test         # 测试版（无提权，方便验证）
#    .\scripts\build-win.ps1 test -OneDir # 文件夹形式，不打成单文件
#
#  这是「窗口版」构建：打出来的 exe 是真正的 GUI 程序（PE subsystem = GUI），
#  不会再弹黑色控制台窗口。因此 stdout 是 None，程序的日志只在
#    %LOCALAPPDATA%\AnyLoc\logs\anyloc.log
#  里；出问题先看那个文件。
#
#  依赖：Python 3.10+。脚本在 %TEMP% 中创建一次性 venv 和工作目录，
#  结束时自动清理，不把构建依赖或中间文件写进 OneDrive 仓库。
# =============================================================================
[CmdletBinding()]
param(
    # "final"（默认）= 正式版，带 requireAdministrator 清单；"test" = 测试版，不提权。
    [ValidateSet("final", "test")]
    [string]$Variant = "final",

    # 打成文件夹而不是单文件。单文件是默认，也是发给用户的形式。
    [switch]$OneDir,

    # 保留参数兼容性；一次性环境中不能跳过依赖安装。
    [switch]$SkipDeps,

    # Optional interpreter path when several Python versions are installed.
    [string]$PythonExe
)

# 任何未捕获的错误都要让脚本立刻失败 —— 半成品 exe 比没有 exe 更糟。
$ErrorActionPreference = "Stop"

# this script lives in scripts\, so cd up to the project root first
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$WinDir  = "platforms\windows"
$DistDir = "$WinDir\dist"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("anyloc-win-build-" + [guid]::NewGuid().ToString("N"))
$VenvDir = Join-Path $TempRoot "venv"
$WorkDir = Join-Path $TempRoot "work"
$VenvPy  = "$VenvDir\Scripts\python.exe"

if ($Variant -eq "test") { $AppName = "AnyLocTest" } else { $AppName = "AnyLoc" }
$ExePath = "$DistDir\$AppName.exe"

Write-Host ""
Write-Host "==> AnyLoc Windows 构建：variant=$Variant  onedir=$($OneDir.IsPresent)" -ForegroundColor Cyan

# ---- 0) find a Python to CREATE the venv with -------------------------------
# 只用来造 venv；真正打包用的是 venv 里那个解释器。
function Find-BootstrapPython {
    $candidates = if ($PythonExe) { @($PythonExe) } else { @("py", "python3", "python") }
    foreach ($cand in $candidates) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # `py` 是启动器，要 -3 才拿得到版本；其余直接问。
        if ($cand -eq "py") { $v = (& $cand -3 --version 2>&1) } else { $v = (& $cand --version 2>&1) }
        if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python (\d+)\.(\d+)') {
            if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10) { return $cand }
        }
    }
    return $null
}

# Everything after this point is wrapped so failures and `exit` also remove the
# temporary environment. Only the final exe remains under platforms\windows\dist.
try {
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

# ---- 1) create the disposable build venv -----------------------------------
if (-not (Test-Path $VenvPy)) {
    $boot = Find-BootstrapPython
    if (-not $boot) {
        Write-Host "!! 找不到 Python 3.10+。请安装 Python 3.12，或用 -PythonExe 指定解释器路径。" -ForegroundColor Red
        exit 1
    }
    Write-Host "==> [1/4] 在临时目录新建一次性构建环境 $VenvDir （用 $boot）..."
    if ($boot -eq "py") { & $boot -3 -m venv $VenvDir } else { & $boot -m venv $VenvDir }
    if ($LASTEXITCODE -ne 0) { Write-Host "!! 创建虚拟环境失败。" -ForegroundColor Red; exit 1 }
}
Write-Host "    Python: $VenvPy  ($(& $VenvPy --version 2>&1))"

# ---- 2) dependencies --------------------------------------------------------
# pywebview 必须装在 *这个* venv 里：PyInstaller 只打包它看得见的东西，
# 而窗口版没有它就只剩浏览器兜底。
if ($SkipDeps) {
    Write-Host "    -SkipDeps 已忽略：一次性构建环境必须安装依赖。" -ForegroundColor Yellow
}
    Write-Host "==> [2/4] 安装依赖（requirements + pyinstaller + pywebview）..."
    & $VenvPy -m pip install -U pip 2>&1 | Out-Null
    & $VenvPy -m pip install -U -r requirements.txt pyinstaller pywebview
    if ($LASTEXITCODE -ne 0) {
        # 常见于公司内网：pip 被配置成走内部镜像，而某些包（例如
        # pymobiledevice3 依赖的 hexdump，只有 sdist）在镜像上取不到，
        # 报 HTTP 400。这不是我们的代码有问题，换回官方 PyPI 再试一次就好。
        Write-Host "    默认源装不上，改用官方 PyPI 重试一次 ..." -ForegroundColor Yellow
        & $VenvPy -m pip install -U --index-url https://pypi.org/simple `
            -r requirements.txt pyinstaller pywebview
        if ($LASTEXITCODE -ne 0) { Write-Host "!! pip 安装失败。" -ForegroundColor Red; exit 1 }
    }

# 打包前确认 pywebview 真的在 venv 里，而不是碰巧在系统 Python 里。
# 装错地方的话 PyInstaller 不会报错，只会打出一个永远走浏览器兜底的 exe。
& $VenvPy -c "import webview, importlib.metadata as m; print('    pywebview ' + m.version('pywebview'))"
if ($LASTEXITCODE -ne 0) {
    Write-Host "!! 这个虚拟环境里没有 pywebview —— 打出来的 exe 会没有原生窗口。" -ForegroundColor Red
    exit 1
}

# ---- 3) PyInstaller ---------------------------------------------------------
Write-Host "==> [3/4] PyInstaller 打包 ($Variant) → $DistDir ..."
$env:ANYLOC_VARIANT = $Variant
if ($OneDir) { $env:ANYLOC_ONEDIR = "1" } else { Remove-Item Env:\ANYLOC_ONEDIR -ErrorAction SilentlyContinue }

# 重试循环。两种偶发错误都会被它兜住，重试基本都能过：
#   1) PyInstaller 6.2x 偶发 OSError: [Errno 22] Invalid argument
#      （isolated\_child.py 里）—— 派子进程跑 hook 时的管道竞争，跟我们无关。
#   2) --clean 删旧 build 目录时 PermissionError / WinError 5 ——
#      OneDrive 同步或杀软正好在扫那个目录，抓着句柄不放。项目就在
#      OneDrive 文件夹里，所以这个尤其常见。
# 所以每次重试前先自己把 build 目录清干净（清不掉就让 PyInstaller 自己再试）。
$maxAttempts = 3
$built = $false
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    if ($attempt -gt 1) {
        Write-Host "    第 $attempt/$maxAttempts 次尝试（上次是偶发错误）..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
        # 抢在 PyInstaller 前面清一次：它的 --clean 失败是直接放弃，我们可以重试。
        if (Test-Path $WorkDir) {
            try { Remove-Item -Recurse -Force $WorkDir -ErrorAction Stop }
            catch { Write-Host "    (build 目录还被占用，交给 PyInstaller 再试)" -ForegroundColor DarkGray }
        }
    }
    & $VenvPy -m PyInstaller --clean --noconfirm `
        --distpath $DistDir --workpath $WorkDir `
        AnyLoc.spec
    if ($LASTEXITCODE -eq 0) { $built = $true; break }
    Write-Host "    PyInstaller 退出码 $LASTEXITCODE" -ForegroundColor Yellow
}
if (-not $built) {
    Write-Host "!! 打包失败（重试 $maxAttempts 次都没过）。往上翻看真正的报错。" -ForegroundColor Red
    exit 1
}

# ---- 4) verify the artifact -------------------------------------------------
# onedir 时 exe 在 dist\<name>\ 里面。
if ($OneDir) { $ExePath = "$DistDir\$AppName\$AppName.exe" }
if (-not (Test-Path $ExePath)) {
    Write-Host "!! 打包报告成功，但找不到 $ExePath" -ForegroundColor Red
    exit 1
}

Write-Host "==> [4/4] 检查产物 ..."
$exeItem = Get-Item $ExePath
Write-Host ("    {0}  ({1:N1} MB)" -f $exeItem.FullName, ($exeItem.Length / 1MB))

# 直接读 PE 头确认这是 GUI 程序而不是控制台程序。这正是「没有黑框」的定义，
# 也是最容易被 spec 里一个字改回去的东西 —— 所以每次构建都验一遍。
#   PE 头偏移在 0x3C；Optional Header 的 Subsystem 在 PE 签名 +0x5C。
#   2 = IMAGE_SUBSYSTEM_WINDOWS_GUI，3 = WINDOWS_CUI。
# 只读前 4KB —— 整个 exe 有 50MB，为了两个字段没必要全读进内存。
$fs = [System.IO.File]::OpenRead($ExePath)
try {
    $bytes = New-Object byte[] 4096
    $null = $fs.Read($bytes, 0, 4096)
} finally { $fs.Dispose() }
$peOff = [BitConverter]::ToInt32($bytes, 0x3C)
$subsystem = [BitConverter]::ToUInt16($bytes, $peOff + 0x5C)
if ($subsystem -eq 2) {
    Write-Host "    PE subsystem: GUI (2) —— 没有控制台窗口 ✓" -ForegroundColor Green
} else {
    Write-Host "    PE subsystem: $subsystem （期望 2 = GUI）—— 会弹黑框！" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  ✓ 完成！"
Write-Host "    Exe:  $ExePath"
Write-Host ""
if ($Variant -eq "test") {
    Write-Host "  验证（不需要管理员）："
    Write-Host "    $ExePath --selftest        # 期望 RESULT: PASS"
} else {
    Write-Host "  这是正式版：双击会弹 UAC 提权，然后开出 AnyLoc 应用窗口。"
    Write-Host "  把 $ExePath 发给用户即可，就这一个文件。"
}
Write-Host ""
Write-Host "  没有控制台了，日志在："
Write-Host "    $env:LOCALAPPDATA\AnyLoc\logs\anyloc.log"
Write-Host "============================================================"
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        # The generated GUID directory must be an immediate child of %TEMP%.
        $resolvedTemp = [System.IO.Path]::GetFullPath($TempRoot)
        $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedTemp) -like "anyloc-win-build-*") {
            Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host "==> 已清理临时构建环境。" -ForegroundColor DarkGray
        } else {
            Write-Warning "拒绝清理意外路径：$resolvedTemp"
        }
    }
}
