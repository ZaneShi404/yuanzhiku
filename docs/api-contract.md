# API 契约

基础 URL：`http://127.0.0.1:<port>/api/v1`。所有 JSON 使用 UTF-8。错误统一为 `{ "detail": { "code": "stable_code", "message": "中文说明" } }`；`code` 是面向客户端的稳定分类，`message` 是可直接展示的说明。`POST /reimports` 的 `409` 在此基础上额外包含 `conflicts` 数组和 `reason`。OpenAPI 位于 `/openapi.json`。

| 区域 | 端点 | 方法 | 要点 |
|---|---|---|---|
| health | `/health`, `/capabilities` | GET | 运行状态、功能与本地限制 |
| settings | `/settings`, `/settings/download-cookies/{platform}` | GET, PUT, POST, DELETE | 非敏感设置、端口和断路器；按平台 cookies.txt 导入/删除（每平台 ≤1MB） |
| imports | `/imports/file`, `/imports/image`, `/imports/paste`, `/imports/prefill` | POST | rights 必填；文件/图片 multipart、文本 JSON；prefill 只读识别元数据，返回可空建议，不持久化、不联网 |
| videos | `/videos/local`, `/videos/link`, `/videos/link/probe`, `/videos/{source_id}`, `/videos/{source_id}/stream`, `/videos/{source_id}/frames/{frame_id}`, `/videos/{source_id}/transcribe`, `/videos/{source_id}/summarize` | GET/POST | 本地 MP4/WebM artifact 与受限白名单链接下载；链接元数据只读探测（REQ-047b）；本地播放、关键帧和受控未来 AI 作业 |
| sources | `/sources`, `/sources/{id}`, `/sources/{id}/metadata`, `/sources/{id}/rights`, `/sources/{id}/relations` | GET/PUT/POST | 不返回本地原路径 |
| documents | `/documents/{version_id}/representations`, `/representations/{id}/evidence`, `/evidence/{id}`, `/citations`, `/knowledge`, `/knowledge/{id}/publish` | GET/POST | 保持证据链和发布校验 |
| search | `/search` | GET | `q` 和显式 advanced 过滤参数 |
| taxonomy | `/tags`, `/topics`, `/topics/{id}/sources` | GET/POST | 固定分类与主题关联 |
| external | `/external/cards`, `/external/douyin` | GET/POST | 仅元数据，绝不发起 URL 请求；拒绝含用户名或密码的 URL |
| jobs | `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/retry`, `/jobs/run-once` | GET/POST | 轮询和协作控制 |
| lifecycle | `/sources/{id}/delete`, `/sources/{id}/restore`, `/sources/{id}/purge` | POST | 软删、恢复、永久删除 |
| transfer | `/backups`, `/backups/{id}/restore`, `/exports`, `/reimports`, `/verify` | GET/POST | 新根还原、PostgreSQL restore 另需空的 `target_database_url`、确认导出、hash 验证 |

`POST /reimports` 在同一主键或自然唯一键的逻辑记录不一致时返回 `409`，响应 `detail` 为 `{ "code": "reimport_conflict", "message": "导入逻辑记录冲突", "conflicts": [...], "reason": "..." }`；该检查发生在 artifact 写入前。外部卡 URL 含 userinfo 时返回 `422`，不会持久化或触发任何网络请求。

`POST /imports/prefill`（REQ-049）使用 multipart，`file` 与 `text` 字段二选一，返回 `{"title", "author", "language", "source_date"}`（均可为 `null`）。文件后缀白名单 `.pdf/.docx/.md/.markdown/.txt/.jpg/.jpeg/.png/.webp`（图片仅以文件名 stem 建议标题），文件超过 20MB → `413`，文本超过 1MB → `413`，不支持的后缀或两者都空 → `422`；损坏/加密文件返回全 `null` 建议。端点只读识别，不持久化、不触碰数据根、不发起网络请求。

关键 DTO 定义由 `backend/app/domain/models.py` 中 Pydantic 模型和 FastAPI OpenAPI 生成，字段改动需同时更新本文件及 `docs/acceptance-matrix.md`（`REQ-043`）。

## 本地视频

`POST /videos/local` 使用 multipart，字段与 `/imports/file` 相同：`file`、`rights`、可选 `title`、`author`、`language`、`notes`、`source_date`、JSON 数组格式的 `categories` 与 `tags`。仅接受文件名后缀为 `.mp4` 或 `.webm` 的本地文件；`rights` 必填。成功时响应 source、content version、artifact 和 `video_analyze` 作业。接口不接收 URL，也不会下载、探测或代理网页视频。

## 本地图片

`POST /imports/image`（REQ-048）使用 multipart，字段与 `/imports/file` 相同。仅接受文件名后缀为 `.jpg`/`.jpeg`/`.png`/`.webp` 的本地图片；`rights` 必填；`title` 缺省回退文件名 stem 或"未命名图片"。成功 `201` 响应 source、content version、artifact 和 `image_analyze` 作业（`source_type=file`，语义由 `media_type` 区分：image/jpeg、image/png、image/webp）。与文件导入同一 Content-Length 前置容量预检（无 Content-Length → `411`，超限 → `413`）。

`image_analyze` 作业只用 Pillow 本地读取尺寸、格式与常见 EXIF 字段（拍摄时间、Artist、ImageDescription，取不到即为空），不做 OCR、AI 描述或网络请求；损坏/无法解码图片 → 作业 `failed`，消息为通用脱敏文案。成功后写入 extraction representation（`parser_name="pillow-local"`，中文摘要文本含尺寸/格式/拍摄时间，可被 `/search` 命中）和一条 `image_metadata` locator 的 evidence：

```json
{"type": "image_metadata", "width": 1920, "height": 1080, "format": "JPEG", "datetime_original": "2024-01-15 10:30:00 或 null"}
```

`GET /sources/{id}/original` 对 `image/jpeg`、`image/png`、`image/webp` 的原件以 `inline` 内容处置和正确图片 Content-Type 返回（保留 `X-Content-Type-Options: nosniff` 与 sandbox CSP），供 `<img>` 直接预览；其余类型行为不变（PDF inline，文本 text/plain attachment，其他 octet-stream attachment）。

## 链接获取（受限下载通道）

`POST /videos/link` 使用 JSON 请求，仅接受白名单平台（哔哩哔哩、抖音）的 HTTPS 链接，成功 `201 {job}`（`job.kind == "video_download"`）；下载成功后自动创建 `source_type=video_link` 的来源并入队 `video_analyze`。本地导入路径不变——`/videos/local` 依旧不接收 URL。

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `url` | 字符串 | 是 | 1..4096 字符；HTTPS；主域或子域匹配 `bilibili.com`/`douyin.com` 白名单，或为 `b23.tv` 短链（显式放行、归属 bilibili，其重定向终点由回环代理限制在 bilibili 注册域）；无 userinfo（凭据）；无内网/回环主机；拒绝消息不含 URL 内容 |
| `platform` | `"bilibili" \| "douyin"` | 是 | 枚举 |
| `rights` | `owned \| authorized \| permitted \| open_license \| other` | 是 | 与本地导入一致（`REQ-011`） |
| `use_cookie` | 布尔 | 否（默认 false） | 使用该链接平台已导入的 Cookie 文件（自动按平台选用）；`true` 且该平台未导入 → `422`；仅用于该次下载 |
| `title` | 字符串 | 否 | ≤500 字符；缺省时下载成功后使用平台标题，退化为"未命名视频" |
| `author` | 字符串或 `null` | 否 | ≤300 字符 |
| `language` | 字符串 | 否 | ≤32 字符 |
| `notes` | 字符串或 `null` | 否 | ≤4000 字符 |
| `source_date` | ISO-8601 日期 | 否 | 与 `imported_at` 独立 |
| `categories` | 固定分类数组 | 否 | 只能取固定分类；空数组有效 |
| `tags` | 字符串数组 | 否 | 空数组有效 |

`POST /settings/download-cookies/{platform}` 使用 multipart 上传该平台的 cookies.txt（Netscape 格式，`platform` 限 `bilibili`/`douyin`，非法 → `422`），成功 `204`；单文件超过 1MB → `413`；同平台重复导入覆盖。文件按平台分别存于 `data/state/download/cookies/<platform>.txt`。`DELETE /settings/download-cookies/{platform}` 幂等删除该平台文件（不存在也返回 `204`）。

## 链接元数据探测（只读，REQ-047b）

`POST /videos/link/probe` 供链接获取表单的「识别链接」按钮回填元数据：JSON 请求仅含 `{url, platform, use_cookie}`（无 `rights`/`title` 等落库字段），URL 与平台校验规则及拒绝消息和 `/videos/link` 完全相同。后端经同一受限通道（请求级回环过滤代理 + 无 shell 子进程）以 yt-dlp `--skip-download` 只取元数据不下载，整体超时 30 秒。成功 `200 {"title": ..., "author": ..., "source_date": ...}`：三字段均可为 `null`，`author` 取平台 uploader（缺省回退 channel），`source_date` 为 ISO 日期（平台日期缺失或非法时为 `null`）。端点只读：不入队作业、不读写数据库、不持久化任何内容，代理随请求结束销毁。`use_cookie=true` 且未导入 cookies.txt → `422 cookie_file_unavailable`，绝不静默回退；downloader 不可用 → `503 downloader_unavailable`；探测失败（反爬、链接失效、平台拒绝、超时）→ `502 probe_failed` 通用脱敏消息（不含 URL 内容）。

错误码（稳定信封 `{ "detail": { "code": ..., "message": ... } }`）：

| HTTP | code | 场景 |
|---|---|---|
| `422` | `request_validation` | Pydantic 校验失败（含 rights 缺失/非法） |
| `422` | `invalid_url` | 非 HTTPS / 非白名单域 / 含凭据 / 超长 / 不支持的类型 |
| `422` | `unsupported_platform` | platform 不在白名单 |
| `422` | `cookie_file_unavailable` | `use_cookie=true` 且该平台 Cookie 文件未导入 |
| `413` | `cookie_file_too_large` | 平台 cookies.txt 超过 1MB |
| `502` | `probe_failed` | 链接元数据探测失败：链接失效、平台拒绝或探测超时（仅 `/videos/link/probe`） |
| `503` | `downloader_unavailable` | yt-dlp 或 FFmpeg 缺失、下载器未配置 |
| `404` | 沿用框架 | 资源不存在 |
| `500` | `internal_error` | 本地服务内部错误 |

`GET /capabilities` 新增 `downloader` 节：`{ "enabled", "adapter": "yt-dlp", "version", "supported_platforms": ["bilibili", "douyin"], "cookies": {"bilibili": ..., "douyin": ...}, "network": true }`。`enabled=false`（yt-dlp 或 FFmpeg 缺失）时前端禁用链接表单提交并显示引导，`POST /videos/link` 返回 `503`。`cookies[platform]`＝`data/state/download/cookies/<platform>.txt` 存在且 ≤1MB；探测失败一律按不可用处理。CORS `allow_methods` 含 `DELETE`，跨源 DELETE 预检放行。

`GET /videos/{source_id}` 默认返回最新本地视频版本；可选 `version_id` 只能选择同一来源的已存视频版本。`analysis` 在分析未完成时为 `null`。完成后 `analysis.metadata` 包含容器、时长毫秒、尺寸及音视频编码，`analysis.frames` 按 `ordinal` 返回关键帧 id、artifact hash、时间毫秒和可选尺寸。

`GET /videos/{source_id}/stream` 与 `GET /videos/{source_id}/frames/{frame_id}` 同样接受可选 `version_id`，并严格只从该版本的原 artifact 或分析帧读取内容。播放流只服务活动来源的 MP4/WebM artifact，支持单一 `Range: bytes=...` 请求；有效范围返回 `206` 和 `Content-Range`，无效或多范围请求返回 `416`。帧接口只服务该版本当前分析记录中的 JPEG 帧。两类响应均包含 `X-Content-Type-Options: nosniff` 和 sandbox CSP。

`POST /videos/{source_id}/transcribe` 与 `/summarize` 仅创建未来媒体 AI 作业。默认服务未配置，该作业最终为 `blocked`，消息为“未配置媒体 AI 服务”，不产生任何外部网络请求，也不改变已完成视频的状态。


`PUT /sources/{id}/metadata` 接受一个局部 JSON 对象。响应为更新后的来源详情；来源不存在时返回 `404`。请求中省略的字段保持既有值，不会被覆盖。

| 字段 | 类型 | 显式 `null` | 规则 |
|---|---|---|---|
| `title` | 非空字符串 | 拒绝（`422`） | 1 至 500 个字符 |
| `author` | 字符串或 `null` | 清除 | 最多 300 个字符 |
| `language` | 非空字符串 | 拒绝（`422`） | 最多 32 个字符 |
| `notes` | 字符串或 `null` | 清除 | 最多 4000 个字符 |
| `source_date` | ISO-8601 日期或 `null` | 清除 | 与 `imported_at` 独立 |
| `categories` | 固定分类数组 | 拒绝（`422`） | 只能取固定分类；空数组有效 |
| `tags` | 字符串数组 | 拒绝（`422`） | 空数组有效 |

`title`、`language`、`categories` 和 `tags` 不接受显式 `null`；客户端应提交有效值或省略字段。`author`、`notes` 与 `source_date` 使用 JSON `null` 表示清除。错误遵循本文件开头的稳定错误信封；Pydantic 请求校验失败为 `422`。
