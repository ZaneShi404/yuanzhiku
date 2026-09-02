[CmdletBinding()]
param(
    [string]$OutFile = (Join-Path $PSScriptRoot '..\.env'),
    [switch]$Force
)

# 生成本地 Compose secrets（加固计划 Task 14）：密码学安全随机数，
# 写入被 .gitignore 忽略的根 .env；绝不提交、绝不输出明文。

$ErrorActionPreference = 'Stop'
$resolved = [System.IO.Path]::GetFullPath($OutFile)
if ((Test-Path -LiteralPath $resolved) -and -not $Force) {
    throw "目标文件已存在：$resolved（使用 -Force 覆盖）"
}

function New-RandomHex {
    param([int]$Bytes = 24)
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    ($buffer | ForEach-Object { $_.ToString('x2') }) -join ''
}

$admin = New-RandomHex
$app = New-RandomHex
$lines = @(
    '# 本地 Compose secrets（new-compose-secrets.ps1 生成；已被 .gitignore 忽略）',
    "YUANZHIKU_DB_ADMIN_PASSWORD=$admin",
    "YUANZHIKU_DB_APP_PASSWORD=$app",
    ''
)
[System.IO.File]::WriteAllLines($resolved, $lines)
Write-Output "已生成 $resolved（含两个数据库密码；明文不回显）"
