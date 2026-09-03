# v1-6-hardening-implementation：开发报告

- 报告 ID：`RPT-V1-6-HARDENING-IMPLEMENTATION-20260903T062808Z-001`
- 记录时间（UTC）：`2026-09-03T06:28:08Z`
- 报告类型：`development`
- 作者角色：`development`
- 独立性：`non_independent`
- 产品版本：`v1.6.0`
- 裁定范围：`archive_local`
- 裁定：`accepted`

## 范围

本报告覆盖 v1.6.0 可靠性与安全加固周期的实现工作（提交区间 `894a510` 之后的 21 个提交，含独立复核后的文档同步与发布前脱敏）。关联需求：`REQ-002`、`REQ-003`、`REQ-011`、`REQ-032`、`REQ-033a`、`REQ-034`、`REQ-040`、`REQ-041`、`REQ-042`、`REQ-045`、`REQ-046`、`REQ-052`。

周期工作按四批次实施，全程「每任务先红后绿、每提交过定向回归」：

1. **访问与秘密**（批次一）：凭据与 Cookie 文件 ACL 收紧（Windows 断继承仅授当前账户、SYSTEM、Administrators；POSIX 0600/0700），凭据文件损坏语义改为 `503 credential_store_corrupt` 且绝不覆盖原文件；Host/Origin 本机访问边界中间件（`403 untrusted_host` / `403 untrusted_origin`）；uvicorn `--no-proxy-headers` 与 nginx 未知 Host 拒绝；AI/relay 出站最小修复（应用内全部 httpx 客户端 `trust_env=False`、DashScope 上传主机逐次校验、中转恒时比较与固定公网地址）。
2. **输入与数据一致性**（批次二）：结构化输入上限（标签/ID 列表/JSON 体积）与文件内容入库前校验（PDF 魔数、DOCX zip 元数据上限、文本空字节）；内容寻址命中逐字节校验与隔离修复；自动作业与派生产物确定性身份（UUIDv5 insert-or-return）；`commit_job_success` 最终租约栅栏（作业终态与业务效果同事务、晚到提交整体无效）；批量删除事务内屏障；前端统一 API 层与异步状态栅栏。
3. **删除与恢复**（批次三）：purge 持久化清理队列（迁移 010，catalog 删除前入队、幂等 sweeper、失败 `503 artifact_cleanup_pending`）；备份恢复目录锚定与同卷暂存原子发布（含外键检查与全量哈希校验）；再导入遍历归档全部 artifact 的物理修复与日期轮转完整性抽样。
4. **部署与发布**（批次四）：Compose 移除未使用 Redis、数据库端口隔离、admin/app 角色分离；staging 目录 marker 化生命周期；发布前 Git index/历史检查脚本；依赖审计升级（starlette/fastapi/torch/pypdf/pytest 与 npm 三包，经用户批准）。

经用户批准的计划偏差：Task 3（API 令牌）砍除；Task 4 仅落地最小修复（4b 全量统一代理砍除）；Task 8 以代表性故障注入覆盖；Task 10 两子项未做（来源类型过滤跨实体语义、上传离页导航）。

## 验证

- 全量后端回归（最终依赖组合：fastapi 0.141.1、starlette 0.49.1、torch 2.10.0、pypdf 6.15.0、pytest 9.0.3）：`tests/unit` + `tests/integration` 共 **489 通过、4 跳过、0 失败**，退出码 0；较基线新增 137 项加固测试全部通过，零回归。
- 前端：node:test 9 通过、`tsc -b` 检查与构建全部退出码 0。
- 独立对抗复核（全新上下文代理）：裁决 APPROVE-WITH-CONDITIONS，零 P1；唯一 P2（规范文档同步缺口）已由后续提交补齐。
- 最终门禁 Gate A–E 全部执行：OpenAPI 与基线对照零删改；日常数据只读快照演练（迁移前后计数一致、完整性检查通过、外键违规 0、全部 artifact 哈希校验通过、既有备份经新锚定与暂存路径还原到新根全链一致）；真实服务启动冒烟（恶意 Host 请求被 403 拒绝）；发布前全历史敏感字面量清除并归零验证。

## 结论

v1.6.0 加固周期的全部计划任务实现完毕，缺陷与门禁证据如上。已知保留风险如实登记：Compose 物理拓扑门禁因本机无容器环境保持阻塞；torch 生态限制导致部分安全公告暂不可修复（本地离线用法暴露面极低）。GATE-BROWSER-BLACKBOX 在门禁执行轮已完成库页面与设置页的浏览器实证并截图留档，本报告将该门禁更新为 passed。
