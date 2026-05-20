# OpenSprite uninstaller for Windows installs created by scripts/install.ps1.

param(
    [string]$InstallDir = "$env:LOCALAPPDATA\OpenSprite\opensprite",
    [string]$AppHome = "$env:USERPROFILE\.opensprite",
    [switch]$Full,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

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

function Test-UnsafePath {
    param([string]$Path)
    if (-not $Path) { return $true }
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    $homePath = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd("\")
    return ($resolved -eq [System.IO.Path]::GetPathRoot($resolved).TrimEnd("\") -or $resolved -eq $homePath)
}

function Confirm-Uninstall {
    if ($Yes) { return }

    Write-Host "OpenSprite uninstall will remove:"
    Write-Host "  Command: $env:LOCALAPPDATA\Microsoft\WindowsApps\opensprite.cmd"
    Write-Host "  Code:    $InstallDir"
    if ($Full) {
        Write-Host "  Data:    $AppHome"
        Write-Warn "Full uninstall deletes configs, sessions, memories, logs, and local databases."
    } else {
        Write-Host "  Data:    kept at $AppHome"
    }
    Write-Host ""
    $answer = Read-Host "Type 'yes' to continue"
    if ($answer -ne "yes") {
        Write-Host "Uninstall cancelled."
        exit 0
    }
}

function Stop-Gateway {
    $openSprite = Join-Path $InstallDir ".venv\Scripts\opensprite.exe"
    if (Test-Path $openSprite) {
        Write-Info "Stopping OpenSprite gateway"
        & $openSprite service stop 2>$null | Out-Null
        return
    }

    $pidPath = Join-Path $AppHome "gateway.pid"
    if (Test-Path $pidPath) {
        $pidText = Get-Content $pidPath -ErrorAction SilentlyContinue
        if ($pidText -match "^\d+$") {
            Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -Force $pidPath -ErrorAction SilentlyContinue
    }
}

function Remove-Shim {
    $shimPath = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\opensprite.cmd"
    if (-not (Test-Path $shimPath)) { return }

    $content = Get-Content $shimPath -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Contains($InstallDir)) {
        Remove-Item -Force $shimPath
        Write-Success "Removed $shimPath"
    } else {
        Write-Warn "Not removing $shimPath because it was not created for $InstallDir"
    }
}

function Remove-Code {
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Success "Removed $InstallDir"
    } else {
        Write-Info "Code directory not found: $InstallDir"
    }
}

function Remove-Home {
    if (-not $Full) {
        Write-Info "Keeping runtime data in $AppHome"
        return
    }
    if (Test-Path $AppHome) {
        Remove-Item -Recurse -Force $AppHome
        Write-Success "Removed $AppHome"
    } else {
        Write-Info "Runtime data directory not found: $AppHome"
    }
}

if ((Test-UnsafePath $InstallDir) -or (Test-UnsafePath $AppHome)) {
    Write-Host "Error: refusing to remove an unsafe install or data path." -ForegroundColor Red
    exit 1
}

Confirm-Uninstall
Stop-Gateway
Remove-Shim
Remove-Code
Remove-Home
Write-Success "OpenSprite uninstall complete"
