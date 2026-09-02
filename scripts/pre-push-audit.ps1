[CmdletBinding()]
param(
    [string]$RepoPath = (Get-Location).Path,
    [int]$MaxHistoryCommits = 500
)

# 发布前检查（加固计划 Task 15B）：只扫描 Git index（暂存内容）与既有
# 历史的文件清单，绝不读取或输出真实凭据值（报告仅含文件、行号与模式
# 名，匹配值一律脱敏）；绝不重写 Git 历史。
# 退出码：0 = 通过；1 = 存在阻断项；2 = 用法/环境错误。

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$resolved = [System.IO.Path]::GetFullPath($RepoPath)
if (-not (Test-Path -LiteralPath (Join-Path $resolved '.git'))) {
    Write-Error "不是 Git 仓库：$resolved"
    exit 2
}
function Invoke-Git {
    param([string[]]$GitArgs)
    $output = & git -C $resolved @GitArgs 2>$null
    return @($output | Where-Object { $_ -ne $null })
}

$violations = 0

# 1) 暂存路径黑名单（README 是 archives 内唯一允许入库的文件）
$blockedPathPatterns = @(
    '^data/',
    '^tests/runtime/',
    '^archives/',
    '(^|/)\.env($|\.)',
    '(^|/)cookies\.txt$',
    '(^|/)cookies/',
    '(^|/)api-token\.txt$'
)
$stagedFiles = @(Invoke-Git @('diff', '--cached', '--name-only', '--diff-filter=ACR'))
foreach ($file in $stagedFiles) {
    foreach ($pattern in $blockedPathPatterns) {
        if ($file -match $pattern) {
            $violations++
            Write-Output "[blocked-path] $file（该路径不得进入 Git index）"
            break
        }
    }
}

# 2) 暂存新增行中的秘密模式（值一律脱敏，只报告文件:行号与模式名）
$secretPatterns = @(
    @{ name = 'private-key'; pattern = '-----BEGIN [A-Z ]*PRIVATE KEY-----' },
    @{ name = 'secret-pattern'; pattern = '(?i)\b(api[_-]?key|apikey|secret|password|passwd|token)\b\s*[:=]\s*["'']?[^\s"'',;]{8,}' },
    @{ name = 'secret-pattern'; pattern = 'Bearer\s+[A-Za-z0-9._\-]{12,}' },
    @{ name = 'secret-pattern'; pattern = '(?i)\b(postgres(ql)?|mysql|mariadb)://[^\s"@/]+:[^\s"@]+@' }
)
$hunks = @(Invoke-Git @('diff', '--cached', '-U0'))
$currentFile = ''
$currentLine = 0
foreach ($line in $hunks) {
    if ($line.StartsWith('+++ b/')) {
        $currentFile = $line.Substring(6)
        continue
    }
    if ($line.StartsWith('@@')) {
        if ($line -match '^\+([0-9]+)') { $currentLine = [int]$Matches[1] - 1 }
        continue
    }
    if (-not $line.StartsWith('+')) { continue }
    $currentLine++
    $content = $line.Substring(1)
    foreach ($entry in $secretPatterns) {
        if ($content -match $entry.pattern) {
            $violations++
            Write-Output "[secret-pattern:$($entry.name)] $currentFile`:$currentLine（匹配值已脱敏）"
            break
        }
    }
}

# 3) 历史中的阻断路径（新增文件清单；不扫描历史正文内容，不重写历史）
$historyFiles = @(Invoke-Git @('log', "--max-count=$MaxHistoryCommits", '--all', '--name-only', '--pretty=format:'))
$historySeen = @{}
foreach ($file in $historyFiles) {
    if (-not $file -or $historySeen.ContainsKey($file)) { continue }
    $historySeen[$file] = $true
    foreach ($pattern in $blockedPathPatterns) {
        if ($file -match $pattern) {
            Write-Output "[history-path] $file（历史中出现过阻断路径：列为隐私决策项，需用户明确选择）"
            break
        }
    }
}

# 4) 未跟踪的用户指南：提交或忽略由用户明确决定
$untracked = @(Invoke-Git @('-c', 'core.quotepath=false', 'ls-files', '--others', '--exclude-standard'))
$guideFound = $false
foreach ($file in $untracked) {
    if ($file.StartsWith('源知库使用指南')) {
        $guideFound = $true
        break
    }
}
if ($guideFound) {
    Write-Output '[user-decision] 源知库使用指南/ 处于未跟踪状态：提交或忽略需用户明确选择（本脚本不代为决定）'
}

if ($violations -gt 0) {
    Write-Output "结论：存在 $violations 项阻断，不得发布。"
    exit 1
}
Write-Output '结论：index 与历史检查通过。'
exit 0
