# 源知库冻结需求

本文将项目基线拆为可追踪的 `REQ-*` 标识。除明确标为开发阶段限制的项目外，所有条目均为冻结需求。

## 产品、网络和运行时

- `REQ-001`：系统是中文、个人单用户、本地证据知识系统；提供浏览器 UI、FastAPI REST API `/api/v1`、OpenAPI、React + TypeScript + Vite 前端，作业使用 REST 轮询。
- `REQ-002`：默认数据根为 `E:\源知库\data`，支持环境变量覆盖；首次启动选择并持久化自定义本地端口至 `data\state`；每个数据根仅一实例，只绑定 IPv4 `127.0.0.1`。
- `REQ-003`：Windows 启动脚本启动应用并打开浏览器；不使用本地 HTTPS，不对应用/磁盘/备份加密，无遥测；日志不得含内容、来源路径、密钥令牌或请求体。
- `REQ-004`：模块边界为 sources、artifacts、documents、evidence、knowledge、search、jobs、taxonomy、lifecycle、external_cards、settings；框架、存储、解析、数据库在 ports/adapters 后。

## 导入、版权和解析

- `REQ-010`：支持本地 PDF/DOCX/Markdown/TXT 与粘贴 UTF-8 文本/Markdown（不超过 10MB）；文件最大 2GB，导入前/中保障至少文件两倍空间及完成后至少 10GB 空闲。
- `REQ-011`：文档/文本导入必须声明权利：owned、authorized、permitted、open_license、other；原始字节流式写入、计算 SHA-256、保存为不可变内容寻址对象；不保存原始本地完整路径。
- `REQ-012`：相同哈希 artifact 只保存一次但可有不同 source；不同内容默认新 source；支持显式关系 new_version_of、revision_of、related_to、user_declared_same_work。
- `REQ-013`：Docling 是首选解析器。其模型只可按预批准、锁定版本/来源/许可证/哈希且无需登录或附加条款的官方默认模型锁文件直接公开下载，缓存于 `data\models`；禁止静默云回退。
- `REQ-014`：Docling 缺失/不可用时记录原因并本地回退 pypdf、python-docx 或原生 Markdown/TXT；扫描/无文本 PDF 保留并标记 awaiting_ocr；加密、损坏、不可解析 PDF/DOCX 在权利确认后保留，作业 blocked/failed，绝不询问、猜测或储存密码。
- `REQ-015`：视频支持本地 MP4/WebM 导入（同样受 2GB、容量预检、权利声明、不可变 SHA-256 artifact、备份和永久清理规则约束）与受限链接获取（`REQ-047`）；不保存原始本地完整路径。链接获取仅接受白名单平台、仅由用户显式提交，下载经无 shell 受限子进程完成并进入同一 artifact/分析/证据链生命周期；本地导入与视频分析仍禁止 shell、网络、URL 获取和静默云回退。视频通过本机显式安装的 FFmpeg/ffprobe 探测元数据并在独立 staging 中有限时间采样 JPEG 关键帧。
- `REQ-016`：视频分析使用可配置的总超时、内存、工作目录磁盘及最大关键帧数断路器；关键帧采样为场景感知混合策略（`REQ-053`），采样配置身份 `config_hash` 全参数化（`ffmpeg-local:2:<策略>:<密度秒>:<锚点>:<缩放宽>:<JPEG 质量>:<帧数上限>` 的 SHA-256），任一采样参数变更即构成新分析身份；分析结果先完成原视频与全部接受派生帧的 hash 校验再持久化（verify-before-persist），多份历史分析按版本列出并显式标记当前项，detail 仅对 complete 版本返回当前 analysis。视频元数据以 `video_metadata` locator 写入可引用 extraction/evidence；转写与摘要产物仅可使用带毫秒起止范围的 `video_time_range` locator。
- `REQ-017`：视频转写和内容摘要经可插拔媒体 AI 端口与作业接口提供，提供方由用户显式配置（双组模型与级联见 `REQ-051`，出站校验与凭据隔离见 `REQ-052`）；默认两组均关闭、无网络流量，作业明确 blocked，不伪造文本、摘要或 evidence。凭据、原路径、媒体内容和 AI 原始响应不得进入数据库、API、导出或日志；AI 调用错误一律脱敏为不含 URL、密钥或响应正文的中文短消息。
- `REQ-051`：媒体 AI 为两个相互独立的显式配置分组：语音转写（provider/base_url/model/key）与理解摘要（provider/base_url/chat_model/vision_model（可选）/key），经 `GET/PUT /settings/ai` 管理、`POST /settings/ai/test` 做连通性检查；分组未启用或无 key 时对应作业 blocked，两组全关时行为与未配置完全一致。转写经本地 ffmpeg 提取 16kHz 单声道音频并按 ≤24MB 分块调用转写端点，产出 kind=transcription representation 与逐段 `video_time_range` 证据（可检索）；摘要为两层级联——先按确定性规则（覆盖率/静音阈值）加约束 JSON 的 LLM 判定（置信阈值 0.6）评估转写完整性，完整走 tier1 纯文本摘要，疑似不完整或 `force_tier2` 走 tier2（逐帧画面理解、强制画面文字提取后增强摘要）；tier2 未配置但被判需要时在摘要中标记 visual_gap；AI 建议（领域/体裁强制收敛到分类清单、标签自由）在摘要作业成功时自动写入来源元数据——只填空缺：领域/体裁仅在当前为空时写入、标签取并集合并、用户已填字段绝不覆盖，摘要建议标记含 `applied` 以区分旧摘要（旧摘要仍可显式采纳）；`ai_auto_pipeline` 总开关（默认开，经 `PUT /settings/ai` 调整）启用且对应分组已配置时，视频分析成功自动串联转写→摘要，文档/粘贴解析成功自动入队 `source_classify` 作业（正文截断至前 8000 字符发理解组分类并按同一只填空缺规则写入，图片不分类）；`source_classify` 与转写/摘要同为附加产物，未配置理解组时 blocked、失败/取消不降低版本与来源状态（`REQ-033a`）。
- `REQ-052`：AI 端点 base_url 经统一校验：仅 HTTPS、仅公网主机、无 userinfo、不超过 2048 字符，拒绝消息不含 URL 内容；API key 只保存在 `<data-root>/state/ai/credentials.json`（原子写入），绝不进入数据库、备份、导出、日志或任何 API 出参（仅回显 has_key 与掩码提示）；音频分块、关键帧图片与转写文本在用户逐视频显式触发或 `ai_auto_pipeline` 自动串联时发往所配置端点，文档/粘贴正文亦在自动分类开启且理解组已配置时发送（截断至前 8000 字符，`REQ-051`），默认关闭即零出站流量；AI 调用错误一律脱敏为不含 URL、密钥或响应正文的中文短消息。
- `REQ-053`：关键帧采样为场景感知混合策略：ffmpeg 场景检测（阈值 0.3）输出候选点，等间隔槽位在半个槽距容差内吸附最近未使用场景点，吸不到保留等间隔位置；首末槽锚定约 5%/95%，短视频（<120s）至少 3 帧，长视频按 120 秒密度并受 video_max_frames 封顶；黑帧（灰度均值 <16）拒绝并按候选序列（未使用场景点、±5% 平移）重试；每帧持久化采样来源 reason（scene/even，数据库 schema v8）与采样参数（随分析元数据），帧宽高为真实像素值。

## 证据、知识和检索

- `REQ-020`：不可变证据链为 source -> content version -> artifact -> representation -> evidence -> citation -> knowledge；evidence 必含 artifact sha256、content-version id、representation/extraction id、parser/config hash、类型化 locator、规范摘录 hash。
- `REQ-021`：PDF locator 为页码和字符范围/可得坐标；DOCX 为结构/序号和字符范围；MD/TXT 为标题段落及 UTF-8 字节/行范围；检索块不是证据。
- `REQ-022`：手工知识类型为 fact、opinion、instruction、case、citation、unverified；发布的实质事实陈述需有效证据，否则只能 draft/unverified。手工编辑创建新 manual representation，引用清楚显示人工修订及原始视图。
- `REQ-023`：引用显示来源标题/状态、locator、可展开 300 字上下文和定位动作。
- `REQ-024`：检索覆盖标题、作者、备注、全文、已发布知识、外部卡元数据；分类（领域/体裁）与标签 token 不进入全文语料，视频容器元数据模板（`ffmpeg-local` representation）不进入全文语料；默认当前完整版本，历史/不完整须显式高级选项；仅中文短语/关键词/子串匹配，不宣称语义检索；排序 relevance、导入/更新、标题，过滤来源类型、领域（重复参数、OR 语义、`_none` 哨兵匹配未分类）、体裁（单值、`_none` 匹配无体裁）、标签、作者、来源/导入日期、语言、处理状态与主题（`topic_id`，仅过滤来源分支）。
- `REQ-025`：分类为领域×体裁双字段（`REQ-050`），取代旧固定分类清单；旧值按映射迁移（technical/business/education/news→领域，interview/podcast/document→体裁，未知值忽略；多体裁遗留行全部保留，下次编辑时强制单选）；自由标签；手工主题可关联多来源、可重命名、删除与移除成员，不复制或取得所有权；来源关系可创建与删除，检索与库支持按主题过滤；用户可显式声明 user_declared_same_work，系统按相同 artifact 哈希或规范化标题给出确定性候选。导入时的领域/体裁选择为可选折叠（默认收起可跳过）；配置理解组后 AI 可按 `REQ-051` 自动分类（只填空缺）。
- `REQ-050`：分类体系为两个独立字段：领域（technical、business、education、news、entertainment、life、other）多选、可空；体裁（document、lecture、interview、podcast、review、recording、other）写入时最多一项、可空。清单以后端为唯一来源，经 `GET /taxonomy` 下发中文标签，前后端不各自硬编码；数据库 schema v9 与可移植归档 schema v8 共用同一旧值拆分映射；导入与元数据更新接口以 `domains`/`genres` 取代 `categories`。

## 外部卡、作业和生命周期

- `REQ-030`：外部卡仅接受用户输入的一般 URL 元数据，不抓取、解析、预览或请求，且不是事实证据。抖音仅接受用户输入、原样保存的 HTTPS `douyin.com` 或其子域 URL；浏览器仅打开原 URL 并提示中文人工定位。（v1.2 保持不变：外部卡仍仅字面保存 URL 元数据，不抓取、不解析、不请求，不是事实证据。）
- `REQ-031`：抖音绝对禁止下载、抓取、内容提取、iframe、cookie/密码/认证、自动化、缓存、代理、逆向和伪造时间参数；任何 HTTP client/worker/parser 均不可接受抖音 URL。**唯一例外是 `REQ-047` 与 `REQ-047a` 定义的受限链接获取下载通道**——它只由用户显式提交、只服务白名单域、只在独立 staging 内工作；外部卡、文档解析器、检索、文档导入、媒体 AI 端口对该例外一无所知。**密码/登录凭据不在例外之列，任何情况下不使用、不保存。**
- `REQ-032`：作业状态 queued、running、retry_wait、succeeded、failed、blocked、cancelled；持久化作业/attempt/audit；单 worker，手动导入/重试/恢复优先于 FIFO；有进度、心跳、最小日志、有限重试、协作取消/重跑，按 version/artifact/config 幂等。
- `REQ-033`：可配置解析安全断路器，默认 24 小时，覆盖内存/磁盘/无进度；仅在输出、证据、索引验证后作业成功。
- `REQ-033a`：视频 `video_analyze` 沿用持久化租约、心跳、取消、优先级和有限重试，依次探测、抽帧、持久化和校验；`video_transcribe`、`video_summarize` 在 AI 未配置时仅阻止该作业，不能降低已经完成的视频版本或来源状态。
- `REQ-047`：受限链接获取（视频下载）通道：
  1. 平台白名单 `bilibili`、`douyin`；URL 严格校验：HTTPS、主域或子域匹配白名单（bilibili 组显式含 `b23.tv` 入口短链）、无内嵌凭据、长度上限；未知或不支持的 URL 拒绝，拒绝消息不含 URL 内容。
  2. 下载器为锁定版本的 yt-dlp（`REQ-046`，写入 `requirements.lock`），仅以无 shell 子进程运行（`shell=False`）、stdin 关闭、忽略用户级配置文件与缓存；**出站一律经作业内回环过滤代理**（仅监听 127.0.0.1、仅作业生命周期内存活），代理逐连接校验目标主机名必须命中平台注册域清单（7.2.1），未登记域、以及解析到回环/内网/保留段的地址直接拒绝；显式 `--proxy` 指向回环代理并清空子进程代理环境变量（等效禁用环境代理）；重定向链的每一个新连接都在代理处强制校验，无静默重定向跟随外平台。
  3. 下载仅写入独立 per-job staging：总超时、内存、磁盘断路器沿用 `REQ-016` 纪律；无进展（静默期）断路器按 `REQ-033` 语义**新实现**（staging 目录总量滚动窗口无增长判定，见 7.2）；全部支持协作取消；下载产物经 ffprobe 校验为合法 MP4/WebM、时长合法、**高度 ≤1080**、≤2GB 且通过容量预检后，流式写入不可变 SHA-256 artifact。
  4. 提交链接时权利声明（owned/authorized/permitted/open_license/other）必填，与本地导入一致（`REQ-011`）。
  5. 下载出处记录（provenance）：平台、脱敏链接（`scheme://host/path`，去 userinfo/query/fragment）、yt-dlp 版本、所选格式、是否使用 Cookie 写入 `video_download_provenance` 表（进 `EXPORT_TABLES`/`BACKUP_TABLES`，随导出 manifest 与备份快照携带并受既有 hash 校验）；审计事件仅记 `event_type`/`entity_id`/`result`；下载作业 `payload_json` 只存脱敏链接；Cookie 内容、原始请求头、下载响应体绝不进入数据库、日志、API 响应、备份或导出（`REQ-042`）。
  6. 新增作业 kind `video_download`：持久租约、心跳、取消、优先级与有限重试；成功后在作业内创建 source/content version/artifact 并自动入队 `video_analyze`；分析失败、取消或 blocked 不降低已完成下载（对齐 `REQ-033a` 精神）。任何失败路径不残留半成品 source；staging 与 Cookie 拷贝作业结束即清理。
  7. 仅单视频；多P/合集/直播/需登录才可见的内容按失败处理，失败消息脱敏。
  8. 下载作业在 yt-dlp 或 FFmpeg 缺失时明确 blocked；因反爬、链接失效、平台拒绝等外部原因失败时状态 failed 且可有限重试，绝不静默切换来源或平台。
  9. 会员/付费墙/DRM 内容一律拒绝：不利用 Cookie 获取超出用户自身已购权益的内容；付费专享、DRM 加密流按失败处理（通用脱敏提示）；仅按平台公开免费档位（≤1080p）选择格式，高度/码率过滤无法区分会员画质时按失败处理。
- `REQ-047a`：Cookie 生命周期（按平台 Cookie 库）：
  1. **按平台 cookies 文件**：用户可显式导入浏览器导出的 cookies.txt（Netscape 格式），按平台分别保存于 `data/state/download/cookies/<platform>.txt`（platform 限白名单平台 bilibili/douyin），每个文件大小上限 1MB；同平台重复导入覆盖旧文件；支持按平台一键删除（幂等）。遗留单文件 `cookies.txt` 在启动时按注册域标签边界匹配自动分拣迁移到对应平台文件并删除旧文件，分拣不打印、不落日志任何 Cookie 内容。
  2. 提交下载时用户显式选择"使用已导入 Cookie"（`use_cookie: true`），仅用于该次下载；后端按链接平台自动选用该平台已导入的 Cookie 文件，无需用户切换；该平台未导入 Cookie 文件却选择使用 → `422`，绝不静默回退到无 Cookie 下载、绝不改用其他平台的 Cookie。
  3. Cookie 内容绝不进入数据库、日志正文、API 响应、备份（`REQ-040`）、导出 ZIP 与 reimport（`REQ-041`）、审计事件；备份/导出/再导入规则显式排除 `data/state/download` 路径。
  4. 使用 Cookie 时把该平台的 Cookie 文件**拷贝**注入该作业 staging，作业结束（无论成败）立即删除拷贝；作业运行期间不触碰、不修改原文件。
  5. 能力接口暴露按平台的 `cookies` 状态映射；某平台不可用时前端在该平台的开关上禁用并给出导入引导。
- `REQ-047b`：链接元数据探测（`POST /videos/link/probe`，REQ-047 通道的只读子能力）：
  1. 与 REQ-047 完全同约束：同平台白名单与 URL 严格校验（拒绝消息不含 URL 内容）、同请求级回环过滤代理（仅监听 127.0.0.1、逐连接校验注册域、仅请求生命周期存活、结束即销毁不留存端口）、同无 shell 子进程（shell=False、stdin 关闭、忽略用户级配置与缓存、清空子进程代理环境变量、显式 `--proxy`）。
  2. yt-dlp `--skip-download` 只取元数据不下载媒体字节：仅捕获 `title`/`uploader`（缺省回退 `channel`）/`upload_date`（YYYYMMDD → YYYY-MM-DD，非法 → null）；清洗上限 title ≤500、author ≤300（去控制字符与换行）；整体超时 30 秒，stdout 有界上限。
  3. 只读：不入队作业、不写任何表、不持久化任何内容；成功返回 `{title, author, source_date}`（均可空）供链接获取表单回填。
  4. Cookie 规则同 REQ-047a：`use_cookie: true` 未导入 cookies.txt → `422`，绝不静默回退为无 Cookie 探测；Cookie 内容绝不进入数据库、日志、API 响应。
  5. downloader 不可用（yt-dlp/FFmpeg 缺失、代理启动失败）→ `503`；探测失败（反爬、链接失效、平台拒绝、超时）→ `502` 通用脱敏消息。
- `REQ-048`：图片导入（`POST /imports/image`）：本地 jpg/jpeg/png/webp 图片作为新来源导入，与文档/视频同一纪律——后缀白名单、权利声明必填（`REQ-011`）、2GB 上限与容量预检、原始字节流式写入不可变 SHA-256 artifact、不保存原始本地完整路径，标题缺省回退文件名 stem 或"未命名图片"。导入入队轻量 `image_analyze` 作业：仅用 Pillow 本地读取尺寸/格式与 EXIF（拍摄时间、Artist、ImageDescription 等常见字段，取不到即为空），不做 OCR、不做 AI 内容描述、无任何网络调用；断路器使用独立设置项 `image_timeout_seconds`/`image_memory_limit_mb`/`image_disk_limit_mb`（缺省与视频断路器一致），解码前按像素估算内存护栏；损坏或无法解码的图片作业 failed，消息通用脱敏。元数据以 `image_metadata` locator（宽高/格式/拍摄时间，由域工厂校验）写入 extraction representation 与 evidence；representation 中文摘要（尺寸、格式、拍摄时间等）进入检索索引，检索仅命中这些元数据，不宣称理解图片内容。零新数据库表：artifact、representation、evidence 复用既有结构，备份、导出、还原与检索自动覆盖。仅在 artifact SHA-256 校验通过后作业成功。
- `REQ-049`：导入预填（`POST /imports/prefill`）：用户选择文件或粘贴文本后，端点只读识别元数据并返回可空建议字段 `title`/`author`/`language`/`source_date`；接受 multipart `file`（后缀白名单 `.pdf/.docx/.md/.markdown/.txt/.jpg/.jpeg/.png/.webp`，图片本阶段仅以文件名 stem 建议标题）或 multipart `text` 字段，两者都空 → `422`；文件上限 20MB、文本上限 1MB。端点不读写数据库、不触碰数据根、不发起任何网络调用；损坏/加密文件返回全空建议而非报错；拒绝消息不含文件内容；权利确认（`REQ-011`）、分类与标签不参与预填。
- `REQ-034`：软删除无限期；显式永久删除才移除 source versions/derived/index/cache，仅在无 active source 引用时删除 artifact；永久删除后最小无内容审计保留 1 年。

## 备份、导出和部署

- `REQ-040`：每日首次成功启动后排低优先级备份（非 Windows 任务）；保留最近 30 个成功日备份。备份含 DB、artifacts、derived/evidence、settings、manifest，排除模型缓存、staging、日志正文；快照一致并 SHA-256 验证。
- `REQ-041`：还原只能到新数据根，绝不覆盖；导出完整可移植 ZIP + JSON manifest，含原 artifact、派生数据、逻辑记录和 sha manifests，排除密钥/凭据/私密权利备注/cookies/原路径/日志正文；UI 必须确认导出。再导入校验 schema/zip/manifest/hash/relations，不覆盖；同 ID 不同链拒绝并报告。
- `REQ-042`：摄取/导出/还原均校验 hash，支持手工完整校验和空闲抽样；操作日志按日保留 30 天且不含内容/路径/令牌。视频分析记录、关键帧 artifact 及其引用也必须进入备份、导出、还原和再导入一致性校验。
- `REQ-043`：所有端点位于 `/api/v1`：health/capabilities、settings、sources/relations/rights/metadata、imports/paste、videos/local、videos/link、videos/{id}/stream/frames/transcribe/summarize、settings/download-cookies/{platform}、settings/ai（双组媒体 AI 配置与连通性检查）、docs/representations/evidence/citations/knowledge、search、taxonomy/tags/topics（主题支持重命名/删除/移除成员）、external/douyin cards、jobs、delete/restore/purge、backup/restore、export/reimport；类型稳定并有 OpenAPI。`/capabilities` 增加 `downloader` 节。其余不变。
- `REQ-044`：UI 页面包括库、导入、来源详情、检索、作业、设置、备份/还原/导出、外部卡；极简中文；导入页为统一智能识别入口：单一大输入框加文件选择，粘贴文本/链接或选择文档（PDF/DOCX/MD/TXT）、图片（JPG/PNG/WebP）、视频（MP4/WebM）文件后自动识别类型并路由到对应导入流程；粘贴命中白名单平台（哔哩哔哩含 b23.tv/抖音）的 HTTPS 视频链接（含抖音分享口令等混合文本，自动提取其中的平台链接并把剩余文案带入备注）即进入"链接获取"提交表单：平台按域名自动判定、URL 输入、权利声明必选、Cookie 开关（按识别出的平台使用该平台已导入的 Cookie 文件，含该平台可用状态提示）、联网告知（提交即向所选平台服务器发起下载请求）、"识别链接"按钮按需读取元数据、提交后跳转作业页，可切换为仅保存外部卡；其他 URL 识别为外部卡创建；不做预览、嗅探或解析展示；外部卡页为只读列表，创建统一在导入页进行；视频仅播放本地 artifact 并显示本地元数据/关键帧；PDF 使用隔离只读预览并禁用嵌入链接，仅显式外开；文本安全渲染；目标 Edge/Chrome。
- `REQ-045`：SQLite 默认于 `data/state`，为 PostgreSQL 提供 production adapter/migrations；Docker Compose 运行 web/api/worker/postgres/redis，端口仅 loopback；集成测试只能使用 `tests\runtime\compose-<run-id>`，不得使用日常 data。
- `REQ-046`：依赖仅接受明确开源许可；锁定依赖/模型/镜像版本，不自动升级；建立合成、无版权 fixtures 和完整文档、决策、开发报告。
