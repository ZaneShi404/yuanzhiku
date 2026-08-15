# archive-tooling-p0-p3：开发报告

- 报告 ID：`RPT-ARCHIVE-TOOLING-P0-P3-20260815T120852Z-001`
- 记录时间（UTC）：`2026-08-15T12:08:52Z`
- 报告类型：`development`
- 作者角色：`development`
- 独立性：`non_independent`
- 产品版本：`v1.3.0`
- 裁定范围：`archive_local`
- 裁定：`accepted`

## 范围

归档流程自身的工程化改进（对应流程复核方案的 P0–P3 项，关联需求：`REQ-042`、`REQ-046`）：

- P0-1 缺陷台账外置：硬编码缺陷列表从 `scripts/archive_v1.py` 迁至 `docs/v1-archive/defect-ledger.json`（41 条原样迁移 + 本周期 3 条修复登记）；构建器改读 JSON，验证器仍从档案内 `index/defect-ledger.md` 提取，全部既有封存档案继续通过验证。
- P0-2 新增 `--check-tree`：只校验不构建的预检模式（报告双件制、REQ/DEF 交叉引用、登记与版本汇总链一致性、脱敏红线），失败退出码 2。
- P1-3 新增 `scripts/new_report.py`：合规报告骨架生成（Markdown + JSON 侧车，REQ/DEF 引用即生成即校验，撞名不覆盖）。
- P1-4 新增 `scripts/register_snapshot.py`：快照登记与全部版本汇总链镜像一步完成，Markdown 表格行打印建议供人工补注。
- P1-5/P2-6/P2-8/P3-9 政策落地：`docs/v1-archive/archive-policy.md` 与 `archives/README.md` 增补中间构建正式地位、小升级单段归档、验收独立性口径（non_independent 自我验收可登记但不得支撑推荐快照、验收必须引用可复现证据）、MAX_PATH 路径预算、报告禁词替代表述。
- P2-7 Git 锚定：manifest 新增可选 `git_state` 字段（head/dirty/dirty_entries），脏树构建打印中文警告；验证器对缺失字段放行、对存在字段校验形状。

## 验证

- 归档套件回归：36 passed（含台账外置、check-tree、git_state 形状、3 个既有封存档案新旧验证器对等的新用例）；archives/ 下 30 个封存目标全部经新验证器复验，5 个历史被拒快照的失败原因与旧验证器逐字相同（历史裁定，非本次引入）。
- 新工具用例：12 passed（骨架生成字段级断言、登记链镜像字节级断言、全部拒绝路径中文报错且不写文件）。
- `--check-tree` 在真实工作树通过；`new_report.py`/`register_snapshot.py` 真实路径 dry-run 通过。
- 骨架门禁状态核对无误：GATE-UNIT-INTEGRATION-REGRESSION 与 GATE-FRONTEND-BUILD passed（本批无前端改动，构建仍通过），其余三项保持 blocked。

## 结论

归档流程的两个实质缺口（缺陷级追踪断裂、登记镜像纯手工）已闭合；独立性口径有了诚实的制度出口。本批改动随后按新的单段式流程归档。
