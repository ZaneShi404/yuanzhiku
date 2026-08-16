# 源知库 v1.5 需求：视频媒体流水线重构（本地转写 + 多模态直送）

## 1. 元数据与状态

- 状态：**已并入 `docs/requirements.md` 冻结基线并已完成实现**（2026-08-16：REQ-054/055 新增、REQ-017/043/044/051/052 修订已进入冻结需求文本，代码与测试落地；ADR-010/011 已归档）。残留事项：供应商真实调用冒烟（Qwen/MiMo 各一次，独立验收登记）、本地模型真实下载与中文样本转写冒烟、中转部署验证（阻塞项：域名）——见 §10 门禁清单，未完成项不伪装通过。
- 来源：用户 2026-08-16 会话提出的目标流程描述（见 §2.1），以及同会话对决策点 D1–D4 的答复与后续质询（MiMo 公网 URL 可行性、云服务器利用、分块直送采纳）。
- 与既有版本关系：v1.4.1 的本地视频分析（`REQ-015`/`REQ-016`）、受限链接获取（`REQ-047` 系列）、媒体 AI 双分组与两层级联（`REQ-051`/`REQ-052`）、场景感知关键帧抽样（`REQ-053`）保持不变；本版重构「转写 → 摘要」链路：新增本地转写引擎与双路径策略，新增核心内容缺失时的视频直送补充理解。文档/粘贴的解析与分类链路（`REQ-051` source_classify）不动。
- 编号：新增 `REQ-054`、`REQ-055`；修订 `REQ-017`、`REQ-043`、`REQ-044`、`REQ-051`、`REQ-052`。决策编号从既有记录（决策 13 为 2026-08-15 下载域登记）之后续排 14–22（§14）。
- 配套文档：`docs/v1-5-implementation-plan.md`（阶段划分与依赖关系）。

## 2. 目标与非目标

### 2.1 目标流程（用户描述，原样照录）

> 下载后的本地视频 > 提取音轨 > 分成两个路径：1 是本地的语音转文字模型（默认），2 是通过多模态 API 用 AI 进行语音转文字 > 通过纯文本 AI 对文本进行理解、摘要、形成知识；若判断核心内容缺失（例如介绍外贸网站的、但信息以图片形式呈现、语音里没有，则判为核心内容缺失），则调用多模态 AI 直接对视频文件进行转写、理解、摘要。

### 2.2 目标

- `G1`：本地语音转写引擎 FunASR/Paraformer 成为默认转写路径（全新能力）。
- `G2`：转写双路径（本地 / 远程端点）与自动降级策略，来源可审计。
- `G3`：完整性判断判定「可能缺失」后，多模态 AI 直接对视频文件做补充转写/理解/摘要（供应商：通义千问与小米 MiMo 双适配，可选经用户自备云服务器中转），由多模态 API 一次性完成补充转写/理解/摘要/建议分类（用户裁定 2026-08-16 偏差 A）；直送不可行/失败时摘要仍按 tier1 产出并标记 visual_gap（关键帧视觉路径已移除，用户裁定偏差 B）。
- `G4`：全部既有纪律不变——证据时间定位（`REQ-016`）、附加产物语义（`REQ-033a`）、出站校验与凭据隔离（`REQ-052`）、断路器与协作取消（`REQ-016`）、错误脱敏（`REQ-017`）。

### 2.3 非目标

- 不改变 `video_analyze`（ffprobe 探测 + 关键帧抽样）与下载通道本身。
- 不做实时/流式转写，不做转写文本的说话人分离（FunASR 支持说话人日志，本版不启用）。
- 视频直送不作为常规路径：成本远高于关键帧，仅在完整性判定触发时发生。
- 音轨不作为永久 artifact 保存（转写文本与摘要已持久化，音轨无独立引用价值）。
- 不涉及文档/粘贴的解析与分类链路、图片分析（`REQ-048`）。
- 不内置隧道/内网穿透管理（决策 22 的用户自备中转是唯一公网 URL 通道，应用不管理其生命周期）。

### 2.4 现状对照

| 目标流程步骤 | 现状（v1.4.1） | 本版改动 |
| --- | --- | --- |
| 下载/导入后的本地视频 | `video_download` / `video` 导入 → artifact + `video_analyze` | 不变 |
| 提取音轨 | 嵌在 API 转写路径内部（`ConfiguredMediaAi._ffmpeg_audio_chunks`），仅远程路径使用 | 提升为转写作业的独立子步骤，本地/远程路径共用（决策 18） |
| 路径 1：本地语音转文字模型（默认） | 无（依赖中无任何本地 STT） | 新增 FunASR/Paraformer 引擎（`REQ-054`，决策 14） |
| 路径 2：多模态 API 语音转文字 | litellm OpenAI 兼容转写端点（transcribe 组） | 保留为降级/指定路径；转写适配器化（`MediaTranscriberPort`） |
| 纯文本 AI 理解、摘要、形成知识 | `video_summarize` tier1（完整性判断 + 摘要 + 建议只填空缺） | 判断机制不变，补充理解按 `REQ-055` 扩展为三级 |
| 核心内容缺失 → 多模态直送视频 | 无视频直送；仅有「关键帧 → 视觉模型」tier2（ADR-006） | 新增视频直送（`REQ-055`），通义千问 + 小米 MiMo 双适配，多模态直接出摘要；关键帧路径已移除（ADR-006 被取代） |

## 3. 选型记录

- **决策 14（本地转写引擎）：FunASR/Paraformer（用户确认 D1）**。中文识别业界最佳（阿里系），段级时间戳经 VAD + 标点辅助模型 pipeline 输出；模型托管 ModelScope 公开源。默认模型 `paraformer-zh`（含 VAD/标点辅助模型），设置项可切换 `paraformer-zh`/`paraformer-zh-quant`（int8 量化）。代价：完整版依赖 torch（镜像体积显著增大），实现期以 onnxruntime 变体（funasr-onnx）为轻量候选（遗留事项 E1）。
- **决策 15（路径策略）：auto 默认本地、失败降级 API**。`ai_transcriber_engine = auto | local | api`，默认 auto：本地模型可用即走本地；本地不可用或转写失败时降级到已配置的远程转写端点；`local`/`api` 为强制模式不做切换。降级事实写入表示的 parser_name/config_hash 与作业消息（用户确认 D4：作业消息+表示标记，不弹确认）。
- **决策 16（缺失回退两级，2026-08-16 用户裁定修订）：视频直送 → visual_gap**。视频直送不可行（模型未配置、供应商能力不可行、超上限、调用失败）时，摘要仍由纯文本模型按 tier1 产出并标记 visual_gap；关键帧视觉路径（原 ADR-006 tier2）彻底移除。任何情况下不空手、不伪造。
- **决策 17（视频直送供应商：通义千问 + 小米 MiMo 双适配，用户确认 D2）**。调研事实（2026-08-16，官方文档核实）：
  - **小米 MiMo**：OpenAI 兼容端点 `https://api.xiaomimimo.com/v1`，模型 `mimo-v2.5` 原生全模态（含音频理解——视频 token 分 video_tokens/audio_tokens，可对视频直接做补充转写），1M 上下文；视频传入仅「公网 URL（≤300MB，MP4/MOV/AVI/WMV）」与「base64（编码后 ≤50MB，约 37MB 原始文件）」两种。**官方 FAQ 明确「mimo-v2.5 model does not currently support uploading local video files」，无任何 files/upload 接口**；公网 URL 要求用户自备公网可达存储，与产品本地优先、无服务器的定位（`REQ-002`）冲突，不内置——经决策 22 的自备中转解决。故 MiMo 适配器对本地视频只能走 base64（实际上限 ≈37MB 原始）或中转 URL，超限经显式重编码（决策 20）与分块直送（决策 21）适配。
  - **通义千问**：DashScope 提供官方「临时上传获取临时 URL」流程（`GET /api/v1/uploads?action=getPolicy` → OSS multipart 上传 → 临时 URL → `video_url`），本地视频可达百 MB 级；qwen-vl 系列为纯视觉模型（不支持音频理解，qwen-vl-plus/max 时长 2s–10min，Qwen3-VL 系列更长），qwen-omni 系列支持音视频——Qwen 适配器模型选型见遗留事项 E2。
  - 适配器为可替换实现（同一端口、能力声明），`ai_video_provider` 设置选择（off/qwen/mimo，默认 off）。
- **决策 18（音轨提取为作业内子步骤）**：`video_transcribe` 作业第一步用 ffmpeg 提取 16kHz 单声道 48kbps mp3（>24MB 按 1800s 分块），本地与远程路径共用同一 staging 音轨；作业结束清理，音轨不落 artifact。
- **决策 19（本地模型显式下载）**：模型文件按 `REQ-013` 纪律管理（锁文件：模型包/版本/来源 URL/许可/SHA-256，公开下载无需登录，缓存 `data/models/stt/`，来源 ModelScope）；下载由设置页显式触发、校验哈希后启用；**绝不静默自动下载、绝不静默回退云转写**（项目零静默出站哲学，`REQ-002`/`REQ-052`）。
- **决策 20（MiMo 直送前显式重编码）**：MiMo 的 base64 上限（编码后 50MB，约 37MB 原始文件）对 1080p 源视频过于苛刻（约 40–75 秒），但 mimo-v2.5 自身按 fps 抽帧（默认 2fps）理解画面、音频 token 只需语音可懂度——直送前允许 ffmpeg 显式重编码（视频降为低码率/低分辨率、音频保留 ≥48kbps 单声道），以适配 base64 上限并覆盖更长视频。重编码是**显式策略而非静默降质**：设置项 `ai_video_reencode`（on|off，默认 on，仅 mimo 适配器适用）、摘要标记注明、审计记录。
- **决策 21（视频分块直送，用户确认采纳）**：超过供应商体积/时长上限（含重编码后仍超限）的长视频按时间切段直送——分段时长上限 `ai_video_chunk_seconds`（默认 600 秒），每段独立适配供应商传入上限后完整发送（多次请求；供应商支持单消息多视频时可按上下文约束合并批次），各段理解结果带时间偏移合并、证据定位到对应 `video_time_range`；某一段仍超限则该段转关键帧兜底并在摘要标记注明。模式与既有音频分块转写（1800s chunk）一致；零额外基础设施，字节只流向用户配置的供应商端点，成本随视频时长线性增长（直送视频的固有代价）。适用：mimo（base64 路径）与 qwen（受模型时长限制）。
- **决策 22（自备云服务器视频中转，用户提议）**：用户自备云服务器（已核查：腾讯云上海、Ubuntu 22.04.4、Docker 27 + 1Panel、80/443 已放行、无域名/TLS，见 E4），部署随仓库分发的轻量「视频中转」服务（§7.11）：应用以 Bearer 密钥上传待直送视频，获得随机 token 的临时公开 URL，将 URL 传给视频直送供应商，TTL 到期服务端自动删除。配置项 `ai_video_relay_base_url`（默认空 = 关闭）+ `ai_video_relay_secret`（仅凭据文件，`REQ-052` 纪律）；配置后 qwen/mimo 适配器**优先**经 relay URL 直送——MiMo 可吃满其 300MB URL 上限（免 base64 重编码/分块），Qwen 免 DashScope getPolicy 临时上传流程；未配置时各走原路径。风险与控制：随机 token（≥32 位十六进制）能力 URL、短 TTL（默认 30 分钟）、上传大小上限（默认 300MB）、无目录列举、路径穿越防护；relay 属用户自有基础设施，不引入第三方隧道商；应用不管理 relay 生命周期。

## 4. 需求文本

### 4.1 REQ-017 修订

> `REQ-017`：视频转写与内容摘要经可插拔媒体 AI 端口与作业接口提供；转写支持两条路径——本地引擎 FunASR（`REQ-054`，默认）与远程转写端点（`REQ-051` 转写组），选取与降级策略见 `REQ-054.2`。提供方由用户显式配置；默认全部关闭、无网络流量，作业明确 blocked，不伪造文本、摘要或 evidence。凭据、原路径、媒体内容和 AI 原始响应不得进入数据库、API、导出或日志；AI 调用错误一律脱敏为不含 URL、密钥或响应正文的中文短消息。摘要侧在完整性判定后按 `REQ-055` 两级补充理解执行（用户裁定 2026-08-16：移除关键帧视觉路径，视频直送时由多模态 API 直接产出摘要；直送不可行则 tier1 摘要 + visual_gap）。

### 4.2 REQ-051 修订

> `REQ-051`：媒体 AI 为两个相互独立的显式配置分组：语音转写（provider/base_url/model/key）与理解摘要（provider/base_url/chat_model/vision_model（可选）/key），经 `GET/PUT /settings/ai` 管理、`POST /settings/ai/test` 做连通性检查；视频直送另经 `ai_video_provider`/`ai_video_model`/`ai_video_max_bytes` 与自备视频中转（`REQ-055`，决策 22）配置；分组未启用或无 key 时对应作业 blocked，两组全关时行为与未配置完全一致。转写路径选择由 `ai_transcriber_engine`（auto/local/api，默认 auto）与本地模型设置（`REQ-054`）共同决定；转写作业统一提取音轨（决策 18）后按所选路径执行，产出 kind=transcription representation 与逐段 `video_time_range` 证据（可检索），表示带引擎标记（本地或远程、降级原因）。摘要为三级联——先按确定性规则（覆盖率/静音阈值）加约束 JSON 的 LLM 判定（置信阈值 0.6）评估转写完整性，完整走 tier1 纯文本摘要；疑似不完整或 `force_tier2` 走 `REQ-055` 补充理解（视频直送优先、关键帧兜底、visual_gap 标记）后增强摘要。AI 建议（领域/体裁强制收敛到分类清单、标签自由）在摘要作业成功时自动写入来源元数据——只填空缺：领域/体裁仅在当前为空时写入、标签取并集合并、用户已填字段绝不覆盖，摘要建议标记含 `applied` 以区分旧摘要（旧摘要仍可显式采纳）；`ai_auto_pipeline` 总开关（默认开，经 `PUT /settings/ai` 调整）启用且任一转写路径可用时，视频分析成功自动串联转写→摘要，文档/粘贴解析成功自动入队 `source_classify` 作业（正文截断至前 8000 字符发理解组分类并按同一只填空缺规则写入，图片不分类）；`source_classify` 与转写/摘要同为附加产物，未配置对应能力时 blocked、失败/取消不降低版本与来源状态（`REQ-033a`）。

### 4.3 REQ-052 修订

> `REQ-052`：AI 端点 base_url 与视频中转地址 `ai_video_relay_base_url` 经统一校验：仅 HTTPS、仅公网主机、无 userinfo、不超过 2048 字符，拒绝消息不含 URL 内容；API key 与视频中转密钥 `ai_video_relay_secret` 只保存在 `<data-root>/state/ai/credentials.json`（原子写入），绝不进入数据库、备份、导出、日志或任何 API 出参（仅回显 has_key 与掩码提示）；音频分块、关键帧图片、转写文本与视频直送的视频字节流（`REQ-055`，含供应商临时上传主机与自备中转地址——同样仅 HTTPS/公网主机/无 userinfo 校验）在用户逐视频显式触发或 `ai_auto_pipeline` 自动串联时发往所配置端点，文档/粘贴正文亦在自动分类开启且理解组已配置时发送（截断至前 8000 字符，`REQ-051`），默认关闭即零出站流量；AI 调用错误一律脱敏为不含 URL、密钥或响应正文的中文短消息。

### 4.4 REQ-043 修订

> `REQ-043`：所有端点位于 `/api/v1`（既有清单不变）；`/settings/ai` 扩展本地转写与视频直送配置（§6.2）；新增 `POST /settings/ai/stt-model`（本地转写模型下载/删除，§6.3）；`/capabilities` 的 `media.ai` 增加 `local_stt` 与 `video_input` 节（§6.4）；类型稳定并有 OpenAPI。

### 4.5 REQ-044 修订

> `REQ-044`：设置页「媒体 AI」区扩展：本地转写引擎配置（路径策略 auto/local/api、模型规格、模型下载/删除按钮与可用状态、断路器 `stt_*`）、视频直送配置（供应商 off/qwen/mimo、视频模型、体积上限、重编码开关、分块时长）、自备中转配置（地址 + 密钥掩码）；视频详情页按转写表示的 parser_name 前缀展示转写来源与降级标记（`local-funasr-*` / `ai-*`）；其余页面与极简中文纪律不变。

### 4.6 新增 REQ-054（本地语音转写引擎，9 条）

1. 引擎为 FunASR/Paraformer（中文识别最优，决策 14），依赖锁定写入 `requirements.lock`；本地转写全程无网络调用（无静默云回退）。
2. 路径策略 `ai_transcriber_engine`（设置项，默认 auto）：auto——本地模型可用时走本地，本地不可用（模型缺失/损坏/引擎异常）或转写失败（超时、空文本、无有效分段）时降级到远程转写端点（transcribe 组已配置 key 时），降级事实写入 transcription 表示的 parser_name/config_hash 与作业消息，可审计；local——强制本地，失败即 failed 可重试；api——强制远程，行为同现状。无可用路径时作业 blocked（消息：「未配置任何可用转写路径：请下载本地转写模型或配置转写 API」）。
3. 模型文件按 `REQ-013` 纪律管理：锁文件（模型包、版本、来源 URL、许可、SHA-256）位于 `data/models/stt/`，来源 ModelScope 公开源；下载经设置页显式触发（`POST /settings/ai/stt-model`，§6.3），下载完成校验哈希后才启用；删除后作业按第 2 条策略处理。绝不静默自动下载。
4. 默认模型 `paraformer-zh`（含 VAD 与标点辅助模型），设置项 `ai_local_stt_model` 可选 `paraformer-zh`/`paraformer-zh-quant`；换模型即换 config_hash 身份。
5. 转写输入为作业统一提取的音轨分块（决策 18）；输出段级时间戳（VAD+标点 pipeline），映射回视频时间轴；所选部署变体不支持时间戳时以整块为段退化（与远程路径无分段时的退化语义一致）。证据纪律同 `REQ-016`/`REQ-017`（`video_time_range`）。
6. 资源断路器独立设置组：`stt_timeout_seconds`（默认 3600）/`stt_memory_limit_mb`（默认 2048）/`stt_disk_limit_mb`（默认 1024），沿用 `REQ-016` 的超时/内存/磁盘/协作取消/心跳纪律；本地转写为 CPU 密集，与解析/下载同单 worker 串行执行，不引入并发竞争。
7. 本地转写器经 `MediaTranscriberPort` 接入作业层，与远程转写适配器同接口、不同 config_hash；能力（引擎可导入、模型可用）经 `/capabilities` 回显（§6.4）。
8. 模型下载、删除操作写审计事件（event_type/entity_id/result，不记内容）；模型文件不进入备份、导出与 reimport（与 Cookie/凭据同一排除纪律，`REQ-040`/`REQ-041`；归档本就只白名单写入 `state/knowledge.db`、`records.json`、manifest 与 artifacts，`data/models` 天然不入档）。
9. 本地转写失败时不改变版本完整性与来源处理状态（`REQ-033a` 附加产物语义），与远程路径一致。

### 4.7 新增 REQ-055（视频直送补充理解，6 条）

1. 触发：`REQ-051` 完整性判断为 `likely_incomplete`（规则或 LLM 判定）或用户显式 `force_tier2` 时进入补充理解；判断机制与阈值不变。
2. 两级补充理解（决策 16 修订，用户裁定 2026-08-16）：① 视频直送——`ai_video_provider` 非 off 且所选供应商适配器能力声明可行时，将视频文件直送多模态模型，由它一次性产出补充转写/画面理解（带时间定位）、200-600 字摘要与建议分类（转写文本始终随附；分块直送时逐段产出理解条目、最终由同一多模态模型纯文本合成摘要），超过供应商体积/时长上限的视频按 `ai_video_chunk_seconds` 分块直送（决策 21，每段完整发送、绝不静默截断）；② 视频直送不可行（未配置、供应商能力不可行、超上限、调用失败）时，摘要仍由纯文本模型按 tier1 产出并标记 visual_gap。任何情况下不伪造补充内容。
3. 视频直送体积上限为设置项 `ai_video_max_bytes`（默认 **300MB**，用户确认 D3；可调，实际以 min(设置值, 供应商能力声明) 为准）。超过可行上限的视频按分块直送处理（决策 21）：按时间切成连续分段、每段完整发送（绝不静默截断或降采样任何一段），各段理解结果以时间偏移合并、证据定位到对应 `video_time_range`；某一段仍超限（如重编码后仍超 base64 上限）则该段跳过并在摘要标记中注明。已知供应商现实约束（决策 17/20/21）：MiMo 无本地文件上传接口（官方 FAQ 核实）、公网 URL 不适用于本地视频，仅 base64（≤50MB 编码 ≈37MB 原始）传入——直送前按 `ai_video_reencode`（默认 on）显式重编码（低码率视频 + ≥48kbps 音轨）；Qwen 经 DashScope 临时上传流程（getPolicy → OSS multipart → 临时 URL）可达百 MB 级，另受所选模型时长限制（qwen-vl-plus/max 为 10 分钟），超时长同按分块直送。配置自备视频中转（决策 22）时，两个适配器优先经中转 URL 直送（MiMo 免 base64 限制、吃满其 300MB URL 上限；Qwen 免 DashScope 临时上传流程），中转未配置或上传失败时按上述各供应商路径。
4. 视频直送字节流只发往用户显式配置的端点（含供应商临时上传主机与自备中转地址），受 `REQ-052` 出站校验与凭据纪律约束；模型输出的补充理解按 `video_time_range` 证据落库（模型未给时间定位时以整片范围定位），API 原始响应不落库不落日志。
5. 视频直送适配器为可替换实现（决策 17）：端口声明 `video_input` 支持、`max_bytes`、`audio_in_video` 与时长限制；首批实现两家——通义千问（DashScope 临时上传 + video_url，模型选型见遗留事项 E2）与小米 MiMo（OpenAI 兼容 `api.xiaomimimo.com/v1` + `mimo-v2.5`，base64 ≤50MB 编码传入）。
6. config_hash 含供应商/视频模型/提示词版本；建议分类仍按 `REQ-051` 只填空缺规则写入来源元数据。

## 5. 威胁模型修订（新增行）

| 威胁 | 缓解 | REQ |
| --- | --- | --- |
| 本地转写模型文件被投毒或损坏 | 锁文件（模型包/版本/来源/许可/SHA-256）+ 下载后校验才启用；显式下载、无静默网络；损坏/缺失按策略降级或 blocked，可重下 | REQ-054 |
| 视频内容经视频直送/临时上传外泄 | 仅发往用户显式配置端点（含上传主机与中转地址经统一校验）；仅自动串联或显式触发；凭据隔离；响应不落库不落日志；错误脱敏；供应商临时存储为短期托管、限时 URL | REQ-055, REQ-052 |
| 视频中转被滥用或 token 泄露 | 随机 32+ hex token + 短 TTL + 到期自动删除 + 上传密钥 + 无目录列举/路径穿越防护；仅用户显式配置启用；中转不持有库元数据 | REQ-052, 决策 22 |
| 视频超上限被截断造成理解失真 | 上限 = min(`ai_video_max_bytes`, 供应商能力声明)；超限按分块直送（每段完整发送），某段仍超限该段跳过并在摘要标记注明，绝不静默截断/降采样；MiMo 直送前重编码为显式开关+标记+审计策略，保留语音可懂度 | REQ-055 |
| 本地转写质量差导致完整性误判 | 完整性判断独立于转写路径（规则+LLM 不变）；引擎标记与降级原因可审计 | REQ-054, REQ-051 |
| 本地转写拖垮 worker（CPU/内存） | 独立断路器设置组；单 worker 串行；推理循环内协作取消 | REQ-054 |

## 6. API 契约

### 6.1 端点

- `GET/PUT /settings/ai`：扩展（§6.2 字段表）。
- `POST /settings/ai/stt-model`：新增（§6.3）。
- `POST /settings/ai/test`：不变。
- `/capabilities`：`media.ai` 扩展（§6.4）。
- 其余端点不变；`GET /videos/{source_id}` 响应结构不变（转写来源经既有 parser_name 字段承载，前端按前缀展示）。

### 6.2 设置字段表（`PUT /settings/ai` 扩展）

| 字段 | 类型 | 默认 | 校验 | 说明 |
| --- | --- | --- | --- | --- |
| `ai_transcriber_engine` | enum | `auto` | `auto\|local\|api` | 转写路径策略（REQ-054.2） |
| `ai_local_stt_model` | enum | `paraformer-zh` | `paraformer-zh\|paraformer-zh-quant` | 本地模型规格（REQ-054.4） |
| `stt_timeout_seconds` | int | `3600` | 60–86400 | 本地转写总超时 |
| `stt_memory_limit_mb` | int | `2048` | 64–32768 | 本地转写内存断路器 |
| `stt_disk_limit_mb` | int | `1024` | 64–32768 | 本地转写 staging 磁盘断路器 |
| `ai_video_provider` | enum | `off` | `off\|qwen\|mimo` | 视频直送供应商（REQ-055.2） |
| `ai_video_model` | str | `""` | ≤100 字符 | 视频模型名；空则按供应商默认（mimo 默认 `mimo-v2.5`，qwen 按 E2） |
| `ai_video_max_bytes` | int | `314572800` | 1048576–536870912 | 直送体积上限（默认 300MB，REQ-055.3） |
| `ai_video_reencode` | enum | `on` | `on\|off` | MiMo 直送前显式重编码（决策 20） |
| `ai_video_chunk_seconds` | int | `600` | 60–3600 | 分块直送段时长上限（决策 21） |
| `ai_video_relay_base_url` | str | `""` | 空或 HTTPS+公网主机+无 userinfo+≤2048（REQ-052） | 自备中转地址（决策 22） |

- `GET /settings/ai` 增加本地转写状态节：`local_stt` = `{engine, model_configured, model_available, model_sha256, downloaded_at}`；增加视频直送状态节：`video_input` = `{provider, model, max_bytes, reencode, chunk_seconds, relay_configured, relay_hint}`。
- 凭据文件 `credentials.json` 增加可选键 `video_qwen`、`video_mimo`（与既有两组同纪律：仅文件、掩码回显、原子写入）；用户理解组已用 DashScope 时可手动复用同一 key（文档说明，不自动复制）；中转密钥键为 `video_relay`（对应设置 `ai_video_relay_secret`，设置视图仅回显 has_key 与掩码提示）。

### 6.3 `POST /settings/ai/stt-model`

- Body：`{"action": "download" | "delete"}`；201/202 接受（下载异步执行、删除同步）。
- download：下载中重复请求 → `409`；已下载且哈希一致 → 幂等返回当前状态；失败 → `502`，detail.code=`model_download_failed`（脱敏消息「本地转写模型下载失败，请检查网络后重试」），staging 清理、可重试。
- delete：幂等（模型不存在也返回成功态）。
- 审计：`stt_model_download` / `stt_model_delete`（event_type/entity_id/result，不记内容）。

### 6.4 capabilities 扩展

- `media.ai.local_stt`：`{enabled, engine, engine_version, model, model_available, model_sha256, downloaded_at, supported_media_types}`。
- `media.ai.video_input`：`{provider, model, supported, max_bytes, audio_in_video, duration_limits, reencode, relay_configured}`。

### 6.5 错误码表

| 场景 | 状态码 | detail.code | 消息 |
| --- | --- | --- | --- |
| stt-model action 非法 | 422 | `model_action_invalid` | 通用校验消息 |
| 下载进行中重复请求 | 409 | `model_download_busy` | 本地转写模型下载进行中 |
| 模型下载失败 | 502 | `model_download_failed` | 本地转写模型下载失败，请检查网络后重试 |
| 设置字段校验失败（含中转地址非 HTTPS/非公网/带 userinfo） | 422 | 既有 settings 校验语义 | 拒绝消息不含 URL 内容 |

- 转写/摘要/直送的可达性问题均在作业层表达（blocked/failed + 作业消息），不新增 API 错误码；消息一律脱敏。

### 6.6 OpenAPI 影响

- `domain/models.py`：`AiSettingsUpdate` 扩展 §6.2 字段（含枚举与范围校验）；新增 `SttModelActionRequest`。OpenAPI 自动生成（`REQ-043` 类型稳定）。

## 7. 接口设计

### 7.1 新端口 `MediaTranscriberPort`（`backend/app/ports/media.py` 扩展）

```
capability() -> dict            # {enabled, engine, model, model_available, network, ...}
config_hash() -> str            # 引擎:模型:版本:提示词版本的 SHA-256
transcribe(audio_chunks: list[tuple[Path, int, int]], cancelled) -> MediaTranscript
```

### 7.2 共享音轨提取助手（决策 18）

- `ConfiguredMediaAi._ffmpeg_audio_chunks` 拆出为共享助手（`backend/app/adapters/media.py` 或新 `services/audio.py`）：16kHz 单声道 48kbps mp3，>24MB 按 1800s 分块；沿用 `_run` 的超时/内存/磁盘/取消纪律；作业层提取一次，两个转写适配器共用；作业结束清理 staging。

### 7.3 适配器 `ApiTranscriber`（`backend/app/adapters/media_ai.py` 内迁出）

- 现有 litellm 转写端点逻辑从 `ConfiguredMediaAi.transcribe` 迁出到 `ApiTranscriber`（同端口实现），`ConfiguredMediaAi` 保留理解/摘要/分类职责（职责收敛，便于测试替身）；行为与 parser_name（`ai-<provider>-<model>`）不变。

### 7.4 适配器 `LocalFunasrTranscriber`（`backend/app/adapters/local_stt.py` 新文件）

- 构造参数：模型目录（`data/models/stt/<模型包>`）、设置读取回调（惰性，改设置即时生效）。
- `transcribe`：对每个音轨分块本地推理，段级时间戳（块偏移 + 段内偏移）映射回视频时间轴；时间戳不可用时整块为段退化。
- 执行纪律：无 shell、无网络；断路器与协作取消由作业层心跳驱动（推理循环内注入 cancelled 检查）。
- 部署变体（遗留事项 E1）：完整版 `funasr`（torch）或轻量 `funasr-onnx`（onnxruntime），实现期按镜像体积/时间戳能力/中文质量实测后定，写死进锁文件。
- 模型不可用（未下载/哈希不符/引擎 ImportError）→ 按 `ai_transcriber_engine` 策略处理。

### 7.5 新端口 `VideoUnderstandingPort`（`backend/app/ports/media.py` 扩展）

```
capability() -> dict            # {video_input, max_bytes, audio_in_video, duration_limits, reencode}
config_hash() -> str
understand_video(video_path, transcript_text, focus, cancelled) -> dict
```

- 输出：补充转写/画面理解结果（尽量带时间定位），由作业层按 `REQ-055` 落证据；适配器抛 `MediaAiUnavailable("video_input")` 时作业层转关键帧兜底。

### 7.6 适配器 `QwenVideoAdapter`（`backend/app/adapters/video_ai.py` 新文件）

- 配置自备中转（决策 22）时优先经 relay URL 直送；否则 getPolicy → OSS multipart 上传（upload_host 经 `REQ-052` 出站校验）→ 临时 URL → `video_url` 消息 + 已有转写文本（qwen-vl 纯视觉）或原生音视频（qwen-omni，模型选型 E2）；超过模型时长/体积上限的视频按 `ai_video_chunk_seconds` 分块直送（决策 21）。

### 7.7 适配器 `MiMoVideoAdapter`（同文件）

- `https://api.xiaomimimo.com/v1` OpenAI 兼容 + `mimo-v2.5`，原生音频理解；无上传接口（E3 已核实）。配置自备中转（决策 22）时优先经 relay URL 直送（≤300MB）；否则 base64（≤50MB 编码）传入，超过 37MB 原始上限时按 `ai_video_reencode` 显式重编码（低码率视频 + ≥48kbps 音轨），仍超限按 `ai_video_chunk_seconds` 分块直送（多段多次请求、段偏移定位），某段仍超限则该段抛 `MediaAiUnavailable("video_input")` 转关键帧兜底。

### 7.8 视频直送辅助策略（决策 20/21 落地）

- 重编码参数（mimo 适配器）：视频缩至 ≤640px 宽、低码率、保留原时长；音频 48kbps 单声道（保留语音可懂度）；重编码在作业 staging 内进行，受既有断路器约束；开启/关闭与结果写摘要标记与审计。
- 分块：按 `ai_video_chunk_seconds` 切段（段边界取整秒，无重叠）；每段独立走供应商传入路径；各段结果带 `offset_ms` 合并；段级失败只影响该段（转关键帧兜底并注明）。

### 7.9 作业流改造（`backend/app/services/jobs.py`）

- `_video_transcribe`：音轨提取（子步骤 ①）→ 按 `ai_transcriber_engine` 选适配器（②）→ 转写（③）→ 失败且 auto → 降级另一路径重转（④）→ 持久化 representation（parser_name 如 `local-funasr-paraformer-zh` / `ai-openai_compatible-whisper-1`，降级时作业消息注明「本地转写不可用，已使用 API 转写」）。
- `_video_summarize`：完整性判断不变；`likely_incomplete` 或 `force_tier2` 时按 `REQ-055` 三级执行：视频直送（`ai_video_provider` 非 off 且 min(设置上限, 供应商上限) 可行）→ 失败/不可行 → 关键帧兜底（现状 `describe_frames`）→ 均不可用 → visual_gap。
- 自动串联条件修订：转写入队条件从「transcribe 组已配置」改为「本地模型可用或 transcribe 组已配置」（`REQ-051` 修订）。
- 作业消息纪律：全部脱敏（无 URL/密钥/路径），见 §6.5。

### 7.10 本地模型管理（`data/models/stt/`）

- 锁文件 `models/stt/manifest.lock.json`：模型包/版本/来源 URL（ModelScope）/许可/文件 SHA-256（同 `models.lock.json` 纪律，`REQ-013`）。
- 下载：ModelScope 公开源下载到 staging 后逐文件校验 SHA-256，原子替换启用；失败清理 staging 并可重试；删除 = 删除目录 + 审计。
- 下载/删除均不落任何内容到日志或数据库正文。

### 7.11 视频中转服务（决策 22 部署物）

- 随仓库分发的独立部署物（如 `tools/video-relay/`）：单文件 FastAPI 应用 + Dockerfile/compose + 部署说明。
- 行为：`POST /upload`（Bearer `VIDEO_RELAY_SECRET`，multipart 文件，大小上限环境变量默认 300MB）→ 生成 ≥32 位十六进制随机 token、写入 token 目录，返回 `{base}/f/{token}`；`GET /f/{token}` 回传对应文件；TTL（默认 30 分钟）到期后台清理删除；无目录列举；token 仅允许 `[0-9a-f]{32,}` 格式校验后映射，杜绝路径穿越。
- 部署要求：公网可达 + HTTPS（域名/TLS 由用户自备）；与知识库应用完全独立，不持有任何库内容或凭据。已核查目标服务器（E4）：腾讯云上海、Ubuntu 22.04.4、2 vCPU/1.9GB、Docker 27 + Compose、1Panel（8090）、80/443 安全组已放行——按「1Panel OpenResty 反向代理 + Let's Encrypt」部署；前置阻塞项：域名。
- 应用侧纪律：上传仅当 `ai_video_relay_base_url`/`ai_video_relay_secret` 已配置且视频直送触发时发生；上传失败按直送失败处理（转兜底）；中转 URL 只出现在发往供应商的请求中，不落库不落日志。

### 7.12 前端改动点（`frontend/src/App.tsx`）

- 设置页「媒体 AI」区扩展（§4.5 REQ-044）：引擎/模型/下载按钮与状态（轮询 `GET /settings/ai` 的 `local_stt` 节）、视频直送配置、中转配置（密钥掩码）。
- 视频详情页：转写来源标记（parser_name 前缀 `local-funasr-*` 显示「本地转写」，`ai-*` 显示「API 转写」，降级原因从作业消息/表示标记展示）。
- 作业页与其余页面通用渲染，无需改动。

## 8. 测试计划

- `T-STT-001`（单元，fake 引擎替身）：分段偏移映射与时间戳退化；断路器（超时/内存/磁盘）与协作取消；config_hash 随引擎/模型变化；模型不可用异常语义。
- `T-STT-002`（单元）：路径策略与降级矩阵（auto/local/api × 模型可用/失败/API 可用/双不可用）；降级事实写入 parser_name/config_hash 与作业消息；`REQ-033a` 失败不降完整性。
- `T-VDIR-001`（单元，fake 视频理解器）：两级直送判定（直送成功/visual_gap）；`min(ai_video_max_bytes, 供应商上限)` 判定矩阵；多模态三合一输出（摘要+理解条目+建议分类收敛）；重编码+分块组合（段偏移合并、段级跳过）；config_hash 含供应商/模型。
- `T-VDIR-002`（集成，fake 转写器/视频理解器全链路）：下载 → 分析 → 自动串联转写（本地）→ 摘要（完整性完整）→ tier1；完整性不完整 → 直送成功（多模态直接出摘要）/失败 → visual_gap 支路；转写本地失败 → 自动降级 API；`REQ-033a` 纪律回归。
- `T-RLY-001`（单元，fake relay 服务器）：上传/取 URL/TTL 删除；配置后两适配器优先 relay 直送；上传失败回退；未配置时行为不变（qwen 临时上传、mimo base64）；relay URL 不落库不落日志。
- `T-MDL-001`（模型管理）：下载成功/校验失败/重试/删除后策略；`POST /settings/ai/stt-model` 错误码逐条命中；审计事件不含内容。
- 纪律回归：出站校验（upload_host/中转地址仅 HTTPS/公网/无 userinfo）、错误脱敏、凭据不出文件、备份/导出排除模型文件（归档白名单断言）。
- 前端冒烟：设置页配置往返与模型下载按钮状态；视频详情转写来源标记。
- 供应商真实调用冒烟（独立验收，不作为自动化门禁）：Qwen 视频直送（临时上传 + video_url）与 MiMo base64 直送各一次真实调用，脱敏摘要登记。

## 9. 验收矩阵新增条目（沿用 `docs/acceptance-matrix.md` 格式）

| 需求组 | 实现证据 | 自测标识 | 独立复核重点 |
|---|---|---|---|
| REQ-054, REQ-017/051/052(修订) | `ports/media.py`, `adapters/local_stt.py`, `adapters/media_ai.py`, `services/jobs.py`, `services/audio.py`, `main.py`, `frontend/src/App.tsx` | T-STT-001, T-STT-002, T-MDL-001 | 本地转写默认路径与 auto 降级可审计；显式模型下载、零静默网络；模型锁文件哈希；断路器与取消；降级事实持久化 |
| REQ-055, REQ-043/044(修订) | `ports/media.py`, `adapters/video_ai.py`, `services/jobs.py`, `domain/models.py`, `main.py`, `tools/video-relay/`, `frontend/src/App.tsx` | T-VDIR-001, T-VDIR-002, T-RLY-001 | 三级回退不伪造；双供应商能力声明；min 上限/重编码/分块绝不静默降质；证据 video_time_range 定位；出站校验含 upload_host 与中转；中转部署物安全（token/TTL/穿越防护） |

供应商真实调用冒烟为独立验收，由 acceptance 角色登记，不写入「自测标识」列。

## 10. 审核与门禁流程

- 四角色分离沿用既有流程（development → testing → acceptance → review），双件报告按 `report-schema-v1.json` 归档。
- 冻结门禁清单（全部满足才允许从 DRAFT 冻结为 v1.5）：
  1. `requirements.lock` 含 FunASR 家族锁定依赖（按 E1 决定），本机 venv 物理验证可导入；
  2. 本地模型物理验证：ModelScope 下载 + SHA-256 校验 + 中文样本真实转写冒烟（脱敏摘要）；
  3. 供应商真实调用独立验收完成（Qwen + MiMo 各一次，脱敏摘要，失败如实登记）；
  4. 中转服务部署验证（D-1 域名就绪后，部署 + MiMo 拉取实测；域名未就绪则该门禁项如实登记为未满足，不伪装通过）；
  5. T-VDIR-002 集成通过 + T-STT-001/002、T-VDIR-001、T-RLY-001、T-MDL-001 通过；
  6. 既有回归不劣化（T-VID-003/004、T-BACK-001 等 v1.2–v1.4.1 全量）；
  7. 出站负向验证：upload_host/中转地址非 HTTPS/非公网/带 userinfo → 拒绝且拒绝消息不含 URL 内容；
  8. 独立审核报告已出具且阻断项已解决、主要项已裁决。
- `release` 门禁保持既有 blocked 语义，不因本版冻结改变。

## 11. 实施任务分解（每步门禁与回滚，详见 `docs/v1-5-implementation-plan.md`）

| 步骤 | 内容 | 门禁 | 回滚 |
|---|---|---|---|
| 1 前置实测 | E1（FunASR 变体）/E2（Qwen 视频模型）实测 + D-4 凭据准备 | 结论归档进本文档 §14；供应商真实冒烟通过 | 仅文档与依赖锁候选 |
| 2 依赖锁定 | `requirements.lock` 追加 FunASR 家族（按 E1） | 现有包零漂移；venv 物理安装/导入成功；许可证确认 | 删除追加行、重建 venv |
| 3 端口与音轨提取 | `MediaTranscriberPort` + 音轨提取助手上移 + `ApiTranscriber` 迁出 | 现有媒体 AI 测试改经 `ApiTranscriber` 全绿，无行为差异 | 删除新增块、还原迁移 |
| 4 本地转写 | `LocalFunasrTranscriber` + 模型管理 + `stt-model` 端点 + `stt_*` 设置 | T-STT-001、T-MDL-001 | 删除适配器与端点，行为回 v1.4.1 |
| 5 作业双路径 | `_video_transcribe` 重构 + 自动串联条件修订 + capabilities.local_stt | T-STT-002、T-VDIR-002 转写支路 | 删除分支，行为回 v1.4.1 |
| 6 视频直送 | `VideoUnderstandingPort` + Qwen/MiMo 适配器 + 三级回退 + 重编码/分块 + 设置/凭据 | T-VDIR-001、T-VDIR-002 直送支路 | 删除适配器与分支，行为回 v1.4.1 |
| 7 中转 | `tools/video-relay/` 交付物 + 应用侧 relay 集成 | T-RLY-001；服务器部署与拉取实测（D-1 就绪后） | 删除集成与交付物目录 |
| 8 前端 | 设置页/视频详情改动（§7.12） | 构建通过；前端冒烟 | 还原对应 JSX 块 |
| 9 文档冻结 | 第 13 章同步清单 + ADR-010/011 | REQ 编号与实现 file:line 交叉核对一致 | 文档 git 层面整体还原 |
| 10 回归发布 | 全量回归 + 本地全链路冒烟 + v1.5.0 提交 | 第 10 章门禁 8 项全绿 | 按 git 回退 |

## 12. 回滚与兼容

- v1.4.1 行为不变：全部新设置取默认值（`ai_transcriber_engine=auto` 且本地模型未下载 + transcribe 组已配置 → 行为与现状「API 转写」一致；`ai_video_provider=off`、中转未配置 → 行为与现状 tier2 关键帧一致）。默认零新增出站。
- 旧转写/摘要表示兼容：新 parser_name 只影响新作业；旧表示照常展示；旧摘要建议标记（含 `applied`）语义不变。
- 凭据文件扩展向后兼容：`video_qwen`/`video_mimo`/`video_relay` 缺省即未配置；既有键结构不变。
- 依赖回滚：FunASR 依赖移除后本地转写即 blocked（其余路径不受影响）；模型文件删除即回退。
- 零 schema 迁移：本地模型状态全部在文件系统 + 设置 + 审计事件；无新表、无 Alembic 迁移。
- 中转未配置时链路自动走原路径，无阻塞；中转服务本身与知识库应用完全独立，停用即停。

## 13. 文档同步清单（冻结时逐文件更新）

- `docs/requirements.md`：REQ-017/043/044/051/052 替换为第 4 章修订文本；新增 REQ-054（9 条）/REQ-055（6 条）。
- `docs/decisions/ADR-010-funasr-local-transcription.md`：决策 14/15/18/19 正式记录；`ADR-011-video-direct-multimodal.md`：决策 16/17/20/21/22 正式记录。
- `docs/threat-model.md`：表尾追加第 5 章 6 行。
- `docs/api-contract.md`：settings 区扩展（§6.2 字段表）、新增 `POST /settings/ai/stt-model`、capabilities 扩展（§6.4）、错误码（§6.5）。
- `docs/acceptance-matrix.md`：追加第 9 章新行。
- `docs/test-plan.md`：追加 T-STT-001/002、T-VDIR-001/007、T-RLY-001、T-MDL-001 与前端口径说明。
- `docs/dependency-installation.md`：FunASR 家族安装、ModelScope 模型下载说明（含本机 fake-IP 代理环境提示）、版本锁定纪律（绝不自动升级）。
- `docs/operations-and-recovery.md`：本地转写 blocked/failed 运维语义；`data/models/stt` 模型文件管理；中转服务运维说明（token/TTL/清理）；日志纪律不变。
- `docs/v1-5-requirements.md` 本文档状态更新为「已并入冻结基线并已完成实现」。

## 14. 决策记录（2026-08-16 已拍板）

1. **本地转写引擎：FunASR/Paraformer**（决策 14，用户确认 D1）。默认模型 `paraformer-zh`；部署变体（torch vs onnxruntime）为 E1 实施期实测项。
2. **路径策略 auto**（决策 15）：本地默认、失败降级 API、强制模式可选；降级可审计。
3. **缺失回退两级**（决策 16，2026-08-16 用户裁定修订）：视频直送（多模态直接出摘要）→ visual_gap。
4. **视频直送供应商：通义千问 + 小米 MiMo 双适配**（决策 17，用户确认 D2）；调研事实（MiMo 无上传接口、base64 50MB 编码上限；Qwen 临时上传流程、qwen-vl 纯视觉 10 分钟时长限制）已核实并写入 §3。
5. **音轨提取作业内子步骤**（决策 18）：共用 staging 音轨、不落 artifact。
6. **本地模型显式下载**（决策 19）：ModelScope + 锁文件哈希 + 设置页触发；零静默网络。
7. **直送上限 300MB**（用户确认 D3）：实际以 min(设置, 供应商能力声明) 为准。
8. **降级提示 = 作业消息 + 表示标记**（用户确认 D4）：不弹确认。
9. **MiMo 直送前显式重编码**（决策 20）：显式开关 + 摘要标记 + 审计；保留 ≥48kbps 音轨。
10. **视频分块直送**（决策 21，用户确认采纳）：`ai_video_chunk_seconds` 默认 600 秒；段偏移合并、段级兜底。
11. **自备云服务器视频中转**（决策 22，用户提议）：部署物随仓库分发；relay 优先；默认关闭。
12. **MiMo 公网 URL 不可行**（E3 已解决）：官方 FAQ「mimo-v2.5 model does not currently support uploading local video files」；不内置隧道/自托管方案；用户自有公网 URL 需求经决策 22 中转满足。
13. **中转部署环境已核查**（E4）：腾讯云上海、Ubuntu 22.04.4、2 vCPU/1.9GB、Docker 27.0.3 + Compose、1Panel（8090）、80/443 已放行、无域名/TLS、到 MiMo/DashScope/ModelScope 均可达；部署方案 = Docker compose + 1Panel OpenResty + Let's Encrypt；**前置阻塞项：域名**。

## 15. 遗留、风险与自述假设

遗留与风险：

- **MiMo 接口行为以官方文档为准、仍可能漂移**：第三方资料与实测可能有偏差；Phase 4 先最小真实调用冒烟再写完整适配器；MiMo 未来若新增上传接口，适配器按能力声明升级（当前无）。
- **E1/E2 未决**：FunASR 部署变体与 Qwen 视频模型选型依赖实测（中文质量/镜像体积/时长限制/成本），结论归档进本文档后冻结。
- **供应商计费与限额波动**：视频直送 token 成本随视频时长线性增长，供应商定价/限额变化直接影响成本；上限与分块参数可调以应对。
- **中转依赖域名 + 备案**：MiMo 拉取要求 HTTPS；用户域名未就绪时中转部署阻塞（代码先行），链路自动走原路径不阻塞功能。
- **本机 fake-IP 代理干扰**：ModelScope 模型下载可能被代理工具影响（下载失败可重试）；沿用决策 10 隧道段例外纪律，不改变出站白名单哲学。
- **FunASR 依赖重**：torch 完整版显著增大镜像/安装体积；E1 优先实测 funasr-onnx 变体；模型文件显式下载、绝不入镜像。
- **真实平台验收不可自动化**：供应商真实调用冒烟为独立验收，不作为自动化门禁，失败如实登记。

自述假设（供测试/审核核对，非结论）：

1. FunASR 家族的具体锁定版本在实施步骤 2 确定；本文档不预判版本号，仅约束锁定与手动评估更新流程。
2. 音轨提取助手复用 `LocalFfmpegMediaAnalyzer._run` 的断路器纪律（超时/内存/磁盘/取消），staging 上限受 `stt_disk_limit_mb` 约束。
3. base64 上限按「编码后 ≤50MB ≈ 37MB 原始文件」折算；重编码参数（≤640px 宽、低码率、≥48kbps 音轨）为初始值，实施时以实测 MiMo 接受度微调并归档。
4. 分块直送默认无重叠、段边界取整秒；各段独立调用（多次请求），段级失败只兜底该段。
5. 中转 TTL 默认 30 分钟：覆盖「上传 → MiMo 拉取 → 响应返回」的同步请求窗口绰绰有余；若实测 MiMo 拉取为异步（响应返回后仍拉取），TTL 相应上调并归档。
6. 视频直送选择逻辑中「供应商能力声明」= `capability()` 回显（max_bytes/audio_in_video/duration_limits），由适配器按供应商文档与实测初始化；不依赖运行时探测网络行为。
7. 本地转写与视频直送在同一单 worker 串行队列执行，与既有解析/下载一致，无并发竞争；`REQ-033a` 附加产物语义覆盖全部新增作业路径。
8. 本文档 `file:line` 引用以 2026-08-16 工作区代码为基准核实；冻结前代码改动不影响需求文本有效性（实施任务分解的门禁行号以实施时为准）。

## 修订记录

- 2026-08-16（本轮，规划阶段）：
  - 按用户目标流程描述建立 v1.5 需求草案（§2.1 原样照录）。
  - 用户拍板 D1–D4（FunASR / Qwen+MiMo 双适配 / 300MB / 作业消息+表示标记），决策 14–17 落地。
  - 官方文档核实 MiMo 无上传接口（E3 解决）、Qwen 临时上传流程与时长限制，写入 §3/§4.7。
  - 用户质询「公网 URL 可行性」→ 裁定不内置隧道；用户云服务器经只读检查（E4）后裁定自备中转（决策 22）+ 显式重编码（决策 20）+ 分块直送（决策 21，用户确认采纳）。
  - 按 v1-2 规格体例扩写完整需求文档（API 字段表/错误码表/UI 修订/测试计划/门禁/回滚/同步清单/决策记录/假设），配套 `docs/v1-5-implementation-plan.md`。

- 2026-08-16（用户裁定修订，随 v1.5.0 实现）：偏差 A——多模态直送时由多模态 API 直接产出转写/理解/摘要三部分（原「多模态出理解、纯文本合成摘要」改为直送三合一输出，纯文本模型仅在 tier1 路径使用）；偏差 B——彻底移除 vision_model 关键帧视觉路径（MediaAiPort.describe_frames 与 tier2 设置删除，ADR-006 标记被取代），缺失回退改为两级（直送 → visual_gap）。REQ-051/052/055 修订文本、威胁模型、API 契约、运维文档同步更新。
