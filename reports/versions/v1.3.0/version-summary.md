# v1.3.0 版本归档汇总

## 版本结论

`v1.3.0`（统一导入与本地化体验升级）的推荐审计快照是 `20260815T082711Z-v1-3-final-record`（最终后继快照，承载候选 `20260815T080921Z-v1-3-candidate` 的独立验收记录与完整登记链）。它已通过独立的 `archive_local` 验收（2026-08-15），表示档案目录、ZIP、证据链、隔离副本重放与本地软件验证记录在该范围内可接受。

这不是产品发布批准。真实 PostgreSQL 迁移/还原、Docker Compose 物理拓扑和 Edge/Chrome 黑盒门禁仍为 `blocked`；`release_readiness` 保持 `blocked`。

## 版本内容

- Windows 桌面入口（快捷方式/启动脚本）与 `InstanceLock` 追加增长修复（REQ-002）。
- 导入预填（REQ-049）：文档/文本/图片元数据的只读识别建议，不持久化、不联网、不覆盖用户已编辑字段。
- 链接元数据探测（REQ-047b）：受限下载通道的只读子能力，「识别链接」按钮回填标题/作者/来源日期。
- 图片导入（REQ-048）：jpg/png/webp 不可变 artifact + Pillow `image_analyze` 作业 + `image_metadata` evidence，零新表。
- 统一导入页（REQ-044 修订）：单入口智能识别文本/文档/图片/视频/链接/外部卡；视频导航收起；外部卡页只读化；分享口令混合文本的平台链接提取。
- B站下载通道修复：注册域补登 `bilivideo.cn`（决策 13，实测证据）；代理转发固定时长强拆缺陷修复。
- 按平台 Cookie 库（REQ-047a 修订）：`cookies/<platform>.txt` 按平台存放、自动选用、遗留单文件启动迁移，全部安全不变量保留。

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
| `20260815T082711Z-v1-3-final-record` | `accepted` | `b0a8d087bcbd07e4bddb1581d53db1c8675679e2cfab4ecbc40bf244edfd5412` | `20260815T101124Z-v1-3-archive-final-record-acceptance` | 后继 `080921`；v1.3 当前推荐，通过独立 archive-local 验收与隔离副本重放 |
| `20260815T121704Z-archive-tooling` | `accepted` | `372535a7b7ed902ac4d4306fb47110530f5474cee701cd5cdaff10e9f4c24fa9` | `20260815T121207Z-v1-3-archive-tooling-acceptance` | 后继 `082711`；归档流程工具化批次（non_independent 验收，不进入推荐位） |
| `20260816T054638Z-v1-4-candidate` | `accepted` | `10336c355f6a642135b9182266218d35400503212eaa76f5ab6ecd2dff45a10a` | `20260816T055218Z-v1-4-archive-candidate-acceptance` | 后继 `121704`；v1.4 候选（non_independent 验收，不进入推荐位） |
| `20260816T055450Z-v1-4-final-record` | `accepted` | `cdb4410112c322cc43438331fb5e205d14f09f7f7cebf32c9d93b3a8bfdd773d` | `20260816T055531Z-v1-4-archive-final-record-acceptance` | 后继 `054638`；v1.4 最终记录（non_independent 验收，不进入推荐位） |

## 已解决的归档复核项

v1.3 档案在 v1.2 冻结契约基础上新增：六项开发记录以 declared 双件制收录（桌面入口与实例锁、导入预填与图片导入、统一导入页、B站下载通道修复、按平台 Cookie 库、分享口令提取）；本地软件验证记录（单元套件 269 passed、2 skipped；下载链路 93 passed；Cookie 库 114 passed）随档案归档；两次 archive-local 验收（候选与最终记录）均通过隔离副本重放（各 29 passed）。本周期三处真实修复（实例锁追加增长、bilivideo.cn 注册域缺口、代理转发强拆）未新增缺陷台账条目——台账登记规则与缺口见归档流程复核记录。

## 发布门禁

| 门禁 | 状态 | 关联需求 |
| --- | --- | --- |
| 真实 PostgreSQL 源到独立空目标的迁移、还原与查询验证 | `blocked` | `REQ-045` |
| Docker Compose migrate、API、worker、PostgreSQL、Redis 与 loopback 拓扑 | `blocked` | `REQ-045` |
| Edge 与 Chrome 黑盒 GUI 验收 | `blocked` | `REQ-001`, `REQ-044` |

## 规范关系

本报告由同名 JSON 侧车定义可机器验证的版本身份、候选链、裁定、缺陷关系、证据引用和门禁状态。后续快照或验收只能追加新报告与新 archive run，不能修改已封存的候选或其 manifest。
