# OpenSprite installer for Windows.
#
# Usage:
#   powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.ps1)"
#   .\install.ps1 -NoStart

param(
    [string]$Repo = "https://github.com/HsinPu/opensprite.git",
    [string]$Branch = "main",
    [string]$InstallDir = "$env:LOCALAPPDATA\OpenSprite\opensprite",
    [string]$AppHome = "$env:USERPROFILE\.opensprite",
    [switch]$Dev,
    [switch]$NoStart,
    [switch]$NoShim,
    [switch]$NoWeb
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
}

function Write-Info {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "OK  $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "!   $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "Error: $Message" -ForegroundColor Red
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory = (Get-Location).Path
    )

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList 2>&1 | ForEach-Object { $_ }
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $previousErrorAction
    }

    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $FilePath $($ArgumentList -join ' ')"
    }
}

function Sync-EnvPath {
    $currentPath = $env:Path
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $items = (($currentPath, $userPath, $machinePath) | Where-Object { $_ }) -join ";"
    $env:Path = (($items -split ";") | Where-Object { $_ } | Select-Object -Unique) -join ";"
}

function Add-UserPathEntry {
    param([string]$PathEntry)

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $items = if ($userPath) { $userPath -split ";" } else { @() }
    if ($items -notcontains $PathEntry) {
        $items += $PathEntry
        [Environment]::SetEnvironmentVariable("Path", ($items -join ";"), "User")
        $env:Path = "$PathEntry;$env:Path"
        Write-Success "Added $PathEntry to user PATH"
    }
}

function Resolve-Npm {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npm) { return $npm.Source }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { return $null }
    if ($npm.Source -like "*.ps1") {
        $cmd = Join-Path (Split-Path $npm.Source -Parent) "npm.cmd"
        if (Test-Path $cmd) { return $cmd }
    }
    return $npm.Source
}

function Test-NodeVersion {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { return $false }

    $versionText = (& $node.Source --version 2>$null).TrimStart("v")
    $parts = $versionText -split "\."
    if ($parts.Count -lt 2) { return $false }
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    return (($major -eq 20 -and $minor -ge 19) -or ($major -eq 22 -and $minor -ge 12) -or ($major -gt 22))
}

function Ensure-Git {
    Write-Info "Checking Git"
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Success "Git found"
        return
    }
    throw "Git was not found. Install Git for Windows from https://git-scm.com/download/win, then re-run this installer."
}

function Resolve-Python {
    Write-Info "Checking Python 3.11+"
    $candidates = @("py", "python")
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }

        $args = if ($candidate -eq "py") { @("-3.11", "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)") } else { @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)") }
        & $cmd.Source @args 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "$candidate provides Python 3.11+"
            return @{ FilePath = $cmd.Source; PrefixArgs = if ($candidate -eq "py") { @("-3.11") } else { @() } }
        }
    }

    throw "Python 3.11+ was not found. Install it from https://www.python.org/downloads/ or run: winget install Python.Python.3.11"
}

function Ensure-Node {
    if ($NoWeb) { return }

    Write-Info "Checking Node.js for Web UI build"
    if ((Test-NodeVersion) -and (Resolve-Npm)) {
        Write-Success "Node.js and npm found"
        return
    }

    throw "Node.js 20.19+ or 22.12+ with npm is required. Install from https://nodejs.org/ or run: winget install OpenJS.NodeJS.LTS"
}

function Install-Repository {
    Write-Info "Installing repository to $InstallDir"
    $parent = Split-Path $InstallDir -Parent
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    if (Test-Path (Join-Path $InstallDir ".git")) {
        Push-Location $InstallDir
        try {
            Invoke-Checked git @("-c", "windows.appendAtomically=false", "fetch", "origin")
            Invoke-Checked git @("-c", "windows.appendAtomically=false", "checkout", $Branch)
            Invoke-Checked git @("-c", "windows.appendAtomically=false", "pull", "--ff-only", "origin", $Branch)
        } finally {
            Pop-Location
        }
        return
    }

    if (Test-Path $InstallDir) {
        throw "Install path exists but is not a git checkout: $InstallDir"
    }

    Invoke-Checked git @("-c", "windows.appendAtomically=false", "clone", "--branch", $Branch, $Repo, $InstallDir)
    Push-Location $InstallDir
    try {
        Invoke-Checked git @("-c", "windows.appendAtomically=false", "config", "windows.appendAtomically", "false")
    } finally {
        Pop-Location
    }
}

function Install-PythonPackage {
    param([hashtable]$Python)

    $venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Info "Creating virtual environment"
        Invoke-Checked $Python.FilePath ($Python.PrefixArgs + @("-m", "venv", (Join-Path $InstallDir ".venv")))
    }

    $target = if ($Dev) { ".[dev]" } else { "." }
    Write-Info "Installing OpenSprite Python package"
    Invoke-Checked $venvPython @("-m", "pip", "install", "--upgrade", "pip") $InstallDir
    Invoke-Checked $venvPython @("-m", "pip", "install", "-e", $target) $InstallDir
}

function Install-WebFrontend {
    if ($NoWeb) { return }

    $webDir = Join-Path $InstallDir "apps\web"
    if (-not (Test-Path (Join-Path $webDir "package.json"))) { return }

    $npm = Resolve-Npm
    if (-not $npm) { throw "npm was not found." }

    Write-Info "Installing Web UI dependencies"
    if (Test-Path (Join-Path $webDir "package-lock.json")) {
        Invoke-Checked $npm @("ci") $webDir
    } else {
        Invoke-Checked $npm @("install") $webDir
    }

    Write-Info "Building Web UI"
    Invoke-Checked $npm @("run", "build") $webDir
}

function Ensure-DefaultConfig {
    $configPath = Join-Path $AppHome "opensprite.json"
    if (Test-Path $configPath) { return $configPath }

    Write-Info "Creating default OpenSprite config"
    New-Item -ItemType Directory -Force -Path $AppHome | Out-Null
    $venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
    $code = "from pathlib import Path; from opensprite.config import Config; Config.copy_template(Path(r'$configPath'))"
    Invoke-Checked $venvPython @("-c", $code) $InstallDir
    return $configPath
}

function Install-Shim {
    if ($NoShim) { return }

    $shimDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    $shimPath = Join-Path $shimDir "opensprite.cmd"
    $venvExe = Join-Path $InstallDir ".venv\Scripts\opensprite.exe"

    @"
@echo off
set "OPENSPRITE_INSTALL_DIR=$InstallDir"
"$venvExe" %*
"@ | Set-Content -Path $shimPath -Encoding ASCII

    Add-UserPathEntry $shimDir
    Write-Success "Installed command shim: $shimPath"
}

function Start-Gateway {
    if ($NoStart) { return }

    $configPath = Ensure-DefaultConfig
    $openSprite = Join-Path $InstallDir ".venv\Scripts\opensprite.exe"
    Write-Info "Starting OpenSprite background gateway"
    & $openSprite service stop 2>$null | Out-Null
    Invoke-Checked $openSprite @("service", "start", "--config", $configPath) $InstallDir
    Invoke-Checked $openSprite @("service", "status") $InstallDir
}

function Verify-Install {
    $openSprite = Join-Path $InstallDir ".venv\Scripts\opensprite.exe"
    Write-Info "Verifying CLI"
    Invoke-Checked $openSprite @("--version") $InstallDir
}

function Print-Success {
    Write-Host ""
    Write-Success "OpenSprite installed successfully."
    Write-Host ""
    Write-Host "Code: $InstallDir"
    Write-Host "Data: $AppHome"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  opensprite update"
    Write-Host "  opensprite service start"
    Write-Host "  opensprite service status"
    Write-Host "  opensprite service stop"
    Write-Host ""
    Write-Host "Logs:"
    Write-Host "  Get-Content -Wait `"$AppHome\logs\gateway.log`""
    Write-Host ""
    if (-not $NoShim) {
        Write-Host "If 'opensprite' is not found, open a new terminal or run:"
        Write-Host "  `$env:Path = `"$env:LOCALAPPDATA\Microsoft\WindowsApps;`$env:Path`""
        Write-Host ""
    }
}

try {
    Write-Info "OpenSprite Windows installer"
    Sync-EnvPath
    Ensure-Git
    $python = Resolve-Python
    Ensure-Node
    Install-Repository
    Install-PythonPackage $python
    Install-WebFrontend
    Install-Shim
    Verify-Install
    Start-Gateway
    Print-Success
} catch {
    Write-Fail $_.Exception.Message
    exit 1
}
