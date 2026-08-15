# V1 过程档案

本目录保存由 `scripts/archive_v1.py` 生成的本机审计档案。它默认不进入 Git，也不应被视为可公开分发的发布物。

使用方式：

```powershell
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
python scripts/archive_v1.py --output-root archives --run-id $runId
python scripts/verify_v1_archive.py --archive archives/V1-current-audit-$runId
```

辅助工具：`python scripts/archive_v1.py --check-tree` 只校验不构建（提交前预检）；`python scripts/new_report.py` 生成合规报告骨架（Markdown + JSON 侧车）；`python scripts/register_snapshot.py` 一步完成快照登记与版本汇总链镜像。

构建器同时生成同名 `.zip`；验证器可接受档案目录或 ZIP 文件。详细边界、证据等级和当前封存口径见 `docs/v1-archive/archive-policy.md`。

## 封存副本回归

归档内的 `test_v1_archive.py` 只能使用工作树中项目既有 `.venv`；不得安装依赖，`backend/requirements.lock` 是完整性基线，不是验收期间安装依赖的指令。必须在新的隔离副本执行，绝不直接在封存目录执行测试。

从仓库根目录运行，替换 `<archive-run-id>` 和 `<replay-run-id>`：

```powershell
$repo = (Resolve-Path .)
$python = Join-Path $repo '.venv\Scripts\python.exe'
$archive = Join-Path $repo 'archives\V1-current-audit-<archive-run-id>'
$copy = Join-Path $repo 'tests\runtime\archive-replay-<replay-run-id>\copy'
New-Item -ItemType Directory -Force $copy | Out-Null
Copy-Item (Join-Path $archive 'baseline\scripts') (Join-Path $copy 'scripts') -Recurse
Copy-Item (Join-Path $archive 'baseline\docs\v1-archive') (Join-Path $copy 'docs\v1-archive') -Recurse
New-Item -ItemType Directory -Force (Join-Path $copy 'archives') | Out-Null
Copy-Item (Join-Path $archive 'baseline\archives\README.md') (Join-Path $copy 'archives\README.md')
New-Item -ItemType Directory -Force (Join-Path $copy 'tests\unit') | Out-Null
Copy-Item (Join-Path $archive 'baseline\tests\unit\test_v1_archive.py') (Join-Path $copy 'tests\unit\test_v1_archive.py')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:YUANZHIKU_TEST_RUNTIME = Join-Path $copy 'runtime'
& $python -m pytest (Join-Path $copy 'tests\unit\test_v1_archive.py') -p no:cacheprovider -q
```

此过程不得读取、枚举或修改日常 `data/`；不得安装依赖；不得向封存目录写入 `.pyc`、pytest cache 或其他文件。完成后对同一封存目录或 ZIP 运行独立验证器，确认成员集合和 manifest 哈希未变化。

## 规范化报告

新的 archive manifest schema v2 会收录同目录、同 stem 的报告对：Markdown 用于人工审阅，JSON 侧车用于机器验证。JSON 遵循 `docs/v1-archive/report-schema-v1.json`，起草结构见 `docs/v1-archive/report-template.md`。构建产物 `index/report-register.json` 将报告链接到封存内的 Markdown/JSON、来源清单、证据登记、冻结需求和缺陷账本。

`docs/v1-archive/legacy-report-register.json` 是无 JSON 侧车历史 Markdown 的冻结路径与 SHA-256 清单。它必须与所有无侧车报告精确对应，不能把新 Markdown 伪装为历史材料。`docs/v1-archive/snapshot-register.json` 冻结每个已知候选的顺序、manifest SHA-256、archive-local 裁定、验收报告与前序关系；`reports/versions/v1.0.0/` 的 JSON 候选链必须逐项复制该清单。`20260730T231357Z-normalized-reports` 继续登记为拒绝候选，任何后继档案都不得修改、替代或略过它。

产品版本与封存快照分开命名：`v1.0.0` 是产品版本，`V1-current-audit-<UTC-run-id>` 是不可变档案。推荐快照只能指向具有可追溯独立 `archive_local` 接受记录的 run。这个结论不解除 `release_readiness: blocked` 的物理 PostgreSQL、Docker Compose 或 Edge/Chrome 门禁。

既有无 JSON 侧车的 Markdown 报告保持原样；v2 登记把它们标为 `legacy_inferred`，只记录可安全推导的路径、标题、类别、`REQ-*` 和现有缺陷关联。后续更正必须追加新的报告对，并通过 `supersedes_report_id` 关联，不得修改已封存档案或历史报告。manifest schema v1 仍按历史规则验证；只有 v2 要求 `index/report-register.json`。

## 发布后封存

Windows 构建器在私有暂存目录完成首次独立验证后，以非覆盖方式发布目录并立即应用只读 ACL；其后才验证目录、创建 ZIP 并验证两种形式。已发布目录不可作为测试工作区。重放或篡改负例只能针对复制到 `tests/runtime/<run-id>` 的隔离副本，且不得改变发布目录、ZIP 或 manifest。

`archives/` 应只保留经过本机访问控制保护的档案。不要将其中内容上传到公开仓库、第三方网盘或外部服务。
