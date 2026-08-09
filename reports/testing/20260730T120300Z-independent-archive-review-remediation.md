# 20260730 独立归档复核与修复记录

## 范围

本记录固化对 `V1-current-audit-20260730T110828Z` 的独立测试和独立验收结论，以及后继档案的修复范围。复核只读取档案和工作树材料；未访问日常 `data/`，未改写旧档案、其 ZIP 或原始运行目录。

## 旧档案结论

旧档案的目录与 ZIP 均曾通过当时的独立清单、成员集合和哈希校验：有 104 个 manifest 条目、106 个物理成员，且目录与 ZIP 的受管成员集合一致。其状态声明保持诚实：`V1 Candidate / BLOCKED`、`archive_integrity: verified`、`local_software_validation: passed`、`release_readiness: blocked`。

后继验证器引入 PID、运行输出和缺陷 ID 闭环检查后，旧目录与 ZIP 被明确拒绝，拒绝原因是 T1 运行输出字段；该拒绝是政策加强后的预期结果，并不改写旧档案的原始清单或其当时的结构性校验记录。

但旧档案未通过其自身的 T1 脱敏与可追溯性政策，因此不能作为合规的 V1 审计档案接受。该结论不否定它作为当时结构性快照的留存价值。

## 独立发现

| ID | 等级 | 发现 | 处置 |
| --- | --- | --- | --- |
| ARCH-REV-001 | High | T1 端口生命周期结果中的 `listener_pid_after_second_launch` 和 `original_listener_pid` 仍保留原始进程标识。 | 后继构建器按 PID 语义键统一替换为固定占位符；验证器拒绝非占位符值。 |
| ARCH-REV-002 | Medium | T1 结果的嵌套 `launch.output` 含运行输出和 traceback 正文。 | 后继构建器删除标准输出、错误、响应、traceback、stacktrace 及其前后缀变体；验证器拒绝该类键。 |
| ARCH-REV-003 | Medium | `DEF-BACK-002` 被 allowlist 和证据登记引用，但旧档案缺陷账本没有对应条目。 | 后继账本补入该条目；验证器要求每个证据登记缺陷 ID 均存在于账本。 |
| ARCH-REV-004 | Medium | 旧档案基线中的归档单元测试把普通 URL 当作敏感输入，和构建器实际仅拒绝 URL userinfo 的规则不一致，导致该档案内的聚焦测试不可复现。 | 工作树回归改为 userinfo 形式；后继档案收录修正后的测试源。 |

## 修复与回归

后继档案构建器和独立验证器覆盖：`pid`、`*_pid`、`pid_*`、`*_pid_*`、`process_id` 语义键，并在值为对象、数组或标量时都不保留原值。输出键覆盖精确名称及标准输出、错误、响应、traceback、stacktrace 的前后缀组合。

新增归档单元测试构造合成 PID 和嵌套输出字段，并在重算 manifest 后直接验证篡改档案仍被拒绝；也验证未知 `DEF-*` 引用被拒绝。聚焦运行结果为 `8 passed, 1 warning`。该 warning 来自故意写入重复 `manifest.json` 的 ZIP 安全夹具，不代表被构建的档案包含重复成员。

## 后继与发布门禁

后继档案以 `provenance/predecessor.json` 引用旧档案 manifest 的 SHA-256，并标记旧档案为 `not_accepted_under_policy`；旧档案不被改写。

本地归档合规修复不解除发布门禁。真实 PostgreSQL 源到独立空目标库的迁移/还原、物理 Docker Compose 拓扑，以及 Edge 和 Chrome 黑盒 GUI 验收仍为 `blocked`。
