[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DataRoot,
    [int]$TtlHours = 24,
    [switch]$Apply
)

# staging 残留检查（加固计划 Task 15A）：默认 dry-run，只报告；
# -Apply 仅清理「合法 marker 且超过 TTL」的目录。无 marker 的遗留内容
# （如 _dy_probe*）与损坏 marker 只列出，绝不删除。报告不含文件内容。

$ErrorActionPreference = 'Stop'
$staging = Join-Path (Resolve-Path -LiteralPath $DataRoot) 'staging'
if (-not (Test-Path -LiteralPath $staging)) {
    Write-Output "staging 目录不存在：$staging"
    exit 0
}
$markerName = '.yuanzhiku-staging.json'
$now = [System.DateTimeOffset]::UtcNow
$summary = [ordered]@{ removed = 0; keptActive = 0; corruptMarker = 0; unknown = 0 }

Get-ChildItem -LiteralPath $staging -Force | ForEach-Object {
    $entry = $_
    $markerPath = $null
    if ($entry.PSIsContainer) { $markerPath = Join-Path $entry.FullName $markerName }
    if (-not $markerPath -or -not (Test-Path -LiteralPath $markerPath)) {
        $summary.unknown++
        Write-Output "[unknown] $($entry.Name)（无 marker：只报告，不自动删除）"
        return
    }
    try {
        $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
        if (-not $marker.operation_id -or -not $marker.created_at) { throw 'marker 无效' }
        $created = [System.DateTimeOffset]::Parse($marker.created_at)
    } catch {
        $summary.corruptMarker++
        Write-Output "[corrupt] $($entry.Name)（损坏 marker：只报告，不自动删除）"
        return
    }
    $ageHours = ($now - $created).TotalHours
    if ($ageHours -lt $TtlHours) {
        $summary.keptActive++
        Write-Output ("[active]  {0}（{1:N1} 小时，未超 TTL）" -f $entry.Name, $ageHours)
        return
    }
    if ($Apply) {
        Remove-Item -LiteralPath $entry.FullName -Recurse -Force
        $summary.removed++
        Write-Output "[removed] $($entry.Name)（超 TTL，已清理）"
    } else {
        Write-Output ("[expired] {0}（超 TTL {1:N1} 小时；加 -Apply 清理）" -f $entry.Name, $ageHours)
    }
}
Write-Output ("汇总：removed={0} active={1} corrupt={2} unknown={3}" -f `
    $summary.removed, $summary.keptActive, $summary.corruptMarker, $summary.unknown)
if (-not $Apply) { Write-Output '（dry-run：未删除任何内容；确认后加 -Apply 重跑）' }
