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
| REQ-043..046 | `main.py`, `app/migrate.py`, `docker-compose.yml`, `Dockerfile`, `tests/integration/test_local_full_chain.py`, docs | T-API-001, T-COMP-001, T-INT-001, T-ARCH-001 | endpoint 覆盖、一次性数据库迁移、锁定前端构建与 loopback 容器验证；归档 v2 报告配对、legacy/snapshot 冻结登记、逐项版本候选链、声明式验收身份、来源/证据/需求/缺陷交叉验证、Windows ACL 封存及 v1 兼容性 |
| REQ-015(修订), REQ-031(例外), REQ-047, REQ-047a | `ports/media.py`, `adapters/downloader.py`, `services/jobs.py`, `services/imports.py`, `domain/models.py`, `main.py`, `frontend/src/App.tsx` | T-VID-003, T-VID-004 | 白名单与 URL 校验、注册域清单与回环代理强制、重定向逐跳拒绝、无 shell 子进程、断路器（含无进展与内存）、单通道 Cookie 不进 DB/日志/备份/导出/reimport、provenance 承载与脱敏、失败无残留、成功自动入队 video_analyze、抖音例外仅限 REQ-047/047a 通道 |
| REQ-047b, REQ-048, REQ-049 | `adapters/downloader.py`, `services/prefill.py`, `services/images.py`, `services/imports.py`, `services/jobs.py`, `domain/media.py`, `main.py`, `frontend/src/App.tsx` | T-VID-006, T-IMG-001, T-ING-003 | 探测与下载同一套白名单/回环代理/无 shell 约束；预填零持久化零网络且不覆盖用户已编辑字段；图片零新表（备份/导出/检索自动覆盖）、EXIF 只读、无 OCR/AI；权利确认、分类、标签不参与自动填写 |
| REQ-016(修订), REQ-053 | `adapters/media.py`, `services/videos.py`, `adapters/sqlite.py`, `services/transfers.py`, `tests/unit/test_video_media.py` | T-VID-007 | 场景吸附与半槽距容差、5%/95% 锚点、短视频 ≥3 帧、黑帧候选重试、帧真实宽高与 scene/even reason 持久化、config_hash 全参数化、verify-before-persist、多分析列表与当前标记、complete 门控、v8 迁移与 ≤v7 归档 reason 默认 |
| REQ-017(修订), REQ-051(修订), REQ-052(修订) | `adapters/media_ai.py`, `services/ai_credentials.py`, `services/jobs.py`, `domain/models.py`, `main.py`, `tests/unit/test_media_ai.py` | T-AI-001 | 双组独立门控与全关即零流量、base_url 标准校验、凭据文件隔离与掩码回显、错误永不回显 URL/密钥/响应正文、音频分块偏移合并、级联 tier1/tier2 与 visual_gap、建议收敛分类清单并自动写入只填空缺（已填不覆盖/标签并集/审计不含内容）、自动流水线串联与开关、source_classify 文档/粘贴分类与正文截断、失败/取消不降版本状态、凭据排除备份与导出 |
| REQ-024(修订), REQ-025(修订), REQ-050 | `domain/models.py`, `services/search.py`, `adapters/sqlite.py`, `services/transfers.py`, `services/imports.py`, `main.py`, `frontend/src/App.tsx`, `tests/unit/test_taxonomy.py` | T-TAX-001 | taxonomy 端点唯一下发、写入校验（领域多选/体裁 ≤1）、v9 迁移拆分映射、多体裁遗留行编辑强制单选、≤v7 归档再导入规范化、领域（OR/`_none`）/体裁/`topic_id` 过滤、分类与标签 token 退出全文、`ffmpeg-local` 元数据退出全文 |
| REQ-025(修订) | `adapters/sqlite.py`, `main.py`, `frontend/src/App.tsx`, `tests/unit/test_topics_relations.py` | T-TOPIC-001 | 主题重命名/重名冲突/删除级联成员/成员移除、关系删除涉及性校验、`topic_id` 只过滤来源分支、same-work 候选（同 artifact 哈希/规范化标题/已声明排除） |
