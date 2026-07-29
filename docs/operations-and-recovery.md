# 运维与恢复

通过 `scripts/start-windows.ps1` 启动，默认仅监听 `127.0.0.1`。启动时获取 `<data-root>/state/instance.lock` 排他锁；已有实例会失败退出（`REQ-002`）。可用 `YUANZHIKU_DATA_ROOT` 覆盖数据根。`YUANZHIKU_DATABASE_URL` 未设置时使用 SQLite。显式 SQLite URL 使用 `sqlite://`；PostgreSQL 使用 `postgresql://`、`postgres://` 或 SQLAlchemy driver URL（例如 Compose 的 `postgresql+psycopg://...`）。所有 PostgreSQL URL 都进入 SQLAlchemy/Alembic PostgreSQL repository；连接、driver 或迁移配置无效时启动明确失败，绝不回落或伪装为 SQLite。

每日首次成功启动入队一次低优先级 backup；备份在 `<data-root>/backups`，成功后保留最近 30 个日期项。SQLite 备份含一致的 SQLite 副本、逻辑记录和 artifact；PostgreSQL 备份含经事务读取的逻辑记录、`backups` catalog 记录和 artifact。两者都有 SHA-256 清单，不含模型、staging 或日志正文（`REQ-040`）。

还原 API 要求 `target_data_root` 不存在或为空且不同于当前根；因此不会覆盖当前库。PostgreSQL 逻辑备份还原还必须提供空的 PostgreSQL `target_database_url`，不会降级写入 SQLite，并在接触目标前拒绝缺失表或结构无效的完整备份记录。导出前由 UI 显式传递 `confirmed: true`；导出和 reimport 保持可移植业务记录，不包含或重建本地 `backups` catalog。reimport 将在写入前验证 zip、manifest、hash、关系和 ID 链冲突（`REQ-041`）。`/api/v1/verify` 提供完整或抽样 hash 校验（`REQ-042`）。

操作日志只记录事件类型、ID、结果和时间，按日保留 30 天，不写正文、路径、令牌或请求体（`REQ-003`, `REQ-042`）。
