# v1-7-archive-candidate-acceptance：验收报告

- 报告 ID：`RPT-V1-7-ARCHIVE-CANDIDATE-ACCEPTANCE-20260904T024054Z-001`
- 记录时间（UTC）：`2026-09-04T02:40:54Z`
- 报告类型：`acceptance`
- 作者角色：`acceptance`
- 独立性：`non_independent`（同会话自我验收；按归档政策可登记但不得进入推荐快照位）
- 产品版本：`v1.7.0`
- 裁定范围：`archive_local`
- 裁定：`accepted`
- 归档身份：run id `20260904T022759Z-v1-7-candidate`，manifest SHA-256 `f83876dd18457541783849f8c0c37278c47b70bc5040453f08a9c22f552797ed`

一句话裁定：`V1-current-audit-20260904T022759Z-v1-7-candidate`（目录 + ZIP）以 `non_independent` 验收通过 `archive_local` 全部检查并被接受登记；后继最终记录归档将收录本验收报告。

## 范围与边界

- 被验收对象：`archives/V1-current-audit-20260904T022759Z-v1-7-candidate/` 与同名 `.zip`（V1 Candidate / BLOCKED 标签为冻结契约的固定状态行）。
- 归档内容基线：master `b623ad6`（v1.7.0 实现 7 提交 `5dfd3bc..7bad9bd` + 独立复核处置 `585dc1b` + 复核报告 `5736666` + 归档工具链修复 `b623ad6`），构建时工作树干净。
- 本验收仅覆盖 `archive_local`（本地归档构建与完整性验证），不等于产品发布批准；`release_readiness` 因 GATE-PROVIDER-FRAME-SMOKE、GATE-UI-BLACKBOX、GATE-PG-PHYSICAL、GATE-COMPOSE-PHYSICAL 未完成而维持 `blocked`。
- 验收为只读验证：除本验收报告与登记簿条目外不改动任何归档内容。

## 验证摘要

| CHK-ID | 验证项 | 结果 |
| --- | --- | --- |
| CHK-1 | 构建器自检（`--check-tree` 等价校验：报告双件制、REQ/DEF 交叉引用、版本汇总快照链与登记簿一致、验收报告归档身份） | 通过（构建内建预检通过；71 份报告、281 基线文件） |
| CHK-2 | 独立验证器（目录）`scripts/verify_v1_archive.py --archive archives/V1-current-audit-20260904T022759Z-v1-7-candidate --quiet` | 退出码 0 |
| CHK-3 | 独立验证器（ZIP）`--archive archives/V1-current-audit-20260904T022759Z-v1-7-candidate.zip --quiet` | 退出码 0 |
| CHK-4 | manifest 自哈希复核（`manifest.sha256` = SHA-256(`manifest.json`)） | 一致（`f83876dd…797ed`） |
| CHK-5 | 构建时 `git_state` | 干净（非 dirty） |
| CHK-6 | 报告收录 | 含 v1.7.0 实现/回归/复核/本验收四类报告与 `reports/review/` 目录（首次随归档收录） |
| CHK-7 | 登记 | `register_snapshot.py` 以 `accepted` 登记，登记簿第 18 条，后继链前项为 `20260903T064539Z-v1-6-final-record` |

## 结论

候选快照 `20260904T022759Z-v1-7-candidate` 按 `docs/v1-archive/archive-policy.md` 两段式流程的第一段验收通过并登记。按独立性口径，本验收为 `non_independent`，v1.7.0 快照不得进入推荐位——推荐审计快照保持 `20260815T082711Z-v1-3-final-record`。后继：最终记录 `*-v1-7-final-record` 将在本验收报告登记之后构建（收录本报告与复核报告），并以独立验收报告登记为登记簿第 19 条。
