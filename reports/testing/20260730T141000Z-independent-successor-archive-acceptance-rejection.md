# 20260730 独立后继档案验收拒绝记录

## 范围

本记录固化独立验收对 `V1-current-audit-20260730T135500Z-archive-contract-remediated` 的只读结论。验收未编辑任何工作树或封存内容，未访问日常 `data/`，且在隔离副本中避免向封存目录写入字节码或 pytest 缓存。

## 已通过的检查

- 原件目录和 ZIP 均通过独立验证器；成员集合、manifest 哈希和大小一致，且隔离检查后原件哈希未变化。
- 未发现 `.pyc`、数据库、SQLite 文件或嵌套 ZIP。
- T1 的 `source_run_id`、来源路径、来源 SHA-256 和用途在白名单、来源清单和证据登记中符合现有契约。
- 前序 manifest 哈希正确引用 `20260730T121500Z-archive-remediated`，并且封存的前序登记表将其列为最新未接受快照。
- 缺陷账本与证据登记引用闭环；候选状态仍是 `V1 Candidate / BLOCKED`，没有把 PostgreSQL、Docker Compose 或 Edge/Chrome 门禁误称通过。

## 独立拒绝发现

| ID | 等级 | 发现 | 后继处置 |
| --- | --- | --- | --- |
| ARCH-REV-010 | High | 验收指令没有明确归档副本复现应使用工作树中既有的项目虚拟环境。验收角色在隔离副本可见的 Python 环境中没有 pytest，且受禁止安装依赖约束，无法执行 archive-contained `test_v1_archive`。 | 在归档政策和 `archives/README.md` 中明确复现前提与精确命令：仅可使用项目既有 `.venv`，不得安装依赖，必须在隔离副本中设置 `PYTHONDONTWRITEBYTECODE=1` 并禁用 pytest cache。后继验收必须在该预置环境下重放测试。 |

## 裁定

由于 archive-contained 回归无法由独立验收执行，严格结论为 **REJECT**。该候选作为不可变中间记录保留，不被原地修改。

- `ARCH-REV-004`：未解决，修复源码存在但本次独立验收未执行回归。
- `ARCH-REV-005`：已解决，T1 三方来源运行标识及相关记录一致。
- `ARCH-REV-006`：未解决，测试语义修复存在但本次独立验收未执行回归。
- `ARCH-REV-007`：未解决，结构化敏感键防护与回归存在但本次独立验收未执行。
- `ARCH-REV-008`：未解决，锁和非覆盖实现与回归存在但本次独立验收未执行。
- `ARCH-REV-009`：已解决，前序登记最新拒绝项和指定 manifest 哈希一致。

真实 PostgreSQL、物理 Docker Compose、Edge 和 Chrome 黑盒 GUI 验收继续保持 `blocked`。
