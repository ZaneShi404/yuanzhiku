# 开发报告

- 日期：2026-07-28。
- 角色边界：本报告仅记录独立开发实现与开发自测，不包含或推断后续独立测试、验收的结论。

## 已实现

- 后端：`backend/app/main.py` 组合 FastAPI `/api/v1`、OpenAPI、仅 loopback 的启动配置、每日启动备份入队与内嵌单 worker；`backend/app/adapters/sqlite.py` 提供 SQLite schema/migration 和 durable jobs；`backend/app/adapters/storage.py` 提供流式 SHA-256 内容寻址 artifact 仓库（`REQ-001..004`, `REQ-010..014`, `REQ-032..034`, `REQ-043`）。
- 证据与检索：`backend/app/services/documents.py` 追加不可变 representation/evidence/citation，含 locator、parser config hash 和 excerpt hash；`search_chunks` 是独立派生索引表，不能作为 evidence。`backend/app/services/search.py` 实现短语/关键词/子串检索及默认完整当前版本（`REQ-020..025`）。
- 外部卡：`backend/app/services/external_cards.py` 不导入 HTTP 客户端；抖音 URL 仅验证 HTTPS `douyin.com`/子域并字面存储（`REQ-030..031`）。`rg` 审计命令未发现后端 HTTP client 调用，见“开发自测”。
- 生命周期与传输：`services/lifecycle.py` 实现软删、恢复、受引用保护的 artifact purge；`services/transfers.py` 实现 hash manifest 备份/导出、仅新根还原、哈希校验、冲突拒绝的只追加 reimport、30 个每日备份保留（`REQ-034`, `REQ-040..042`）。
- UI：`frontend/src/App.tsx` 和 `styles.css` 交付中文资料库、导入、来源文本/证据/人工修订、检索、作业 REST 轮询、外部卡、备份/还原/导出、设置；`frontend/dist` 是构建产物，由 FastAPI 静态交付（`REQ-001`, `REQ-022..024`, `REQ-044`）。
- 交付：`scripts/start-windows.ps1` 选择/持久化端口，启动 `127.0.0.1` Uvicorn 并打开浏览器；`docker-compose.yml` 含 web/api/worker/postgres/redis，宿主端口均显式 loopback，数据卷变量要求 `tests/runtime/compose-<run-id>`。`backend/migrations/postgresql/001_initial.sql` 提供 PostgreSQL 初始迁移边界（`REQ-002`, `REQ-003`, `REQ-045`）。
- 文档：`docs/requirements.md`、`api-contract.md`、`test-plan.md`、`acceptance-matrix.md`、`architecture.md`、`dependency-installation.md`、`operations-and-recovery.md`、`threat-model.md` 和两个 ADR 已建立（`REQ-046`）。

## 开发自测证据

| 命令/标识 | 结果 | 覆盖 |
|---|---|---|
| `PYTHONPATH=E:/源知库/backend .venv/Scripts/python.exe -m pytest -q tests/unit` | `6 passed in 37.70s` | T-API-001、T-ING-001/002、T-JOB-001、T-KNOW-001、T-EXT-001、T-LIFE-001、T-BACK-001；含人工修订与幂等 reimport。 |
| `.venv/Scripts/python.exe -m compileall -q backend/app` | 成功（与最终 pytest 命令串联） | Python 语法/导入。 |
| `cd frontend; npm run lint && npm run build` | 成功；Vite 产物 `index-CnUdVWbG.js` 175.17 kB | TypeScript 和生产 UI 构建。 |
| `http://127.0.0.1:8876/api/v1/health` | 返回 `status: ok`、测试数据根与 `127.0.0.1 only` | 真实 loopback 应用烟测。 |
| 真实 HTTP `POST /imports/paste` 后 `GET /sources`、`GET /jobs` | source 状态为 `succeeded`，parse job `succeeded`；每日 backup job 也 `succeeded` | 导入、worker、备份实际运行。 |
| OpenAPI 脚本 | `OpenAPI required paths: 16` | `REQ-043` 核心区域路由存在。 |
| `rg -n '(requests\\.|httpx\\.|urllib\\.request|aiohttp|curl|axios|fetch\\()' backend/app` | 无匹配 | 后端没有外部 HTTP client 调用；前端 `fetch` 仅调用 `/api/v1`。 |
| `git diff --check` | 成功 | 无空白错误。 |

## 环境行动与限制

- 已创建本地 `.venv` 并从 `backend/requirements.lock` 安装锁定 Python 包；首次 PyPI 读取超时后，使用 180 秒读取超时重试成功。已从 `frontend/package-lock.json` 执行 `npm ci`。
- `npm` 审计报告 2 个上游问题（1 moderate、1 high）。未运行会自动升级锁定依赖的 `npm audit fix`；独立测试应评估升级路径与许可/兼容性（`REQ-046`）。
- `docker compose` 未能运行：本机 `docker: command not found`。仅以 Python YAML 解析验证了 `docker-compose.yml`，没有声称 Compose 集成测试完成（T-COMP-001 未执行）。
- 浏览器自动化在当前独立代理运行时返回“Browser is not available in subagent”。已通过生产静态 UI HTTP 首页检查 `<title>源知库`，但没有 GUI 截图或黑盒交互结论；T-UI-001 留给后续独立测试。
- 开发 smoke 服务已停止；测试 runtime 已清空。未创建或修改 `E:\源知库\data`。

## 已知开发限制

- 默认交付不安装 Docling 或任何模型，`backend/models.lock.json` 的预批准模型列表为空；当前实际解析会明确记录本地 `pypdf`/`python-docx`/native UTF-8 fallback。后续独立测试应验证有审核模型锁时的 Docling 路径（`REQ-013`）。
- PostgreSQL 提供迁移和 adapter 边界，实际本地默认运行时为 SQLite；容器化 PostgreSQL repository 的完整切换待容器环境验证（`REQ-045`）。
- PDF iframe 是 sandboxed、只读浏览器预览；对不同 Edge/Chrome 版本的嵌入链接行为应由独立 GUI 测试确认（`REQ-044`）。
