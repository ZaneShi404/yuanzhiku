# v1.2 需求文档独立验证报告（链接获取 / 视频下载）

- 验证对象：`docs/v1-2-requirements.md`（开发子智能体产出，DRAFT）
- 验证角色：testing（独立测试子智能体）
- 验证时间（UTC）：20260812T225424Z
- 决策基线：`reports/development/20260812T222057Z-link-acquisition-v1-2-draft.md`
- 冻结基线：`docs/requirements.md`、`docs/threat-model.md`、`docs/api-contract.md`、`docs/acceptance-matrix.md`、`docs/test-plan.md`
- 代码核实源：`backend/app/domain/models.py`、`ports/media.py`、`ports/repository.py`、`adapters/media.py`、`adapters/sqlite.py`、`adapters/storage.py`、`services/jobs.py`、`services/imports.py`、`services/transfers.py`、`main.py`、`core/config.py`、`migrations/postgresql/001_initial.sql`、`requirements.lock`、`frontend/src/App.tsx`、`tests/unit/test_gitignore.py`、`.gitignore`
- 验证纪律：所有引用均亲自 Read/grep/`git check-ignore` 核实；未运行 pytest 全套；未修改任何被验证文件；未联网；未 git add/commit。

## 1. 验证范围与方法

六类验证全部执行：

1. **内部一致性**：REQ 编号（015/030/031/043/044/047/047a）无重复、无缺口；双通道 Cookie 条款、错误码、字段名、默认值跨章节（2/4.4/6.x/7.x/8/12/14）比对。
2. **基线一致性**：REQ-015/031/043/044 四处"原文"引用与 `docs/requirements.md` 第 19/34/35/46/47 行逐句比对；REQ-030"不变"声明核对；新增 REQ 与未修订条款（REQ-011/016/033/033a/040/041/042/046）冲突扫描。
3. **引用真实性**：文档内全部 45 处 `file:line` 引用逐一用 Read/grep 定位核实。
4. **完整性**：REQ-047/047a 逐条款 → T-VID-003/004/005 及门禁/步骤映射；范围→设计→测试→验收链路；必要步骤（迁移结论、回滚、文档同步、审核门禁、Cookie 治理审计）齐全性。
5. **可测试性**：每条用例触发条件与预期结果；负面用例覆盖（URL 白名单、断路器、取消清理、双通道 Cookie、失败无残留）。
6. **仓库纪律**：绝对路径/凭据/原始输出转储扫描；报告与文档命名惯例核对。

## 2. Finding 列表

共 **11 条**：阻断 0、主要 3、次要 8。

### F-01（主要）REQ-047.5"审计与导出 manifest"元数据落点无可行机制，自述假设 3 结论过度

- 位置：4.3 REQ-047.5；15.3 自述假设 3；8 T-VID-003 用例 7
- 证据：REQ-047.5 要求下载元数据（平台、链接、是否用 Cookie、yt-dlp 版本、格式）"以脱敏形式进入审计与导出 manifest"。核实：
  - `EXPORT_TABLES`（`backend/app/adapters/sqlite.py:139-143`）**不含** `jobs` 与 `audit_events` → 导出 ZIP 的 `records.json` 不会携带这些元数据；
  - `audit_events` 表只有 `id/event_type/entity_id/result/created_at`（`sqlite.py:119-121`），无字段承载平台/版本/格式，审计侧也无落点；
  - 自述假设 3 仅论证 `jobs ∈ BACKUP_TABLES`（`sqlite.py:145-149`）→ 备份快照含脱敏 URL 元数据，只覆盖备份路径；"符合 REQ-047.5"对审计与导出两部分不成立；
  - T-VID-003 用例 7 只断言"不含敏感部分"（缺席断言），未断言脱敏元数据的"存在性"。
- 修复建议：为审计（如 event_type/result 承载脱敏摘要，或新增字段）与导出（如 manifest 扩展或 EXPORT_TABLES 内表增加脱敏字段）各指定一个明确落点；或将 REQ-047.5 收窄为仅备份快照；用例 7 改为"存在性 + 脱敏"双重断言。

### F-02（主要）新增 DELETE 端点与现有 CORS 白名单冲突，门禁手段无法暴露

- 位置：6.1 端点清单；6.3 中间件影响分析；11 步骤 5/6
- 证据：`DELETE /api/v1/settings/download-cookie` 为新增方法，但 `main.py:211-217` 的 CORSMiddleware `allow_methods=["GET", "POST", "PUT"]` **不含 DELETE**；开发模式（前端 Vite 于 `localhost:5173`，跨源请求）下 DELETE 预检（OPTIONS）被拒，浏览器拦截。文档 6.3 逐项分析了审计中间件与容量预检中间件的影响却遗漏 CORS；步骤 5 用 TestClient 验证（不强制 CORS），无法暴露该问题。
- 修复建议：文档明确步骤 5/6 需将 `"DELETE"` 加入 `allow_methods`；T-API-001 扩展或 T-UI-001 增加真实浏览器 DELETE 预检断言。

### F-03（主要）REQ-047.2 出站/代理/重定向约束缺乏可实现设计，字面执行将阻断真实下载

- 位置：4.3 REQ-047.2；7.2 骨架参数；2 范围（b23.tv 短链）；5 威胁模型第 1 行
- 证据：
  1. "无代理"未落到骨架参数——yt-dlp 默认继承环境代理，骨架（7.2）无 `--proxy ""`/`--noproxy "*"` 类参数；
  2. "无静默重定向跟随外平台"无校验机制，而范围（第 2 章）明确包含 `b23.tv` 短链，其解析**必须**跟随 b23.tv→bilibili.com 重定向，二者协调规则未给出；
  3. "仅出站连接到白名单平台域"字面执行将导致真实下载失败——B站媒体实际由 `*.bilivideo.com` 等 CDN 域下发（非 bilibili.com 子域），文档未说明出口域白名单是否包含平台 CDN 域。
- 修复建议：明确出口域允许集合（含平台 CDN 域清单）、重定向链校验规则（白名单内跳转允许、出白名单拒绝）、禁用代理参数；威胁模型"仅出站白名单域"表述同步澄清。

### F-04（次要）T-VID-004 localhost 合成服务器与"无内网/回环主机"URL 校验冲突

- 位置：8 T-VID-004；6.2 url 规则；8 T-VID-003 用例 1；15.5 自述假设 5
- 证据：6.2 与负面用例 1 要求拒绝回环/内网主机 URL，而 T-VID-004 建议真实 yt-dlp 指向 localhost 合成服务器；文档未说明该测试在何层绕过 API 校验（适配器/服务层直调？测试专用主机名？），自述假设 5 仅给"假下载器"备选，两条路径均未闭合此矛盾。
- 修复建议：明确 T-VID-004 使用真实 yt-dlp 时须在适配器/服务层直调（绕过 API 层 URL 校验）且该测试配置不得进入生产路径，或明确改用受控假下载器装配。

### F-05（次要）"作业页无需改动"与极简中文要求不完整

- 位置：7.7 作业页；4.6 REQ-044 修订全文
- 证据：`frontend/src/App.tsx:1116-1126` 的 `jobLabel` 对未知 kind 回退显示原始字符串，`video_download` 将显示英文 "video_download"；REQ-044 要求极简中文，7.7 声称作业页"无需改动"不完整。
- 修复建议：7.7 增加 `jobLabel` 条目 `video_download: '链接下载'` 更新点。

### F-06（次要）断路器引用指向错误，无进展断路器缺实施机制

- 位置：4.3 REQ-047.3；7.2 断路器复用表述；8 T-VID-003 用例 3
- 证据：REQ-047.3 称断路器"全部沿用 `REQ-033a` 纪律"，但 REQ-033a（requirements.md 第 38 行）只规定租约/心跳/取消/优先级/有限重试；断路器定义在 REQ-016（总超时/内存/磁盘/帧数）与 REQ-033（无进度）。7.2 声称复用 `media.py:68-131` 的 `_run` 模式，核实 `_run` 只实现总超时/内存 RSS/磁盘上限/心跳，**无"无进展静默期"检测**；REQ-047.3 要求的无进展断路器无实施机制，与用例 3 未闭环。
- 修复建议：更正引用为 REQ-016/REQ-033，并补充下载产物文件大小增长监测（无进展判定）的设计点。

### F-07（次要）格式选择器兜底可能突破 ≤1080p 决策

- 位置：7.2 骨架参数；14 决策 2；4.3 REQ-047.3
- 证据：`-f "bv*[height<=1080]+ba/b"` 的 `/b` 兜底不带高度过滤，当平台无 ≤1080p 格式时可能选取更高画质，与"限 ≤1080p"决策冲突；REQ-047.3 的产物校验仅要求"尺寸合法"（复用 probe 的宽高>0 检查），不含高度上限校验。
- 修复建议：改用 `-S "res:1080"` 或收紧兜底选择器；或在 probe 校验中增加高度 ≤1080 检查；或明确声明兜底行为属"需登录/会员画质按失败处理"的执行方式。

### F-08（次要）T-VID-003 负面用例未显式覆盖 REQ-047.4 与 REQ-047.7

- 位置：8 T-VID-003 用例清单
- 证据：REQ-047.4（权利声明必填）与 REQ-047.7（多P/合集/直播/需登录可见内容按失败处理、消息脱敏）在八组负面用例中无显式条目；仅可间接经 request_validation（步骤 5 T-API-001 扩展）与脱敏用例（用例 7）追溯，条款→测试映射不完整。
- 修复建议：用例清单补充：rights 缺失/非法值 → 422；多P/合集/需登录链接 → failed 且消息脱敏。

### F-09（次要）验收矩阵"自测标识"列混入独立验收 ID

- 位置：9 验收矩阵新行
- 证据：第 10 章明确 T-VID-005 由 acceptance 角色登记（`independence=independent`），但第 9 章将 "T-VID-005(手工)" 写入"自测标识"列，与冻结矩阵该列语义（开发自测 ID）及三角色分离原则不一致。
- 修复建议：T-VID-005 移出"自测标识"列，在"独立复核重点"列注明由 acceptance 角色登记。

### F-10（次要）威胁模型既有"外部平台合规"行未标注 REQ-047 例外

- 位置：5 威胁模型修订；13 文档同步清单（threat-model.md）
- 证据：REQ-031 修订后"抖音绝对禁止下载"存在 REQ-047 例外；threat-model.md 既有行"外部卡没有 HTTP 请求逻辑；抖音 URL 严格验证/字面保存/仅显式浏览器打开"虽限定外部卡范围仍为真，但未标注例外；同步计划只"表尾追加 5 行"，冻结后表内可能出现"仅显式浏览器打开"与新增下载通道并存的歧义。
- 修复建议：同步时在既有行或新增行交叉注明"REQ-031 例外仅限 REQ-047 通道，外部卡/解析器/检索/导入/AI 端口不受影响"。

### F-11（次要）dependency-installation.md 职责句需加限定以避免误读

- 位置：13 文档同步清单 dependency-installation.md 条目
- 证据：`docs/dependency-installation.md:13` "它们只由本机子进程调用，使用 `shell=False`，不承担下载、联网、网页解析或访问任意 URL 的职责"——主语为 ffmpeg/ffprobe 仍为真，但 v1.2 引入下载通道后易被误读为整个视频管线不联网；同步清单只提"FFmpeg/ffprobe 段落注明同时服务分析与下载"，未要求为该句加限定。
- 修复建议：同步时将职责句加限定，如"ffmpeg/ffprobe 自身不承担联网下载职责；链接下载由 yt-dlp 受限通道单独承担"。

## 3. 通过项列表

1. **REQ 编号**：新增 REQ-047（8 条）/REQ-047a（5 条）与既有 001–046 无重复、无冲突。
2. **基线一致性**：REQ-015/031/043/044 四处"原文"引用与 `docs/requirements.md` 第 19/34/35/46/47 行**逐字一致**；REQ-030 原文引用与"保持不变"声明一致；REQ-031 修订例外边界（仅 REQ-047 通道、外部卡/解析器/检索/导入/AI 端口一无所知）与 12 章回滚声明一致，未与任何未修订条款冲突。
3. **引用真实性**：文档内全部 45 处 `file:line` 引用逐一核实**全部命中**。关键清单：`media.py:68-131`（_run）、`media.py:133-178`（probe）、`media.py:28-30`（环境发现）；`jobs.py:133-181/180-181`（分支链与 else）、`189-195/196-202/203-209/222-233`（异常映射）、`360-370`（设置取值模式）；`main.py:219-239`（容量预检，仅覆盖 /imports/file 与 /videos/local）、`main.py:241-248`（最小审计）、`main.py:138-143`（空 source 入队先例）；`sqlite.py:42-47/106-112`（无 CHECK）、`119-121`（audit 无内容列）、`139-149`（表白名单）、`665-709/677-709`（create_ingest 单事务）、`1188-1197`（create_job）、`1398-1413`（retry_job 支持 failed/blocked/cancelled）；`storage.py:49/57`；`repository.py:76`；`config.py:19-68/66-68`；`models.py:20-24/140-150`；`postgresql/001_initial.sql:5`；`transfers.py:95-102/224-268/262`；`App.tsx:237-239/918-978/976/1080/1128/1232/1238/1245`；`test_gitignore.py:19-25`；`.gitignore:16`；`requirements.lock:1-10`。
4. **关键事实声明核实通过**：requirements.lock 共 10 包且不含 yt-dlp；sources/jobs 无 CHECK 约束；`audit_events` 无内容列；`retry_job` 支持 failed/blocked/cancelled 重试；`create_job` 允许 source/version/artifact/config 全空；backup/integrity_sample 作业按空 source 模式入队；审计中间件只记路由模板+状态码（满足脱敏）；`_build_archive` 只白名单写入 snapshot/records.json/artifacts/manifest，从不遍历数据根；`.gitignore` 第 16 行 `data/` 经 `git check-ignore` 实证覆盖 `data/state/download/cookies.txt`；`docs/v1-archive/report-schema-v1.json` 的 `file_pair.same_stem_required`、`author_role`/`independence`/`decision_scope` 枚举、`safety.forbidden_content` 八项全部存在且与文档引用一致；probe ≤24h 时长与宽高校验（自述假设 4）属实。
5. **内部一致性**：双通道 Cookie 条款（2/4.4/6.2/7.1/7.2/14 章）表述一致；1MB→413、`cookie_source_unavailable`→422、`downloader_unavailable`→503、`internal_error`→500（与 `main.py:196-202` 现有实现一致）跨章节一致；字段名（cookie_source/cookie_browser/downloader 节）与默认值（3600/2048 及 ge/le 边界）一致；`video_link`/`video_download` 命名一致；URL 上限 4096 与现有 `ExternalCardCreate.url` 一致。
6. **完整性**：REQ-047/047a 全部 13 条均有测试映射（仅 F-08 两点需补显式用例）；范围→选型→端口/适配器/作业/API/前端设计→测试→验收矩阵链路完整；必要步骤齐全：迁移结论（7.6 无迁移 + 12 章可逆性）、回滚（11 章逐步骤回滚列 + 12 章）、文档同步（13 章七文件，全部实际存在）、审核门禁（10 章六项清单）、Cookie 治理审计（门禁 4 + T-VID-003 用例 2）。
7. **可测试性**：T-VID-003 八组负面用例均有明确触发条件与预期结果，覆盖 URL 白名单（含子域冒充/userinfo/超长/回环）、双通道 Cookie 治理、断路器、取消清理、产物校验回滚、工具缺失、脱敏、settings 边界；T-VID-004 合成无版权 fixture 纪律与 REQ-046 一致且禁止触网真实平台；T-VID-005 手工验收明确脱敏摘要与如实登记成功率、不作自动化门禁。
8. **仓库纪律**：文档无 Windows 绝对路径、无凭据、无原始输出转储（扫描确认；"user:pw"仅为负面用例合成 URL，合规）；`reports/development/20260812T222057Z-link-acquisition-v1-2-draft.md` 命名符合仓库 UTC 惯例；`git status` 确认工作区无代码改动（自述假设 7 的"冻结前不改动代码"前提成立）。
9. **决策基线一致性**：第 14 章六项决策与决策草案第 8 章逐条相符（双通道/≤1080p/单视频/video_link/脱敏提示/合规边界）；范围与非目标与草案一致。

## 4. 总体结论

**有条件通过（conditionally pass）**

条件：

1. 冻结前必须修复 F-01（REQ-047.5 审计/导出 manifest 元数据落点）、F-02（CORS allow_methods 缺 DELETE）、F-03（REQ-047.2 出站白名单/代理/重定向的可实现设计）——三条主要 finding 均涉及需求→设计/实施链路缺口，不修复会在实施或验收阶段暴露。
2. F-04 至 F-11（8 条次要）应在冻结版修订或首轮实施任务分解中一并处理，其中 F-04/F-06 建议在测试计划落地前定稿。

未发现阻断级缺陷：无 REQ 编号问题、无原文引用失真、无与冻结基线的直接冲突、45 处 file:line 引用全部属实。文档整体质量良好，开发子智能体的引用与事实核实工作基本可信，仅个别机制论证（自述假设 3）存在过度结论。

---

- 报告作者角色：testing（独立测试）
- 被验证文档：`docs/v1-2-requirements.md`（未做任何修改）
- 本报告仅代表本地可归档测试结论（archive-local），不构成发布门禁变更；release_readiness 维持 blocked。
