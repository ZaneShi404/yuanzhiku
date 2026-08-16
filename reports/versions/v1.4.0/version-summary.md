# v1.4.0 版本归档汇总

## 版本结论

`v1.4.0`（分类双字段、可配置媒体 AI、AI 出站安全与场景感知关键帧升级）的本周期候选快照 `20260816T054638Z-v1-4-candidate` 与最终记录快照均以 `non_independent`（同会话自我验收）通过 `archive_local` 验收并被接受登记。按归档政策的独立性口径，`non_independent` 验收的候选可被接受登记，但不得作为版本推荐快照：**本版本推荐审计快照保持为上一版本最终记录 `20260815T082711Z-v1-3-final-record`**（独立 `archive_local` 验收，2026-08-15），v1.4.0 快照待独立 `archive_local` 验收后方可进入推荐位。

这不是产品发布批准。真实 PostgreSQL 迁移/还原、Docker Compose 物理拓扑和 Edge/Chrome 黑盒门禁仍为 `blocked`；`release_readiness` 保持 `blocked`。

## 版本内容

- 分类双字段（REQ-050）：领域 domains 多选 × 体裁 genres 单选，`GET /taxonomy` 单源下发；SQLite schema v9 迁移，可移植归档 schema v8。
- 可配置媒体 AI（REQ-051）：转写/理解双组独立配置，两层级联理解（转写→完整性判定→tier1/tier2 摘要）；`litellm==1.96.2` 唯一新增锁定依赖。
- AI 出站校验与凭据隔离（REQ-052）：`validate_ai_base_url` 出站校验、数据根下 `state/ai/credentials.json` 凭据文件、错误脱敏。
- 场景感知关键帧采样（REQ-053）：场景检测+等距混合、5%/95% 锚定、黑帧拒绝、帧 reason 持久化（可移植归档 schema v8）。
- 正确性修复与功能化：视频分析（verify 前置、帧尺寸、多 analysis 可见）、检索（ffmpeg-local 退出全文、分类标签退出 haystack、domain/genre/_none/topic_id 过滤）、主题/关系（管理端点、导航、版本链、same-work 候选）、图片分析独立 `image_*` 设置。
- 归档验证器口径修正：登记一致性检查不再强制所有已登记验收为 `independent`（该强制与政策「non_independent 可登记、不可推荐」冲突并阻断一切新构建）；独立性强制收敛于推荐快照检查，附回归测试。
- 缺陷闭环：缺陷台账 60 条，本周期 16 条全部 `resolved_locally`（DEF-SEARCH-002、DEF-VID-001..008、DEF-TAX-001、DEF-SEARCH-003、DEF-SEARCH-004、DEF-TAX-002、DEF-IMG-001、DEF-TOPIC-001、DEF-REL-001）。

## 候选链

| Run ID | 本地档案裁定 | Manifest SHA-256 | 裁定记录 | 后继关系 |
| --- | --- | --- | --- | --- |
| `20260730T110828Z` | `rejected` | `7fd9cc5afd3576b959989c1a43abca4f75b5599d126fd8017ca56f55577b49da` | `20260730T120300Z-independent-archive-review-remediation` | 初始候选 |
| `20260730T121500Z-archive-remediated` | `rejected` | `f196d50e81518bd4ed4c8ac702095bd3794864af585e7cad27950b415ab6e708` | `20260730T123000Z-independent-successor-archive-rejection` | 后继 `110828` |
| `20260730T135500Z-archive-contract-remediated` | `rejected` | `55e6cf2ebb9bf743e9830b64bca5402df5d4246478d98af0e36f4baf75d4424e` | `20260730T141000Z-independent-successor-archive-acceptance-rejection` | 后继 `121500` |
| `20260730T145000Z-replay-contract-remediated` | `accepted` | `1b03170cec6e9db53df1c8f1ad1a8966becc1f110bb45b61fa8edc3cca22cd8d` | `20260730T150000Z-independent-successor-archive-acceptance` | 后继 `135500` |
| `20260730T231357Z-normalized-reports` | `rejected` | `b5bcdbd6cfad51dc9babd428571bc751f706f382df3cd9eb3ccb494ac03f9655` | `20260730T232000Z-independent-normalized-archive-acceptance-rejection` | 后继 `145000` |
| `20260731T003731Z-normalized-reports-remediated` | `rejected` | `279aae29fed0eadb402c77a8faea30429afb43c376eb36cfd2fed87c7194b8bb` | `20260731T004200Z-independent-acl-candidate-acceptance-rejection` | 后继 `231357` |
| `20260731T010513Z-acl-sealing-remediated` | `accepted` | `437146f5d6b8360b50c1e8db15697ed63766370b8dac5a7d5b05854b876c2784` | `20260731T011000Z-independent-acl-successor-acceptance` | 后继 `003731` |
| `20260731T011535Z-accepted-acl-successor` | `accepted` | `9c8fe2ca617e78e30c0aa63171b66d8ba9ce6f39d4b2ff7502463df5aed32bde` | `20260731T011700Z-independent-accepted-record-archive-acceptance` | 后继 `010513` |
| `20260814T162733Z` | `accepted` | `4c3bf7815f6e168bcf98a74bf4122503b9c3b44defcc9f5106fe6a3bd380965a` | `20260814T163250Z-v1-2-archive-local-acceptance` | 后继 `011535`；v1.2 候选，通过独立 archive-local 验收与隔离副本重放 |
| `20260814T174651Z` | `accepted` | `f4d1454742553624ec848a20b8fd0c5a24aabfc8d97a3c3bc2968061c86bb21b` | `20260814T175203Z-v1-2-archive-final-record-acceptance` | 后继 `162733`；v1.2 最终后继 |
| `20260815T080921Z-v1-3-candidate` | `accepted` | `084a6c2ca26e184e806cc7c0f203bced823249f469577aa196b946d4f42a0169` | `20260815T081829Z-v1-3-archive-candidate-acceptance` | 后继 `174651`；v1.3 候选，通过独立 archive-local 验收与隔离副本重放 |
| `20260815T082711Z-v1-3-final-record` | `accepted` | `b0a8d087bcbd07e4bddb1581d53db1c8675679e2cfab4ecbc40bf244edfd5412` | `20260815T101124Z-v1-3-archive-final-record-acceptance` | 后继 `080921`；v1.3 最终记录，独立验收，保持推荐位 |
| `20260815T121704Z-archive-tooling` | `accepted` | `372535a7b7ed902ac4d4306fb47110530f5474cee701cd5cdaff10e9f4c24fa9` | `20260815T121207Z-v1-3-archive-tooling-acceptance` | 后继 `082711`；归档流程工具化批次（non_independent 验收，不进入推荐位） |
| `20260816T054638Z-v1-4-candidate` | `accepted` | `10336c355f6a642135b9182266218d35400503212eaa76f5ab6ecd2dff45a10a` | `20260816T055218Z-v1-4-archive-candidate-acceptance` | 后继 `121704`；v1.4 候选，non_independent archive-local 验收与隔离副本重放通过（36 passed、1 skipped），不进入推荐位 |
| `20260816T055450Z-v1-4-final-record` | `accepted` | `cdb4410112c322cc43438331fb5e205d14f09f7f7cebf32c9d93b3a8bfdd773d` | `20260816T055531Z-v1-4-archive-final-record-acceptance` | 后继 `054638`；v1.4 最终记录，承载候选验收与完整登记链，non_independent archive-local 验收与隔离副本重放通过（36 passed、1 skipped），不进入推荐位 |

## 已解决的归档复核项

v1.4 档案在 v1.3 冻结契约基础上新增：本周期开发报告以 declared 双件制收录（v1.4 升级：REQ-050..053 与同周期修复）；本地软件验证记录（单元套件 347 passed、2 skipped；归档套件 37 passed、1 warning；集成 3 passed 含 AI 全链路；前端 tsc+vite 构建通过）随档案归档；候选与最终记录两次 archive-local 验收均通过隔离副本重放（各 36 passed、1 skipped、1 warning）。归档验证器登记一致性口径修正（non_independent 可登记、推荐位仍须 independent）随本周期进入基线并附回归测试；本周期两次验收均为 non_independent，按政策登记为 accepted 但不进入推荐位。

## 发布门禁

| 门禁 | 状态 | 关联需求 |
| --- | --- | --- |
| 真实 PostgreSQL 源到独立空目标的迁移、还原与查询验证 | `blocked` | `REQ-045` |
| Docker Compose migrate、API、worker、PostgreSQL、Redis 与 loopback 拓扑 | `blocked` | `REQ-045` |
| Edge 与 Chrome 黑盒 GUI 验收 | `blocked` | `REQ-001`, `REQ-044` |

## 规范关系

本报告由同名 JSON 侧车定义可机器验证的版本身份、候选链、裁定、缺陷关系、证据引用和门禁状态。后续快照或验收只能追加新报告与新 archive run，不能修改已封存的候选或其 manifest。
