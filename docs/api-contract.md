# API 契约

基础 URL：`http://127.0.0.1:<port>/api/v1`。所有 JSON 使用 UTF-8，错误为 `{ "detail": "稳定错误码或中文说明" }`。OpenAPI 位于 `/openapi.json`。

| 区域 | 端点 | 方法 | 要点 |
|---|---|---|---|
| health | `/health`, `/capabilities` | GET | 运行状态、功能与本地限制 |
| settings | `/settings` | GET, PUT | 非敏感设置、端口和断路器 |
| imports | `/imports/file`, `/imports/paste` | POST | rights 必填；文件 multipart、文本 JSON |
| sources | `/sources`, `/sources/{id}`, `/sources/{id}/metadata`, `/sources/{id}/rights`, `/sources/{id}/relations` | GET/PUT/POST | 不返回本地原路径 |
| documents | `/documents/{version_id}/representations`, `/representations/{id}/evidence`, `/evidence/{id}`, `/citations`, `/knowledge`, `/knowledge/{id}/publish` | GET/POST | 保持证据链和发布校验 |
| search | `/search` | GET | `q` 和显式 advanced 过滤参数 |
| taxonomy | `/tags`, `/topics`, `/topics/{id}/sources` | GET/POST | 固定分类与主题关联 |
| external | `/external/cards`, `/external/douyin` | GET/POST | 仅元数据，绝不发起 URL 请求 |
| jobs | `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/retry`, `/jobs/run-once` | GET/POST | 轮询和协作控制 |
| lifecycle | `/sources/{id}/delete`, `/sources/{id}/restore`, `/sources/{id}/purge` | POST | 软删、恢复、永久删除 |
| transfer | `/backups`, `/backups/{id}/restore`, `/exports`, `/reimports`, `/verify` | GET/POST | 新根还原、确认导出、hash 验证 |

关键 DTO 定义由 `backend/app/domain/models.py` 中 Pydantic 模型和 FastAPI OpenAPI 生成，字段改动需同时更新本文件及 `docs/acceptance-matrix.md`（`REQ-043`）。
