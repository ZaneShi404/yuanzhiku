# 架构

系统采用单机分层结构（`REQ-004`）：React UI 仅通过 REST 调 FastAPI；`main.py` 为交付组合根；`domain` 放稳定类型，`ports` 为仓储/存储抽象，`adapters` 处理 SQLite、文件系统及本地解析器，`services` 承载各业务模块。依赖方向为 UI -> API -> services -> ports <- adapters，domain 不依赖框架。

SQLite 位于 `<data-root>/state/knowledge.db`，artifact 位于 `<data-root>/artifacts/<sha256>`，临时文件只在 `<data-root>/staging`。完整本地路径不入数据库、API 或日志（`REQ-011`, `REQ-003`）。每次内容解析生成不可变 representation 与 evidence；人类修订创建新的 manual representation（`REQ-020`）。

视频沿用普通 `file` source 与不可变原始 artifact：`ImportService.video` 仅接收 MP4/WebM，创建 `video_analyze` durable job。`VideoService` 将 `MediaAnalyzerPort` 的本地探测结果写为 extraction representation 与 `video_metadata` evidence，并将 JPEG 时间采样帧保存为独立内容寻址 artifact，关联 `video_analyses`、`video_frames`。`LocalFfmpegMediaAnalyzer` 只以 `shell=False` 运行显式安装的 ffprobe/ffmpeg，带总 deadline、取消、租约心跳、内存、staging 磁盘及输出上限；不会处理 URL 或网络。`MediaAiPort` 当前由 `UnconfiguredMediaAi` 实现，稳定报告禁用且不发起网络连接。视频 Range 播放只读取已验证的本地原 artifact；架构中没有 URL 获取适配器，抖音继续仅由 external-card 元数据和用户主动外部浏览器打开构成。

本地进程内单 worker 用 durable jobs 表轮询；容器部署拆为 API/worker、一次性 `migrate` 服务、PostgreSQL 与 Redis。`migrate` 是唯一执行 Alembic 的容器职责；API/worker 只检查数据库 revision 已到镜像 head，未就绪即失败关闭。PostgreSQL 作业领取使用行锁和 `SKIP LOCKED`，使独立 API/worker 进程不会领取同一作业；运行时后端由环境配置决定（`REQ-032`, `REQ-045`）。
