# 架构

系统采用单机分层结构（`REQ-004`）：React UI 仅通过 REST 调 FastAPI；`main.py` 为交付组合根；`domain` 放稳定类型，`ports` 为仓储/存储抽象，`adapters` 处理 SQLite、文件系统及本地解析器，`services` 承载各业务模块。依赖方向为 UI -> API -> services -> ports <- adapters，domain 不依赖框架。

SQLite 位于 `<data-root>/state/knowledge.db`，artifact 位于 `<data-root>/artifacts/<sha256>`，临时文件只在 `<data-root>/staging`。完整本地路径不入数据库、API 或日志（`REQ-011`, `REQ-003`）。每次内容解析生成不可变 representation 与 evidence；人类修订创建新的 manual representation（`REQ-020`）。

视频沿用普通 `file` source 与不可变原始 artifact：`ImportService.video` 仅接收 MP4/WebM，创建 `video_analyze` durable job。`VideoService` 将 `MediaAnalyzerPort` 的本地探测结果写为 extraction representation 与 `video_metadata` evidence，并将 JPEG 时间采样帧保存为独立内容寻址 artifact，关联 `video_analyses`、`video_frames`。`LocalFfmpegMediaAnalyzer` 只以 `shell=False` 运行显式安装的 ffprobe/ffmpeg，带总 deadline、取消、租约心跳、内存、staging 磁盘及输出上限；不会处理 URL 或网络。`MediaAiPort` 当前由 `UnconfiguredMediaAi` 实现，稳定报告禁用且不发起网络连接。视频 Range 播放只读取已验证的本地原 artifact；架构中没有 URL 获取适配器，抖音继续仅由 external-card 元数据和用户主动外部浏览器打开构成。

本地进程内单 worker 用 durable jobs 表轮询；容器部署拆为 API/worker、一次性 `migrate` 服务、PostgreSQL 与 Redis。`migrate` 是唯一执行 Alembic 的容器职责；API/worker 只检查数据库 revision 已到镜像 head，未就绪即失败关闭。PostgreSQL 作业领取使用行锁和 `SKIP LOCKED`，使独立 API/worker 进程不会领取同一作业；运行时后端由环境配置决定（`REQ-032`, `REQ-045`）。

## REQ-004 冻结模块边界 → 代码载体映射

`REQ-004` 列出 11 个具名模块边界。实际代码按服务聚合粒度实现，命名并非一一同名；对应关系如下（2026-08-14 审计登记，命名粒度偏差已确认并保留）：

| 冻结模块名 | 实际代码载体 |
|---|---|
| sources | `main.py` sources 端点组 + `ports/repository.py` 仓储契约（无独立服务模块） |
| artifacts | `ports/storage.py` + `adapters/storage.py`（内容寻址不可变存储） |
| documents | `services/documents.py` + `services/imports.py` |
| evidence | `services/documents.py`（evidence/citation 与文档表示同服务承载） |
| knowledge | `services/documents.py`（知识创建/发布校验） |
| search | `services/search.py` |
| jobs | `services/jobs.py` + `worker.py` |
| taxonomy | `main.py` tags/topics 端点 + 仓储方法（固定分类/自由标签/主题） |
| lifecycle | `services/lifecycle.py` |
| external_cards | `services/external_cards.py` |
| settings | `main.py` settings 端点 + 仓储 settings 表 |

分层约束（框架/存储/解析/数据库在 ports/adapters 之后）经 import 扫描验证成立：fastapi 仅出现于 `main.py`，`sqlite3` 仅在 `adapters/`，解析仅在 `adapters/parsers.py`，`domain` 只依赖标准库。另有清单外服务模块 `imports`/`videos`/`transfers`，分别承载 REQ-010/015/040 的扩展职责。
