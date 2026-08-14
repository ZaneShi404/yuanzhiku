# v1.2.0 版本归档汇总

## 版本结论

`v1.2.0`（链接获取 / 受限视频下载）的推荐审计快照是 `20260814T162733Z`。它已通过独立的 `archive_local` 验收（2026-08-14），表示档案目录、ZIP、证据链、隔离副本重放与本地软件验证记录在该范围内可接受。

这不是产品发布批准。真实 PostgreSQL 迁移/还原、Docker Compose 物理拓扑和 Edge/Chrome 黑盒门禁仍为 `blocked`；`release_readiness` 保持 `blocked`。

同日较早构建 `20260814T160204Z` 为未送审的中间构建（同一工作树、缺少本地验证记录），不进入登记链；正式候选仅 `20260814T162733Z`。

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
| `20260814T162733Z` | `accepted` | `4c3bf7815f6e168bcf98a74bf4122503b9c3b44defcc9f5106fe6a3bd380965a` | `20260814T163250Z-v1-2-archive-local-acceptance` | 后继 `011535`；v1.2 当前推荐，通过独立 archive-local 验收与隔离副本重放 |

## 已解决的归档复核项

v1.2 档案在 v1.0 冻结契约基础上新增：七个链接获取真实缺陷（`DEF-LINK-001..007`）与四项外部优化缺口（标题编码、Docling 页面定位、引用详情、集成测试）均已进入缺陷台账并闭环；`video_download_provenance` 表与 schema v7/Alembic 008 进入档案基线；报告双件化（18 份 declared + 24 份 legacy_inferred 登记一一对应）；本地软件验证记录（212 passed, 2 skipped）随档案归档。

## 发布门禁

| 门禁 | 状态 | 关联需求 |
| --- | --- | --- |
| 真实 PostgreSQL 源到独立空目标的迁移、还原与查询验证 | `blocked` | `REQ-045` |
| Docker Compose migrate、API、worker、PostgreSQL、Redis 与 loopback 拓扑 | `blocked` | `REQ-045` |
| Edge 与 Chrome 黑盒 GUI 验收 | `blocked` | `REQ-001`, `REQ-044` |

## 规范关系

本报告由同名 JSON 侧车定义可机器验证的版本身份、候选链、裁定、缺陷关系、证据引用和门禁状态。后续快照或验收只能追加新报告与新 archive run，不能修改已封存的候选或其 manifest。
