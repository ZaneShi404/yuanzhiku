# 链接获取（Link Acquisition）v1.2 需求草案

- 状态：DRAFT（未冻结；冻结前不改动任何代码）
- 日期：2026-08-12（UTC `20260812T222057Z`）
- 范围：把 v1.1 视频工作区的"链接获取"预留位升级为受限下载通道
- 决策依据（2026-08-13 已拍板）：平台范围＝抖音（国内版）+ 哔哩哔哩；认证边界＝允许浏览器 Cookie，**cookies.txt 显式导入与 `--cookies-from-browser` 双通道支持**；接入形态＝yt-dlp Python 依赖；画质＝≤1080p remux MP4；多P＝仅单视频；source_type＝新增 `video_link`；失败提示＝通用脱敏；合规边界＝仅个人本地使用、不绕过 DRM/会员

## 1. 目标

v1.1 的"链接获取"是不可提交的静态占位（`frontend/src/App.tsx:976`）。本需求将其升级为可提交的受限下载通道：用户显式提交白名单平台（哔哩哔哩、抖音国内版）的视频链接，经 yt-dlp 下载为本地 MP4 artifact，随后自动进入与本地导入完全相同的证据链、`video_analyze` 分析、播放、备份、导出、再导入与永久清理生命周期。

下载坚持现有安全纪律：显式用户操作、平台白名单、无 shell 子进程、总超时/无进展/内存/磁盘断路器、可协作取消、权利声明必填、内容寻址不可变、Cookie 与凭据零持久化。

## 2. 范围与非目标

范围：

- 平台白名单：`bilibili.com`（BV/av 视频链接及 b23.tv 短链）、`douyin.com` 及其子域（抖音国内版分享短链/视频链接）。
- 单视频下载：单个链接对应单个视频；下载产物由现有 FFmpeg 依赖 remux/合并为 MP4（如为 webm 可原样保留），受现有 2GB 上限与容量预检约束。
- 浏览器 Cookie：双通道——用户显式导入 cookies.txt（Netscape 格式，主通道）或授权 yt-dlp 在下载作业内临时读取本机 Edge/Chrome Cookie 库（`--cookies-from-browser`，备选通道）；提交下载时显式选择通道，仅用于该次下载。
- 下载完成后自动入队 `video_analyze`（与本地导入同路径）。

非目标（本版明确不做）：

- YouTube 等海外站点、小红书/快手等其他国内平台。
- 多P/合集批量、番剧、直播、订阅、定时抓取、字幕/弹幕/评论提取。
- 登录凭据或密码的保存；任何云服务、代理、静默回退。
- 绕过会员、DRM、付费墙或平台限速。

## 3. 选型记录

| 工具 | 结论 | 理由 |
|---|---|---|
| yt-dlp | ✅ 采纳 | 与后端同栈（pip 装入现有 venv，`requirements.lock` 锁定）；Unlicense 无协议污染；B站成熟、抖音支持但受反爬影响；可完全按现有 `LocalFfmpegMediaAnalyzer` 的无 shell 子进程 + 断路器模式封装；站点覆盖未来可扩展 |
| lux | 备选 | 单文件二进制可行，但引入外部二进制随附、版本管理与更新机制，与"依赖全部锁定进 venv"纪律冲突；抖音支持弱于 yt-dlp |
| mediago | ❌ 排除 | 桌面 GUI，无法作为后端组件集成 |
| BBDown | ❌ 排除 | 仓库已归档停更、GPLv3、仅 B站 |

## 4. 需求修订草案

### 4.1 REQ-015 修订

> 原文：视频第一版仅支持本地 MP4/WebM（……）；不保存原始本地完整路径。视频通过本机显式安装的 FFmpeg/ffprobe 探测元数据并在独立 staging 中有限时间采样 JPEG 关键帧，禁止 shell、网络、URL 获取和静默云回退。

修订为：视频支持本地 MP4/WebM 导入与受限链接获取（`REQ-047`）。链接获取仅接受白名单平台、仅由用户显式提交，下载经无 shell 受限子进程完成并进入同一 artifact/分析/证据链生命周期；本地导入与视频分析仍禁止 shell、网络、URL 获取和静默云回退。

### 4.2 REQ-030 / REQ-031 修订

- `REQ-030` 不变：外部卡仍仅字面保存 URL 元数据，不抓取、不解析、不请求，不是事实证据。
- `REQ-031` 修订：抖音绝对禁止下载、抓取、内容提取、iframe、cookie/密码/认证、自动化、缓存、代理、逆向和伪造时间参数；任何 HTTP client/worker/parser 均不可接受抖音 URL。**唯一例外是 `REQ-047` 定义的受限链接获取下载通道**——它只由用户显式提交、只服务白名单域、只在独立 staging 内工作；外部卡、文档解析器、检索、文档导入、媒体 AI 端口对该例外一无所知。

### 4.3 新增 REQ-047（链接获取）

1. 平台白名单 `bilibili`、`douyin`；URL 严格校验：HTTPS、主域或子域匹配白名单、无内嵌凭据、长度上限；未知或不支持的 URL 拒绝，拒绝消息不含 URL 内容。
2. 下载器为锁定版本的 yt-dlp（`REQ-046`，写入 `requirements.lock`），仅以无 shell 子进程运行（`shell=False`）、stdin 关闭、忽略用户级配置文件与缓存；仅出站连接到白名单平台域；无代理、无静默重定向跟随外平台。
3. 下载仅写入独立 per-job staging：总超时、无进展（静默期）、内存、磁盘断路器全部沿用 `REQ-033a` 纪律并支持协作取消；下载产物经 ffprobe 校验为合法 MP4/WebM、时长与尺寸合法、≤2GB 且通过容量预检后，流式写入不可变 SHA-256 artifact。
4. 提交链接时权利声明（owned/authorized/permitted/open_license/other）必填，与本地导入一致（`REQ-011`）。
5. 下载元数据（平台、链接原文、是否使用 Cookie、yt-dlp 版本、所选格式）以脱敏形式进入审计与导出 manifest（`REQ-042`）；Cookie 内容、原始请求头、下载响应体绝不进入数据库、日志、API 响应、备份或导出。
6. 新增作业 kind `video_download`：持久租约、心跳、取消、优先级与有限重试；成功后在作业内创建 source/content version/artifact 并自动入队 `video_analyze`；分析失败、取消或 blocked 不降低已完成下载（对齐 `REQ-033a` 精神）。任何失败路径不残留半成品 source；staging 与 Cookie 拷贝作业结束即清理。
7. 仅单视频；多P/合集/直播/需登录才可见的内容按失败处理，失败消息脱敏。
8. 下载作业在 yt-dlp 或 FFmpeg 缺失时明确 blocked；因反爬、链接失效、平台拒绝等外部原因失败时状态 failed 且可有限重试，绝不静默切换来源或平台。

### 4.4 新增 REQ-047a（Cookie 生命周期）

1. **cookies.txt 通道（主）**：用户可显式导入浏览器导出的 cookies.txt（Netscape 格式），大小上限 1MB，仅存于 `data/state/download/cookies.txt`；重复导入覆盖旧文件；支持一键删除（幂等）。
2. **浏览器直读通道（备选）**：仅当用户提交下载并显式选择"从本机浏览器读取"时，yt-dlp 才在**该作业进程内**以 `--cookies-from-browser <edge|chrome>` 临时读取本机浏览器 Cookie 库；浏览器类型仅限 Edge/Chrome 白名单，且仅支持浏览器完全关闭或可解锁的环境。读取失败按下载失败处理（通用脱敏提示），绝不静默回退到另一通道或另一浏览器。
3. 无论哪个通道，Cookie 内容绝不进入数据库、日志正文、API 响应、备份（`REQ-040`）、导出 ZIP 与 reimport（`REQ-041`）、审计事件；备份/导出/再导入规则显式排除 `data/state/download` 路径。
4. cookies.txt 通道在使用时把 Cookie 文件**拷贝**注入该作业 staging，作业结束（无论成败）立即删除拷贝；浏览器直读通道不产生任何 Cookie 落盘副本。
5. 能力接口暴露 `cookie_file_available` 与 `cookie_browser_available` 状态；前端在两条通道均不可用时给出引导。

### 4.5 REQ-043 修订

`/api/v1` 端点列表增加：`videos/link`（POST 创建下载作业）、`settings/download-cookie`（POST 导入、DELETE 删除）；`/capabilities` 增加 `downloader` 节。其余不变。

### 4.6 REQ-044 修订

视频页"链接获取"从不可提交预留改为可提交表单：平台选择（哔哩哔哩/抖音）、URL 输入、权利声明必选、Cookie 状态提示与开关、提交后跳转作业页；不做预览、嗅探或解析展示。

## 5. 威胁模型修订（新增行）

| 风险 | 控制 | 需求 |
|---|---|---|
| 下载器被恶意/伪造 URL 利用（SSRF、外平台跳转） | 平台白名单 + 严格 URL 校验；yt-dlp 无 shell 子进程、忽略用户配置；仅出站白名单域；断路器限制资源 | REQ-047 |
| Cookie 泄露或进入分发物 | cookies.txt 仅存 `data/state/download`、逐作业拷贝即删；浏览器直读仅在作业进程内、不落盘；任何通道都不进 DB/日志/备份/导出/reimport；UI 显式删除 | REQ-047a, REQ-040..042 |
| 平台反爬、账号风控 | 单 worker、无并发批量、无自动化调度、有限重试；Cookie 由用户自愿提供（导入文件或显式授权作业内读取浏览器）且可随时删除 | REQ-047, REQ-047a |
| 下载内容不可信（伪造/损坏/超限） | 复用 REQ-015/016 全部断路器与 ffprobe/hash 校验；失败保留原状 | REQ-047, REQ-016 |
| 平台条款与版权 | 用户权利声明必填；仅个人本地使用；外部卡仍不构成证据；不绕过 DRM/会员 | REQ-011, REQ-030, REQ-047 |

## 6. 接口设计

### 6.1 新端口 `MediaDownloaderPort`（`backend/app/ports/media.py` 扩展）

```
class MediaDownloaderPort(Protocol):
    def capability(self) -> dict   # {"enabled", "adapter": "yt-dlp", "version", "supported_platforms",
                                   #  "cookie_file_available", "cookie_browser_available", "network": True}
    def config_hash(self, platform: str, format_profile: str) -> str
    def download(self, *, url, platform, workspace, limits,
                 cookie_source: "none" | "file" | "browser", cookie_path: Path | None,
                 cookie_browser: "edge" | "chrome" | None,
                 cancelled, heartbeat, progress) -> DownloadedVideo
```

- `DownloadedVideo`：`filename`、`media_type`（video/mp4 | video/webm）、`byte_size`。
- 异常：`DownloadUnavailable`（工具缺失→blocked）、`DownloadInputInvalid`（URL/平台/反爬/Cookie 读取失败/超限→failed）、`DownloadProcessingCancelled`（→cancelled）。

### 6.2 新适配器 `YtDlpDownloader`（`backend/app/adapters/downloader.py` 新文件）

- 子进程 `sys.executable -m yt_dlp`，`shell=False`、`stdin=DEVNULL`，stderr 不落日志。
- 骨架参数：`--no-playlist --no-simulate --ignore-config --no-cache-dir --retries 1 --socket-timeout 30 --merge-output-format mp4 --remux-video mp4 -f "bv*[height<=1080]+ba/b" -o <staging>/video.%(ext)s <url>`；`cookie_source=file` 时追加 `--cookies <staging-copy>`，`cookie_source=browser` 时追加 `--cookies-from-browser <edge|chrome>`。
- 断路器复用 `media.py` 现有 `_run` 模式：总超时、内存 RSS、staging 磁盘上限、输出静默期心跳；下载中检查输出文件大小，超过 2GB 立即终止并判失败。
- 产物校验：复用 `LocalFfmpegMediaAnalyzer.probe` 验证容器/时长/尺寸，再 `store_stream` 写入 artifact（与上传路径同一入口）。
- 依赖：`requirements.lock` 增加 `yt-dlp==<撰写时锁定版本>`；ffprobe/ffmpeg 复用现有 `YUANZHIKU_FFPROBE_BIN`/`YUANZHIKU_FFMPEG_BIN` 环境发现。

### 6.3 API（`backend/app/main.py`）

- `POST /api/v1/videos/link`：JSON `{url, platform: "bilibili"|"douyin", rights, cookie_source: "none"|"file"|"browser", cookie_browser: "edge"|"chrome"|null, title?, author?, language?, notes?, source_date?, categories[], tags[]}` → `201 {job}`（`job.kind == "video_download"`，`create_job` 已支持 source/version/artifact 为空）。URL 校验失败/平台不支持 → 422；下载器不可用 → 503；`cookie_source` 与可用状态不匹配（如未导入文件却选 file）→ 422。
- `POST /api/v1/settings/download-cookie`：multipart cookies.txt → 204；超过 1MB → 413。
- `DELETE /api/v1/settings/download-cookie` → 204（幂等）。
- `GET /api/v1/capabilities` 增加 `"downloader": {...}`；现有最小审计中间件只记路由与状态码，天然满足脱敏要求，无需改动。
- 上传容量预检中间件（`main.py:219`）不覆盖新端点（下载大小由作业内断路器与 2GB 检查约束）。

### 6.4 作业流（`backend/app/services/jobs.py` 扩展）

`video_download`：校验 payload → 检查 yt-dlp/FFmpeg 可用（缺失→blocked）→ 建 staging + 按需拷贝 Cookie → `downloader.download`（心跳/进度/取消）→ probe 校验 → 容量预检 → `store_stream` 写 artifact → 创建 source/content version（`source_type` 见开放问题 4）→ 脱敏审计 → 入队 `video_analyze` → finally 清理 staging 与 Cookie 拷贝。

失败语义：`DownloadUnavailable`→blocked；`DownloadInputInvalid`→failed（可重试）；`DownloadProcessingCancelled`→cancelled；任何异常不残留半成品 source，消息不含 URL/Cookie/平台响应。

### 6.5 前端（`frontend/src/App.tsx`）

- `VideoWorkspace`（约 919–978 行）：link 模式替换静态占位为表单——平台选择、URL、权利声明（复用 `rights` 列表）、Cookie 通道选择（不使用 / 已导入 cookies.txt / 本机浏览器，含可用状态提示）、提交后调用 `/videos/link` 并跳转作业页；保留"不会预览或嗅探"提示。
- 设置页：新增"下载 Cookie"导入/删除控件与"链接下载"断路器设置（超时、磁盘上限）。
- 来源类型过滤（约 1080 行）：若采用新 `source_type`，增加对应筛选项与徽标（见开放问题 4）。

### 6.6 设置（`backend/app/domain/models.py` `SettingsUpdate`）

新增：`download_timeout_seconds`（60..86400，默认 3600）、`download_disk_limit_mb`（64..32768，默认 2048）。`video_analyze` 现有断路器设置对下载作业独立生效（下载与分析是先后两个作业，各自持有租约）。

## 7. 验收标准（映射现有验收矩阵）

| 需求组 | 实现证据 | 自测标识 | 独立复核重点 |
|---|---|---|---|
| REQ-047, 047a | `ports/media.py`、`adapters/downloader.py`、`services/jobs.py`、`services/imports.py`、`main.py`、`frontend/src/App.tsx` | T-VID-003, T-VID-004 | 白名单与 URL 校验、无 shell 子进程、断路器、Cookie 不进 DB/日志/备份/导出、失败无残留、成功自动入队 video_analyze |

- T-VID-003（单元）：URL 校验（B站 BV/av/b23.tv、抖音分享短链；拒绝非白名单域/非 HTTPS/带凭据 URL）；作业幂等、有限重试、取消清理；cookies.txt 导入大小上限、逐作业拷贝即删与备份/导出排除断言；浏览器直读通道在浏览器不可用/被占用时的优雅失败（不回退到其他通道）；断路器触发；产物校验失败回滚；能力接口与设置边界。
- T-VID-004（合成集成）：本地合成 HTTP 服务器提供小型无版权 MP4 fixture，全链路验证下载→artifact→video_analyze→播放→导出→清理，全程不访问真实平台（延续现有"合成、无版权 fixtures"纪律，REQ-046）。
- 独立验收：真实 B站/抖音链接手工验收（记录脱敏摘要与成功率），因平台反爬不稳定，不作为自动化门禁；抖音成功率如实登记，不伪装通过。

## 8. 决策记录（2026-08-13 已拍板，替代原开放问题）

1. **Cookie 来源：双通道**。cookies.txt 显式导入为主通道，`--cookies-from-browser <edge|chrome>` 为备选通道，提交时由用户显式选择；浏览器直读仅在作业进程内、不落盘、失败不静默回退（见 REQ-047a）。实现与测试面因此翻倍，两条通道的治理规则已在 4.4 节分别写明。
2. **格式与画质**：限 ≤1080p、remux 为 MP4；需登录/会员才可得的画质按失败处理。
3. **多P/合集**：仅单视频，一个链接对应一个视频。
4. **source_type**：新增 `video_link` 枚举；前端来源类型过滤与徽标同步新增"链接视频"。
5. **抖音失效提示**：通用脱敏提示（"链接失效/平台拒绝，请重新复制分享链接或稍后重试"），不写入平台特征诊断。
6. **合规边界**：已确认仅个人本地使用、不绕过 DRM/会员、不批量、不对外分发；在此基础上可冻结。

## 9. 流程

本草案经审阅、修订并冻结后，按 `REQ-047`/`REQ-047a` 拆分实现与测试任务；冻结前不改动任何代码。文档同步范围：`docs/requirements.md`、`docs/threat-model.md`、`docs/api-contract.md`、`docs/acceptance-matrix.md`、`docs/test-plan.md`、`docs/dependency-installation.md`、`docs/operations-and-recovery.md`；完成后按 v1.2 双件报告体系归档。
