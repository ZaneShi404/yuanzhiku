# API 契约

基础 URL：`http://127.0.0.1:<port>/api/v1`。所有 JSON 使用 UTF-8。错误统一为 `{ "detail": { "code": "stable_code", "message": "中文说明" } }`；`code` 是面向客户端的稳定分类，`message` 是可直接展示的说明。`POST /reimports` 的 `409` 在此基础上额外包含 `conflicts` 数组和 `reason`。OpenAPI 位于 `/openapi.json`。

| 区域 | 端点 | 方法 | 要点 |
|---|---|---|---|
| health | `/health`, `/capabilities` | GET | 运行状态、功能与本地限制 |
| settings | `/settings`, `/settings/ai`, `/settings/ai/test`, `/settings/ai/stt-model`, `/settings/download-cookies/{platform}` | GET, PUT, POST, DELETE | 非敏感设置、端口和断路器；双组媒体 AI 配置与连通性检查（密钥仅存凭据文件，回显掩码）；本地转写模型下载/删除（REQ-054）；按平台 cookies.txt 导入/删除（每平台 ≤1MB） |
| imports | `/imports/file`, `/imports/image`, `/imports/paste`, `/imports/prefill` | POST | rights 必填；文件/图片 multipart、文本 JSON；分类字段为 `domains`/`genres`（`categories` 已移除）；prefill 只读识别元数据，返回可空建议，不持久化、不联网 |
| videos | `/videos/local`, `/videos/link`, `/videos/link/probe`, `/videos/{source_id}`, `/videos/{source_id}/stream`, `/videos/{source_id}/frames/{frame_id}`, `/videos/{source_id}/transcribe`, `/videos/{source_id}/summarize` | GET/POST | 本地 MP4/WebM artifact 与受限白名单链接下载；链接元数据只读探测（REQ-047b）；本地播放、关键帧；transcribe/summarize 入队媒体 AI 作业（未配置时终态 blocked，REQ-051） |
| sources | `/sources`, `/sources/{id}`, `/sources/{id}/metadata`, `/sources/{id}/rights`, `/sources/{id}/relations`, `/sources/{id}/relations/{relation_id}` | GET/PUT/POST/DELETE | 不返回本地原路径；来源详情含 `same_work_candidates`；关系可创建与删除 |
| documents | `/documents/{version_id}/representations`, `/representations/{id}/evidence`, `/evidence/{id}`, `/citations`, `/knowledge`, `/knowledge/{id}/publish` | GET/POST | 保持证据链和发布校验 |
| search | `/search` | GET | `q` 和显式 advanced 过滤参数：`domains`（重复、OR、`_none` 哨兵）、`genre`（单值、`_none`）、`topic_id` 等（REQ-024） |
| taxonomy | `/taxonomy`, `/tags`, `/topics`, `/topics/{id}`, `/topics/{id}/sources/{source_id}` | GET/POST/PUT/DELETE | 分类清单唯一下发；主题创建/重命名/删除、成员增删 |
| external | `/external/cards`, `/external/douyin` | GET/POST | 仅元数据，绝不发起 URL 请求；拒绝含用户名或密码的 URL |
| jobs | `/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel`, `/jobs/{id}/retry`, `/jobs/run-once` | GET/POST | 轮询和协作控制 |
| lifecycle | `/sources/{id}/delete`, `/sources/{id}/restore`, `/sources/{id}/purge` | POST | 软删、恢复、永久删除 |
| transfer | `/backups`, `/backups/{id}/restore`, `/exports`, `/reimports`, `/verify` | GET/POST | 新根还原、PostgreSQL restore 另需空的 `target_database_url`、确认导出、hash 验证 |

`POST /reimports` 在同一主键或自然唯一键的逻辑记录不一致时返回 `409`，响应 `detail` 为 `{ "code": "reimport_conflict", "message": "导入逻辑记录冲突", "conflicts": [...], "reason": "..." }`；该检查发生在 artifact 写入前。外部卡 URL 含 userinfo 时返回 `422`，不会持久化或触发任何网络请求。

`POST /imports/prefill`（REQ-049）使用 multipart，`file` 与 `text` 字段二选一，返回 `{"title", "author", "language", "source_date"}`（均可为 `null`）。文件后缀白名单 `.pdf/.docx/.md/.markdown/.txt/.jpg/.jpeg/.png/.webp`（图片仅以文件名 stem 建议标题），文件超过 20MB → `413`，文本超过 1MB → `413`，不支持的后缀或两者都空 → `422`；损坏/加密文件返回全 `null` 建议。端点只读识别，不持久化、不触碰数据根、不发起网络请求。

关键 DTO 定义由 `backend/app/domain/models.py` 中 Pydantic 模型和 FastAPI OpenAPI 生成，字段改动需同时更新本文件及 `docs/acceptance-matrix.md`（`REQ-043`）。

## 本地视频

`POST /videos/local` 使用 multipart，字段与 `/imports/file` 相同：`file`、`rights`、可选 `title`、`author`、`language`、`notes`、`source_date`、JSON 数组格式的 `domains`、`genres` 与 `tags`。仅接受文件名后缀为 `.mp4` 或 `.webm` 的本地文件；`rights` 必填。成功时响应 source、content version、artifact 和 `video_analyze` 作业。接口不接收 URL，也不会下载、探测或代理网页视频。

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
| `domains` | 领域数组 | 否 | 只能取 `/taxonomy` 领域清单；空数组有效 |
| `genres` | 体裁数组 | 否 | 只能取 `/taxonomy` 体裁清单，最多一项；空数组有效 |
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
| `503` | `credential_store_corrupt` | AI 凭据文件损坏（任何读取凭据的端点：`GET/PUT /settings/ai`、连通性测试、媒体 AI 作业等）；原文件保持不变，修复或删除后重试 |
| `403` | `untrusted_host` | 请求 Host 头非本机回环主机（127.0.0.1/localhost/::1；测试环境另有 `testserver`）——DNS rebinding 与非本机访问边界 |
| `403` | `untrusted_origin` | 写方法（POST/PUT/PATCH/DELETE）携带的本机以外或 `null` 的 Origin——跨站写入边界；GET 与未携带 Origin 的本地 CLI 请求不受影响 |
| `503` | `artifact_cleanup_pending` | `POST /sources/{id}/purge` 的逻辑删除已提交，但部分 artifact 文件物理清理未完成；清理任务持久保留，由启动重试与作业页重试继续消化 |
| `404` | 沿用框架 | 资源不存在 |
| `500` | `internal_error` | 本地服务内部错误 |

`GET /capabilities` 新增 `downloader` 节：`{ "enabled", "adapter": "yt-dlp", "version", "supported_platforms": ["bilibili", "douyin"], "cookies": {"bilibili": ..., "douyin": ...}, "network": true }`。`enabled=false`（yt-dlp 或 FFmpeg 缺失）时前端禁用链接表单提交并显示引导，`POST /videos/link` 返回 `503`。`cookies[platform]`＝`data/state/download/cookies/<platform>.txt` 存在且 ≤1MB；探测失败一律按不可用处理。CORS `allow_methods` 含 `DELETE`，跨源 DELETE 预检放行。

`GET /videos/{source_id}` 默认返回最新本地视频版本；可选 `version_id` 只能选择同一来源的已存视频版本。`analysis` 仅在该版本 `completeness=complete` 时返回当前分析，否则为 `null`；`analyses` 按时间列出该版本全部分析记录摘要并显式标记 `current_analysis_id`。`analysis.metadata` 包含容器、时长毫秒、尺寸、音视频编码与采样参数，`analysis.frames` 按 `ordinal` 返回关键帧 id、artifact hash、时间毫秒、真实像素宽高和采样来源 `reason`（`scene`/`even`）。

`POST /videos/{source_id}/transcribe`（`201`）入队 `video_transcribe` 作业：转写分组未启用或无 key 时作业终态 `blocked`（消息“未配置媒体 AI 服务”），不产生任何外部网络请求，也不改变已完成视频的状态。已配置时本地提取音频分块调用所配置转写端点，成功后写入 kind=`transcription` representation 与逐段 `video_time_range` evidence（进入检索索引）。

`POST /videos/{source_id}/summarize`（`201`）入队 `video_summarize` 作业，可选 JSON 请求体 `{"force_tier2": false}`。理解分组未配置时同样终态 `blocked`；无转写产物时终态 `failed`（“请先完成语音转写”，不进重试循环）。已配置时执行两级（用户裁定 2026-08-16）：完整性判断（确定性覆盖率/静音规则 + 约束 JSON 的 LLM 判定，阈值 0.6）→ tier1 纯文本摘要；判定疑似不完整/`force_tier2` 且视频直送（`ai_video_provider`）已配置时，视频文件直送多模态模型、由其直接产出补充转写/理解+摘要+建议分类；直送不可行/失败时摘要仍按 tier1 产出并标记 `visual_gap`。摘要写入 kind=`summary` representation（parent 为转写表示），AI 建议的领域/体裁/标签在作业成功时按只填空缺规则自动写入来源元数据（领域/体裁仅在当前为空时写入、标签并集合并、已填字段不覆盖；实际写入记 `ai_classify_applied` 审计事件，只含字段与数量），同时以 `<!--yuanzhiku:suggestions ...-->` 标记（含 `applied: true`）嵌入摘要文本；无 `applied` 的旧摘要仍可由用户显式采纳（`PUT /sources/{id}/metadata` 合并，幂等）。

`source_classify` 作业（REQ-051 修订）无独立入队端点：`ai_auto_pipeline` 开启且理解组已配置时，文档/粘贴的 parse 作业成功即自动入队（payload `{}`、priority 100；同版本同类作业已排队/运行中则不重复入队）；同一开关下 `video_analyze` 成功自动串联 `video_transcribe`，`video_transcribe` 成功自动串联 `video_summarize`。`source_classify` 取该版本最新 extraction 正文（截断至前 8000 字符）经理解组产出领域/体裁/标签，按同一只填空缺规则自动写入来源元数据；理解组未配置时终态 `blocked`（“未配置媒体 AI 服务”），无可分类正文时终态 `failed`（不进重试循环），失败/取消均不改变版本完整性与来源处理状态（REQ-033a）。图片不参与自动分类（无正文文本）。

## 分类体系（REQ-050）

`GET /taxonomy` 返回分类清单的唯一来源：`{"domains": [{"value", "label"}, ...], "genres": [{"value", "label"}, ...]}`（value 为稳定英文标识，label 为中文显示名）。领域多选、可空；体裁写入时最多一项、可空（超出 → `422`）。所有接受分类的接口（`/imports/file`、`/imports/image`、`/videos/local`、`/videos/link`、`PUT /sources/{id}/metadata`）统一使用 `domains`/`genres` 字段；旧 `categories` 字段已移除，数据库（schema v9）与 ≤v7 归档再导入按固定映射迁移。

## 媒体 AI 设置（REQ-051, REQ-052）

`GET /settings/ai` 返回双组配置视图：`transcribe`（`provider`/`base_url`/`model`/`has_key`/`key_hint`）、`understand`（`provider`/`base_url`/`chat_model`/`has_key`/`key_hint`）、`timeout_seconds` 与 `auto_pipeline`（自动流水线总开关，默认 `true`）。`provider` 取 `off` 或 `openai_compatible`；`key_hint` 为掩码提示（仅尾号），绝不回显完整密钥。

`PUT /settings/ai` 接受局部更新：省略的分组/字段保持不变；`api_key` 省略或空串不触碰既有凭据，非空则原子写入 `<data-root>/state/ai/credentials.json`。`auto_pipeline` 省略保持不变，为 `false` 时不再自动串联转写/摘要/分类作业（手动触发与摘要建议自动写入不受影响）。`base_url` 空串表示提供方默认端点，非空必须是 ≤2048 字符的 HTTPS 公网地址、无 userinfo，否则 `422 request_validation`；密钥绝不进入数据库、日志、备份、导出或任何 API 出参。两组均为 `off`（默认）时无任何出站流量，行为与未配置完全一致。

`POST /settings/ai/test` 请求体 `{"part": "transcribe" | "understand"}`，使用已保存配置做轻量连通性检查（转写分组 GET `/models`，理解分组一次最小 completion），返回 `{"ok": true}` 或 `{"ok": false, "message": "<脱敏中文原因>"}`；失败消息不含 URL、密钥或响应正文。

## 本地转写与视频直送（REQ-054, REQ-055，v1.5）

`GET /settings/ai` 扩展三节：`transcriber`（`engine`=auto|local|api、`local_stt_model`=paraformer-zh|paraformer-zh-quant、`stt_timeout_seconds`/`stt_memory_limit_mb`/`stt_disk_limit_mb`）、`local_stt`（模型状态：`model_name`/`model_configured`/`model_available`/`downloaded_at`/`revisions`）、`video`（`provider`=off|qwen|mimo、`model`、`max_bytes`（默认 314572800）、`reencode`、`chunk_seconds`、`qwen`/`mimo` 密钥掩码、`relay`（`base_url`/`has_secret`/`secret_hint`））。

`PUT /settings/ai` 新增局部分组：`transcriber`（引擎/模型/三个 stt 断路器）与 `video`（供应商/模型/上限/重编码开关/分块秒数/中转地址/中转形态 `relay_kind`=off|http|cos/COS 桶与地域 + `qwen_api_key`/`mimo_api_key`/`relay_secret`/`cos_secret_id`/`cos_secret_key` 凭据，同分组密钥纪律仅入凭据文件、掩码回显；`provider: "off"` 时移除 qwen/mimo 凭据，`relay_kind: "cos"` 时移除 http 中转密钥）。`relay_base_url` 非空必须是 HTTPS 公网地址、无 userinfo、≤2048 字符，否则 `422 request_validation`。凭据文件新增可选键 `video_qwen`/`video_mimo`/`video_relay`（同一原子写入纪律）。

`POST /settings/ai/stt-model` 请求体 `{"action": "download" | "delete"}`：`download` 入 `stt_model_download` 作业异步执行（返回 201 + `job_id`；已有排队/运行中下载时 `409 model_download_busy`；下载失败作业 failed 且消息脱敏，可重试）；`delete` 同步幂等（201），两者均写审计事件。

`/capabilities` 的 `media.ai` 扩展：`local_stt`（`enabled`/`engine`/`model`/`model_available`/`network:false`）与 `video_input`（按 `ai_video_provider` 回显所选适配器能力：`video_input`/`max_bytes`/`audio_in_video`/`duration_limits`/`reencode`/`relay_configured`）。

## 检索过滤（REQ-024）

`GET /search` 参数：`q`、`include_historical`、`include_incomplete`、`source_type`、`domains`（重复查询参数，OR 语义，`_none` 匹配未分类来源）、`genre`（单值，`_none` 匹配无体裁）、`tag`、`author`、`language`、`processing_state`、`source_date_from/to`、`imported_at_from/to`、`topic_id`（仅过滤来源分支，知识与外部卡不受影响）、`sort`（`relevance`/`updated`/`title`）。`domains`/`genre` 取值超出清单（`_none` 除外）→ `422 request_validation`。全文语料只含标题/作者/备注与正文类 representation 文本：分类与标签 token 不进入全文，`ffmpeg-local` 视频元数据模板不进入全文。

**破坏性变更**：旧 `category` 过滤参数与导入/元数据接口的 `categories` 字段已移除；API 消费方须改用 `domains`（重复参数）与 `genre`。

## 主题与来源关系

`POST /topics`（`201`）创建主题（名称唯一，重复 → `409`）；`PUT /topics/{topic_id}` 重命名（不存在 → `404`，重名 → `409`）；`DELETE /topics/{topic_id}`（`204`）删除主题及其全部成员关联；`POST /topics/{topic_id}/sources/{source_id}` 添加成员；`DELETE /topics/{topic_id}/sources/{source_id}`（`204`）移除成员（关联不存在 → `404`）。

`POST /sources/{id}/relations`（`201`）创建关系（`new_version_of`/`revision_of`/`related_to`/`user_declared_same_work`，重复或无效 → `409`）；`DELETE /sources/{id}/relations/{relation_id}`（`204`）删除关系，关系不涉及该来源 → `404`。`GET /sources/{id}` 详情含 `same_work_candidates`：按相同 artifact 哈希（`same_artifact`）或规范化标题（`same_title`）计算的确定性候选，已声明 same-work 的来源不再出现；候选可一键创建 `user_declared_same_work` 关系。

`GET /videos/{source_id}/stream` 与 `GET /videos/{source_id}/frames/{frame_id}` 同样接受可选 `version_id`，并严格只从该版本的原 artifact 或分析帧读取内容。播放流只服务活动来源的 MP4/WebM artifact，支持单一 `Range: bytes=...` 请求；有效范围返回 `206` 和 `Content-Range`，无效或多范围请求返回 `416`。帧接口只服务该版本当前分析记录中的 JPEG 帧。两类响应均包含 `X-Content-Type-Options: nosniff` 和 sandbox CSP。

`PUT /sources/{id}/metadata` 接受一个局部 JSON 对象。响应为更新后的来源详情；来源不存在时返回 `404`。请求中省略的字段保持既有值，不会被覆盖。

| 字段 | 类型 | 显式 `null` | 规则 |
|---|---|---|---|
| `title` | 非空字符串 | 拒绝（`422`） | 1 至 500 个字符 |
| `author` | 字符串或 `null` | 清除 | 最多 300 个字符 |
| `language` | 非空字符串 | 拒绝（`422`） | 最多 32 个字符 |
| `notes` | 字符串或 `null` | 清除 | 最多 4000 个字符 |
| `source_date` | ISO-8601 日期或 `null` | 清除 | 与 `imported_at` 独立 |
| `domains` | 领域数组 | 拒绝（`422`） | 只能取 `/taxonomy` 领域清单；空数组有效 |
| `genres` | 体裁数组 | 拒绝（`422`） | 只能取 `/taxonomy` 体裁清单，最多一项；空数组有效 |
| `tags` | 字符串数组 | 拒绝（`422`） | 空数组有效 |

`title`、`language`、`domains`、`genres` 和 `tags` 不接受显式 `null`；客户端应提交有效值或省略字段。`author`、`notes` 与 `source_date` 使用 JSON `null` 表示清除。错误遵循本文件开头的稳定错误信封；Pydantic 请求校验失败为 `422`。
