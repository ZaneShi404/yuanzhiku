# 20260730 独立后继档案拒绝记录

## 范围

本记录固化独立测试与独立验收对 `V1-current-audit-20260730T121500Z-archive-remediated` 的只读结论。复核未访问日常 `data/`，未改写该目录、其 ZIP 或原始 T1 运行目录。测试期间归档目录被 Python 自动生成三份 `.pyc` 缓存；这些未受 manifest 管理的文件已被单独核对并移除，随后目录再次通过独立验证器。

## 已确认的修复

独立角色确认先前以下问题已在该中间快照解决：

- T1 中的 PID 语义键不再保留原始值；递归检查到的 PID 语义值均为固定占位符。
- T1 不再保留 output、stdout、stderr、response、traceback、stacktrace 及其前后缀变体。
- `DEF-BACK-002` 已出现在缺陷账本，证据登记中的缺陷引用均能在账本中找到。
- 目录与 ZIP 的 manifest 受管成员集合、哈希和大小一致；前序 manifest 哈希引用正确；发布状态仍为 `blocked`。

## 独立拒绝发现

| ID | 等级 | 发现 | 后继处置 |
| --- | --- | --- | --- |
| ARCH-REV-005 | High | T1 政策要求记录来源运行标识，但白名单、来源清单和证据登记没有显式且可交叉验证的 `source_run_id`。 | 白名单新增 `source_run_id`；构建器将其写入来源清单和证据登记；验证器要求三方与来源路径一致。 |
| ARCH-REV-006 | Medium | T0 文本脱敏删除归档单元测试里的合成 URL userinfo，使收录后的测试与工作树测试语义不同，档案内归档测试无法复现已记录的通过结果。 | 测试改为在运行时拼接合成 userinfo，归档副本保持字节语义；新增 T0 测试源码保真回归。 |

## 中间快照状态

`V1-current-audit-20260730T121500Z-archive-remediated` 作为不可变的中间构建记录保留，但不接受为符合本地 V1 档案政策的快照。后继档案必须以其 manifest 哈希作为前序引用，并包含本报告、修复源码和实际复测结果。

## 持续阻塞项

本次档案修复不解除发布门禁。真实 PostgreSQL 迁移/还原、物理 Docker Compose 拓扑，以及 Edge 和 Chrome 黑盒 GUI 验收仍为 `blocked`。
