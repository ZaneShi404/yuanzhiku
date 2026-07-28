# 架构

系统采用单机分层结构（`REQ-004`）：React UI 仅通过 REST 调 FastAPI；`main.py` 为交付组合根；`domain` 放稳定类型，`ports` 为仓储/存储抽象，`adapters` 处理 SQLite、文件系统及本地解析器，`services` 承载各业务模块。依赖方向为 UI -> API -> services -> ports <- adapters，domain 不依赖框架。

SQLite 位于 `<data-root>/state/knowledge.db`，artifact 位于 `<data-root>/artifacts/<sha256>`，临时文件只在 `<data-root>/staging`。完整本地路径不入数据库、API 或日志（`REQ-011`, `REQ-003`）。每次内容解析生成不可变 representation 与 evidence；人类修订创建新的 manual representation（`REQ-020`）。

本地进程内单 worker 用 durable jobs 表轮询；容器部署拆为 API/worker，并使用 SQLAlchemy/Alembic PostgreSQL repository 和 Redis 服务。PostgreSQL 作业领取使用行锁和 `SKIP LOCKED`，使独立 API/worker 进程不会领取同一作业；运行时后端由环境配置决定（`REQ-032`, `REQ-045`）。
