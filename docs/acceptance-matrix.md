# 验收映射

| 需求组 | 实现证据 | 自测标识 | 独立复核重点 |
|---|---|---|---|
| REQ-001..004 | `backend/app/main.py`, `frontend/src/App.tsx`, `scripts/start-windows.ps1` | T-API-001, T-UI-001 | IPv4、端口持久化、无敏感日志 |
| REQ-010..014 | `services/imports.py`, `adapters/storage.py`, `adapters/parsers.py` | T-ING-001, T-ING-002 | 大文件空间、Docling 可用时路径、损坏文件 |
| REQ-015..017 | `domain/media.py`, `ports/media.py`, `adapters/media.py`, `services/videos.py`, `frontend/src/App.tsx` | T-VID-001, T-VID-002 | 仅本地 MP4/WebM、无 shell/网络、FFmpeg 缺失阻止、Range 播放、关键帧哈希和禁用 AI |
| REQ-020..025 | `services/documents.py`, `services/search.py` | T-ING-002, T-KNOW-001 | locator 完整性、检索默认范围 |
| REQ-030..031 | `services/external_cards.py` | T-EXT-001 | 代码审计无 URL 获取路径，抖音不进入 HTTP client、worker 或 parser |
| REQ-032..034 | `services/jobs.py`, `services/lifecycle.py` | T-JOB-001, T-LIFE-001 | 断路器、重试、取消、清理 |
| REQ-033a | `services/jobs.py`, `services/videos.py`, `tests/unit/test_video_media.py` | T-VID-001 | 视频作业状态、取消、重试与 AI 阻止不破坏已完成版本 |
| REQ-040..042 | `services/transfers.py` | T-BACK-001, T-VID-002 | 快照一致性、restore 新根、视频帧引用和 artifact 清理 |
| REQ-043..046 | `main.py`, `app/migrate.py`, `docker-compose.yml`, `Dockerfile`, docs | T-API-001, T-COMP-001, T-ARCH-001 | endpoint 覆盖、一次性数据库迁移、锁定前端构建与 loopback 容器验证；归档 v2 报告配对、legacy/snapshot 冻结登记、逐项版本候选链、声明式验收身份、来源/证据/需求/缺陷交叉验证、Windows ACL 封存及 v1 兼容性 |
