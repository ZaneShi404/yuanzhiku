# Per-Platform Cookie Library - 20260815T071523Z

## Scope And Status

This is a developer feature and self-test report only. It makes no independent-test or user-acceptance claim.

User request: replace the single-channel cookies.txt (REQ-047a) with a per-platform cookie library, so that once a link's platform is recognized, downloads and probes automatically use that platform's cookie file without manual switching.

## What Was Built

### Backend

- `backend/app/core/config.py`：`DataPaths.download_cookies`（`state/download/cookies/`）+ `download_cookie_file(platform)` 单一路径入口（合法平台 = `DOWNLOAD_REGISTRY` 键，延迟导入避免循环依赖；非法平台 ValueError）
- `backend/app/adapters/downloader.py`：`YtDlpDownloader` 构造参数 `cookie_file_path` → `cookie_resolver`；`_cookie_file_available(platform)`；`capability()` 的 `cookie_file_available` 字段 → `cookies: {platform: bool}` 映射；`probe_metadata` 按平台经 resolver 取件，未导入仍 `DownloadInputInvalid("cookie")` 不静默回退
- `backend/app/ports/media.py`：协议声明同步
- `backend/app/services/jobs.py`：video_download 的 staging 拷贝源改为 `paths.download_cookie_file(platform)`，拷贝即清纪律不变
- `backend/app/main.py`：
  - 新端点 `POST/DELETE /api/v1/settings/download-cookies/{platform}`（每平台 1MB、原子写、删除幂等、非法平台 422）；旧 `/settings/download-cookie` 端点移除；Content-Length 预检中间件改前缀匹配
  - `/videos/link` 与 `/videos/link/probe` 预检按请求 platform 检查
  - 启动迁移 `_migrate_legacy_download_cookie`：遗留 `cookies.txt` 逐行按 Netscape 格式解析，经注册域标签边界匹配分拣到 `cookies/bilibili.txt`/`cookies/douyin.txt`（保留注释头、同行进一个文件、无关域不进任何文件、内容绝不打印落日志），分拣完成才删旧文件；失败不阻断启动并记操作日志事件

### Frontend（`frontend/src/App.tsx`）

- `DownloaderCapability` 类型与 `/capabilities` 消费改为 `cookies: Record<string, boolean>`
- 设置页 Cookie 区重构为哔哩哔哩/抖音两行：各自已导入状态 + 导入（Netscape ≤1MB）+ 删除（confirm 二次确认），接新端点，操作后重新拉取 capabilities
- 统一导入页链接模式：Cookie 开关按识别出的平台显示「使用已导入的{平台名} Cookie」，按该平台导入状态启用/禁用引导

## Commands Actually Run

| Command | Outcome |
| --- | --- |
| `pytest tests/unit/test_video_download.py tests/unit/test_link_probe.py tests/unit/test_api.py -q` | 114 passed in 532s |
| `npm run lint` / `npm run build`（frontend/） | 零错误 / 构建成功（`index-ROH1y9FS.js`） |
| 线上迁移验证（重启运行实例后） | 遗留 bilibili cookies.txt 25 条完整分拣至 `cookies/bilibili.txt`（SESSDATA/bili_jct 在），旧文件删除；`/capabilities` 上报 `cookies: {bilibili: true, douyin: false}` |

## Docs Updated

- `docs/requirements.md`：REQ-047a 修订为按平台 Cookie 库（五条安全不变量保留并声明）；REQ-043 端点清单、REQ-044 Cookie 开关措辞同步
- `docs/v1-2-requirements.md`：§4.4 现行文本更新 + 2026-08-15 变更日志条目
- `docs/api-contract.md`：settings 端点表、use_cookie 字段、错误码表、capabilities 契约
- `docs/test-plan.md`：T-API-001、T-VID-003（Cookie 治理行）、T-UI-001
- `docs/operations-and-recovery.md`、`docs/threat-model.md`：Cookie 存储与不变量描述同步

## Invariants Preserved（逐条核验）

- Cookie 内容绝不进入数据库、日志正文、API 响应、备份、导出、reimport、审计事件
- 使用 Cookie 的作业仍 staging 拷贝注入、结束即清；运行期间不触碰原文件
- `use_cookie=true` 且该平台未导入 → 422；绝不静默回退、绝不跨平台借用
- 每文件 1MB 上限；删除幂等；浏览器 Cookie 库直读通道不存在

## Known Boundaries

- Cookie 使用仍需用户每次显式勾选（知情同意语义保留），变化的只是文件按平台自动选定
- 用户此前的抖音 Cookie 已在上一轮被 bilibili 覆盖而丢失（单文件时代）；本次迁移只保全了现存 bilibili 凭据。抖音下载前需在设置页重新导入抖音 cookies.txt
