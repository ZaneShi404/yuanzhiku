# V1.2 链接获取升级档案快照独立 archive_local 验收报告

- 报告 ID：`RPT-V1-2-ARCHIVE-LOCAL-ACCEPTANCE-20260814T163250Z-001`
- 记录时间（UTC）：`2026-08-14T16:32:50Z`
- 报告类型：acceptance
- 作者角色：acceptance
- 独立性：independent（本报告由独立验收子智能体出具，与构建方、开发方、测试执行方分离）
- 产品版本：v1.2.0
- 裁定范围：archive_local
- 裁定结果：accepted

## 一句话裁定

accept for archive-local acceptance only.

## 范围与边界

- 被验收对象：封存快照 `archives/V1-current-audit-20260814T162733Z`（目录）与同名 `.zip` 压缩包，manifest schema 2、168 条目。
- 本裁定仅限 archive_local（档案完整性、归档边界与封存副本复现），不等于 release approval。
- release_readiness 保持 blocked：真实 PostgreSQL 迁移/还原、Docker Compose 拓扑与 Edge/Chrome 黑盒验收的独立证据仍待补齐。
- 验收全程只读：封存目录由 Windows ACL 限制为只读；任何重放均在其隔离副本内进行，未向封存目录写入、修改或删除任何文件。

## 验证摘要（仅检查 ID 与计数）

| 检查 ID | 说明 | 结果 |
|---|---|---|
| CHK-ARCH-001 | 归档政策与报告模板通读，明确裁定边界 | pass |
| CHK-ARCH-002 | manifest 自哈希复核与声明值一致 | pass |
| CHK-ARCH-003 | manifest 结构：schema 2、168 条目、三状态口径 | pass |
| CHK-ARCH-004 | 独立验证器：封存目录（首轮） | pass |
| CHK-ARCH-005 | 独立验证器：同名 .zip（首轮） | pass |
| CHK-ARCH-006 | 关键成员抽查：报告登记、缺陷账本、本地验证、证据登记、冻结快照登记 | pass |
| CHK-ARCH-007 | T2 排除：日常 data/、.venv、日志、凭据类文件、数据库、artifact 均不在档案内 | pass |
| CHK-ARCH-008 | 报告双件配对（.md/.json 同 stem）与 legacy 登记一致性 | pass |
| CHK-ARCH-009 | v1.2 决策 1-12 在冻结需求文档与相关报告中的可追溯性 | pass |
| CHK-ARCH-010 | 封存副本回归：隔离副本内运行封存单元测试（29 passed，0 failed，1 warning） | pass |
| CHK-ARCH-011 | 重放隔离性：封存目录成员集合与 manifest 哈希无变化 | pass |
| CHK-ARCH-012 | 独立验证器复验：封存目录（重放后） | pass |
| CHK-ARCH-013 | 独立验证器复验：同名 .zip（重放后） | pass |

合计：13 项检查，13 通过，0 失败，0 跳过。

- 封存副本回归：29 passed、0 failed、1 warning（用例预期告警，与本版无关）；重放未读取或枚举日常 data/，未安装依赖，未在封存目录留下任何文件。
- 重放前后，封存目录与 .zip 的独立验证结论一致（各 168 条目、verified），manifest 自哈希与声明值始终一致。

## 三状态口径核对

- archive_integrity：verified（清单与独立验证器通过）。
- local_software_validation：passed（本地验证记录含 212 passed、2 skipped；封存副本回归 29 passed、1 warning）。
- release_readiness：blocked（保持，符合政策）。

## 要点核验

- T2 排除：档案不含 data/、.venv、日志、Cookie/令牌/凭据类文件、数据库、artifact 与压缩包成员；manifest 排除声明与成员集合一致，独立验证器未检出禁止路径。
- 报告登记：`index/report-register.json` 共 42 条，其中 declared 18 条、legacy_inferred 24 条；legacy 清单与无侧车 Markdown 一一对应；v1.2 的 declared 报告（需求验证/评审、实现评审/验证、真实平台验收、注册域门禁等）均存在且 .md/.json 双件配对。
- 缺陷账本：`index/defect-ledger.md` 含 DEF-LINK-001..007 与外部优化 4 条，均已闭环。
- v1.2 决策追溯：决策 1-12 全部记录于冻结需求文档的决策记录章节，并在相应 v1.2 报告中按主题引用。
- 冻结快照登记：`baseline/docs/v1-archive/snapshot-register.json` 含 8 条 v1.0 冻结候选，不含本快照自身。

## 需求、缺陷与门禁

- requirements：REQ-042、REQ-046。
- defects：无新增，未登记。
- 门禁：GATE-ARCHIVE-LOCAL-V1-2 passed；GATE-RELEASE-READINESS blocked。

## 裁定结论

本快照通过 archive_local 全部检查；archive_local 裁定为 accepted。accept for archive-local acceptance only. 此裁定不构成发布批准，release_readiness 保持 blocked。
