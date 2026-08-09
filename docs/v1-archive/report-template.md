# 规范化归档报告模板

每份新报告由同名 Markdown 与 JSON 侧车组成，例如 `20260730T150000Z-archive-acceptance.md` 和 `20260730T150000Z-archive-acceptance.json`。JSON 遵循 `report-schema-v1.json`，是机器验证的权威结构；Markdown 是人工审阅入口。

## 报告身份

- 报告 ID：`RPT-...`
- 记录时间：UTC ISO 8601
- 报告类型：`archive_snapshot`、`version_summary`、`development`、`testing`、`acceptance` 或 `infrastructure`
- 角色与独立性：明确报告是否由独立角色产生
- 产品版本：语义版本，例如 `v1.0.0`

## 范围与裁定

说明本报告实际审阅或验证的对象。裁定必须与 JSON 的 `decision_scope` 和 `verdict` 一致。

- `archive_local` 仅裁定档案完整性、归档边界与封存副本复现，不表示产品可发布。
- `version_archive` 裁定产品版本的归档链和推荐快照，不表示产品可发布。
- `release` 仅在所有必需物理门禁均具独立证据时可为 `accepted`。

## 验证摘要

仅列出检查 ID、通过/失败/跳过计数、时长摘要和已归档证据引用。不得包含命令行、绝对路径、stdout/stderr、traceback、请求内容、PID、凭据、Cookie、令牌或原始内容。

## 需求、缺陷与门禁

- 使用冻结的 `REQ-*`、稳定的 `DEF-*` 和已登记的证据引用。
- 以关系描述缺陷发现、修复、复测、接受或拒绝，不改写历史报告。
- 每个发布门禁必须明确 `passed`、`blocked` 或 `not_applicable`。
- 当任一必需发布门禁为 `blocked` 时，`decision_scope: release` 不得为 `accepted`。

## 档案关系与追加

快照报告记录 archive run ID、manifest SHA-256、前序关系和对应独立验收报告。版本汇总的 `snapshot_chain` 必须按顺序逐项复制 `snapshot-register.json` 的冻结登记，并只能推荐已由独立 `archive_local` 验收接受的快照。`legacy-report-register.json` 只登记历史无侧车 Markdown 的既有路径与 SHA-256；新报告不得使用 `legacy_inferred` 规避 JSON 侧车。

更正和后续验收必须创建新的报告和新的归档 run；不得原地修改已封存档案、ZIP、manifest 或历史报告。发布后的目录由 Windows ACL 封存，任何复现或测试都必须在隔离副本中运行。
