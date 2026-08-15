# v1.0.0 版本归档汇总

## 版本结论

`v1.0.0` 的推荐审计快照是 `20260731T011535Z-accepted-acl-successor`。它已通过独立的 `archive_local` 验收，表示档案目录、ZIP、证据链和隔离副本重放在该范围内可接受。

这不是产品发布批准。真实 PostgreSQL 迁移/还原、Docker Compose 物理拓扑和 Edge/Chrome 黑盒门禁仍为 `blocked`。

## 候选链

| Run ID | 本地档案裁定 | Manifest SHA-256 | 裁定记录 | 后继关系 |
| --- | --- | --- | --- | --- |
| `20260730T110828Z` | `rejected` | `7fd9cc5afd3576b959989c1a43abca4f75b5599d126fd8017ca56f55577b49da` | `20260730T120300Z-independent-archive-review-remediation` | 初始候选 |
| `20260730T121500Z-archive-remediated` | `rejected` | `f196d50e81518bd4ed4c8ac702095bd3794864af585e7cad27950b415ab6e708` | `20260730T123000Z-independent-successor-archive-rejection` | 后继 `110828` |
| `20260730T135500Z-archive-contract-remediated` | `rejected` | `55e6cf2ebb9bf743e9830b64bca5402df5d4246478d98af0e36f4baf75d4424e` | `20260730T141000Z-independent-successor-archive-acceptance-rejection` | 后继 `121500` |
| `20260730T145000Z-replay-contract-remediated` | `accepted` | `1b03170cec6e9db53df1c8f1ad1a8966becc1f110bb45b61fa8edc3cca22cd8d` | `20260730T150000Z-independent-successor-archive-acceptance` | 后继 `135500`；历史 archive-local 接受 |
| `20260730T231357Z-normalized-reports` | `rejected` | `b5bcdbd6cfad51dc9babd428571bc751f706f382df3cd9eb3ccb494ac03f9655` | `20260730T232000Z-independent-normalized-archive-acceptance-rejection` | 后继 `145000`；目录含未受 manifest 管理的字节码成员，且当时版本汇总遗漏该候选 |
| `20260731T003731Z-normalized-reports-remediated` | `rejected` | `279aae29fed0eadb402c77a8faea30429afb43c376eb36cfd2fed87c7194b8bb` | `20260731T004200Z-independent-acl-candidate-acceptance-rejection` | 后继 `231357`；发布目录允许新增未受 manifest 管理的成员，虽匹配 ZIP 仍有效 |
| `20260731T010513Z-acl-sealing-remediated` | `accepted` | `437146f5d6b8360b50c1e8db15697ed63766370b8dac5a7d5b05854b876c2784` | `20260731T011000Z-independent-acl-successor-acceptance` | 后继 `003731`；历史 archive-local 接受 |
| `20260731T011535Z-accepted-acl-successor` | `accepted` | `9c8fe2ca617e78e30c0aa63171b66d8ba9ce6f39d4b2ff7502463df5aed32bde` | `20260731T011700Z-independent-accepted-record-archive-acceptance` | 后继 `010513`；当前推荐，承载前一接受记录并通过独立 archive-local 验收 |
| `20260814T162733Z` | `accepted` | `4c3bf7815f6e168bcf98a74bf4122503b9c3b44defcc9f5106fe6a3bd380965a` | `20260814T163250Z-v1-2-archive-local-acceptance` | 后继 `011535`；v1.2 候选（本版本汇总的推荐快照仍为 `011535`，v1.2 推荐见 `reports/versions/v1.2.0/`） |
| `20260814T174651Z` | `accepted` | `f4d1454742553624ec848a20b8fd0c5a24aabfc8d97a3c3bc2968061c86bb21b` | `20260814T175203Z-v1-2-archive-final-record-acceptance` | 后继 `162733`；v1.2 最终后继（本版本汇总的推荐快照仍为 `011535`） |
| `20260815T080921Z-v1-3-candidate` | `accepted` | `084a6c2ca26e184e806cc7c0f203bced823249f469577aa196b946d4f42a0169` | `20260815T081829Z-v1-3-archive-candidate-acceptance` | 后继 `174651`；v1.3 升级候选，通过独立 archive-local 验收与隔离副本重放 |
| `20260815T082711Z-v1-3-final-record` | `accepted` | `b0a8d087bcbd07e4bddb1581d53db1c8675679e2cfab4ecbc40bf244edfd5412` | `20260815T101124Z-v1-3-archive-final-record-acceptance` | 后继 `080921`；v1.3 最终后继（本版本汇总的推荐快照仍为 `011535`） |
| `20260815T121704Z-archive-tooling` | `accepted` | `372535a7b7ed902ac4d4306fb47110530f5474cee701cd5cdaff10e9f4c24fa9` | `20260815T121207Z-v1-3-archive-tooling-acceptance` | 后继 `082711`；归档流程工具化批次（non_independent 验收，不进入推荐位） |

## 已解决的归档复核项

独立验收已确认 `ARCH-REV-004` 至 `ARCH-REV-010` 的归档范围处置完成，包括 T1 来源运行标识三方核对、敏感 JSON 键防护、非覆盖封存、前序登记选择和既有项目虚拟环境下的隔离副本重放。

## 发布门禁

| 门禁 | 状态 | 关联需求 |
| --- | --- | --- |
| 真实 PostgreSQL 源到独立空目标的迁移、还原与查询验证 | `blocked` | `REQ-045` |
| Docker Compose migrate、API、worker、PostgreSQL、Redis 与 loopback 拓扑 | `blocked` | `REQ-045` |
| Edge 与 Chrome 黑盒 GUI 验收 | `blocked` | `REQ-001`, `REQ-044` |

## 规范关系

本报告由同名 JSON 侧车定义可机器验证的版本身份、候选链、裁定、缺陷关系、证据引用和门禁状态。后续快照或验收只能追加新报告与新 archive run，不能修改本版本已封存的候选或其 manifest。
