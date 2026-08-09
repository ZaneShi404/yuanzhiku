# 依赖安装

开发环境需要 Python 3.12+、Node 20+。后端在 `backend/requirements.lock` 锁定 Python 包；前端在 `frontend/package-lock.json` 锁定 npm 包（`REQ-046`）。执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.lock
Push-Location frontend; npm ci; npm run build; Pop-Location
```

Docling 是可选的首选解析能力。未验证许可、来源、哈希的模型不会下载；模型缓存路径是 `<data-root>\models`。`backend/models.lock.json` 是唯一允许自动模型来源的审核清单（`REQ-013`）。当前基础安装包含纯本地回退解析器 pypdf、python-docx，绝无云回退（`REQ-014`）。

本地视频分析另需在系统 `PATH` 中显式安装兼容版本的 `ffmpeg` 与 `ffprobe`。可通过 `YUANZHIKU_FFMPEG_BIN`、`YUANZHIKU_FFPROBE_BIN` 指定二进制文件名或绝对路径。它们只由本机子进程调用，使用 `shell=False`，不承担下载、联网、网页解析或访问任意 URL 的职责。任一工具缺失时，视频仍可作为不可变 artifact 导入，但 `video_analyze` 会明确处于 `blocked`。

容器前端构建以 `frontend/package-lock.json` 执行 `npm ci --ignore-scripts`，`web` 镜像仅服务该构建产物；Compose 不挂载宿主 `frontend/dist`。Compose 由 `migrate` 构建共享的本地 application image，API/worker 复用该镜像；一次性 `migrate` 服务是唯一执行 Alembic upgrade 的组件，API/worker 仅接受已到 head 的 schema。当前基础镜像使用版本标签，但标签不是不可变 digest pin；在官方来源、许可与 digest 准入记录补齐前，不能把镜像供应链验证标记为完成。容器 API/worker 通过 `YUANZHIKU_DATABASE_URL` 选择固定版本的 SQLAlchemy/Alembic/psycopg PostgreSQL repository；连接、driver 或 schema 状态错误会明确失败，绝不静默回落 SQLite。Docker Desktop/WSL 不可用时可使用 Windows 脚本本地运行，不能将该状态误记为 Compose 验证通过（`REQ-045`）。
