#Requires -Version 5.1
<#
  Build PyInstaller onedir + optional Inno Setup installer.
  Version: edit APP_VERSION in version_info.py only; script reads it for Inno.

  Prerequisites:
    pip install -r requirements-build.txt
    Inno Setup 6 (optional, for Setup.exe)

  Usage:
    .\build_installer.ps1
    .\build_installer.ps1 -SkipInno
#>
param(
    [switch] $SkipInno
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Prefer project venv so Selenium / webdriver-manager are bundled (pip install -r requirements.txt).
$PythonExe = $null
foreach ($rel in @("venv\Scripts\python.exe", ".venv\Scripts\python.exe")) {
    $cand = Join-Path $PSScriptRoot $rel
    if (Test-Path $cand) { $PythonExe = $cand; break }
}
if (-not $PythonExe) {
    $PythonExe = "py"
    Write-Warning "No venv\Scripts\python.exe found; using 'py'. Install deps: pip install -r requirements.txt"
}

function Get-AppVersion {
    $raw = Get-Content -Path "version_info.py" -Raw -Encoding UTF8
    if ($raw -match 'APP_VERSION\s*=\s*"([^"]+)"') {
        return $Matches[1].Trim()
    }
    throw "Cannot parse APP_VERSION from version_info.py"
}

$AppVersion = Get-AppVersion
Write-Host ("Version: " + $AppVersion)

$icon = Join-Path $PSScriptRoot "data\openclaw.ico"
$iconArg = @()
if (Test-Path $icon) { $iconArg = @("--icon", $icon) }

$pyiArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", "TreasureClawLauncher",
    "--add-data", "data;data",
    "--add-data", "test.py;.",
    "--add-data", "version_info.py;.",
    "--add-data", "external_data.py;.",
    "--collect-all", "selenium",
    "--collect-all", "webdriver_manager",
    "--collect-all", "PIL",
    "--collect-submodules", "tkinter",
    "--hidden-import", "tkinter",
    "--hidden-import", "tkinter.messagebox",
    "--hidden-import", "tkinter.ttk",
    "--hidden-import", "tkinter.font",
    "--hidden-import", "tkinter.filedialog",
    "--hidden-import", "tkinter.simpledialog",
    "--hidden-import", "requests",
    "--hidden-import", "json",
    "--hidden-import", "selenium.webdriver",
    "--hidden-import", "selenium.webdriver.chrome.service",
    "--hidden-import", "selenium.webdriver.chrome.options",
    "--hidden-import", "selenium.webdriver.common.by",
    "--hidden-import", "selenium.webdriver.support.ui",
    "--hidden-import", "selenium.webdriver.support.expected_conditions",
    "--hidden-import", "selenium.webdriver.common.action_chains",
    "--hidden-import", "selenium.webdriver.common.keys",
    "--hidden-import", "selenium.common.exceptions",
    "--hidden-import", "webdriver_manager",
    "--hidden-import", "webdriver_manager.chrome",
    "--hidden-import", "PIL.Image",
    "--hidden-import", "PIL.ImageTk",
    "--hidden-import", "PIL.ImageSequence"
) + $iconArg + @("launcher.py")

Write-Host ("Using Python: " + $PythonExe)
Write-Host "Ensuring dependencies (requirements.txt + requirements-build.txt)..."
& $PythonExe -m pip install -q -r (Join-Path $PSScriptRoot "requirements.txt") -r (Join-Path $PSScriptRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "Running PyInstaller..."
& $PythonExe @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$distDir = Join-Path $PSScriptRoot "dist\TreasureClawLauncher"
if (-not (Test-Path $distDir)) { throw "Missing output: $distDir" }

# launcher 讀取的是 update_config.json（與 exe 同層）；必須一併打入 dist 供 Inno 安裝
foreach ($f in @("update_config.json", "update_config.example.json", "update_manifest.example.json")) {
    $src = Join-Path $PSScriptRoot $f
    if (Test-Path $src) {
        Copy-Item -Force $src (Join-Path $distDir $f)
    }
}

Write-Host ("Output: " + $distDir)

if ($SkipInno) {
    Write-Host "Skipped Inno (-SkipInno)."
    exit 0
}

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    $iscc = "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $iscc)) {
    Write-Warning "ISCC.exe not found. Install Inno Setup 6 or use -SkipInno."
    exit 0
}

& $iscc "/DMyAppVersion=$AppVersion" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
$out = Join-Path $PSScriptRoot "installer_output"
Write-Host ("Installer output: " + $out)
