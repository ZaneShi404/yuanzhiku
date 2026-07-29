[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 0,
    [string]$DataRoot = $env:YUANZHIKU_DATA_ROOT
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "Local Python environment is missing. Install dependencies first: $ProjectRoot\docs\dependency-installation.md"
}
if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $DataRoot = Join-Path $ProjectRoot 'data'
}
$env:YUANZHIKU_DATA_ROOT = $DataRoot
$env:PYTHONPATH = Join-Path $ProjectRoot 'backend'
$env:YUANZHIKU_EMBEDDED_WORKER = 'true'

function Get-LocalPortStatus([int]$CandidatePort) {
    $existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $CandidatePort -State Listen -ErrorAction SilentlyContinue
    if (-not $existing) {
        return 'available'
    }
    try {
        $matchesDataRoot = & $Python -c "import json,sys,urllib.request; from pathlib import Path; from app.core.config import data_paths; health=json.load(urllib.request.urlopen(f'http://127.0.0.1:{sys.argv[1]}/api/v1/health', timeout=1)); print(health.get('status') == 'ok' and data_paths().root == Path(health['data_root']).resolve())" $CandidatePort
        if ($matchesDataRoot.Trim() -eq 'True') {
            return 'same-data-root'
        }
    } catch {
        # A listener without a matching health response is not this data root.
    }
    return 'occupied'
}

$savedPort = & $Python -c "from app.core.config import data_paths,saved_port; port=saved_port(data_paths()); print('' if port is None else port)"
if ($LASTEXITCODE -ne 0) { throw 'Unable to read the saved local port.' }
$hasSavedPort = -not [string]::IsNullOrWhiteSpace($savedPort)
if ($hasSavedPort) {
    $savedPort = [int]$savedPort
    $savedPortStatus = Get-LocalPortStatus $savedPort
    if ($savedPortStatus -eq 'same-data-root') {
        $uri = "http://127.0.0.1:$savedPort"
        Start-Process $uri
        Write-Output "YuanZhiKu is already running: $uri"
        exit 0
    }
    if ($Port -eq 0 -and $savedPortStatus -eq 'occupied') {
        throw "Saved local port $savedPort is occupied by another process; the saved port preference was not changed."
    }
}

if ($Port -gt 0) {
    $requestedPortStatus = Get-LocalPortStatus $Port
    if ($requestedPortStatus -eq 'same-data-root') {
        $uri = "http://127.0.0.1:$Port"
        Start-Process $uri
        Write-Output "YuanZhiKu is already running: $uri"
        exit 0
    }
    if ($requestedPortStatus -eq 'occupied') {
        throw "Requested local port $Port is occupied; the saved port preference was not changed."
    }
}

$null = & $Python -c "from app.core.config import InstanceLock,data_paths; lock=InstanceLock(data_paths().lock_file); lock.acquire(); lock.release()" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'This data root already has a running instance; the saved port preference was not changed.'
}

$selectedPort = if ($Port -gt 0) {
    & $Python -c "from app.core.config import choose_port,data_paths; import sys; print(choose_port(data_paths(), int(sys.argv[1])))" $Port
} else {
    & $Python -c "from app.core.config import choose_port,data_paths; print(choose_port(data_paths()))"
}
if ($LASTEXITCODE -ne 0) { throw 'Unable to select a local port.' }
$selectedPort = [int]$selectedPort
$uri = "http://127.0.0.1:$selectedPort"

$arguments = @('-m', 'uvicorn', 'app.main:application', '--factory', '--host', '127.0.0.1', '--port', $selectedPort, '--no-access-log')
$process = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $ProjectRoot -PassThru
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    try {
        $health = Invoke-RestMethod -Uri "$uri/api/v1/health" -TimeoutSec 1
        if ($health.status -eq 'ok') {
            Start-Process $uri
            Write-Output "YuanZhiKu started: $uri"
            exit 0
        }
    } catch {
        if ($process.HasExited) { throw "YuanZhiKu failed to start; exit code: $($process.ExitCode)" }
    }
}
throw "YuanZhiKu did not start at the expected address: $uri"
