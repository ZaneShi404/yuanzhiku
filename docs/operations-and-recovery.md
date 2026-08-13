# 运维与恢复

通过 `scripts/start-windows.ps1` 启动，默认仅监听 `127.0.0.1`。启动时获取 `<data-root>/state/instance.lock` 排他锁；已有实例不会启动第二个服务（`REQ-002`）。可用 `YUANZHIKU_DATA_ROOT` 覆盖数据根。首次无 `-Port` 启动会选择 `8765..8865` 中可用端口并写入 `<data-root>/state/port.json`；显式 `-Port` 会在确认该端口可用后替换该数据根的保存偏好。后续无 `-Port` 启动仅重用保存端口。若保存端口已由同一数据根的服务监听，启动器打开该服务；若端口被其他进程占用或已不可绑定，启动明确失败且不改写 `port.json`。`YUANZHIKU_DATABASE_URL` 未设置时使用 SQLite。显式 SQLite URL 使用 `sqlite://`；PostgreSQL 使用 `postgresql://`、`postgres://` 或 SQLAlchemy driver URL（例如 Compose 的 `postgresql+psycopg://...`）。所有 PostgreSQL URL 都进入 PostgreSQL repository；常规 API/worker 只检查 Alembic revision 已达到镜像 head，未就绪、连接、driver 或配置错误都会明确失败，绝不回落或伪装为 SQLite。Compose 仅由一次性 `migrate` 服务执行 schema upgrade，API/worker 依赖其成功完成。

每日首次成功启动入队一次低优先级 backup；备份在 `<data-root>/backups`，成功后保留最近 30 个日期项。SQLite 备份含一致的 SQLite 副本、逻辑记录和 artifact；PostgreSQL 备份含经事务读取的逻辑记录、`backups` catalog 记录和 artifact。两者都有 SHA-256 清单，不含模型、staging 或日志正文（`REQ-040`）。

还原 API 要求 `target_data_root` 不存在或为空且不同于当前根；因此不会覆盖当前库。PostgreSQL 逻辑备份还原还必须提供一个独立、无任何表的 PostgreSQL `target_database_url`：先完成归档验证和空目标检查，再对该目标显式迁移到 head，最后写入还原记录；不会降级写入 SQLite。缺失表或结构无效的完整备份记录会在接触目标前被拒绝。导出前由 UI 显式传递 `confirmed: true`；导出和 reimport 保持可移植业务记录，不包含或重建本地 `backups` catalog。reimport 将在写入前验证 zip、manifest、hash、关系和 ID 链冲突（`REQ-041`）。`/api/v1/verify` 提供完整或抽样 hash 校验（`REQ-042`）。

本地视频分析要求 `ffmpeg` 和 `ffprobe` 可由服务进程找到，或通过 `YUANZHIKU_FFMPEG_BIN`、`YUANZHIKU_FFPROBE_BIN` 明确指定。`GET /api/v1/capabilities` 的 `media.local` 显示二进制可用性；工具缺失时视频导入仍成功保存原件，但分析作业为 `blocked`，安装工具后可从作业页重试。视频总超时、内存、staging 磁盘、最大关键帧在设置页和 `/settings` 配置。日常备份、导出、还原与再导入都包含并校验原视频及受引用的关键帧 artifact、视频分析和帧关系；永久删除来源时只清理无引用的原件和关键帧。

链接下载（`video_download` 作业）在 yt-dlp 或 FFmpeg 缺失时明确 `blocked`（`/capabilities` 的 `downloader.enabled=false`，`POST /videos/link` 返回 `503`）；因反爬、链接失效、平台拒绝等外部原因失败时为 `failed` 且可有限重试（作业页重试按钮），绝不静默切换来源或平台。下载总超时、无进展观察窗口、staging 磁盘上限在设置页与 `/settings` 配置（`download_timeout_seconds`、`download_no_progress_seconds`、`download_disk_limit_mb`）。回环过滤代理仅监听 127.0.0.1 随机端口、仅存活于单个下载作业生命周期，作业结束（无论成败）即关闭，无长驻端口。`data/state/download` 只放用户显式导入的 `cookies.txt`（1MB 上限，重复导入覆盖旧文件）；删除该文件或调用 `DELETE /settings/download-cookie` 即彻底移除 Cookie。备份、导出与 reimport 显式排除 `data/state/download` 路径（manifest `exclusions` 含 `state/download`），Cookie 内容绝不进入备份快照、导出 ZIP 或再导入；作业内 Cookie 拷贝随 per-job staging 作业结束即清理。

操作日志只记录事件类型、ID、结果和时间，按日保留 30 天，不写正文、路径、令牌或请求体（`REQ-003`, `REQ-042`）。任何运行、恢复或排障步骤都不得获取来源 URL、调用网页视频平台或以程序方式处理抖音；外部卡始终只保留用户输入的元数据。
