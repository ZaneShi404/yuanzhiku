# Import Prefill, Link Probe And Image Import - 20260815T052125Z

## Scope And Status

This is a developer feature and self-test report only. It makes no independent-test or user-acceptance claim. Three related capabilities were delivered together:

- `REQ-049` import prefill: read-only metadata suggestions for the import forms
- `REQ-047b` link metadata probe: a read-only sub-capability of the restricted link-acquisition channel
- `REQ-048` image import: jpg/jpeg/png/webp sources with a lightweight `image_analyze` job

## What Was Built

### REQ-049 导入预填

- `backend/app/services/prefill.py`（新建，纯函数、无网络、无持久化）：PDF 经 pypdf `PdfReader.metadata` 取 title/author/CreationDate（畸形日期回退 `D:YYYYMMDD` 正则）；DOCX 经 `core_properties`；MD/TXT 取首个 `# ` 标题或首个非空行；语言启发（前 4000 字符，CJK 占比 ≥20% → zh，近纯拉丁 → en）；图片经 Pillow 读 EXIF Artist/DateTimeOriginal，标题回退文件名 stem；损坏文件返回全 null 不抛异常
- `backend/app/main.py`：`POST /api/v1/imports/prefill`（multipart `file` ≤20MB 或 `text` ≤1MB），返回 `{title, author, language, source_date}` 均可空；不注入 ApplicationServices，结构上零持久化；错误信封 `{code, message}` 不含文件内容
- 权利确认、固定分类、自由标签、备注不参与预填

### REQ-047b 链接元数据探测

- `backend/app/adapters/downloader.py`：新增 `probe_metadata(url, platform, use_cookie)`。抽出三个共用私有方法供下载与探测两条链路复用（`_scoped_proxy` 回环过滤代理生命周期、`_base_command` 基础命令、`_spawn` 无 shell 子进程），未复制第二套安全约束；yt-dlp `--skip-download --print "%()j"`，30s 整体超时，标准输出 2MB 上限；author 取 uploader 回退 channel；upload_date 校验归一化为 YYYY-MM-DD
- `backend/app/main.py`：`POST /api/v1/videos/link/probe`（仅 url/platform/use_cookie）；白名单拒绝 422（消息不含 URL）、cookie 未导入 422、工具不可用 503、探测失败/超时 502 脱敏；不入队、不写表、代理随请求销毁
- `backend/app/domain/models.py`：`LinkProbeRequest`；`backend/app/ports/media.py`：协议声明

### REQ-048 图片导入

- `backend/requirements.lock`：追加 `pillow==12.3.0`（REQ-046 锁定治理）
- `backend/app/services/imports.py`：`IMAGE_SUFFIXES`{.jpg/.jpeg/.png/.webp} + media type 映射 + `image()`（复用 `_persist_ingest`，`job_kind="image_analyze"`，source_type 与本地视频一致为 `"file"`，标题回退 stem）
- `backend/app/services/images.py`（新建）：Pillow `open→verify→load` 读尺寸/格式/EXIF（DateTimeOriginal 兼容顶层与 Exif IFD、Artist、ImageDescription）；解码前按 `宽×高×4` 内存护栏（沿用媒体内存断路器设置，未新增设置项）；产出中文可检索 representation（`pillow-local`）+ `image_metadata` locator 的 evidence；artifact SHA-256 校验后成功；损坏文件 failed 脱敏；无 OCR/AI/网络
- `backend/app/domain/media.py`：`IMAGE_METADATA_LOCATOR` + `image_metadata_locator` 工厂；零新表 → 备份/导出/检索自动覆盖
- `backend/app/services/jobs.py`：`run_once` 分发 `image_analyze` + `_image_analyze`（仿 `_video_analyze`）
- `backend/app/main.py`：`POST /api/v1/imports/image`（字段同 `/imports/file`）；容量预检中间件白名单加该路径；`GET /sources/{id}/original` 对三种图片 media_type 返回 inline（正确 Content-Type，保留 nosniff + sandbox CSP）

### 前端（frontend/src/App.tsx + styles.css）

- ImportPage 与 VideoWorkspace 加 touched 跟踪（`useRef<Set<string>>`）；预填/探测回填只写用户未编辑的字段，language 仅在为默认 'zh' 且未编辑时被覆盖；预填失败静默不阻断表单
- ImportPage 新增「图片」模式（accept jpg/jpeg/png/webp，提交 `/imports/image`）；本地文件/图片选择后即调 prefill；粘贴文本失焦后调 prefill
- VideoWorkspace 本地模式选择文件后预填文件名 stem；链接模式新增「识别链接」按钮（忙态禁用、错误透传后端脱敏 message、仅用户点击触发联网）
- SourceDetail 新增图片预览面板（`<img src="/sources/{id}/original">`，软删除时隐藏）
- `npm run lint` 零错误；`npm run build` 成功，dist 已重建由后端托管

## Commands Actually Run

| Command | Outcome |
| --- | --- |
| `pytest tests/unit/test_import_prefill.py -q` | 24 passed |
| `pytest tests/unit/test_link_probe.py -q` | 24 passed |
| `pytest tests/unit/test_video_download.py -q` | 67 passed（下载链路回归无损） |
| `pytest tests/unit/test_image_import.py -q` | 8 passed |
| `pytest tests/unit/test_defect_fixes.py tests/unit/test_api.py -q` | 34 passed |
| `pytest tests/unit -q`（全量单元回归） | 269 passed, 2 skipped in 1130.99s |
| `npm run lint` / `npm run build`（frontend/） | 零错误 / 构建成功 |
| 线上冒烟（重启实例后）：health、prefill 中文文本（title+zh）、prefill 带 EXIF JPEG（author+source_date）、probe 非白名单 URL → 422 脱敏 | 全部符合预期 |

## Docs Updated

- `docs/requirements.md`：REQ-047b、REQ-048、REQ-049
- `docs/api-contract.md`：`/imports/prefill`、`/videos/link/probe`、`/imports/image`、image_metadata locator、original 端点 image inline
- `docs/test-plan.md`：T-ING-003、T-VID-006、T-IMG-001
- `docs/acceptance-matrix.md`：REQ-047b/048/049 映射行

## Known Boundaries

- 真实平台（抖音/B站）探测联调属 T-VID-005 类手工验收，未在本次执行
- 图片无 OCR/AI 描述，检索只命中元数据 representation
- 预填永不覆盖用户已编辑字段；权利确认永远手动
- 应用实例已重启（新端点与新 dist 生效），重启前运行实例为旧代码
