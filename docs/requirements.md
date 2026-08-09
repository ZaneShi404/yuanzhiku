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
- `REQ-015`：视频第一版仅支持本地 MP4/WebM（同样受 2GB、容量预检、权利声明、不可变 SHA-256 artifact、备份和永久清理规则约束）；不保存原始本地完整路径。视频通过本机显式安装的 FFmpeg/ffprobe 探测元数据并在独立 staging 中有限时间采样 JPEG 关键帧，禁止 shell、网络、URL 获取和静默云回退。
- `REQ-016`：视频分析使用可配置的总超时、内存、工作目录磁盘及最大关键帧数断路器；仅在原视频和所有接受的派生帧完成 hash 校验后成功。视频元数据以 `video_metadata` locator 写入可引用 extraction/evidence；未来转写仅可使用带毫秒起止范围的 `video_time_range` locator。
- `REQ-017`：视频转写和内容摘要只定义可插拔端口与作业接口；默认 AI 未配置、无网络流量，作业明确 blocked，不伪造文本、摘要或 evidence。外部提供方只能经日后明确配置的适配器接入，凭据、原路径、媒体内容和原始响应不得进入数据库、API、导出或日志。

## 证据、知识和检索

- `REQ-020`：不可变证据链为 source -> content version -> artifact -> representation -> evidence -> citation -> knowledge；evidence 必含 artifact sha256、content-version id、representation/extraction id、parser/config hash、类型化 locator、规范摘录 hash。
- `REQ-021`：PDF locator 为页码和字符范围/可得坐标；DOCX 为结构/序号和字符范围；MD/TXT 为标题段落及 UTF-8 字节/行范围；检索块不是证据。
- `REQ-022`：手工知识类型为 fact、opinion、instruction、case、citation、unverified；发布的实质事实陈述需有效证据，否则只能 draft/unverified。手工编辑创建新 manual representation，引用清楚显示人工修订及原始视图。
- `REQ-023`：引用显示来源标题/状态、locator、可展开 300 字上下文和定位动作。
- `REQ-024`：检索覆盖标题、作者、标签、备注、固定分类、全文、已发布知识、外部卡元数据；默认当前完整版本，历史/不完整须显式高级选项；仅中文短语/关键词/子串匹配，不宣称语义检索；排序 relevance、导入/更新、标题，过滤来源类型、分类、标签、作者、来源/导入日期、语言、处理状态。
- `REQ-025`：固定分类可多选 technical、business、education、news、interview、podcast、document；自由标签；手工主题可关联多来源，不复制或取得所有权。

## 外部卡、作业和生命周期

- `REQ-030`：外部卡仅接受用户输入的一般 URL 元数据，不抓取、解析、预览或请求，且不是事实证据。抖音仅接受用户输入、原样保存的 HTTPS `douyin.com` 或其子域 URL；浏览器仅打开原 URL 并提示中文人工定位。
- `REQ-031`：抖音绝对禁止下载、抓取、内容/媒体/字幕/文本/图像提取、iframe、cookie/密码/认证、自动化、缓存、代理、逆向和伪造时间参数；任何 HTTP client/worker/parser 均不可接受抖音 URL。
- `REQ-032`：作业状态 queued、running、retry_wait、succeeded、failed、blocked、cancelled；持久化作业/attempt/audit；单 worker，手动导入/重试/恢复优先于 FIFO；有进度、心跳、最小日志、有限重试、协作取消/重跑，按 version/artifact/config 幂等。
- `REQ-033`：可配置解析安全断路器，默认 24 小时，覆盖内存/磁盘/无进度；仅在输出、证据、索引验证后作业成功。
- `REQ-033a`：视频 `video_analyze` 沿用持久化租约、心跳、取消、优先级和有限重试，依次探测、抽帧、持久化和校验；`video_transcribe`、`video_summarize` 在 AI 未配置时仅阻止该作业，不能降低已经完成的视频版本或来源状态。
- `REQ-034`：软删除无限期；显式永久删除才移除 source versions/derived/index/cache，仅在无 active source 引用时删除 artifact；永久删除后最小无内容审计保留 1 年。

## 备份、导出和部署

- `REQ-040`：每日首次成功启动后排低优先级备份（非 Windows 任务）；保留最近 30 个成功日备份。备份含 DB、artifacts、derived/evidence、settings、manifest，排除模型缓存、staging、日志正文；快照一致并 SHA-256 验证。
- `REQ-041`：还原只能到新数据根，绝不覆盖；导出完整可移植 ZIP + JSON manifest，含原 artifact、派生数据、逻辑记录和 sha manifests，排除密钥/凭据/私密权利备注/cookies/原路径/日志正文；UI 必须确认导出。再导入校验 schema/zip/manifest/hash/relations，不覆盖；同 ID 不同链拒绝并报告。
- `REQ-042`：摄取/导出/还原均校验 hash，支持手工完整校验和空闲抽样；操作日志按日保留 30 天且不含内容/路径/令牌。视频分析记录、关键帧 artifact 及其引用也必须进入备份、导出、还原和再导入一致性校验。
- `REQ-043`：所有端点位于 `/api/v1`：health/capabilities、settings、sources/relations/rights/metadata、imports/paste、videos/local、videos/{id}/stream/frames/transcribe/summarize、docs/representations/evidence/citations/knowledge、search、tags/topics、external/douyin cards、jobs、delete/restore/purge、backup/restore、export/reimport；类型稳定并有 OpenAPI。
- `REQ-044`：UI 页面包括库、导入、视频、来源详情、检索、作业、设置、备份/还原/导出、外部卡；极简中文；视频仅播放本地 artifact 并显示本地元数据/关键帧，链接获取只可作为不可提交的预留状态；PDF 使用隔离只读预览并禁用嵌入链接，仅显式外开；文本安全渲染；目标 Edge/Chrome。
- `REQ-045`：SQLite 默认于 `data/state`，为 PostgreSQL 提供 production adapter/migrations；Docker Compose 运行 web/api/worker/postgres/redis，端口仅 loopback；集成测试只能使用 `tests\runtime\compose-<run-id>`，不得使用日常 data。
- `REQ-046`：依赖仅接受明确开源许可；锁定依赖/模型/镜像版本，不自动升级；建立合成、无版权 fixtures 和完整文档、决策、开发报告。
