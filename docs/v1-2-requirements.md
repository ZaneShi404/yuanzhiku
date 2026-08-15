# 源知库 v1.2 需求：链接获取 / 视频下载（受限通道）

## 1. 元数据与状态

- 版本：v1.2（链接获取 / 视频下载）
- 状态：**已并入 `docs/requirements.md` 冻结基线并已完成实现**（REQ-015/031/043/044 修订与 REQ-047/047a 新增均已进入冻结需求文本，代码与测试已落地）。残留形式事项：§10 冻结门禁第 3 项（真实平台验收）的登记形式已按双件约定补齐 sidecar（`independence=non_independent`，如实登记 operator-assisted）；`release_readiness` 仍保持 blocked，发布门禁不受影响。2026-08-14 状态同步。
- 日期：2026-08-13（决策拍板与修订日；首稿由开发子智能体撰写，本轮按测试/审核 findings 修订）
- 上游草案：`reports/development/20260812T222057Z-link-acquisition-v1-2-draft.md`（2026-08-12 起稿，2026-08-13 六项决策拍板）
- 首轮验证/审核输入：`reports/testing/20260812T225424Z-v1-2-requirements-verification.md`（11 条）、`reports/testing/20260812T230103Z-v1-2-requirements-review.md`（17 条）
- 范围：把 v1.1 视频工作区的"链接获取"预留位升级为受限下载通道；平台范围＝抖音（国内版）+ 哔哩哔哩；接入形态＝yt-dlp Python 依赖；画质＝≤1080p remux MP4；仅单视频；source_type＝新增 `video_link`；Cookie＝仅 cookies.txt 单通道；出站＝平台注册域清单 + 作业内回环过滤代理硬强制；失败提示＝通用脱敏；合规边界＝仅个人本地使用、不绕过 DRM/会员。
- 与既有版本关系：v1.0/v1.1 的本地视频导入、`video_analyze`、播放、外部卡（含抖音字面卡）、备份/导出/再导入行为全部保持不变；本版仅在 `REQ-031` 抖音禁令上开一个显式例外（`REQ-047`/`REQ-047a` 受限下载通道），其余冻结需求不动。
- 立场声明（延续仓库文化）：
  - **archive-local acceptance 不等于 release approval**——需求/测试/验收报告按 v2 过程档案归档（`docs/v1-archive/report-schema-v1.json` 双件报告约定），只代表本仓库本地可归档结论。
  - **release_readiness 保持 blocked**——本版冻结与验收都不改变发布门禁状态；发布仍需独立 release_management 角色在完整门禁通过后另行判定。
- 角色分工：本文档由开发子智能体产出；测试、审核、验收由独立子智能体进行，本文档不代其做测试或审核结论。

## 2. 目标与非目标

目标：

- 把 v1.1"链接获取"静态占位（`frontend/src/App.tsx:976`）升级为可提交的受限下载通道：用户显式提交白名单平台（哔哩哔哩、抖音国内版）视频链接，经锁定版本 yt-dlp 下载为本地 MP4 artifact，随后自动进入与本地导入完全相同的证据链、`video_analyze` 分析、播放、备份、导出、再导入与永久清理生命周期。
- 范围：
  - 平台白名单：`bilibili.com`（BV/av 视频链接及 b23.tv 短链）、`douyin.com` 及其子域（抖音国内版分享短链/视频链接）。URL 层校验之外的**出站注册域清单**见 7.2.1 与决策 7。
  - 单视频下载：单个链接对应单个视频；产物由现有 FFmpeg 依赖 remux/合并为 MP4（如为 webm 可原样保留），受现有 2GB 上限与容量预检约束。
  - Cookie：**仅 cookies.txt 单通道**——用户显式导入 Netscape 格式 cookies.txt，提交下载时显式选择"使用已导入 Cookie"（`use_cookie`），仅用于该次下载；浏览器直读通道已按二次决策删除（见决策 1 追加与决策 8）。
  - 下载完成后自动入队 `video_analyze`（与本地导入同路径）。
- 下载坚持现有安全纪律：显式用户操作、平台白名单与注册域清单、无 shell 子进程、回环过滤代理硬强制出站、总超时/无进展/内存/磁盘断路器、可协作取消、权利声明必填、内容寻址不可变、Cookie 与凭据零持久化。
- 分发边界说明：产品**不提供分发能力**；导出遵循既有用户确认纪律（`REQ-041`），导出后的后续使用由用户自担（审核 F-12 澄清）。

非目标（本版明确不做）：

- YouTube 等海外站点、小红书/快手等其他国内平台。
- 多P/合集批量、番剧、直播、订阅、定时抓取、字幕/弹幕/评论提取。
- 登录凭据或密码的保存与使用；浏览器 Cookie 库直读；任何云服务、代理（用户环境代理）、静默回退。
- 绕过会员、DRM、付费墙或平台限速。

## 3. 选型记录

| 工具 | 结论 | 理由 |
|---|---|---|
| yt-dlp | 采纳 | 与后端同栈（pip 装入现有 venv，`requirements.lock` 锁定）；Unlicense 无协议污染；B站成熟、抖音支持但受反爬影响；可完全按现有 `LocalFfmpegMediaAnalyzer` 的无 shell 子进程 + 断路器模式（`backend/app/adapters/media.py:68-131`）封装，出站经作业内回环过滤代理（决策 7）；站点覆盖未来可扩展 |
| lux | 备选 | 单文件二进制可行，但引入外部二进制随附、版本管理与更新机制，与"依赖全部锁定进 venv"纪律冲突；抖音支持弱于 yt-dlp |
| mediago | 排除 | 桌面 GUI，无法作为后端组件集成 |
| BBDown | 排除 | 仓库已归档停更、GPLv3、仅 B站 |

出站控制选型（决策 7，二选一落地结论）：**回环过滤代理**（方案 a）。理由：链路层硬强制（每个新 TCP 连接在 CONNECT 阶段校验目标主机，不依赖 yt-dlp 内部 opener 注入点，上游内部重构也无法绕过）；与 `REQ-002` loopback 哲学一致（仅监听 127.0.0.1 随机端口、仅作业生命周期内存活）；适配器保持 `sys.executable -m yt_dlp` 无 shell 子进程模式，断路器继续由父进程监控；显式 `--proxy` 指向回环代理即覆盖环境代理（另清空子进程代理环境变量双保险）；标准库 socket/threading 即可实现，零新增依赖。备选方案（b，子进程内 yt-dlp 库 API + 自定义 opener）被否：属应用层约定，yt-dlp 版本演进会改变内部请求/重定向代码路径，CLI 行为（上游主要测试面）与库 API 行为存在差异，硬强制承诺不可维持。

## 4. 需求文本

原文一律取自 `docs/requirements.md` 冻结基线，逐字引用。

### 4.1 REQ-015 修订

原文：

> 视频第一版仅支持本地 MP4/WebM（同样受 2GB、容量预检、权利声明、不可变 SHA-256 artifact、备份和永久清理规则约束）；不保存原始本地完整路径。视频通过本机显式安装的 FFmpeg/ffprobe 探测元数据并在独立 staging 中有限时间采样 JPEG 关键帧，禁止 shell、网络、URL 获取和静默云回退。

修订后全文：

> `REQ-015`：视频支持本地 MP4/WebM 导入（同样受 2GB、容量预检、权利声明、不可变 SHA-256 artifact、备份和永久清理规则约束）与受限链接获取（`REQ-047`）；不保存原始本地完整路径。链接获取仅接受白名单平台、仅由用户显式提交，下载经无 shell 受限子进程完成并进入同一 artifact/分析/证据链生命周期；本地导入与视频分析仍禁止 shell、网络、URL 获取和静默云回退。视频通过本机显式安装的 FFmpeg/ffprobe 探测元数据并在独立 staging 中有限时间采样 JPEG 关键帧。

### 4.2 REQ-030 不变 / REQ-031 修订

- `REQ-030` 保持不变，原文不变：

  > 外部卡仅接受用户输入的一般 URL 元数据，不抓取、解析、预览或请求，且不是事实证据。抖音仅接受用户输入、原样保存的 HTTPS `douyin.com` 或其子域 URL；浏览器仅打开原 URL 并提示中文人工定位。

  外部卡仍仅字面保存 URL 元数据，不抓取、不解析、不请求，不是事实证据。

- `REQ-031` 原文：

  > 抖音绝对禁止下载、抓取、内容/媒体/字幕/文本/图像提取、iframe、cookie/密码/认证、自动化、缓存、代理、逆向和伪造时间参数；任何 HTTP client/worker/parser 均不可接受抖音 URL。

  修订后全文：

  > `REQ-031`：抖音绝对禁止下载、抓取、内容提取、iframe、cookie/密码/认证、自动化、缓存、代理、逆向和伪造时间参数；任何 HTTP client/worker/parser 均不可接受抖音 URL。**唯一例外是 `REQ-047` 与 `REQ-047a` 定义的受限链接获取下载通道**——它只由用户显式提交、只服务白名单域、只在独立 staging 内工作；外部卡、文档解析器、检索、文档导入、媒体 AI 端口对该例外一无所知。**密码/登录凭据不在例外之列，任何情况下不使用、不保存。**

### 4.3 新增 REQ-047（链接获取，9 条）

1. 平台白名单 `bilibili`、`douyin`；URL 严格校验：HTTPS、主域或子域匹配白名单（bilibili 组显式含 `b23.tv` 入口短链）、无内嵌凭据、长度上限；未知或不支持的 URL 拒绝，拒绝消息不含 URL 内容。
2. 下载器为锁定版本的 yt-dlp（`REQ-046`，写入 `requirements.lock`），仅以无 shell 子进程运行（`shell=False`）、stdin 关闭、忽略用户级配置文件与缓存；**出站一律经作业内回环过滤代理**（仅监听 127.0.0.1、仅作业生命周期内存活），代理逐连接校验目标主机名必须命中平台注册域清单（7.2.1），未登记域直接拒绝；解析到回环/内网/保留段的地址拒绝，但注册域主机名解析落入隧道段（198.18.0.0/15、28.0.0.0/8，代理工具 fake-IP 环境）时按 7.2.1 隧道段例外处理（决策 10）；显式 `--proxy` 指向回环代理并清空子进程代理环境变量（等效禁用环境代理）；重定向链的每一个新连接都在代理处强制校验，无静默重定向跟随外平台。
3. 下载仅写入独立 per-job staging：总超时、内存、磁盘断路器沿用 `REQ-016` 纪律；无进展（静默期）断路器按 `REQ-033` 语义**新实现**（staging 目录总量滚动窗口无增长判定，见 7.2）；全部支持协作取消；下载产物经 ffprobe 校验为合法 MP4/WebM、时长合法、**分辨率档位 ≤1080p（短边 ≤1080 且长边 ≤1920，含竖屏 1080×1920，决策 12）**、≤2GB 且通过容量预检后，流式写入不可变 SHA-256 artifact。
4. 提交链接时权利声明（owned/authorized/permitted/open_license/other）必填，与本地导入一致（`REQ-011`）。
5. 下载出处记录（provenance）：平台、脱敏链接（`scheme://host/path`，去 userinfo/query/fragment）、yt-dlp 版本、所选格式、是否使用 Cookie 写入 `video_download_provenance` 表（进 `EXPORT_TABLES`/`BACKUP_TABLES`，随导出 manifest 与备份快照携带并受既有 hash 校验）；审计事件仅记 `event_type`/`entity_id`/`result`；下载作业 `payload_json` 只存脱敏链接；Cookie 内容、原始请求头、下载响应体绝不进入数据库、日志、API 响应、备份或导出（`REQ-042`）。
6. 新增作业 kind `video_download`：持久租约、心跳、取消、优先级与有限重试；成功后在作业内创建 source/content version/artifact 并自动入队 `video_analyze`；分析失败、取消或 blocked 不降低已完成下载（对齐 `REQ-033a` 精神）。任何失败路径不残留半成品 source；staging 与 Cookie 拷贝作业结束即清理。
7. 仅单视频；多P/合集/直播/需登录才可见的内容按失败处理，失败消息脱敏。
8. 下载作业在 yt-dlp 或 FFmpeg 缺失时明确 blocked；因反爬、链接失效、平台拒绝等外部原因失败时状态 failed 且可有限重试，绝不静默切换来源或平台。
9. 会员/付费墙/DRM 内容一律拒绝：不利用 Cookie 获取超出用户自身已购权益的内容；付费专享、DRM 加密流按失败处理（通用脱敏提示）；仅按平台公开免费档位（≤1080p）选择格式，高度/码率过滤无法区分会员画质时按失败处理。

### 4.4 新增 REQ-047a（Cookie 生命周期，仅 cookies.txt 单通道，5 条）

> 2026-08-15 修订为**按平台 Cookie 库**（见变更日志）：单文件 `cookies.txt` → `cookies/<platform>.txt`，下载/探测按链接平台自动选用；安全不变量不变。以下为现行文本：

1. **按平台 cookies 文件**：用户可显式导入浏览器导出的 cookies.txt（Netscape 格式），按平台分别保存于 `data/state/download/cookies/<platform>.txt`（platform 限白名单平台 bilibili/douyin），每个文件大小上限 1MB；同平台重复导入覆盖旧文件；支持按平台一键删除（幂等）。遗留单文件 `cookies.txt` 在启动时按注册域标签边界匹配自动分拣迁移到对应平台文件并删除旧文件，分拣不打印、不落日志任何 Cookie 内容。
2. 提交下载时用户显式选择"使用已导入 Cookie"（`use_cookie: true`），仅用于该次下载；后端按链接平台自动选用该平台已导入的 Cookie 文件，无需用户切换；该平台未导入 Cookie 文件却选择使用 → `422`，绝不静默回退到无 Cookie 下载、绝不改用其他平台的 Cookie。
3. Cookie 内容绝不进入数据库、日志正文、API 响应、备份（`REQ-040`）、导出 ZIP 与 reimport（`REQ-041`）、审计事件；备份/导出/再导入规则显式排除 `data/state/download` 路径。
4. 使用 Cookie 时把该平台的 Cookie 文件**拷贝**注入该作业 staging，作业结束（无论成败）立即删除拷贝；作业运行期间不触碰、不修改原文件。
5. 能力接口暴露按平台的 `cookies` 状态映射；某平台不可用时前端在该平台的开关上禁用并给出导入引导。

### 4.5 REQ-043 修订

原文：

> 所有端点位于 `/api/v1`：health/capabilities、settings、sources/relations/rights/metadata、imports/paste、videos/local、videos/{id}/stream/frames/transcribe/summarize、docs/representations/evidence/citations/knowledge、search、tags/topics、external/douyin cards、jobs、delete/restore/purge、backup/restore、export/reimport；类型稳定并有 OpenAPI。

修订后全文：

> `REQ-043`：所有端点位于 `/api/v1`：health/capabilities、settings、sources/relations/rights/metadata、imports/paste、videos/local、videos/link、videos/{id}/stream/frames/transcribe/summarize、settings/download-cookie、docs/representations/evidence/citations/knowledge、search、tags/topics、external/douyin cards、jobs、delete/restore/purge、backup/restore、export/reimport；类型稳定并有 OpenAPI。`/capabilities` 增加 `downloader` 节。其余不变。

### 4.6 REQ-044 修订

原文：

> UI 页面包括库、导入、视频、来源详情、检索、作业、设置、备份/还原/导出、外部卡；极简中文；视频仅播放本地 artifact 并显示本地元数据/关键帧，链接获取只可作为不可提交的预留状态；PDF 使用隔离只读预览并禁用嵌入链接，仅显式外开；文本安全渲染；目标 Edge/Chrome。

修订后全文：

> `REQ-044`：UI 页面包括库、导入、视频、来源详情、检索、作业、设置、备份/还原/导出、外部卡；极简中文；视频仅播放本地 artifact 并显示本地元数据/关键帧，视频页"链接获取"为可提交表单：平台选择（哔哩哔哩/抖音）、URL 输入、权利声明必选、Cookie 开关（使用已导入 cookies.txt，含可用状态提示）、联网告知（提交即向所选平台服务器发起下载请求）、提交后跳转作业页；不做预览、嗅探或解析展示；PDF 使用隔离只读预览并禁用嵌入链接，仅显式外开；文本安全渲染；目标 Edge/Chrome。

## 5. 威胁模型修订（新增行）

| 风险 | 控制 | 需求 |
|---|---|---|
| 下载器被恶意/伪造 URL 利用（SSRF、外平台跳转） | 平台白名单 + 严格 URL 校验；yt-dlp 无 shell 子进程、忽略用户配置；出站经作业内回环过滤代理逐连接校验注册域清单、拒绝内网/回环解析目标；显式代理覆盖环境代理；断路器限制资源 | REQ-047 |
| Cookie 泄露或进入分发物 | cookies.txt 仅存 `data/state/download`、逐作业拷贝即删；不进 DB/日志/备份/导出/reimport；UI 显式删除；浏览器 Cookie 库直读已按二次决策整体删除 | REQ-047a, REQ-040..042 |
| 平台反爬、账号风控 | 单 worker、无并发批量、无自动化调度、有限重试；Cookie 由用户自愿导入且可随时删除 | REQ-047, REQ-047a |
| 下载内容不可信（伪造/损坏/超限） | 复用 REQ-015/016 全部断路器与 ffprobe/hash 校验，分辨率档位（短边 ≤1080 且长边 ≤1920）后置校验；失败保留原状 | REQ-047, REQ-016 |
| 平台条款与版权 | 用户权利声明必填；仅个人本地使用；不绕过 DRM/会员/付费墙；产品不提供分发能力，导出后使用由用户自担；外部卡仍不构成证据 | REQ-011, REQ-030, REQ-047 |

## 6. API 契约

错误沿用 `docs/api-contract.md` 稳定信封：`{ "detail": { "code": "stable_code", "message": "中文说明" } }`。

### 6.1 端点

- `POST /api/v1/videos/link`：JSON 请求，成功 `201 {job}`（`job.kind == "video_download"`）。
- `POST /api/v1/settings/download-cookie`：multipart 上传 cookies.txt，成功 `204`；超过 1MB → `413`。
- `DELETE /api/v1/settings/download-cookie`：`204`（幂等；不存在也返回 `204`）。
- `GET /api/v1/capabilities`：新增 `"downloader": {...}` 节（见 6.3）。
- CORS：新增的 DELETE 方法必须加入现有 CORSMiddleware `allow_methods`（`backend/app/main.py:211-217` 现为 `["GET", "POST", "PUT"]`，需改为含 `"DELETE"`）；否则开发模式跨源 DELETE 预检被拒（测试 F-02 修复）。

### 6.2 `POST /videos/link` 字段表

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `url` | 字符串 | 是 | 1..4096 字符；HTTPS；主域或子域匹配 `bilibili.com`/`douyin.com` 白名单，或为 `b23.tv` 短链（显式放行、归属 bilibili，其重定向终点由回环代理限制在 bilibili 注册域）；无 userinfo（凭据）；无内网/回环主机；拒绝消息不含 URL 内容 |
| `platform` | `"bilibili" \| "douyin"` | 是 | 枚举 |
| `rights` | `owned \| authorized \| permitted \| open_license \| other` | 是 | 与本地导入一致（`REQ-011`） |
| `use_cookie` | 布尔 | 否（默认 false） | 使用已导入 cookies.txt；`true` 且未导入 → `422`；仅用于该次下载 |
| `title` | 字符串 | 否 | ≤500 字符；缺省时下载成功后使用平台标题，退化为"未命名视频" |
| `author` | 字符串或 `null` | 否 | ≤300 字符 |
| `language` | 字符串 | 否 | ≤32 字符 |
| `notes` | 字符串或 `null` | 否 | ≤4000 字符 |
| `source_date` | ISO-8601 日期 | 否 | 与 `imported_at` 独立 |
| `categories` | 固定分类数组 | 否 | 只能取固定分类；空数组有效 |
| `tags` | 字符串数组 | 否 | 空数组有效 |

### 6.3 capabilities 扩展

```json
"downloader": {
  "enabled": true,
  "adapter": "yt-dlp",
  "version": "<锁定版本>",
  "supported_platforms": ["bilibili", "douyin"],
  "cookie_file_available": true,
  "network": true
}
```

- `enabled=false`（yt-dlp 或 FFmpeg 缺失）时前端禁用链接表单提交并显示引导；`POST /videos/link` 返回 `503`。
- `cookie_file_available`＝`data/state/download/cookies.txt` 存在且 ≤1MB；不承诺精确可访问性判断，探测失败一律按不可用处理并给出导入引导（浏览器直读相关探测已随单通道决策删除）。
- 现有最小审计中间件（`backend/app/main.py:241-248`）只记路由模板与状态码，天然满足脱敏要求，无需改动。
- 上传容量预检中间件（`backend/app/main.py:219-239`）只覆盖 `/imports/file` 与 `/videos/local`，不覆盖新端点；下载大小由作业内断路器与 2GB 检查约束。

### 6.4 错误码表

| HTTP | code | 场景 |
|---|---|---|
| `422` | `request_validation` | Pydantic 校验失败（含 rights 缺失/非法） |
| `422` | `invalid_url` | 非 HTTPS / 非白名单域 / 含凭据 / 超长 / 不支持的类型 |
| `422` | `unsupported_platform` | platform 不在白名单 |
| `422` | `cookie_file_unavailable` | `use_cookie=true` 且 cookies.txt 未导入 |
| `413` | `cookie_file_too_large` | cookies.txt 超过 1MB |
| `503` | `downloader_unavailable` | yt-dlp 或 FFmpeg 缺失、下载器未配置 |
| `404` | 沿用框架 | 资源不存在 |
| `500` | `internal_error` | 本地服务内部错误 |

### 6.5 OpenAPI 影响

- 新增 Pydantic 模型 `DownloadLinkRequest`（含字段表 6.2 全部校验）放入 `backend/app/domain/models.py`，OpenAPI 自动生成（`REQ-043` 类型稳定）。
- `capabilities` 响应 DTO 增加 `downloader` 节。
- `docs/api-contract.md` 端点表与"本地视频"节同步（见第 13 章）。

## 7. 接口设计

### 7.1 新端口 `MediaDownloaderPort`（`backend/app/ports/media.py` 扩展）

```python
class DownloadedVideo:  # dataclass
    filename: str        # 不含路径，仅文件名
    media_type: str      # "video/mp4" | "video/webm"
    byte_size: int

class DownloadUnavailable(RuntimeError): ...     # 工具缺失/未配置 → blocked
class DownloadInputInvalid(ValueError): ...      # URL/平台/反爬/Cookie 缺失/超限/白名单出站拒绝 → failed（可重试）
class DownloadProcessingCancelled(RuntimeError): ...  # → cancelled

class MediaDownloaderPort(Protocol):
    def capability(self) -> dict: ...
    # {"enabled", "adapter": "yt-dlp", "version", "supported_platforms",
    #  "cookie_file_available", "network": True}
    def config_hash(self, platform: str, format_profile: str) -> str: ...
    def download(self, *, url: str, platform: str, workspace: Path, limits: MediaProcessingLimits,
                 use_cookie: bool,               # 是否使用已导入 cookies.txt
                 cookie_path: Path | None,       # use_cookie=True 时指向 staging 内 Cookie 拷贝
                 cancelled: Callable[[], bool],
                 heartbeat: Callable[[], None],
                 progress: Callable[[int, str], None]) -> DownloadedVideo: ...
```

### 7.2 新适配器 `YtDlpDownloader`（`backend/app/adapters/downloader.py` 新文件）

- 子进程 `sys.executable -m yt_dlp`，`shell=False`、`stdin=DEVNULL`、stderr 不落日志（不进日志正文/数据库）。
- 骨架参数（单通道 Cookie + 回环代理）：
  `--proxy http://127.0.0.1:<作业端口> --no-playlist --no-simulate --ignore-config --no-cache-dir --retries 1 --socket-timeout 30 --merge-output-format mp4 --remux-video mp4 -S "res:1080" -o <staging>/video.%(ext)s <url>`
  - `use_cookie=true` 时追加 `--cookies <staging 内 Cookie 拷贝>`；
  - 子进程环境清空 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`no_proxy`（显式 `--proxy` 已覆盖环境代理，清空为双保险）。
- 格式与画质：`-S "res:1080"` 语义为"选择不超过 1080p 档位的最佳组合"（取代原 `-f .../b` 无高度过滤的兜底）；另在产物校验阶段**后置断言分辨率档位 ≤1080p（短边 ≤1080 且长边 ≤1920，竖屏 1080×1920 属 1080p 档位）**，超出 → `DownloadInputInvalid`（测试 F-07 修复 + 决策 12 竖屏语义修正，格式选择 + probe 双保险）。
- FFmpeg 角色限定（审核 N-02）：FFmpeg 在本功能中**仅作本地音视频合并/remux 工具，绝不作为网络下载器使用**；网络取流仅 yt-dlp 经回环代理完成，不得向 yt-dlp 指定任何以 ffmpeg 为下载器的选项。
- 断路器（审核 F-03 归因更正后的可实现设计）：
  - 总超时/内存/磁盘：沿用 `backend/app/adapters/media.py:68-131` `_run` 的检查循环模式（总超时 deadline、`_workspace_size` 磁盘、内存 RSS）——`_run` 本身**没有**无进展检测，故不再声称"复用无进展断路器"；
  - 内存 RSS：`_process_memory_bytes`（`media.py:48-57`）依赖可选导入 psutil，而 `backend/requirements.lock` 现不含 psutil 导致内存断路器静默失效。**结论：把 `psutil` 锁定进 `requirements.lock`**（MIT 许可、与 `media.py` 现有优先路径一致），媒体分析与下载两条路径的内存断路器同时生效；不采用 jobs.py 解析路径的 ctypes 回退（那是解析专用模式，不混用）；
  - 无进展（静默期）断路器：**新实现**——父进程监控循环以 `download_no_progress_seconds`（默认 10s）为观察窗口间隔，触发阈值＝**连续两个观察窗口内 staging 目录总字节数无增长且子进程无输出**（输出字节计数由适配器维护、不落日志正文；总大小统计复用 `_workspace_size` 同源）→ `DownloadInputInvalid`；
  - 2GB 检查对象：**staging 目录总量**（yt-dlp 合并/remux 期间存在多个中间文件，单文件检查会漏判；与 `maximum_workspace_bytes` 统一，审核 F-14）。
- 取消清理（审核 F-15）：取消时终止 yt-dlp 进程并**进程树终止**其拉起的 ffmpeg 等子进程（实施时确认锁定 yt-dlp 版本收到终止信号后的自清理行为，必要时用 psutil 进程树终止），随后清理 staging 与 Cookie 拷贝；T-VID-003 覆盖。
- 产物校验：复用 `LocalFfmpegMediaAnalyzer.probe`（`backend/app/adapters/media.py:133-178`）验证容器/时长/尺寸并**追加分辨率档位 ≤1080p 断言（短边 ≤1080 且长边 ≤1920）**，再 `ArtifactStore.store_stream`（`backend/app/adapters/storage.py:57`）写入 artifact——与上传路径同一入口。
- 依赖：`backend/requirements.lock` 增加 `yt-dlp==<实施时锁定版本>` 与 `psutil==<实施时锁定版本>`（当前 lock 共 10 个包，`backend/requirements.lock:1-10`，两者均不在列）；ffprobe/ffmpeg 复用现有 `YUANZHIKU_FFPROBE_BIN`/`YUANZHIKU_FFMPEG_BIN` 环境发现（`backend/app/adapters/media.py:28-30`）。

#### 7.2.1 出站注册域清单与回环过滤代理（决策 7）

回环过滤代理：作业内以标准库 socket/threading 实现的最小 HTTP CONNECT 过滤代理，仅监听 `127.0.0.1:<随机端口>`，仅存活于该下载作业生命周期，作业结束（无论成败）即关闭。规则：

- 每个新连接（CONNECT host:port，以及明文 HTTP 请求行）的目标主机名必须命中注册域清单：主机名等于注册域或为其子域（按 label 边界匹配）；IP 字面量（含 IPv6 括号形式）天然不命中注册域，直接拒绝；未命中 → 立即断开，yt-dlp 收连接失败 → 作业 `failed`（通用脱敏消息）。
- DNS 重绑定防御（审核 N-01 加固）：代理**先解析目标主机并校验，连接必须使用该次已校验的 IP（resolve-then-connect），不得再次解析主机名**；连接建立后以对端地址复核其属本次已校验的解析结果范围。目标解析结果必须非回环/非内网/非保留段（127.0.0.0/8、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、169.254.0.0/16、::1、fe80::/10 等）；命中保留段 → 拒绝。TOCTOU 残留风险如实说明：解析至连接建立窗口内 DNS 记录被篡改的理论残余无法完全消除，缓解措施为"不二次解析 + 连接后对端复核 + 拒绝 IP 字面量"，在单用户本地、无上游恶意 DNS 的威胁假设（`REQ-002` loopback 环境）下视为可接受。
- **隧道段例外（决策 10，fake-IP 环境兼容）**：当且仅当目标主机名已通过注册域校验时，允许解析结果落在隧道段 `198.18.0.0/15`（代理工具 fake-IP/TUN 模式的常见隧道地址，公网不可路由）。安全论证：该段由本地 TUN 设备独占路由、映射到工具自身配置的真实目的地——攻击者无法用它把连接引向受害主机内部服务（经典 DNS 重绑定目标），主机名注册域白名单仍是第一道且唯一的域名控制；若环境无 fake-IP 工具，对该段的连接不可达并快速失败，无可用攻击面。`28.0.0.0/8`（部分 fake-IP 工具使用的另一常用段）已是全局单播前缀、本就放行，无需豁免，列入仅作环境文档记录（审核 F-10 澄清）。其余保留段（100.64.0.0/10、169.254.0.0/16、文档段、多播段等）、回环与内网段**无条件拒绝**，不受此例外影响。属安全边界变更，须经独立审核门禁。
- 代理启动失败 → 作业 `blocked`（fail-closed）：任何情况下**绝不直连回退**（审核 N-02）。
- 重定向链逐跳校验即由此强制：重定向产生的每一个新连接都重新过上述校验；链上任一跳未登记即断连，不静默跟随。
- 代理仅记录每个 CONNECT 目标主机于内存计数表（作业结束丢弃），供测试断言"全部出站 ⊆ 注册表"。
- 保留段拒绝是生产 fail-closed 规则；仅测试代码路径可注入"保留段拒绝豁免"（决策 9），生产代码无该分支；豁免只影响回环/保留段解析拒绝，**不影响注册域主机名校验与出站计数断言**。（隧道段例外是**生产规则**，仅限 `198.18.0.0/15` 与 `28.0.0.0/8` 且要求主机名已过注册域校验（决策 10），与测试注入豁免无关。）

注册域清单（初始登记集；**实施时以锁定 yt-dlp 版本实测真实链接的出站域集合为准逐项比对，实测新增域须经人工安全评估后登记，未实证域一律不默认登记**）：

| 平台组 | 注册域 | 用途 |
|---|---|---|
| bilibili | `bilibili.com` | 主站/API（含 `api.bilibili.com` 等子域） |
| bilibili | `bilivideo.com` | 视频媒体 CDN |
| bilibili | `bilivideo.cn` | B站 MCDN 镜像媒体域（2026-08-15 实测登记：真实链接出站命中 `xy119x188x120x16xy.mcdn.bilivideo.cn:8082`；决策 13） |
| bilibili | `hdslb.com` | 接口/媒体镜像域 |
| bilibili | `b23.tv` | 入口短链（仅作跳转入口，其重定向终点由代理限制在 bilibili 组内注册域） |
| douyin | `douyin.com` | 主站（含 `v.douyin.com` 分享短链） |
| douyin | `iesdouyin.com` | 分享短链跳转后的媒体页面/API 域 |
| douyin | `snssdk.com` | 抖音 API（如 aweme 服务） |
| douyin | `douyinvod.com` | 视频媒体 CDN |
| douyin | `365yg.com` | 抖音/字节系媒体 CDN（2026-08-14 实测登记：真实链接出站命中 `v95-aw-default.365yg.com`；决策 11） |

维护规则：清单变更（增删域）属于安全边界变更，必须附实测证据（锁定版本下真实链接的出站域抓取结果）并经独立审核门禁；历史/未实证 CDN 域一律不默认登记。注意：白名单注册域清单（出站控制）与 URL 层校验（`bilibili.com`/`douyin.com` 子域 + `b23.tv` 显式放行）是**两层独立控制**：前者由回环代理在链路层强制，后者由 API 在入口校验。

### 7.3 `video_download` 作业流（`backend/app/services/jobs.py` 扩展）

- 现状：`run_once` 只有 parse/video_analyze/video_transcribe/summarize/backup/integrity_sample 分支（`backend/app/services/jobs.py:133-181`），未知 kind 落入 `else` 分支判 `failed "未知作业类型"`（`jobs.py:180-181`）。本版在分支链中新增 `elif job["kind"] == "video_download"`。
- 流程：校验 payload → 检查 yt-dlp/FFmpeg 可用（缺失 → blocked，对齐 `MediaToolUnavailable` 处理 `jobs.py:196-202`）→ 建 per-job staging + 按需拷贝 Cookie → 启动回环过滤代理（**启动失败 → blocked，fail-closed，绝不直连回退**）→ `downloader.download`（心跳/进度/取消）→ `probe` 校验（含分辨率档位 ≤1080p，短边 ≤1080 且长边 ≤1920）→ `check_capacity`（`storage.py:49`）→ `store_stream` 写 artifact → 经 `create_ingest`（`backend/app/adapters/sqlite.py:665-709`，参数 `source_type="video_link"`、`job_kind="video_analyze"`、`audit_event="video_download"`）**在同一数据库事务内**创建 source/content version/artifact、写入 `video_download_provenance` 行并自动入队 `video_analyze`（幂等不变量见 7.4）→ 事务提交后 `audit("video_download", source_id, "succeeded")` → `finally` 关闭代理、清理 staging 与 Cookie 拷贝。
- 失败语义：
  - `DownloadUnavailable` → `blocked`（对齐 `MediaToolUnavailable`，`jobs.py:196-202`）；
  - `DownloadInputInvalid` → `failed`（可重试；对齐 `MediaInputInvalid`，`jobs.py:203-209`）；
  - `DownloadProcessingCancelled` → `cancelled`（对齐 `MediaProcessingCancelled`，`jobs.py:189-195`）；
  - 其他异常 → 现有通用处理器有限重试 `retry_wait`/`failed`（`jobs.py:222-233`；下载作业 `source_id` 为空，不会误写 source 状态）；
  - 任何异常不残留半成品 source（source 仅在下载成功后随 `create_ingest` 一次事务创建，`sqlite.py:677-709`），消息不含 URL/Cookie/平台响应。
- 手动重试：`retry_job` 已支持 failed/blocked/cancelled 重试（`backend/app/adapters/sqlite.py:1398-1413`），无需改动。
- 入队方式核实：`create_job` 允许 source/version/artifact/config 全空（`backend/app/ports/repository.py:76`；`backend/app/adapters/sqlite.py:1188-1197`；jobs 表对应列可空，`sqlite.py:106-112`），现有 backup/integrity_sample 作业即按此模式入队（`backend/app/main.py:138-143`）。`POST /videos/link` 以空 source/version 建 `video_download` 作业可行。

### 7.4 下载出处记录承载（REQ-047.5 落地，测试 F-01 / 审核 F-07 修复）

新增表 `video_download_provenance`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PRIMARY KEY | 记录 ID |
| `source_id` | TEXT NOT NULL REFERENCES sources(id) UNIQUE | 下载产出的来源 |
| `platform` | TEXT NOT NULL | `bilibili` \| `douyin` |
| `url_sanitized` | TEXT NOT NULL | 脱敏链接 |
| `yt_dlp_version` | TEXT NOT NULL | 下载所用锁定版本 |
| `format_profile` | TEXT NOT NULL | 所选格式/档位归一化描述（不含 URL） |
| `cookie_used` | INTEGER NOT NULL | 0/1 |
| `config_hash` | TEXT NOT NULL | `downloader.config_hash` |
| `created_at` | TEXT NOT NULL | 时间戳 |

- **脱敏变换定义**：`url_sanitized = scheme://host/path`，去除 userinfo、query、fragment，并截断至 4096 字符。下载作业 `payload_json` 只存该脱敏链接，下载执行即使用脱敏链接；依赖 query 参数才能解析的链接按不支持处理（failed + 通用脱敏消息）。由此 jobs 表（∈ `BACKUP_TABLES`，`sqlite.py:145-149`）随备份快照只携带脱敏链接，不含原文 URL 参数。
- **进 EXPORT_TABLES/BACKUP_TABLES**：把 `video_download_provenance` 加入 `backend/app/adapters/sqlite.py:139-149` 的两个表清单 → 导出 ZIP 的 `records.json` 与备份快照自动携带；hash 校验沿用既有机制（`_build_archive` 对 records.json 整体 SHA-256 + 逐条目 `entries`，`backend/app/services/transfers.py:224-268`），reimport 侧既有 manifest/hash 校验自动覆盖新表。
- **审计侧**：`audit_events` 无内容列（`sqlite.py:119-121`），审计事件记 `audit("video_download", source_id, "succeeded")`（仅 event_type/entity_id/result，符合 `REQ-003`/`REQ-042` 脱敏纪律）；出处内容承载在 provenance 表而非审计事件正文。
- **绝不含 Cookie 内容**：上表所有列均不承载 Cookie/请求头/响应体；T-VID-003 用例 7 做"存在性 + 脱敏"双重断言。
- **迁移**：新增表需要一次性 schema 变更（枚举扩展本身仍无需迁移）。SQLite：沿 `backend/app/adapters/sqlite.py:530-548` 版本 6 先例，在 `initialize()` 增加版本 7 块（`CREATE TABLE IF NOT EXISTS video_download_provenance(...)` + `INSERT INTO schema_migrations(version, applied_at) VALUES(7, ...)`，对老库幂等补表）；PostgreSQL：新增 Alembic 迁移 `backend/alembic/versions/008_video_download_provenance.py`（沿既有 007_video_media.py 模式；`migrate.py` 经 `migrate_to_head` → `alembic command.upgrade("head")` 应用）。回滚可逆：新表无既有数据，删除建表语句与表清单条目即回退。
- **幂等性与重试不变量（测试 N-3）**：provenance 行与 source/content version/artifact 在**同一数据库事务**写入——扩展 `create_ingest`（`sqlite.py:665-709`）或在其同一 `with self.connection()` 块内追加 INSERT；`source_id UNIQUE` 保证每来源至多一条出处记录。该事务失败则整体回滚，并补偿删除刚写入的 artifact（沿用 `backend/app/services/imports.py:41-66` 的补偿模式），作业落 `failed` 可重试。**重试不变量：任何失败路径都不残留 source/content version；重试从头执行，绝不重复创建第二个 source/version**（与 `REQ-032` 幂等纪律一致）。

### 7.5 设置（`backend/app/domain/models.py` `SettingsUpdate`）

- 新增字段（追加于 `models.py:140-150` 的 `SettingsUpdate`）：
  - `download_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)`（默认 3600，使用侧取值，参照 `jobs.py:360-370` 模式）；
  - `download_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)`（默认 2048，作为 staging 目录总量上限与 2GB 检查的统一口径）；
  - `download_no_progress_seconds: int | None = Field(default=None, ge=10, le=86_400)`（默认 10，无进展断路器观察窗口间隔；默认种子随现有 defaults 字典模式登记，`sqlite.py:549-562`）。
- 下界协调说明（测试 N-2）：现有 `SettingsUpdate` 中"时间总超时类"字段惯例为 `ge=60`，但字段边界按语义设定已有先例（`video_max_frames` `ge=1`、`max_retry_attempts` `ge=0`，`models.py:148/150`）。`download_no_progress_seconds` 是**观察窗口间隔（轮询粒度）而非总超时**，故下界取 `ge=10`、默认 10；阈值语义＝**连续两个观察窗口（默认 2×10s＝20s）内 staging 目录总字节数无增长且子进程无输出**。
- `video_analyze` 现有断路器设置对下载作业独立生效：下载与分析是先后两个作业，各自持有租约。

### 7.6 DataPaths 新增目录（`backend/app/core/config.py`）

- `DataPaths`（`config.py:19-68`）新增属性 `download`，返回 `self.state / "download"`（即 `data/state/download`），并加入 `create()`（`config.py:66-68`）的建目录列表。
- 该目录只放 `cookies.txt`（用户显式导入，1MB 上限）；per-job 下载 staging 沿用 `data/staging` 体系，作业结束清理。

### 7.7 source_type=video_link 存储结论（已核实）

- SQLite：`sources.source_type TEXT NOT NULL`，**无 CHECK 约束**（`backend/app/adapters/sqlite.py:42-47`）；`jobs.kind` 同样无 CHECK（`sqlite.py:106-112`）。
- PostgreSQL：`backend/migrations/postgresql/001_initial.sql:5` 的 `source_type TEXT NOT NULL` 同样无 CHECK；仓库实现不校验枚举。
- **结论：新增 `video_link` 枚举值不需要任何数据库迁移**；唯一动作：
  1. `backend/app/domain/models.py:20-24` 的 `SourceType` 枚举增加 `VIDEO_LINK = "video_link"`；
  2. 前端 `sourceType()` 标签（`frontend/src/App.tsx:237-239`）增加"链接视频"文案；
  3. 前端来源类型过滤下拉（`frontend/src/App.tsx:1080`）增加 `<option value="video_link">链接视频</option>`。
- 与枚举不同，7.4 的 provenance 表属于新功能承载，需要一次性 schema v7（SQLite）/ Alembic 008（PostgreSQL），可逆（见 7.4）。
- 备份/导出按表白名单（`sqlite.py:139-149`）自动覆盖新枚举值。

### 7.8 前端改动点（`frontend/src/App.tsx`）

- `VideoWorkspace`（`App.tsx:918-978`）：link 模式（占位 `App.tsx:976`）替换为可提交表单——平台选择（哔哩哔哩/抖音）、URL 输入、权利声明（复用 `rights` 列表）、Cookie 开关（"使用已导入 cookies.txt"，按 `/capabilities` 的 `downloader.cookie_file_available` 禁用并引导）、联网告知文案（"提交即向所选平台服务器发起下载请求"）、提交后调用 `/videos/link` 并跳转作业页；保留"不会预览或嗅探"提示。
- 作业页标签：`jobLabel`（`frontend/src/App.tsx:1116-1126`）增加 `video_download: '链接下载'` 条目（测试 F-05 修复，未知 kind 目前回退显示英文原文，违反 REQ-044 极简中文）。
- 设置页（`App.tsx:1232` 起）：新增"下载 Cookie"导入/删除控件与"链接下载"断路器设置（`download_timeout_seconds`、`download_no_progress_seconds`、`download_disk_limit_mb`）；`App.tsx:1238` 的 PUT body 与 `App.tsx:1245` 政策列表文案（"视频仅分析本地 MP4/WebM，链接获取尚未接入"）同步更新。
- 来源类型过滤（`App.tsx:1080`）与徽标（`App.tsx:237-239`）新增"链接视频"。
- 作业页（`App.tsx:1128`）其余渲染通用，无需改动。

## 8. 测试计划

- T-VID-003（单元）负面用例清单：
  1. URL 白名单：拒绝 `http://`（非 HTTPS）、非白名单域、白名单子域冒充（如 `douyin.com.evil.com`、`evil-bilibili.com`）、带 userinfo（`https://user:pw@douyin.com/...`）、超 4096 字符、回环/内网主机；`b23.tv` 短链放行且归属 bilibili；拒绝消息不含 URL 内容；
  2. Cookie 单通道治理：cookies.txt >1MB → `413`；重复导入覆盖旧文件；DELETE 幂等；`use_cookie=true` 而未导入 → `422`；`use_cookie=false` 时全程不读取 Cookie 文件；作业结束（成功/失败/取消）staging 内 Cookie 拷贝即删且原文件未被修改；
  3. 断路器：总超时、无进展（连续两个观察窗口（`download_no_progress_seconds`，默认 10s）内 staging 目录总字节数无增长且子进程无输出 → failed）、内存 RSS（psutil 锁定后生效）、staging 磁盘总量、目录总量 >2GB 立即终止；
  4. 取消清理：协作取消 → 终止 yt-dlp 并进程树终止其 ffmpeg 子进程、staging 与 Cookie 拷贝清理、无半成品 source；
  5. 产物校验回滚：probe 非法容器/时长/尺寸/**分辨率档位超限（短边 >1080 或长边 >1920，如 2560×1440、2160×3840、1080×1921）** → failed、不写 artifact；竖屏 1080×1920 合法放行（决策 12）；
  6. 工具缺失：yt-dlp/FFmpeg 缺失 → capabilities `enabled=false`、API `503`、作业 blocked；
  7. 出处记录与脱敏双重断言：`video_download_provenance` 行存在（platform/url_sanitized/yt_dlp_version/format_profile/cookie_used/config_hash 齐全）且 `url_sanitized` 无 userinfo/query/fragment；作业 payload 无原文 URL 参数；备份快照 payload 断言；导出 `records.json` 含 provenance 行；审计事件仅 event_type/entity_id/result；重试幂等断言：provenance 写入失败 → 事务回滚、无 source 残留、重试不重复创建 source/version（`source_id UNIQUE` 断言）；
  8. 权利声明：rights 缺失或非法值 → `422`（REQ-047.4 显式覆盖）；
  9. 多P/合集/直播/需登录/会员/付费/DRM：→ failed 且消息为通用脱敏（REQ-047.7/9 显式覆盖）；
  10. 外联控制（决策 7）：回环代理拒绝未登记域（CONNECT 阶段，无任何字节出站）；重定向链落到非白名单域 → failed 且无该域出站请求；子进程环境存在 `HTTP_PROXY`/`HTTPS_PROXY` 时流量仍只经回环代理（环境代理被覆盖并清空）；注册域主机名解析到隧道段 `198.18.0.0/15`/`28.0.0.0/8` → 放行（决策 10），未登记主机名解析到隧道段 → 拒绝；其余保留段（`100.64.0.0/10`、`169.254.0.0/16`、文档段等）仍无条件拒绝；
  11. settings 边界：`download_timeout_seconds`、`download_no_progress_seconds`、`download_disk_limit_mb` 上下限。
- T-VID-004（合成集成）：本地合成 HTTP 服务器提供小型**无版权 MP4 fixture**（存放于 `tests/fixtures`），全链路验证下载 → artifact → `video_analyze` → 播放（Range `206`）→ 导出/备份/再导入 → 清理，全程不触网真实平台；延续"合成、无版权 fixtures"纪律（`REQ-046`）。fixture 纪律：素材必须无版权或自产（ffmpeg 合成），fixture 与运行时数据只放 `tests/fixtures` 与 `tests/runtime/<run-id>`。**localhost 冲突的闭合方式（测试 F-04 + 审核 N-03 修复，按上层裁决落"测试注入模式"）**：T-VID-004 使用真实 yt-dlp 指向 localhost 合成服务器时，在适配器/服务层**直调**（绕过 API 层 URL 校验——两层控制独立），并注入**测试专用注册域清单**与"保留段拒绝豁免"标志（仅测试代码路径注入、不进生产注册表、生产代码无该分支，fail-closed 语义不变；豁免只影响回环/保留段解析拒绝，**不影响注册域主机名校验**，决策 9）；回环过滤代理记录全部 CONNECT 目标主机，断言「全部出站主机 ⊆ 测试注册表」且无任何外联（真实下载全程经白名单的验证点，决策 7）。
- T-VID-005（独立验收）：真实 B站/抖音链接手工验收（记录脱敏摘要与成功率），因平台反爬不稳定，**不作为自动化门禁**；抖音成功率如实登记，不伪装通过；摘要不含 Cookie、账号、完整 URL 原文、平台响应正文；观察项：会员/付费/DRM 链接按 REQ-047.9 拒绝。由 acceptance 角色登记，见第 10 章。
- T-API-001 扩展 / T-UI-001 扩展（测试 F-02 修复）：`DELETE /settings/download-cookie` 的 CORS 预检（OPTIONS）在真实浏览器/跨源下通过——断言 `allow_methods` 含 `DELETE` 且预检响应放行。
- `tests/unit/test_gitignore.py`（`tests/unit/test_gitignore.py:19-25`）：`.gitignore` 第 16 行 `data/` 已忽略整个数据根，`data/state/download/cookies.txt` 已被覆盖。**结论：无需功能性扩展**；建议（可选、防御性文档化）追加一条断言 `assert _ignored_path("data/state/download/cookies.txt")` 作为 Cookie 文件绝不进版本库的显式回归锚点。

## 9. 验收矩阵新增条目（沿用 `docs/acceptance-matrix.md` 格式）

| 需求组 | 实现证据 | 自测标识 | 独立复核重点 |
|---|---|---|---|
| REQ-015(修订), REQ-031(例外), REQ-047, REQ-047a | `ports/media.py`, `adapters/downloader.py`, `services/jobs.py`, `services/imports.py`, `domain/models.py`, `main.py`, `frontend/src/App.tsx` | T-VID-003, T-VID-004 | 白名单与 URL 校验、注册域清单与回环代理强制、重定向逐跳拒绝、无 shell 子进程、断路器（含无进展与内存）、单通道 Cookie 不进 DB/日志/备份/导出/reimport、provenance 承载与脱敏、失败无残留、成功自动入队 video_analyze、抖音例外仅限 REQ-047/047a 通道 |

真实平台手工验收 T-VID-005 为**独立验收**，由 acceptance 角色登记（`independence=independent`），**不写入"自测标识"列**（测试 F-09 修复）；其复核要点并入上表"独立复核重点"列。

## 10. 审核与门禁流程

- 四角色分离：开发自测（development）→ 独立测试（testing）→ 独立验收（acceptance）→ 独立审核（review/audit），各角色报告独立、互不代出结论。
- 独立审核角色的档案定位（审核 F-10 修复）：将 `docs/v1-archive/report-schema-v1.json` 的 `author_role` 枚举扩展增加 `review`，审核报告与测试/验收报告同样按双件约定登记（同 stem .md + .json）；若 schema 冻结不允许扩展，则审核报告作为非声明式辅助产物、不进入双件报告登记，但冻结门禁仍要求其出具并裁决阻断/主要项——实施时二选一并留档。
- 证据要求（参考 `docs/v1-archive/report-schema-v1.json` 双件报告约定）：
  - 每份报告 Markdown + JSON sidecar 同 stem 配对（`file_pair.same_stem_required`）；
  - testing 报告登记 T-VID-003/T-VID-004 结果并映射 REQ/DEF 与实现证据（file:line）；
  - acceptance 报告登记 T-VID-005 手工验收的脱敏摘要、成功率与 `decision_scope=archive_local`、`independence=independent`；
  - 报告内容遵守 schema `safety.forbidden_content`（无命令、绝对路径、运行输出正文、请求体、凭据、Cookie、令牌）。
- 冻结门禁清单（全部满足才允许从 DRAFT 冻结为 v1.2）：
  1. `requirements.lock` 含锁定 yt-dlp **与 psutil** 且本机 venv **物理验证**可导入（`REQ-046`）；
  2. FFmpeg/ffprobe 物理可用验证（`YUANZHIKU_FFMPEG_BIN`/`YUANZHIKU_FFPROBE_BIN` 或 PATH）；
  3. 真实平台独立验收完成（B站 + 抖音，手工、脱敏摘要；失败率如实登记）；
  4. Cookie 治理审计：代码审计 + T-VID-003 负面用例证据（单通道）；静态证据覆盖"URL 原文不落入备份/导出/日志/审计正文"（审核 F-11 细化）；
  5. T-VID-004 合成集成通过（全程不触网真实平台；回环代理记录断言全部出站 ⊆ 测试注册表）；
  6. **外联域控制负向验证**（审核 F-11 新增）：合成服务器返回重定向至非白名单域，断言下载拒绝且无该域出站请求；注册域清单变更须过本项；
  7. T-VID-003 单元通过；T-VID-001/T-VID-002/T-BACK-001 等既有回归不劣化；
  8. **独立审核报告已出具且阻断项已解决、主要项已裁决**（审核 F-10 新增）。
- **release 保持 blocked**：冻结 v1.2 与 archive-local acceptance 均不改变发布门禁；release_readiness 仍为 blocked，待独立 release_management 判定。

## 11. 实施任务分解（每步门禁与回滚）

| 步骤 | 内容 | 门禁 | 回滚 |
|---|---|---|---|
| 1 依赖锁定 | `backend/requirements.lock` 追加 `yt-dlp==<锁定版本>`、`psutil==<锁定版本>` | 现有 10 包零漂移；venv 物理安装/导入成功；许可证确认（Unlicense/MIT） | 删除追加行、重建 venv |
| 2 端口 | `ports/media.py` 增加 `MediaDownloaderPort`/`DownloadedVideo`/三异常（`use_cookie: bool` 签名） | 导入测试 + 类型检查；不触碰现有 `MediaAnalyzerPort` | 删除新增块 |
| 3 适配器 | 新建 `adapters/downloader.py`：`YtDlpDownloader` + 回环过滤代理（标准库实现）+ 注册域清单模块 | T-VID-003 适配器子集（断路器/取消/Cookie 参数装配/代理拒绝未登记域） | 删除新文件 |
| 4 作业 | `services/jobs.py` 增加 `video_download` 分支与异常映射、staging/Cookie 拷贝清理、代理启停；`imports.py` 复用 `_persist_ingest`/`create_ingest`（source_type=video_link, job_kind=video_analyze, audit_event=video_download） | T-VID-003 作业子集；成功自动入队 video_analyze 断言；失败无半成品 source | 删除分支，行为回 v1.1 |
| 5 API | `domain/models.py` `DownloadLinkRequest` + `main.py` 三端点 + capabilities `downloader` 节 + **CORS `allow_methods` 增加 `"DELETE"`**（`main.py:211-217`） | T-API-001 扩展（TestClient + `/openapi.json` 覆盖新端点；错误码表逐条命中）+ T-UI-001 跨源 DELETE 预检断言 | 删除路由与模型，还原 CORS 列表 |
| 6 前端 | `App.tsx` 表单/设置页/过滤/徽标/`jobLabel` 新增 `video_download: '链接下载'`（7.8 清单） | `npm ci` + 构建通过；T-UI-001 扩展（表单提交、Cookie 开关状态、联网告知） | 还原对应 JSX 块 |
| 7 设置/目录/迁移 | `SettingsUpdate` 三字段、`DataPaths.download`、`SourceType.video_link`、`video_download_provenance` 表（SQLite `initialize()` 版本 7 块 + PostgreSQL Alembic `008_...py`）+ `EXPORT_TABLES`/`BACKUP_TABLES` 清单追加（`sqlite.py:139-149`） | 枚举兼容断言；新 settings 键 GET/PUT 往返；老库幂等补表验证；导出 records.json 含 provenance 行 | 删除表定义/清单条目/迁移文件（新表无既有数据） |
| 8 文档 | 本文档冻结版 + 第 13 章同步清单各文件更新 | REQ-* 编号与实现 file:line 交叉核对一致；决策 7/8 与门禁对齐 | 文档 git 层面可整体还原 |
| 9 测试 | T-VID-003/004 落地；T-VID-005 手工验收执行；独立审核报告出具 | 四角色双件报告按 `report-schema-v1.json` 归档；冻结门禁 8 项全绿 | 测试用例独立于实现可保留或撤回 |

## 12. 回滚与兼容

- v1.1 现有行为不变：本地视频导入/分析、播放、外部卡（含抖音字面卡）、备份/导出/再导入、作业语义全部不变；`REQ-031` 例外仅限 `REQ-047`/`REQ-047a` 通道，外部卡/解析器/检索/导入/AI 端口对该例外一无所知。
- 下载器不可用/未配置时降级：`capabilities.downloader.enabled=false` → 前端禁用链接表单并显示引导文案；`POST /videos/link` → `503`；已入队而运行时工具消失 → 作业 `blocked`（可安装工具后从作业页重试，对齐视频分析行为）。
- 数据迁移可逆性：`video_link` 枚举与 `video_download` 作业 kind 无 schema 约束、无需迁移；provenance 表为一次性 schema v7（SQLite）/ Alembic 008（PostgreSQL），新表无既有数据，删除建表语句与 `EXPORT_TABLES`/`BACKUP_TABLES` 条目即回退；新增 settings 键删除即回退默认；`source_type=video_link` 记录走现有生命周期（软删/恢复/purge），无专用清理路径；cookies.txt 为普通文件，删除即回退；回环代理随作业生命周期关闭，无长驻端口。

## 13. 文档同步清单（冻结时逐文件更新）

- `docs/requirements.md`：REQ-015/031/043/044 替换为第 4 章修订文本；新增 REQ-047（9 条）/REQ-047a（5 条单通道）；REQ-030 保持并注明不变。
- `docs/threat-model.md`：表尾追加第 5 章 5 行；**既有"外部平台合规"行加注"REQ-031 例外仅限 REQ-047/047a 通道，外部卡/解析器/检索/导入/AI 端口不受影响"**（测试 F-10 修复，避免"仅显式浏览器打开"与下载通道并存的歧义）。
- `docs/api-contract.md`：videos 区域加 `/videos/link`；settings 区域加 `/settings/download-cookie`；新增 6.2 字段表（`use_cookie`）、6.4 错误码、6.3 capabilities `downloader` 节（`cookie_file_available`）；"本地视频"节补注链接获取与接口不接收 URL 的差异说明（本地导入路径不变）。
- `docs/acceptance-matrix.md`：追加第 9 章新行（T-VID-005 不写入"自测标识"列）。
- `docs/test-plan.md`：追加 T-VID-003/004/005 三行；T-API-001/T-UI-001 补 DELETE 预检断言说明。
- `docs/dependency-installation.md`：yt-dlp 与 psutil 安装及 `requirements.lock` 锁定说明；版本评估纪律（手动评估更新、绝不自动升级）；FFmpeg/ffprobe 段落注明同时服务分析与下载，并**为职责句加限定**（测试 F-11 修复）："ffmpeg/ffprobe 自身不承担联网下载职责；链接下载由 yt-dlp 受限通道单独承担"。
- `docs/operations-and-recovery.md`：下载作业 blocked/failed 运维语义；`data/state/download` 的 Cookie 文件删除/覆盖说明；备份/导出/reimport 显式排除 `data/state/download`（配合 `backend/app/services/transfers.py:262` 的 manifest `exclusions` 列表更新）；回环代理作业级生命周期说明；日志纪律不变。
- 代码内关联更新（实施步骤 1-7）：`transfers.py:262` manifest `exclusions` 增加 `"state/download"`（归档构建本身只白名单写入 `state/knowledge.db`、`records.json`、manifest 与 artifacts（`transfers.py:224-268`、`transfers.py:95-102`），从不遍历数据根，`data/state/download` 天然不入档；此行是显式声明与回归锚点）。

## 14. 决策记录（2026-08-13 已拍板）

1. **Cookie 来源：双通道**。cookies.txt 显式导入为主通道，`--cookies-from-browser <edge|chrome>` 为备选通道，提交时由用户显式选择；浏览器直读仅在作业进程内、不落盘、失败不静默回退（见 REQ-047a）。实现与测试面因此翻倍，两条通道的治理规则已在 4.4 节分别写明。
   > **2026-08-13 二次决策（追加，不覆盖原文）**：改选**仅 cookies.txt 单通道**，原因＝独立审核发现浏览器直读暴露整个浏览器 Cookie 库（审核 F-06）且"不落盘"依赖 yt-dlp 内部行为、超出调用方可控范围（审核 F-05）；浏览器直读通道自本稿删除，REQ-047a 缩为单通道条款（见决策 8）。原文"两条通道的治理规则已在 4.4 节分别写明"随单通道修订失效，现行治理规则见单通道 REQ-047a（4.4 节）。
2. **格式与画质**：限 ≤1080p、remux 为 MP4；需登录/会员才可得的画质按失败处理。
3. **多P/合集**：仅单视频，一个链接对应一个视频。
4. **source_type**：新增 `video_link` 枚举；前端来源类型过滤与徽标同步新增"链接视频"。
5. **抖音失效提示**：通用脱敏提示（"链接失效/平台拒绝，请重新复制分享链接或稍后重试"），不写入平台特征诊断。
6. **合规边界**：已确认仅个人本地使用、不绕过 DRM/会员、不批量、不对外分发；在此基础上可冻结。
   > 边界澄清（审核 F-12，追加）："不对外分发"落地为"产品不提供分发能力；导出遵循既有用户确认纪律（REQ-041），导出的后续使用由用户自担"。
7. **出站白名单＝完整机制**（2026-08-13 二次决策）：白名单由"平台主域"扩展为"平台主域 + 显式登记的 CDN/API 注册域清单"（bilibili 组：`bilibili.com`、`bilivideo.com`、`hdslb.com`、`b23.tv`；douyin 组：`douyin.com`、`iesdouyin.com`、`snssdk.com`、`douyinvod.com`；实施时以锁定 yt-dlp 版本实测清单为准、经人工安全评估与审核门禁后登记，未实证域一律不登记）。下载器经作业内**回环过滤代理**（仅 127.0.0.1、仅作业生命周期）出站，代理逐连接校验注册域并拒绝内网/回环解析目标，重定向链逐跳强制校验；显式 `--proxy` 指向回环代理并清空子进程代理环境变量（等效禁用环境代理）。选择回环代理而非 yt-dlp 库 API 注入 opener：链路层硬强制、与 REQ-002 loopback 哲学一致、不依赖 yt-dlp 内部 API、零新增依赖。
8. **Cookie 通道收敛为仅 cookies.txt**（2026-08-13 二次决策）：端口签名改 `use_cookie: bool`（去掉 cookie_source/cookie_browser），适配器去掉 `--cookies-from-browser`，API 字段、前端通道选择、能力标志（去掉 `cookie_browser_available`）、威胁模型与 T-VID-003 用例同步收敛；浏览器直读通道整体删除。
9. **T-VID-004 测试注入模式豁免**（2026-08-13 上层裁决，审核 N-03 落地）：T-VID-004 以真实 yt-dlp 下载 localhost 合成 fixture 时，仅测试代码路径注入"保留段拒绝豁免"标志——不进生产注册表、生产代码无该分支（fail-closed 语义不变）；豁免只影响回环/保留段解析拒绝，**不影响注册域主机名校验与出站计数断言**。
10. **隧道段例外（2026-08-13 真实链接验收发现，fake-IP 环境兼容）**：验收环境为代理工具 fake-IP 模式（`b23.tv`/`v.douyin.com`/`upos*.bilivideo.com` 等均解析到 `198.18.x` 隧道段，且对公共 DNS 的查询同样被网络层接管），按规范原文本拒绝保留段将阻断全部真实下载。裁定：注册域主机名校验通过的前提下，放行隧道段 `198.18.0.0/15` 与 `28.0.0.0/8`（公网不可路由、本地 TUN 独占）；其余保留段仍无条件拒绝。安全论证见 7.2.1；属安全边界变更，经独立审核门禁后生效。
11. **注册域登记：`365yg.com`（douyin 组，2026-08-14 实测登记）**：真实抖音链接下载实测出站命中 `v95-aw-default.365yg.com`（字节系媒体 CDN），未登记导致代理拒绝、下载失败——即 §15"注册域清单漂移"的预期场景。按维护规则（7.2.1）附实测证据登记 `365yg.com`，经人工安全评估（字节跳动媒体 CDN 域，非第三方）与独立审核门禁后生效。
12. **分辨率档位语义修正（2026-08-14 抖音竖屏实测）**："≤1080p"的后置断言原实现为 `高度 ≤1080`，会拒绝所有竖屏 1080×1920 视频（抖音主流形态）。修正为**短边 ≤1080 且长边 ≤1920**（1080p 档位：横向 1920×1080 与竖屏 1080×1920 均属之；2K/4K 拒绝）。REQ-047.3、7.2、7.3、威胁模型、T-VID-003 用例 5、假设 4 同步修订。

## 15. 遗留、风险与开发子智能体自述假设

遗留与风险：

- 抖音反爬不稳定性：抖音链接成功率不可保证，失败如实登记、绝不伪装通过；反爬升级可能使整条通道周期性失效，属已知平台风险，不进缺陷库。
- **平台条款风险（审核 F-09 修复，如实披露）**：自动化下载可能违反抖音/B站用户协议，法律风险由用户自担；"仅个人本地使用"不豁免平台协议违约风险；产品仅提供受限技术通道（白名单、单视频、限速不绕过、通用脱敏提示），不提供批量或绕过能力。
- yt-dlp 版本锁定策略：`REQ-046` 锁定后**绝不自动升级**；每次候选版本升级须手动评估（变更日志、许可证、行为变化、出站域集合变化），并经 T-VID-003/004 全量回归 + T-VID-005 手工验收后才可更新 lock。
- 真实平台验收不可自动化：平台页面/接口变化、无稳定测试账号、Cookie 依赖用户环境，T-VID-005 只能手工执行且不作为自动化门禁。
- 注册域清单漂移：平台新增/变更 CDN 域会导致下载失败（代理拒绝），需按维护规则（7.2.1）实测登记；这是安全优先的预期行为，不视为缺陷。

开发子智能体自述假设（供测试/审核子智能体核对，非结论）：

1. yt-dlp 与 psutil 的具体锁定版本在实施步骤 1 确定；本文档不预判版本号，仅约束锁定与手动评估更新流程。
2. 下载作业先以 `create_job`（空 source/version）入队、成功后经 `create_ingest`（source_type=video_link、job_kind=video_analyze、audit_event=video_download）落库，是本次核实确认的可行路径（`sqlite.py:665-709`、`sqlite.py:1188-1197`）；实施者若走等价路径须保持相同不变量（失败无半成品 source、成功自动入队）。
3. 下载元数据承载：payload_json 只存脱敏链接（`scheme://host/path`）；出处详情存 `video_download_provenance`（进 EXPORT/BACKUP_TABLES，随导出 manifest 与备份快照并受 hash 校验）；审计事件只记 event_type/entity_id/result（audit_events 无内容列，`sqlite.py:119-121`）。由此备份快照与导出均不含原文 URL 参数、不含 Cookie 内容。
4. 产物校验复用 `LocalFfmpegMediaAnalyzer.probe`（`media.py:133-178`，≤24h 时长与宽高校验）并追加分辨率档位 ≤1080p 后置断言（短边 ≤1080 且长边 ≤1920，决策 12）；格式选择 `-S "res:1080"` 与后置断言双保险保证 ≤1080p 档位。
5. T-VID-004 使用真实 yt-dlp 指向 localhost 合成服务器时在适配器/服务层直调（绕过 API 层 URL 校验——两层控制独立），并注入测试专用注册域清单（仅 fixture 域、仅测试代码注入，不进生产注册表）；回环代理记录断言全部出站 ⊆ 测试注册表。
6. `cookie_file_available`＝`data/state/download/cookies.txt` 存在且 ≤1MB；探测失败一律按不可用处理并给出导入引导。
7. 本文档全部 `file:line` 引用以 2026-08-13 工作区代码为基准核实；冻结前不改动代码，行号持续有效。
8. 注册域清单（7.2.1）为初始登记集；实施时以锁定 yt-dlp 版本实测真实链接的出站域集合逐项比对，实测新增域经人工安全评估与审核门禁后登记，未实证域一律不默认登记。

## 修订记录

- 2026-08-13（本轮）：按独立测试验证报告（11 条）与独立审核报告（17 条）修复并纳入两项人工拍板。
  - 拍板 1 落地：出站白名单改为"平台主域 + 注册域清单"（7.2.1、REQ-047.2、决策 7）；选定回环过滤代理（方案 a）并写明理由与测试；显式 `--proxy` + 清空代理环境变量；重定向链逐跳校验由代理强制。
  - 拍板 2 落地：Cookie 收敛为仅 cookies.txt 单通道——REQ-047a 重写为 5 条单通道条款；端口签名改 `use_cookie: bool`；适配器去掉 `--cookies-from-browser`；API 字段、capabilities、威胁模型、T-VID-003 用例、§2/§3 同步收敛；决策 1 追加二次决策并新增决策 8。
  - 测试 F-01 + 审核 F-07：REQ-047.5 承载机制落地——新增 `video_download_provenance` 表（7.4），进 EXPORT/BACKUP_TABLES，脱敏变换定义为 `scheme://host/path`，payload 只存脱敏链接；用例 7 改"存在性 + 脱敏"双重断言。
  - 测试 F-02：CORS `allow_methods` 增 `DELETE`（6.1、步骤 5）+ T-API-001/T-UI-001 DELETE 预检断言。
  - 测试 F-03 + 审核 F-03（断路器归因更正）：不再声称复用 `_run` 的无进展能力；无进展断路器新实现（staging 总量滚动窗口）；psutil 锁定进 lock（内存断路器不再静默失效）。
  - 测试 F-04：T-VID-004 localhost 冲突闭合（适配器/服务层直调 + 测试专用注册表注入）。
  - 测试 F-05：`jobLabel` 增 `video_download: '链接下载'`（7.8）。
  - 测试 F-06：并入断路器重写（REQ-047.3 引用更正为 REQ-016/REQ-033）。
  - 测试 F-07：`-S "res:1080"` + probe 高度 ≤1080 后置校验双保险。
  - 测试 F-08：T-VID-003 增 rights 必填与多P/需登录/会员/付费/DRM 失败用例（REQ-047 增第 9 条，审核 F-08 一并修复）。
  - 测试 F-09：T-VID-005 移出"自测标识"列，标注 acceptance 角色登记。
  - 测试 F-10：威胁模型既有"外部平台合规"行加注 REQ-031 例外（写入同步清单）。
  - 测试 F-11：dependency-installation 职责句加限定。
  - 审核 F-02：b23.tv 显式放行（归属 bilibili）+ 重定向终点由代理限制在 bilibili 组。
  - 审核 F-04：并入决策 7（--proxy 覆盖 + 清空环境变量）。
  - 审核 F-05/F-06/F-13：随单通道决策自动消解（浏览器直读整体删除）。
  - 审核 F-09：ToS/条款风险如实披露（§15）。
  - 审核 F-10：独立审核角色与冻结门禁补入（§10；author_role 扩展说明）。
  - 审核 F-11：冻结门禁增"外联域控制负向验证"；Cookie 治理审计细化。
  - 审核 F-12：分发边界澄清（§2、决策 6 追加）。
  - 审核 F-14：2GB 检查对象明确为 staging 目录总量。
  - 审核 F-15：取消清理明确进程树终止（含 ffmpeg）+ T-VID-003 覆盖。
  - 审核 F-16：REQ-031 例外条款点名 REQ-047a 并复禁密码/登录凭据。
  - 审核 F-17：UI 表单增联网告知文案。
- 2026-08-13（第二轮复核/终审收尾）：按第二轮验证报告（N-1/N-2/N-3）与独立终审报告（N-01/N-02/N-03）修订。
  - 测试 N-1：决策 1 追加说明补注原文"两条通道的治理规则已在 4.4 节分别写明"随单通道修订失效（原决策文本保留不动，现行规则指向单通道 REQ-047a）。
  - 测试 N-2：7.5 新增 `download_no_progress_seconds`（ge=10、le=86400、默认 10；下界协调说明：观察窗口间隔而非总超时，先例 `video_max_frames ge=1`/`max_retry_attempts ge=0`）；阈值语义＝连续两个观察窗口内 staging 目录总字节数无增长且子进程无输出；§7.2 与 T-VID-003 用例 3 同步。
  - 测试 N-3：provenance 行与 `create_ingest` 同事务写入 + `source_id UNIQUE` 去重；重试不变量（失败不残留 source、重试不重复创建 source/version）写入 7.3/7.4 与 T-VID-003 用例 7。
  - 审核 N-03（上层裁决）：T-VID-004 采用测试注入模式（仅测试代码路径注入保留段拒绝豁免、不影响注册域校验）写入夹具纪律；新增决策 9。
  - 审核 N-01：回环代理 DNS 重绑定表述加固——resolve-then-connect（不得二次解析）、连接后对端复核、拒绝 IP 字面量（含 IPv6 括号形式）、TOCTOU 残留风险与缓解如实说明（7.2.1）。
  - 审核 N-02：补两行加固句——代理启动失败 → 作业 blocked（fail-closed，绝不直连回退，7.2.1/7.3）；FFmpeg 仅作本地合并/remux、绝不作为网络下载器（网络取流仅 yt-dlp 经代理，7.2）。
- 2026-08-13（真实链接验收环境发现）：B站/抖音真实下载在 fake-IP 网络环境被代理的保留段拒绝阻断（`b23.tv`→`198.18.0.55` 等，公共 DNS 查询同样被网络层接管）。裁定新增**隧道段例外**（决策 10）——注册域主机名校验通过时放行 `198.18.0.0/15` 与 `28.0.0.0/8`，其余保留段仍无条件拒绝；REQ-047.2、7.2.1、T-VID-003 用例 10 同步修订，属安全边界变更、经独立审核门禁。
- 2026-08-14（抖音真实下载实测）：douyin 组注册域清单增补 `365yg.com`（媒体 CDN，实测 `v95-aw-default.365yg.com`；决策 11），7.2.1 表格同步。
- 2026-08-14（抖音竖屏实测）：分辨率档位语义修正（决策 12）——"高度 ≤1080"后置断言改为"短边 ≤1080 且长边 ≤1920"，竖屏 1080×1920 放行；REQ-047.3、7.2、7.3、威胁模型行、T-VID-003 用例 5、假设 4 同步。
- 2026-08-15（B站真实链接排障）：bilibili 组注册域清单增补 `bilivideo.cn`（MCDN 镜像媒体域，实测 `xy119x188x120x16xy.mcdn.bilivideo.cn:8082`；决策 13），7.2.1 表格同步；修复回环代理 `_bidirectional_relay` 的固定时长强拆缺陷（join 带超时导致活跃传输在 ~2×IO 超时后被拦腰切断，大文件下载必现）——转发时长改由套接字 IO 超时与对端关闭驱动，无绝对上限。
- 2026-08-15（按平台 Cookie 库）：REQ-047a 修订——单文件 `cookies.txt` 改为按平台 `cookies/<platform>.txt`（bilibili/douyin 各一份、每平台 1MB、覆盖导入、按平台幂等删除）；下载与探测按链接平台自动选用对应文件，该平台未导入却勾选 → 422，绝不静默回退或跨平台借用；能力接口 `cookie_file_available` 改为按平台 `cookies` 映射；设置端点改为 `POST/DELETE /settings/download-cookies/{platform}`；遗留单文件启动时按注册域分拣迁移（内容不打印不落日志）。安全不变量（不进 DB/日志/备份/导出/reimport、作业 staging 拷贝即删）全部保留；§4.4 与 T-VID-003 同步。
