# v1.2 链接获取需求文档独立审核报告

- 报告 ID：`20260812T230103Z-v1-2-requirements-review`
- 角色：独立审核子智能体（audit / review）。本报告是单件 Markdown 审查记录，不构成测试或验收结论，也不进入双件报告登记（见 F-10）。
- 被审对象：`docs/v1-2-requirements.md`（v1.2 链接获取 / 视频下载需求文档，状态 DRAFT）。
- 审核日期：2026-08-13（UTC 20260812T230103Z）。
- 独立性：未阅读开发子智能体的聊天记录或结论；仅以文档与代码事实为据。未修改任何文件，未执行 git add/commit，未联网。

## 1. 审核范围

- 输入基线：
  - 被审文档 `docs/v1-2-requirements.md`；
  - 决策基线 `reports/development/20260812T222057Z-link-acquisition-v1-2-draft.md`；
  - 冻结基线 `docs/requirements.md`、`docs/threat-model.md`、`docs/v1-archive/archive-policy.md`、`docs/v1-archive/report-schema-v1.json`；
  - 参考既有独立报告的语言与裁定惯例（`reports/testing/20260728T225152Z-independent-test-report.md`、`20260730T141000Z-independent-successor-archive-acceptance-rejection.md`、`20260731T011000Z-independent-acl-successor-acceptance.md`）。
- 代码核实点（全部为只读静态核对）：
  - `backend/app/services/external_cards.py`（抖音字面卡现状，无 HTTP client）；
  - `backend/app/adapters/media.py`（`_run` 断路器模式、`probe`、环境发现）；
  - `backend/app/services/jobs.py`（作业分支链、异常映射、设置读取）；
  - `backend/app/adapters/storage.py`（容量预检、staging、store_stream）；
  - `backend/app/adapters/sqlite.py`（sources/jobs/audit_events 表结构、create_ingest、create_job、retry_job、EXPORT/BACKUP_TABLES）；
  - `backend/app/services/transfers.py`（备份/导出白名单构建、manifest exclusions）；
  - `backend/app/main.py`（容量预检与审计中间件）、`backend/app/core/config.py`（DataPaths）；
  - `backend/app/domain/models.py`（SourceType、SettingsUpdate）、`backend/app/ports/media.py`；
  - `frontend/src/App.tsx`（976 占位、237/1080/918-978/1232/1238/1245 各引用点）；
  - `tests/unit/test_gitignore.py`、`.gitignore`、`backend/requirements.lock`、`backend/migrations/postgresql/001_initial.sql`；
  - `docs/` 下 api-contract.md、acceptance-matrix.md、test-plan.md、dependency-installation.md、operations-and-recovery.md 的存在性与被引用章节。
- 本审核不做：功能测试、真实平台验证、代码执行验证；这些属测试/验收子智能体职责，本报告不复述其工作。

严重度定义：阻断 = 冻结前必须解决（否则冻结基线将固化虚假或不可实现的安全声明）；主要 = 冻结前应解决或经人工裁决显式接受；次要 = 可在实施中消化，但建议在冻结文本中一并澄清。

## 2. Finding 列表

### F-01（阻断，安全边界）"仅出站连接到白名单平台域"没有强制机制，且与平台实际域名结构冲突

- 证据：REQ-047.2 与威胁模型第 1 行宣称"仅出站连接到白名单平台域""无静默重定向跟随外平台"，但 §7.2 的适配器设计只给出一条 yt-dlp 命令行骨架，没有任何出站域强制机制（无自定义 HTTP/重定向处理器、无回环过滤代理、无 DNS/域名层拦截）。同时白名单按 `bilibili.com`/`douyin.com` 及其子域定义（§2、§6.2），而真实平台的媒体与 API 流量落在其他注册域：B 站 CDN 为 `*.bilivideo.com`，抖音分享链路经 `v.douyin.com` 重定向到 `www.iesdouyin.com`、`aweme.snssdk.com` 及 `*.douyinvod.com` 等——这些域名都不是 `douyin.com`/`bilibili.com` 的子域。按字面执行该需求，真实链接下载必然失败；放宽执行则"仅出站白名单域"变成空话。测试计划也没有覆盖"重定向落到非白名单域必须拒绝"的用例（T-VID-003 只覆盖 API 层 URL 校验；T-VID-004 的"断言无外联"是夹具纪律，不是运行时重定向控制验证）。
- 建议：在需求中显式定义"扩展出站白名单"（平台登记 API/CDN 注册域清单，如 `bilibili.com`、`bilivideo.com`、`hdslb.com`、`douyin.com`、`iesdouyin.com`、`snssdk.com`、`douyinvod.com` 等，实施时以锁定 yt-dlp 版本实测清单为准并写死），并指定强制机制（yt-dlp Python API 自定义请求/重定向处理器，或回环过滤代理）；新增负面用例：URL 或重定向链终到非白名单域 → 拒绝且 failed + 通用脱敏消息；同步加入冻结门禁（见 F-11）。

### F-02（主要，可追溯性）b23.tv 短链与域名白名单校验规则互相矛盾

- 证据：§2 范围与 T-VID-003 把 `b23.tv` 短链归入 bilibili 支持范围，但 REQ-047.1 与 §6.2 的校验规则是"主域或子域匹配 `bilibili.com`/`douyin.com`"，`b23.tv` 是独立注册域，按规则会被拒绝。文档未定义 b23.tv 的放行方式及"仅归 bilibili"在重定向层的强制方式。
- 建议：二选一——把 b23.tv 从范围删除，或在 URL 校验中显式放行 `b23.tv` 并把其重定向终点限制在 bilibili 域（依赖 F-01 的机制），并在 6.2/T-VID-003 中写明。

### F-03（主要，安全边界）"无进展（静默期）断路器沿用 _run 模式"归因错误，_run 并无该能力；内存断路器依赖未锁定的可选 psutil

- 证据：§7.2 声称复用 `backend/app/adapters/media.py:68-131` 的 `_run` 模式"总超时、内存 RSS、staging 磁盘上限、输出静默期心跳"，但 `_run` 实际只有总超时、workspace 磁盘、内存（media.py:96-110 循环）三项检查，没有任何无进展检测；REQ-047.3 却要求"无进展（静默期）"断路器。此外 `_process_memory_bytes`（media.py:48-57）仅在可选导入 psutil 成功时才返回数值，而 `backend/requirements.lock`（10 个包）不含 psutil——本机环境下内存断路器会静默失效（媒体路径没有 jobs.py 解析路径那样的 ctypes 回退）。"沿用 REQ-033a 纪律"同样不成立：REQ-033a 文本并未包含无进展断路器。
- 建议：在需求中明确无进展断路器需新建（如按 staging 目录大小/mtime 增量判静默期），不得声称"复用 _run"；把 psutil 加入 `requirements.lock`（或为下载路径实现 jobs.py 式 ctypes 回退）；T-VID-003 的断路器用例明确覆盖静默期与内存两项在无 psutil 场景下的行为（要么强制依赖、要么声明不可用）。

### F-04（主要，安全边界）骨架参数未禁用环境代理，与"无代理"纪律冲突

- 证据：REQ-047.2 与 §2 纪律要求"无代理"，但 §7.2 骨架参数没有 `--proxy ""`，也没有清除子进程环境中的 `HTTP_PROXY`/`HTTPS_PROXY`；yt-dlp 默认读取环境代理。在常见系统代理环境下（国内用户尤甚），下载流量（含 Cookie 关联请求）会经第三方本地代理转发。
- 建议：骨架增加 `--proxy ""` 并清理子进程环境中的代理变量；T-VID-003 增加"环境存在代理变量时下载不经过代理"的用例（可断言 yt-dlp 收到空代理参数）。

### F-05（主要，安全边界/可追溯性）浏览器直读通道缺少 Windows 解密依赖，且"不产生任何 Cookie 落盘副本"超出调用方可控范围

- 证据：§3/§7.2/步骤 1 的依赖计划只新增 `yt-dlp`；但 `--cookies-from-browser <edge|chrome>` 在 Windows 上解密 Chrome/Edge 的 AES-GCM Cookie 库需要 yt-dlp 的可选依赖 pycryptodomex/pycryptodome，未锁定该依赖则本机浏览器通道大概率不可用。冻结门禁第 1 项只物理验证"yt-dlp 可导入"，浏览器通道的可用性要到 T-VID-005 才可能暴露。REQ-047a.2/4 断言浏览器直读"不产生任何 Cookie 落盘副本"——这是 yt-dlp 内部行为（浏览器库被锁时其 cookie 模块可能先复制临时文件再解密），调用方无法从外部保证；冻结时该断言必须针对锁定版本核实后才可写入需求。
- 建议：依赖计划补充浏览器解密依赖并锁定；冻结门禁增加"浏览器通道本机物理验证一次（或明确标记 unavailable 及原因）"；将"不落盘"措辞改为"适配器自身不产生落盘副本；yt-dlp 锁定版本的内部临时副本行为须实施时核实并在依赖文档中披露"。

### F-06（主要，安全边界）浏览器直读暴露整个浏览器 Cookie 库，威胁模型与 UI 均未披露

- 证据：威胁模型第 2 行只讲双通道 Cookie 的存储治理；但 `--cookies-from-browser` 会在 yt-dlp 子进程内存中解密该浏览器**全部站点**的 Cookie（不只 bilibili/douyin），进程可见用户所有域会话凭据，仅发送目标域 Cookie。文档未向用户披露这一面，REQ-044 修订的 UI 说明也没有相应提示。
- 建议：威胁模型新增/扩展该行："浏览器直读会在下载进程内读取浏览器全部站点 Cookie，仅向目标平台发送其对应 Cookie；锁定版本须人工评估其 Cookie 处理代码"；UI 在选通道时给出相同中文告知；此为一次性人审义务，写入依赖文档。

### F-07（主要，合规/可追溯性）"下载元数据以脱敏形式进入审计与导出 manifest"没有承载记录，"脱敏"变换未定义，且备份会携带 payload 原文 URL

- 证据：REQ-047.5 要求下载元数据（含"链接原文"）以脱敏形式进入审计与导出 manifest；但 `audit_events` 表无内容列（sqlite.py:119-121），`EXPORT_TABLES` 不含 jobs（sqlite.py:139-144），导出 `records.json` 实际不含任何下载元数据——需求与设计之间没有对应的承载记录。jobs 表在 `BACKUP_TABLES`（sqlite.py:145-149），备份快照会含 `payload_json` 中的原始 URL；文档假设 15.3 一边承认 payload 含 url，一边声称"备份快照会含脱敏后的 URL 元数据"，自相矛盾。且"脱敏"变换规则未定义（抖音分享短链可携带跟踪/邀请参数，可能关联用户标识）。
- 建议：定义脱敏变换（如仅保留 host+路径+去 query、或存 hash）；明确载荷存脱敏后 URL（或定义备份期脱敏）；为 REQ-047.5 指定具体承载（sources 字段、video_analyses 风格记录或 job payload + manifest 排除标注），T-VID-003 增加对备份快照内容的断言。

### F-08（主要，合规）会员/付费墙/DRM 内容无明确拒绝条款与测试

- 证据：非目标与决策 2 声明"不绕过 DRM/会员/付费墙"，但 REQ-047 八条只有第 7 条"需登录才可见的内容按失败处理"；没有对会员/付费/DRM 内容的拒绝条款；`bv*[height<=1080]` 高度过滤无法区分大会员高码率（同为 height<=1080）；使用会员 Cookie 下载会员限定内容与"不绕过付费墙"的边界未界定；T-VID-003 负面用例无此场景。
- 建议：REQ-047 增加条款：会员/付费/DRM 内容一律 failed（不利用 Cookie 获取超出用户自身权益的内容；DRM 因 yt-dlp 无法解密而天然失败，应写明）；T-VID-003 加负面用例，T-VID-005 加观察项。

### F-09（主要，合规）平台条款风险未如实披露

- 证据：§15 披露了反爬不稳定性与抖音通道周期性失效，但未披露"自动化下载可能违反抖音/B 站用户协议，法律风险由用户承担"这一层；§1 与决策 6 以"仅个人本地使用、不绕过 DRM/会员"作为合规结论，缺乏条款引证或风险声明。这与本仓库"如实登记、不伪装通过"的文化不一致（风险披露留了反爬，漏了条款）。
- 建议：威胁模型"平台条款与版权"行与 §15 增加条款风险声明（个人本地使用不豁免平台协议违约风险，用户自担；产品仅提供受限技术通道），失败提示通用脱敏保持不变。

### F-10（主要，可追溯性/流程门禁）审核角色未进入 report-schema 与冻结门禁

- 证据：§1 声明"测试与审核由独立子智能体进行"，但 §10 三角色定义为 development/testing/acceptance；`docs/v1-archive/report-schema-v1.json` 的 `author_role` 枚举只有 development/testing/acceptance/release_management/infrastructure（schema 第 35 行），没有 review/audit 角色；冻结门禁 6 项不含"独立审核报告已出具且阻断/主要项已裁决"。本报告为单件 .md，不满足 `same_stem_required` 双件约定。
- 建议：明确审核报告在档案体系中的位置（扩展 schema 的 author_role，或显式声明审核报告为非声明式辅助产物、不进入报告登记），并把"独立审核报告出具 + 阻断项解决、主要项裁决"列入冻结门禁清单。

### F-11（主要，流程门禁）冻结门禁缺少外联控制负向验证项

- 证据：§10 门禁清单 6 项（依赖锁定/FFmpeg 物理验证/真实平台验收/Cookie 治理审计/T-VID-004 合成集成/T-VID-003 单元与回归）齐全，但没有一项验证 F-01 的核心声明（重定向或非白名单域请求被拒绝）。
- 建议：增加门禁项："外联域控制负向验证——合成服务器返回重定向至非白名单域，断言下载拒绝且无该域出站请求"；Cookie 治理审计（门禁 4）明确覆盖"URL 原文不落入备份/导出/日志/审计正文"的静态证据。

### F-12（次要，合规）"仅个人本地使用、不对外分发"与既有导出能力的表述边界

- 证据：决策 6 把"不对外分发"写入合规边界；但 REQ-041 的导出 ZIP 本就包含视频 artifact（EXPORT_TABLES + artifacts 白名单遍历，transfers.py），用户可导出下载的视频后自行分发。产品无法技术上保证"不对外分发"。
- 建议：把边界表述改为"产品不提供分发能力；导出遵循既有用户确认纪律，导出的后续使用由用户自担"，避免写出不可执行的绝对保证。

### F-13（次要，安全边界）`cookie_browser_available` 探测语义模糊

- 证据：REQ-047a.5 与假设 15.6 将可访问性探测定义为"本机存在 Edge/Chrome Cookie 库且可访问"，但不实际尝试解密无法可靠判断（浏览器运行中、密钥不可用、缺解密依赖等）；"仅支持浏览器完全关闭或可解锁的环境"与 Windows DPAPI 下 Chrome/Edge 边运行边解密可行的现实也存在张力。
- 建议：明确探测策略为"存在性检查 + 首次惰性解密尝试"，探测失败一律按 unavailable/failed 处理并给通用引导，不承诺精确可访问性判断。

### F-14（次要，安全边界）2GB 即时检查对象应明确为 staging 目录总量

- 证据：§7.2 说"下载中检查输出文件大小"；yt-dlp 合并/remux 过程中 staging 内会同时存在多个中间文件（分片、.part、合并产物），单文件大小检查可能漏判。
- 建议：明确按 staging 目录总量检查（media.py 的 `_workspace_size` 已支持），与 `maximum_workspace_bytes` 统一。

### F-15（次要，安全边界）取消时 yt-dlp 拉起的 ffmpeg 子进程树清理未交代

- 证据：`_run` 的 finally（media.py:122-129）只 terminate 直接子进程；yt-dlp 合并阶段会自行拉起 ffmpeg，Windows 上 terminate 不杀进程树，取消后 ffmpeg 可能短暂残留并向 staging 继续写入。
- 建议：实施时确认锁定 yt-dlp 版本在收到终止信号后自清理其 ffmpeg 子进程，必要时扩展为进程树终止；并入 T-VID-003 取消清理用例。

### F-16（次要，安全边界）REQ-031 例外条款未点名 REQ-047a，密码使用未显式复禁

- 证据：修订后 REQ-031 保留"cookie/密码/认证"禁项，但例外条款只点名"REQ-047 定义的受限链接获取下载通道"，未把 Cookie 例外（REQ-047a）显式纳入，也未重申"密码/登录凭据绝不使用或保存"（§2 非目标只写"保存"不做）。
- 建议：例外条款写为"唯一例外是 REQ-047 与 REQ-047a 定义的受限下载通道"，并补一句"密码/登录凭据不在例外之列，任何情况下不使用、不保存"。

### F-17（次要，合规）首次引入 network=true 的能力未要求 UI 风险提示

- 证据：§6.3 的 `downloader.network=true` 是产品首个联网能力；REQ-044 修订的 UI 只保留"不会预览或嗅探"提示，未要求"将向平台服务器发起请求"的告知。
- 建议：链接表单提示文案补充联网告知（提交即向所选平台服务器发起下载请求），成本极低且与既有透明度文化一致。

## 3. 通过项

- REQ-031 例外收窄方向正确：例外仅限 REQ-047 通道，并显式排除外部卡、文档解析器、检索、文档导入、媒体 AI 端口（"对该例外一无所知"）；REQ-030 全文不动，`external_cards.py` 现状（无任何 HTTP client、字面保存、无凭据校验）核实无误，抖音字面卡行为不会被本版放宽。
- URL 校验规则完整：HTTPS、主域/子域匹配、无 userinfo、4096 长度上限、回环/内网主机拒绝、拒绝消息不含 URL 内容，均与既有 external_cards.py 纪律风格一致。
- 双通道 Cookie 治理的排除路径设计完整且与代码事实相符：audit_events 无内容列（sqlite.py:119-121）、审计中间件只记路由模板与状态码（main.py:241-248）、日志纪律（REQ-003）、备份/导出为表白名单构建（transfers.py:224-268，只写 state/knowledge.db、records.json、manifest 与 artifacts），`data/state/download/cookies.txt` 按构造不可能进备份/导出/reimport；`.gitignore` 第 16 行 `data/` 已覆盖该文件，`test_gitignore.py:19-25` 核实无误，追加显式断言的建议合理。
- 失败语义映射与现有作业层异常模型完全对齐（blocked/failed/cancelled 对应 jobs.py:189-233 现有处理器）；"无 DB 迁移"结论经核实成立（sources.source_type 与 jobs.kind 在 SQLite 与 PostgreSQL 001_initial.sql:5 均无 CHECK 约束）；create_job 空 source/version 入队（sqlite.py:1188-1197）、retry_job 覆盖 failed/blocked/cancelled（sqlite.py:1398-1413）、create_ingest 一次事务落 source/version/job（sqlite.py:665-709）均与代码一致；"失败不残留半成品 source"（source 仅随 create_ingest 创建）成立。
- 文档全部 `file:line` 引用抽查全部命中：App.tsx 976/237/1080/918-978/1232/1238/1245、media.py 28-30/68-131/133-178、storage.py 49/57、jobs.py 133-181/189-233/360-370、sqlite.py 42-47/106-112/119-121/139-149、main.py 219-248、transfers.py 262、models.py 20-24/140-150、config.py 19-68。
- 可追溯性链完整：REQ-015/031/043/044 修订均有原文对照与新全文；REQ-047/047a → §6-7 设计 → T-VID-003/004/005 → 验收矩阵行（§9）链路闭合；证据要求（双件报告、same_stem_required、author_role/decision_scope/independence/forbidden_content 引用）与 report-schema-v1.json 逐项一致（除 F-10 的角色缺口）。
- 流程立场符合仓库文化：archive-local acceptance 不等于 release approval；`release_readiness` 保持 blocked；DRAFT 冻结纪律（冻结前不改代码）明确；三角色独立、互不代出结论。
- 报告卫生：被审文档自身无凭据、绝对路径、原始运行输出、Cookie 值或请求体；命令骨架仅参数名（`<url>`、`<edge|chrome>`）无实际值。
- 回滚面干净：无 schema 迁移、cookies.txt 即删即回退、设置键删除回退默认、v1.0/v1.1 行为不变声明（§12）与冻结基线一致。

## 4. 总体裁定

**accepted_with_remediation**

理由：文档在流程立场（release blocked、archive-local 口径）、冻结纪律、报告卫生、异常语义映射与可追溯性上完全符合仓库既有文化与代码事实，REQ-031 例外的收窄方向正确；但存在 1 项阻断级问题——核心安全声明"仅出站白名单域、无外平台重定向"没有任何强制机制、与平台真实域名结构冲突且无对应门禁/测试（F-01，F-11 为其门禁补丁），按字面冻结会固化一个不可实现的安全承诺。另有 10 项主要发现集中在浏览器直读通道的依赖与暴露面（F-05、F-06）、代理绕过（F-04）、断路器归因错误（F-03）、元数据脱敏无承载与无定义（F-07）、会员/付费/DRM 与条款合规缺口（F-08、F-09）、审核角色的档案定位（F-10）以及 b23.tv 规则矛盾（F-02）。这些均为冻结前可文本修复的项，不涉及推翻六项已拍板决策。

冻结前提：F-01（及 F-11 门禁项）必须解决；F-02 至 F-09 须在文档中修复或由人工明确裁决接受；次要项（F-12..F-17）建议随修订一并澄清。本报告不构成对实现的批准；测试与验收结论仍由独立测试/验收子智能体另行出具。
