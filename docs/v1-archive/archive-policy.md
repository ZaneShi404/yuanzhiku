# V1 过程档案政策

## 目的

`V1-current-audit-*` 是本机可审计快照。它保存冻结需求、当前工作树、设计决策、开发修复、独立测试和已选择的原始证据，以便追溯第一版的搭建过程与缺陷闭环。

它不是发布声明。只有 `release_readiness` 为 `ready` 的后续档案才可称为最终发布档案。当前 V1 快照必须保持 `blocked`，直到独立证据覆盖真实 PostgreSQL 源库到独立空目标库的迁移/还原、Docker Compose 拓扑和 Edge/Chrome 黑盒验收。

## 档案等级

- `T0`：可审阅基线。源码、迁移、锁文件、需求和设计文档、测试源、Markdown 开发/测试/基础设施报告。
- `T1`：本机受限过程证据。仅 `evidence-allowlist.json` 明确登记的合成 JSON、manifest 或截图；每项必须在白名单、来源清单和证据登记中记录相同的 `source_run_id`，并与来源路径交叉核对。来源清单保留原始 SHA-256；白名单和证据登记保留一致的用途。原始输入只在构建时读取，档案中保存经路径、URL userinfo 与进程标识脱敏后的派生副本。
- `T2`：绝不归档。日常 `data/`、真实原件、真实备份或导出 ZIP、SQLite 数据库、artifact、日志正文、凭据、Cookie、令牌、请求体、原始本地路径、模型缓存、依赖缓存、`.venv` 和未登记的 runtime 文件。

历史报告不被改写或覆盖。报告中的失败、修复和复测应由档案索引关联，而不是仅保留最终通过结果。

## 输入边界

归档构建器只读取其固定的 T0 路径及 `evidence-allowlist.json` 列出的 T1 文件。它不会递归包含 `tests/runtime`，也不会读取 `data/`。

T1 候选会在复制前接受检查：

- 拒绝数据库、ZIP、artifact、日志和其他未批准二进制文件；T1 结构化结果中的 stdout、stderr、response、traceback、stacktrace 及其嵌套运行输出字段一律剔除。
- 拒绝 URL userinfo、Cookie 赋值、未知 token/password/secret/API key 赋值；隔离运行目录、用户配置目录路径和任何 PID 语义字段只允许在内存中脱敏后以派生副本归档，绝不保留原值。
- 已声明的合成测试占位值可被记录为例外，但不会被自动扩展为真实凭据例外。
- 检查报告只保存规则标识和计数，绝不回显敏感匹配正文。

报告正文禁词清单与中文替代表述（误伤易发，写作时注意）：`stdout`→「标准输出」、`stderr`→「标准错误」、`response`→「响应」、`traceback`/`stacktrace`→「调用栈」；Windows 绝对路径一律改写为仓库相对路径（如 `reports/...`、`backend/...`）。

## 产物与验证

构建命令仅允许将结果写到 `<repo>/archives` 下一个不存在的新目录，并生成同名 ZIP。目录结构为：

```text
V1-current-audit-<UTC-run-id>/
  manifest.json
  manifest.sha256
  provenance/
  baseline/
  evidence/
  index/
  verification/
```

`manifest.json` 列出除自身和 `manifest.sha256` 外的每个条目及 SHA-256、字节数和等级。`manifest.sha256` 保存 manifest 自身哈希。构建器进行自检；独立验证器以只读方式校验目录或 ZIP 的条目集合、路径安全性、哈希、大小、敏感排除和状态口径。

`manifest.json` 可携带可选顶层字段 `git_state`（`head` commit 哈希、`dirty` 布尔、`dirty_entries` 仓库相对路径列表），锚定构建时的 Git 状态；工作树有未提交变更时构建器向标准输出打印中文警告但不阻断。无该字段的历史档案仍按原规则验证通过。

构建前可随时运行 `python scripts/archive_v1.py --check-tree` 做只校验不构建的预检（报告双件制、REQ/DEF 交叉引用、登记表与版本汇总链一致性、脱敏红线），失败即时报告原因；不要把一致性错误留到构建时才暴露。

ZIP 字节会因创建时间等元数据而变化，因此可审计的稳定对象是条目集合和各条目的 SHA-256，不是 ZIP 文件自身的字节哈希。

## 封存副本回归

归档内的 `test_v1_archive.py` 只能使用工作树中项目既有 `.venv` 运行；不得安装依赖，`backend/requirements.lock` 仅是完整性基线，不是验收期间安装依赖的指令。必须先将封存的 `baseline/scripts/` 与 `baseline/tests/unit/test_v1_archive.py` 复制到新的隔离副本，绝不在封存目录执行测试或写入文件。

验收应在仓库根目录以 PowerShell 执行以下步骤，其中 `<archive-run-id>` 和 `<replay-run-id>` 必须替换为新的安全 run ID。Windows MAX_PATH 约束：隔离副本内的嵌套路径已接近 259 字符上限，`<replay-run-id>` 必须短小（纯 UTC 时间戳、不带描述后缀），超过预算时优先缩短目录名而不是更换位置：

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

该重放不得读取、枚举或修改日常 `data/`，不得安装依赖，也不得向封存目录写入 `.pyc`、pytest cache 或其他文件。验收前后应以独立验证器读取同一封存目录或 ZIP，确认其成员集合和 manifest 哈希没有变化。

## 命名和追加

运行 ID 必须使用安全字符，推荐 UTC 形式：`YYYYMMDDTHHMMSSZ`。当前档案使用 `V1-current-audit-<run-id>`；已被独立复核拒绝的快照登记在版本化 `predecessor-register.json`，新后继档案必须引用其中 run ID 最新项的 manifest 哈希和未接受原因。构建对相同 run ID 使用独占锁，目录和 ZIP 均以非覆盖方式发布；失败只清理私有暂存物，绝不删除最终路径。后续所有外部门禁完成后，创建新的 `V1-final-audit-<run-id>`。不得原地修改已封存档案。

为一致性检查而构建、但不进入 `snapshot-register.json` 的中间档案是正式允许的（先例：`20260814T160204Z`、`20260815T101415Z-v1-3-summary-check`）；它们用于验证工作树登记/链一致性，没有验收记录、不推荐、不可被引用为候选。

登记与链镜像由 `python scripts/register_snapshot.py --run-id ... --verdict ... --acceptance ...` 一步完成：校验验收报告、读取归档 manifest 哈希、追加登记并镜像全部版本汇总 JSON 链；版本汇总 Markdown 表格行由它打印建议后人工补注。小型升级（修复/工具/文档类，无新 REQ）允许单段归档（一次构建 + 一次验收后即登记）；含新需求或安全边界变更的版本仍走「候选 → 验收 → 最终记录」两段式。

## 规范化报告

从 archive manifest schema v2 起，报告采用同目录、同 stem 的 Markdown 与 JSON 侧车：`report.md` 与 `report.json`。Markdown 是人工审阅入口；JSON 是机器验证的事实来源，格式由 `report-schema-v1.json` 定义，作者可从 `report-template.md` 起草，或用 `python scripts/new_report.py --kind ... --slug ... --reqs ...` 生成合规骨架（自动填充 report_id、UTC 时间、角色、门禁骨架并校验 REQ/DEF 引用）。每个声明式报告必须有唯一 `report_id`、UTC 记录时间、语义产品版本、角色与独立性、裁定范围和结果、`REQ-*`/`DEF-*` 关联、封存内证据引用及发布门禁状态。

验收独立性口径：验收报告应尽可能由与构建/开发分离的验收角色出具（`independent`）；同会话自我验收允许存在但必须如实标注 `non_independent`。推荐快照只能由登记为 `accepted` 且具有 `independent` archive_local 验收记录的候选支撑；`non_independent` 验收的候选可被接受登记，但不得作为版本推荐快照。无论独立性如何，验收报告必须引用可独立复现的证据（独立验证器计数与退出码、隔离副本回归计数、manifest 自哈希复核结果），不接受无证据的断言式验收。

`legacy-report-register.json` 是历史豁免的冻结清单。它逐项绑定无 JSON 侧车的历史 Markdown 来源路径和来源 SHA-256；其条目集合必须与档案中全部无侧车报告完全相等。新建 Markdown 报告没有同名 JSON 时不得作为历史报告推断，构建和独立验证都必须拒绝。历史 Markdown 不补写、不改名、不重判；在 `index/report-register.json` 中仅标为 `legacy_inferred`，只登记可从冻结材料安全推导的来源路径、档案路径、标题、类别、正则提取的 `REQ-*` 和既有 `REPORT_DEFECTS` 关联；不推断时间、裁定、角色、独立性或验证结论。

`v1.0.0` 是产品版本身份，不是档案目录名。每个不可变快照仍以 UTC run ID 标识，例如 `20260730T145000Z-replay-contract-remediated`。`snapshot-register.json` 冻结所有已知候选的有序 run ID、manifest SHA-256、archive-local 裁定、验收报告路径和 SHA-256 及前序关系；版本汇总的 `snapshot_chain` 必须按顺序逐项与该登记完全相等，不得遗漏、重排或改写。推荐快照必须是登记为 `accepted` 的候选，并具有同一档案可追溯的独立 `archive_local` 接受记录。`20260730T231357Z-normalized-reports` 保持为被拒绝的不可变历史候选，不能重建、替换或从汇总链移除。

声明式报告中的未知需求、未知缺陷、缺失同名 Markdown、跨档案引用、敏感键、运行输出、PID、绝对路径或凭据均会使构建或独立验证失败。更正一律追加新的同名 `.md` + `.json` 报告，并以 `supersedes_report_id` 指向此前声明式记录；不得修改封存快照、旧报告或其 manifest。manifest schema v1 档案继续按原有规则验证且不要求报告登记；schema v2 额外要求 `index/report-register.json` 及其全部交叉验证。

## 发布后封存

构建器先在私有暂存目录完成独立验证，再以非覆盖方式发布目录。在 Windows 上，发布后必须立即使用 ACL 将目录及其成员限制为当前账户的只读执行权限，然后独立验证目录、创建 ZIP，并再次独立验证目录和 ZIP。封存目录是只读审计对象；重放或任何需要写入的测试只能复制基线到 `tests/runtime/<run-id>` 下的隔离副本，不能对封存目录解除保护或写入缓存、字节码、测试缓存或其他成员。

## 结论口径

档案同时记录三个独立状态：

- `archive_integrity`：归档清单和验证器是否通过。
- `local_software_validation`：本地编译和隔离单元测试的实际结果，包含跳过与 warning。
- `release_readiness`：只有所有环境门禁具备独立证据时才可为 `ready`；当前必须为 `blocked`。
