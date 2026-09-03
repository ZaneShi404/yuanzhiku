# v1.6.0 版本归档汇总

## 版本结论

`v1.6.0`（可靠性与安全加固：访问边界与凭据加固、输入与数据一致性、删除/恢复可靠性、部署收紧、发布前脱敏）的本周期候选快照 `20260903T063956Z-v1-6-hardening-candidate` 与最终记录快照 `20260903T064539Z-v1-6-final-record` 均以 `non_independent`（同会话自我验收，另含一次全新上下文代理的对抗性代码复核）通过 `archive_local` 验收并被接受登记。按归档政策的独立性口径，`non_independent` 验收的候选可被接受登记，但不得作为版本推荐快照：**本版本推荐审计快照保持为上一独立验收记录 `20260815T082711Z-v1-3-final-record`**（2026-08-15），v1.6.0 快照待独立 `archive_local` 验收后方可进入推荐位。

这不是产品发布批准。真实 PostgreSQL 迁移/还原与 Docker Compose 物理拓扑门禁仍为 `blocked`；`release_readiness` 保持 `blocked`（浏览器黑盒已在门禁执行轮完成库页面与设置页实证并截图留档）。

## 版本内容

- **访问与秘密**（`REQ-002`、`REQ-003`、`REQ-011`、`REQ-052`）：凭据与 Cookie 文件 ACL 收紧（断继承、仅当前账户+SYSTEM+Administrators）；凭据损坏语义 `503 credential_store_corrupt`；Host/Origin 本机访问边界（`403 untrusted_host` / `403 untrusted_origin`）；uvicorn/nginx 代理头与未知 Host 收紧；AI/relay 出站最小修复（`trust_env=False` 全覆盖、上传主机逐次校验、恒时比较）。
- **输入与数据一致性**（`REQ-011`、`REQ-032`、`REQ-033a`、`REQ-040..042`）：结构化输入上限与文件内容入库前校验；内容寻址命中逐字节校验与隔离修复；确定性身份与 insert-or-return 幂等重放；`commit_job_success` 最终租约栅栏（终态与业务效果同事务）；批量删除事务屏障；前端异步栅栏。
- **删除与恢复**（`REQ-034`、`REQ-040..042`）：purge 持久化清理队列（迁移 010）；恢复目录锚定 + 同卷暂存原子发布 + 外键检查；再导入全量 artifact 物理修复；日期轮转完整性抽样。
- **部署与发布**（`REQ-045`、`REQ-046`）：Compose 移除 Redis、端口隔离、admin/app 角色分离；staging marker 化生命周期；发布前检查脚本；发布前历史脱敏（域名/IP/本机用户名全历史清零）。
- **依赖升级**（用户批准）：starlette 0.49.1 + fastapi 0.141.1、torch 2.10.0、pypdf 6.15.0、pytest 9.0.3、npm nanoid/vite；torch 两条公告因生态限制登记为已接受风险。

## 候选链

| Run ID | 本地档案裁定 | Manifest SHA-256 | 裁定记录 | 后继关系 |
| --- | --- | --- | --- | --- |
| `20260903T063956Z-v1-6-hardening-candidate` | `accepted` | `dd1819b0e68ce7826b9cdb79549224982b9319235080c5921c074c4f9eec5a6e` | `20260903T064125Z-v1-6-hardening-candidate-acceptance` | 后继 `20260816T055450Z-v1-4-final-record` |
| `20260903T064539Z-v1-6-final-record` | `accepted` | `16dd16d22e9f039f651dcbeafb1be67e42f7728e089a40e501ba1e2d8d7682eb` | `20260903T064624Z-v1-6-final-record-acceptance` | 后继 `20260903T063956Z-v1-6-hardening-candidate` |
