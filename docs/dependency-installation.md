# 依赖安装

开发环境需要 Python 3.12+、Node 20+。后端在 `backend/requirements.lock` 锁定 Python 包；前端在 `frontend/package-lock.json` 锁定 npm 包（`REQ-046`）。执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.lock
Push-Location frontend; npm ci; npm run build; Pop-Location
```

Docling 是可选的首选解析能力，Python 包（MIT 许可证）以锁定版本写入 `backend/requirements.lock`（`docling==2.120.1`，2026-08-14 起随 venv 安装，用于实测 Docling 解析路径的页码 segments 行为）。未验证许可、来源、哈希的模型不会下载；模型缓存路径是 `<data-root>\models`。`backend/models.lock.json` 是唯一允许自动模型来源的审核清单（`REQ-013`）。锁条目 schema 为六个必需字符串字段：`name`、`version`、`source_url`、`license`、`cache_path`、`sha256`——缺一即视为未批准，Docling 保持不可用。**"按锁文件直接公开下载"的通道刻意未实现**：当前基线为空白名单（`models: []`），即零下载、零网络面；日后若批准模型，须先实现并独立审核合规下载通道，再把条目写入锁文件。当前基础安装包含纯本地回退解析器 pypdf、python-docx，绝无云回退（`REQ-014`）。

链接获取（受限下载通道）依赖锁定版本的 `yt-dlp`（Unlicense）与 `psutil`（MIT，内存断路器）——两者已写入 `backend/requirements.lock`（`yt-dlp==2026.8.19`、`psutil==7.2.2`），随 venv 一次性安装，无额外二进制。版本评估纪律：`REQ-046` 锁定后**绝不自动升级**，**唯一例外（用户预授权 2026-09-04）：`yt-dlp` 跟随上游日历版本升级**——平台反爬参数高频变化，yt-dlp 以日历版本高频修复对应抽取器（实证：2026-09-04 抖音 web detail 接口 403 导致下载全量失败，升级 2026.7.4→2026.8.19 后同一链接、同一代理、同一 Cookie 立即恢复）；每次升级须手动评估（变更日志、许可证、行为变化、出站域集合变化）并经下载链路回归 + 全量回归后才可更新 lock。下载故障诊断使用 `tools/diagnose_download.py`（四分类定位：链接/Cookie/代理拦截/版本过时，与下载作业走同一受限通道）。

可配置媒体 AI（`REQ-051`/`REQ-052`）依赖锁定版本的 `litellm==1.96.2`（MIT）——本次升级唯一新增 Python 依赖，已写入 `backend/requirements.lock`（`REQ-046`）；本环境经阿里云 PyPI 镜像安装，锁定之时镜像上更新的 1.98.0 仅为 dev 预发布，故钉最新稳定版。litellm 仅在用户显式配置 AI 分组后才被调用，默认关闭时零网络面；升级纪律与 yt-dlp 相同：绝不自动升级，候选版本须手动评估并经 T-AI-001 回归后才可更新 lock。

本地视频分析另需在系统 `PATH` 中显式安装兼容版本的 `ffmpeg` 与 `ffprobe`。可通过 `YUANZHIKU_FFMPEG_BIN`、`YUANZHIKU_FFPROBE_BIN` 指定二进制文件名或绝对路径。它们只由本机子进程调用，使用 `shell=False`，同时服务本地视频分析与链接下载的产物校验/本地合并；ffmpeg/ffprobe 自身不承担联网下载职责；链接下载由 yt-dlp 受限通道单独承担（仅作本地合并/remux 工具，绝不作为网络下载器）。任一工具缺失时，视频仍可作为不可变 artifact 导入，但 `video_analyze` 与链接下载会明确处于 `blocked`（`/capabilities` 的 `downloader.enabled=false`，`POST /videos/link` 返回 `503`）。

容器前端构建以 `frontend/package-lock.json` 执行 `npm ci --ignore-scripts`，`web` 镜像仅服务该构建产物；Compose 不挂载宿主 `frontend/dist`。Compose 由 `migrate` 构建共享的本地 application image，API/worker 复用该镜像；一次性 `migrate` 服务是唯一执行 Alembic upgrade 的组件，API/worker 仅接受已到 head 的 schema。当前基础镜像使用版本标签，但标签不是不可变 digest pin；在官方来源、许可与 digest 准入记录补齐前，不能把镜像供应链验证标记为完成。容器 API/worker 通过 `YUANZHIKU_DATABASE_URL` 选择固定版本的 SQLAlchemy/Alembic/psycopg PostgreSQL repository；连接、driver 或 schema 状态错误会明确失败，绝不静默回落 SQLite。Docker Desktop/WSL 不可用时可使用 Windows 脚本本地运行，不能将该状态误记为 Compose 验证通过（`REQ-045`）。


本地语音转写（`REQ-054`，v1.5）依赖锁定版本的 `funasr==1.4.2`（Apache-2.0，阿里达摩院语音框架）与 `modelscope==1.39.1`（Apache-2.0，模型下载），随附 `torch==2.9.1`/`torchaudio==2.9.1`（BSD-3 风格许可，CPU 推理），已写入 `backend/requirements.lock`（`REQ-046`）；升级纪律与 yt-dlp 相同：绝不自动升级，候选版本须手动评估并经 T-STT 回归后才可更新 lock。E1 实测结论（2026-08-16）：`funasr-onnx` 锁死 `numpy<=1.26.4`，与 Python 3.13 无轮子不兼容，故锁定 torch 完整版；`torch` 体积较大（约 2.5GB 安装），模型文件不随依赖分发。本地转写模型（paraformer-zh + VAD + 标点辅助模型，共约 1GB）经设置页「下载模型」按钮显式下载（ModelScope 公开源，`data/models/stt/`，锁文件 `backend/stt-models.lock.json` 固定模型包/来源/许可，SHA-256 于首次下载校验后自钉在 `manifest.state.json`）；**绝不静默自动下载**。本机代理工具（fake-IP/TUN 模式）可能干扰 ModelScope 下载——失败可重试，作业页可见失败消息；无可用本地模型且未配置转写 API 时转写作业 blocked。
