# V1.3 归档链最终后继快照独立 archive_local 最终记录验收报告

- 报告 ID：`RPT-V1-3-ARCHIVE-FINAL-RECORD-20260815T101124Z-001`
- 记录时间（UTC）：`2026-08-15T10:11:24Z`
- 报告类型：acceptance
- 作者角色：acceptance
- 独立性：independent（本报告按验收角色独立出具，与构建方、开发方、测试执行方分离）
- 产品版本：v1.3.0
- 裁定范围：archive_local
- 裁定结果：accepted

## 一句话裁定

accept for archive-local acceptance only.

## 范围与边界

- 被验收对象：v1.3 归档链最终后继快照 `V1-current-audit-20260815T082711Z-v1-3-final-record`（封存目录）与同名压缩包，manifest schema 2、194 条目。
- 该快照承载候选 `20260815T080921Z-v1-3-candidate` 的独立验收记录与更新后的冻结快照登记（11 条目），即 v1.3 升级（桌面入口与实例锁修复、REQ-049 导入预填、REQ-047b 链接探测、REQ-048 图片导入、REQ-044 统一导入页修订、REQ-047a 按平台 Cookie 库修订、决策 13 注册域补登与代理转发修复、分享口令链接提取）的最终记录。
- 本裁定仅限 archive_local（档案完整性、归档边界与封存副本复现），不等于 release approval。
- release_readiness 保持 blocked：真实 PostgreSQL 迁移/还原、Docker Compose 拓扑与 Edge/Chrome 黑盒验收的独立证据仍待补齐。
- 验收全程只读：封存目录受 Windows ACL 只读保护；重放仅在隔离副本内进行，未向封存目录写入、修改或删除任何文件。

## 验证摘要（仅检查 ID 与计数）

| 检查 ID | 说明 | 结果 |
|---|---|---|
| CHK-ARC-F-001 | manifest 自哈希复核与声明值一致 | pass |
| CHK-ARC-F-002 | manifest 结构：schema 2、194 条目、三状态口径（archive_integrity verified、local_software_validation passed、release_readiness blocked） | pass |
| CHK-ARC-F-003 | 独立验证器：封存目录（重放前） | pass |
| CHK-ARC-F-004 | 独立验证器：同名压缩包（重放前） | pass |
| CHK-ARC-F-005 | 封存副本回归：隔离副本内重放封存单元测试（29 passed、0 failed、1 warning；首次重放因回归目录命名过长触发 Windows 路径长度上限，缩短目录名后重跑通过，与封存内容无关） | pass |
| CHK-ARC-F-006 | 重放隔离性：日常数据目录未被读取或修改；未安装依赖；封存目录无新增成员、无字节码与测试缓存残留 | pass |
| CHK-ARC-F-007 | 独立验证器复验：封存目录（重放后） | pass |
| CHK-ARC-F-008 | 独立验证器复验：同名压缩包（重放后） | pass |
| CHK-ARC-F-009 | 报告登记抽查：候选验收报告为 declared/independent/accepted，归档身份指向 20260815T080921Z-v1-3-candidate；6 篇开发报告双件制收录 | pass |
| CHK-ARC-F-010 | 冻结快照登记抽查：11 条目，最新条目 20260815T080921Z-v1-3-candidate（accepted）且验收报告路径与哈希可追溯 | pass |
| CHK-ARC-F-011 | v1.2.0 版本汇总抽查：snapshot_chain 11 条目与登记逐项一致，推荐快照保持 20260814T174651Z | pass |
| CHK-ARC-F-012 | v1.0.0 版本汇总抽查：snapshot_chain 11 条目与登记逐项一致，推荐快照保持 20260731T011535Z-accepted-acl-successor | pass |

合计：12 项检查，12 通过，0 失败，0 跳过。

- 封存副本回归：29 passed、0 failed、1 warning（用例预期告警，与本版无关）。
- 重放前后，封存目录与同名压缩包的独立验证结论一致，manifest 自哈希与声明值始终一致。

## 三状态口径核对

- archive_integrity：verified（清单与独立验证器通过）。
- local_software_validation：passed（本地验证记录含单元套件 269 passed、2 skipped；下载链路 93 passed；Cookie 库改造 114 passed；封存副本回归 29 passed、1 warning）。
- release_readiness：blocked（保持，符合政策）。

## 要点核验

- T2 排除：档案不含日常数据目录、虚拟环境、日志、Cookie/令牌/凭据类文件、数据库与 artifact 成员；独立验证器未检出禁止路径。
- 报告登记：候选验收报告（RPT-V1-3-ARCHIVE-LOCAL-ACCEPTANCE-20260815T081829Z-001）为 declared、independent、accepted，归档身份指向 20260815T080921Z-v1-3-candidate。
- 冻结快照登记：11 条目，最新条目指向候选及其验收报告，报告路径与 SHA-256 可追溯；后继关系严格链式。
- 版本汇总：v1.2.0 与 v1.0.0 两份汇总的 snapshot_chain 均与冻结快照登记逐项一致（11 条目）。

## 需求、缺陷与门禁

- requirements：REQ-042、REQ-046。
- defects：无新增，未登记。
- 门禁：GATE-ARCHIVE-LOCAL-V1-3-FINAL passed；GATE-RELEASE-READINESS blocked。

## 裁定结论

本快照通过 archive_local 全部检查；archive_local 裁定为 accepted。accept for archive-local acceptance only. 此裁定不构成发布批准，release_readiness 保持 blocked。
