# V1.4 升级候选快照 archive_local 验收报告

- 报告 ID：`RPT-V1-4-ARCHIVE-CANDIDATE-ACCEPTANCE-20260816T055218Z-001`
- 记录时间（UTC）：`2026-08-16T05:52:18Z`
- 报告类型：acceptance
- 作者角色：acceptance
- 独立性：non_independent（本验收与构建、开发同出一会话，按政策如实标注；本快照可被接受登记，但不得作为任何版本汇总的推荐快照）
- 产品版本：v1.4.0
- 裁定范围：archive_local
- 裁定结果：accepted

## 一句话裁定

accept for archive-local acceptance only.

## 范围与边界

- 被验收对象：v1.4 升级候选快照 `V1-current-audit-20260816T054638Z-v1-4-candidate`（封存目录）与同名压缩包，manifest schema 2、221 条目。
- 升级内容：REQ-050 分类双字段（domains 多选 × genres 单选、taxonomy 单源下发、schema v9 迁移、可移植归档 schema v8）、REQ-051 可配置媒体 AI（转写/理解双组配置、两层级联理解、litellm==1.96.2 唯一新依赖）、REQ-052 AI 出站校验与凭据隔离、REQ-053 场景感知关键帧采样；另有视频分析、检索、主题/关系与图片分析设置修复；本周期 16 条缺陷全部 resolved_locally。
- 本裁定仅限 archive_local（档案完整性、归档边界与封存副本复现），不等于 release approval。
- release_readiness 保持 blocked：真实 PostgreSQL 迁移/还原、Docker Compose 拓扑与 Edge/Chrome 黑盒验收的独立证据仍待补齐。
- 验收全程只读：封存目录受 Windows ACL 只读保护；重放仅在隔离副本内进行，未向封存目录写入、修改或删除任何文件。

## 验证摘要（仅检查 ID 与计数）

| 检查 ID | 说明 | 结果 |
|---|---|---|
| CHK-ARC-C-001 | manifest 自哈希复核与声明值一致 | pass |
| CHK-ARC-C-002 | manifest 结构：schema 2、221 条目、三状态口径（archive_integrity verified、local_software_validation passed、release_readiness blocked） | pass |
| CHK-ARC-C-003 | 独立验证器：封存目录（重放前，221 条目，退出码 0） | pass |
| CHK-ARC-C-004 | 独立验证器：同名压缩包（重放前，221 条目，退出码 0） | pass |
| CHK-ARC-C-005 | 封存副本回归：隔离副本内重放封存单元测试（36 passed、1 skipped、1 warning；skipped 为封存档案兼容性用例在隔离副本内按预期跳过） | pass |
| CHK-ARC-C-006 | 重放隔离性：日常数据目录未被读取或修改；未安装依赖；封存目录无新增成员、无字节码与测试缓存残留 | pass |
| CHK-ARC-C-007 | 独立验证器复验：封存目录（重放后，221 条目，退出码 0） | pass |
| CHK-ARC-C-008 | 独立验证器复验：同名压缩包（重放后，221 条目，退出码 0） | pass |
| CHK-ARC-C-009 | 报告登记抽查：本周期开发报告为 declared/non_independent/accepted，REQ 交叉引用命中冻结需求基线；check-tree 预检通过（213 基线文件、58 报告、60 缺陷、4 证据） | pass |
| CHK-ARC-C-010 | 冻结快照登记抽查：13 条目，最新条目 20260815T121704Z-archive-tooling（accepted）且验收报告路径与哈希可追溯 | pass |
| CHK-ARC-C-011 | 过程记录：首次构建（20260816T050548Z-v1-4-candidate）被独立验证器拒绝——登记一致性检查强制全部已登记验收为 independent，与政策「non_independent 可登记、不可推荐」冲突；修正验证器（独立性强制收敛于推荐快照检查）并新增回归测试后以新 run-id 重建；失败构建仅清理私有暂存、未发布最终路径，按政策不登记 | noted |

合计：11 项检查，10 通过，1 项过程记录（noted），0 失败。

- 封存副本回归：36 passed、1 skipped、1 warning（skipped 与 warning 均为用例预期，与本版内容无关）。
- 重放前后，封存目录与同名压缩包的独立验证结论一致，manifest 自哈希与声明值始终一致。

## 三状态口径核对

- archive_integrity：verified（清单与独立验证器通过）。
- local_software_validation：passed（本地验证记录含单元套件 347 passed、2 skipped；归档套件 37 passed、1 warning；集成 3 passed 含 AI 全链路；前端构建通过；check-tree 预检通过）。
- release_readiness：blocked（保持，符合政策）。

## 要点核验

- T2 排除：档案不含日常数据目录、虚拟环境、日志、Cookie/令牌/凭据类文件、数据库与 artifact 成员；独立验证器未检出禁止路径。
- 报告登记：本周期开发报告以 declared 双件制收录，JSON 侧车通过 report-schema-v1 逐字段校验；产品版本 v1.4.0。
- 冻结快照登记：13 条目，最新条目指向 20260815T121704Z-archive-tooling 及其验收报告，报告路径与 SHA-256 可追溯。

## 需求、缺陷与门禁

- requirements：REQ-042、REQ-046。
- defects：无新增，未登记。
- 门禁：GATE-ARCHIVE-LOCAL-V1-4-CANDIDATE passed；GATE-RELEASE-READINESS blocked。

## 裁定结论

本快照通过 archive_local 全部检查；archive_local 裁定为 accepted（non_independent）。accept for archive-local acceptance only. 此裁定不构成发布批准，release_readiness 保持 blocked；按政策本快照可被接受登记，但不得作为版本推荐快照。
