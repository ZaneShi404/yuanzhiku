# 依赖安装

开发环境需要 Python 3.12+、Node 20+。后端在 `backend/requirements.lock` 锁定 Python 包；前端在 `frontend/package-lock.json` 锁定 npm 包（`REQ-046`）。执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.lock
Push-Location frontend; npm ci; npm run build; Pop-Location
```

Docling 是可选的首选解析能力。未验证许可、来源、哈希的模型不会下载；模型缓存路径是 `<data-root>\models`。`backend/models.lock.json` 是唯一允许自动模型来源的审核清单（`REQ-013`）。当前基础安装包含纯本地回退解析器 pypdf、python-docx，绝无云回退（`REQ-014`）。

容器镜像在 `docker-compose.yml` 中固定标签。Docker Desktop/WSL 不可用时可使用 Windows 脚本本地运行，不能将该状态误记为 Compose 验证通过（`REQ-045`）。
