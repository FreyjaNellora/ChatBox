#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install AgentChat Broker as a Windows service using NSSM.

.DESCRIPTION
    Downloads NSSM if not present, then registers broker_daemon.py
    as a Windows service that auto-starts on boot.

.PARAMETER NssmPath
    Path to nssm.exe. If not found, downloads to C:\Tools\nssm.exe

.PARAMETER ServiceName
    Name of the Windows service (default: AgentChatBroker)

.PARAMETER Port
    HTTP port for the broker (default: 8765)

.EXAMPLE
    .\install_service.ps1
    .\install_service.ps1 -Port 8080
#>
param(
    [string]$NssmPath = "C:\Tools\nssm.exe",
    [string]$ServiceName = "AgentChatBroker",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BrokerDir = Resolve-Path $ScriptDir
$BrokerScript = Join-Path $BrokerDir "broker_daemon.py"
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    throw "Python not found in PATH. Install Python and try again."
}

if (-not (Test-Path $BrokerScript)) {
    throw "broker_daemon.py not found at: $BrokerScript"
}

Write-Host "Broker directory : $BrokerDir" -ForegroundColor Cyan
Write-Host "Python executable: $PythonExe" -ForegroundColor Cyan
Write-Host "Broker script    : $BrokerScript" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Download NSSM if missing
# ---------------------------------------------------------------------------
if (-not (Test-Path $NssmPath)) {
    Write-Host "NSSM not found at $NssmPath. Downloading..." -ForegroundColor Yellow
    $ToolsDir = Split-Path -Parent $NssmPath
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    }

    $NssmVersion = "2.24"
    $NssmZip = "$env:TEMP\nssm-$NssmVersion.zip"
    $NssmUrl = "https://nssm.cc/release/nssm-$NssmVersion.zip"
    # SHA256 of nssm-2.24.zip as of 2024-06 (verify at https://nssm.cc/download)
    $ExpectedHash = "727D1E42275C605E0F04ABA98095C38A8E1E46DEF453CDFFCE42869428AA6743"

    Invoke-WebRequest -Uri $NssmUrl -OutFile $NssmZip -UseBasicParsing

    # Verify SHA256 before extraction
    $ActualHash = (Get-FileHash -Path $NssmZip -Algorithm SHA256).Hash
    if ($ActualHash -ne $ExpectedHash) {
        Remove-Item $NssmZip -Force -ErrorAction SilentlyContinue
        throw @"
NSSM download hash mismatch!
  Expected: $ExpectedHash
  Actual:   $ActualHash
  File:     $NssmZip

Possible causes:
  - The NSSM release was updated (check https://nssm.cc/download)
  - Network tampering or corrupted download
  - Update the `$ExpectedHash` constant in this script after verifying the new release

The file has been deleted. No changes were made.
"@
    }

    Expand-Archive -Path $NssmZip -DestinationPath $env:TEMP -Force

    $Arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    $ExtractedNssm = "$env:TEMP\nssm-$NssmVersion\$Arch\nssm.exe"

    Copy-Item -Path $ExtractedNssm -Destination $NssmPath -Force
    Remove-Item $NssmZip -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:TEMP\nssm-$NssmVersion" -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host "NSSM installed to: $NssmPath" -ForegroundColor Green
} else {
    Write-Host "Using existing NSSM at: $NssmPath" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Stop and remove existing service if present
# ---------------------------------------------------------------------------
$Existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Service '$ServiceName' already exists. Stopping and removing..." -ForegroundColor Yellow
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    & $NssmPath remove $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 1
}

# ---------------------------------------------------------------------------
# Install service
# ---------------------------------------------------------------------------
Write-Host "Installing service '$ServiceName'..." -ForegroundColor Cyan
& $NssmPath install $ServiceName $PythonExe $BrokerScript

# Configure service
& $NssmPath set $ServiceName DisplayName "AgentChat Broker"
& $NssmPath set $ServiceName Description "Persistent HTTP + MCP broker for AgentChat multi-agent coordination"
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppDirectory $BrokerDir
& $NssmPath set $ServiceName AppEnvironmentExtra "AGENTCHAT_HTTP_PORT=$Port"
& $NssmPath set $ServiceName AppStdout (Join-Path $BrokerDir "broker.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $BrokerDir "broker.log")
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateOnline 1
& $NssmPath set $ServiceName AppRotateBytes 10485760  # 10MB

# ---------------------------------------------------------------------------
# Start service
# ---------------------------------------------------------------------------
Start-Service -Name $ServiceName
Start-Sleep -Seconds 2

$Service = Get-Service -Name $ServiceName
Write-Host ""
Write-Host "Service status: $($Service.Status)" -ForegroundColor $(if ($Service.Status -eq 'Running') { 'Green' } else { 'Red' })
Write-Host ""
Write-Host "AgentChat Broker installed!" -ForegroundColor Green
Write-Host "  HTTP API : http://localhost:$Port" -ForegroundColor Cyan
Write-Host "  Logs     : $(Join-Path $BrokerDir 'broker.log')" -ForegroundColor Cyan
Write-Host "  Manage   : nssm status $ServiceName" -ForegroundColor Cyan
Write-Host ""
Write-Host "Tailscale access: http://<pc-tailscale-ip>:$Port" -ForegroundColor Cyan
