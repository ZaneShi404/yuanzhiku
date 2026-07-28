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
    throw "缺少本地 Python 环境。请先执行文档中的依赖安装步骤：$ProjectRoot\docs\dependency-installation.md"
}
if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $DataRoot = Join-Path $ProjectRoot 'data'
}
$env:YUANZHIKU_DATA_ROOT = $DataRoot
$env:PYTHONPATH = Join-Path $ProjectRoot 'backend'
$env:YUANZHIKU_EMBEDDED_WORKER = 'true'

$selectedPort = if ($Port -gt 0) { $Port } else {
    & $Python -c "from app.core.config import choose_port,data_paths; print(choose_port(data_paths()))"
    if ($LASTEXITCODE -ne 0) { throw '无法选择本地端口' }
}
$uri = "http://127.0.0.1:$selectedPort"
$existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $selectedPort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Start-Process $uri
    Write-Output "源知库已在运行：$uri"
    exit 0
}

$arguments = @('-m', 'uvicorn', 'app.main:application', '--factory', '--host', '127.0.0.1', '--port', $selectedPort, '--no-access-log')
$process = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $ProjectRoot -PassThru
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    try {
        $health = Invoke-RestMethod -Uri "$uri/api/v1/health" -TimeoutSec 1
        if ($health.status -eq 'ok') {
            Start-Process $uri
            Write-Output "源知库已启动：$uri"
            exit 0
        }
    } catch {
        if ($process.HasExited) { throw "源知库启动失败，退出代码：$($process.ExitCode)" }
    }
}
throw "源知库未能在预期时间内启动：$uri"
