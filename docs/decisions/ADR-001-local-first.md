# ADR-001：本地优先与 SQLite 默认

状态：接受。

选择 SQLite 为默认本地数据库，文件系统为 artifact 内容寻址仓库。原因是单用户、loopback 运行与无遥测要求（`REQ-001..003`）。生产容器另提供 PostgreSQL service/adaptor 边界，避免将 SQLite 细节扩散到领域服务（`REQ-045`）。
