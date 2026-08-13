# v1.2 链接获取实施独立验证报告

- 报告 ID：20260813T010642Z-v1-2-implementation-verification
- 角色：独立测试子智能体（testing）；不信任开发角色任何结论，全部亲自复核
- 对象：master 上 6 个实施提交 fa7a7f3、397e2db、247c829、1bc7530、b71e83e、d660c26
- 权威规范：`docs/v1-2-requirements.md`（第 4 章 REQ、第 6/7 章接口、第 8 章测试计划、第 11 章步骤、第 13 章文档同步清单）
- 结论：**有条件通过**（零阻断/主要发现；3 条次要发现；冻结门禁若干项尚未满足，按项目政策 release 保持 blocked）

## 1. 验证范围与方法

方法：

1. 提交与工作树：`git status`/`git log`（父子链、顺序、干净度）；6 个提交逐个查看 diff 范围与提交信息一致性。
2. 静态核查：规范 4.3/4.4/6.x/7.x 逐条对照实现文件（file:line 级证据）；T-VID-003 11 组负面用例逐组审查断言是否"真断言"（防空转）。
3. 独立跑测：先跑目标文件（test_video_download / test_api / test_gitignore），再跑一次全套 tests/unit 复核开发角色宣称的 177/2/0。
4. 环境物理验证：本机 ffmpeg/ffprobe 可用性、venv 中 yt-dlp/psutil 版本与 requirements.lock 一致性。
5. 前端离线构建烟测（tsc + vite build，不触网）。
6. 回归风险：既有回归文件（test_video_media、test_job_leases、test_v1_archive、test_defect_fixes 等）全套实跑 + 静态审查 v1.1 行为路径未被改动。

限制：禁止联网，故未做真实平台下载（T-VID-005 属 acceptance 角色手工验收，本报告不代出）；未运行 Compose/PostgreSQL 集成（POSTGRES_TEST_URL 未配置，2 条跳过与此一致）。

## 2. 提交与工作树核查

- 6 个提交全部存在、线性父子链正确：fa7a7f3 ← 397e2db ← 247c829 ← 1bc7530 ← b71e83e ← d660c26（父为 1a841f5）。
- 工作树干净（`git status --porcelain` 对已跟踪文件无输出；仅存在未跟踪的既有审查报告与本次验证报告）。
- 各提交范围与信息一致：
  - fa7a7f3：`ports/media.py`(+46/-1) + 新文件 `adapters/downloader.py`(615 行) — 端口与回环过滤代理适配器，相符。
  - 397e2db：`domain/models.py`(+91)、`main.py`(+87/-4)、`services/imports.py`(+74)、`services/jobs.py`(+161/-4) — 作业流/API/Cookie 端点，相符。
  - 247c829：`alembic/versions/008_*.py`(新)、`sqlite.py`(+49/-2)、`postgres.py`(+4)、`config.py`(+6/-1)、`transfers.py`(+36/-14) — provenance 持久化与归档排除，相符。
  - 1bc7530：`frontend/src/App.tsx`(+91/-8)、`styles.css`(+2) — 表单/Cookie 控件/断路器设置 UI，相符。
  - b71e83e：`test_video_download.py`(969 行新增)、`test_api.py`(+58)、`test_gitignore.py`(+2) — 负面用例与合成集成，相符。
  - d660c26：docs 7 个文件(+75/-11) — 第 13 章同步清单逐文件落实，相符。
- 范围说明（非缺陷）：v1.2 相关提交实际为 7 个——`1a841f5`（chore: lock yt-dlp and psutil，实施步骤 1）先于 fa7a7f3 落地，未计入"6 个实施提交"口径。顺序与内容无冲突。

## 3. 测试复核结果（本子智能体亲自执行）

| 轮次 | 命令对象 | 结果 | 耗时 |
|---|---|---|---|
| 1 | test_video_download.py + test_api.py + test_gitignore.py | **55 passed, 0 failed, 0 skipped** | 306s |
| 2 | tests/unit 全套 | **177 passed, 2 skipped, 0 failed**（2 warnings，均为 zipfile 重复成员测试的预期告警） | 867s |

- 与开发角色宣称的 **177 passed / 2 skipped / 0 failed 完全一致**，声明属实。
- 2 skipped = test_postgres_repository.py 两条需要 POSTGRES_TEST_URL 的 PG 集成用例（本机未配置，skipif 条件与理由相符），与 v1.2 实现无关。
- 关键点：T-VID-004 合成集成（真实 yt-dlp 子进程指向 localhost fixture + 测试注入注册域/保留段豁免 + 环境代理指向死端口）在本机**无 ffmpeg** 条件下实跑通过——证明流量只经回环代理、单文件 MP4 无需 ffmpeg 合并可全链路成功。
- 前端离线构建烟测：`tsc -b && vite build` 通过（1577 模块，2.24s）。

## 4. REQ → 实现 → 测试映射

### 4.1 REQ-047（9 条）

| # | 需求要点 | 实现证据（file:line，相对仓库根） | 测试断言（tests/unit/test_video_download.py 等） |
|---|---|---|---|
| 1 | 平台白名单 + URL 严格校验 + 拒绝消息不含 URL | backend/app/domain/models.py:159-206（validate_download_url：HTTPS、无 userinfo、保留段拒绝、主域/子域匹配、≤4096；b23.tv 显式放行）；main.py:387-396（invalid_url 静态消息） | test_download_url_whitelist_rejections（11 组参数化：http/非白名单/子域冒充/userinfo/超长/127.0.0.1/10.x/192.168.x/169.254.x/[::1]，断言 422+invalid_url+消息不含 URL）；test_download_url_platform_mismatch_rejected；test_download_url_whitelist_accepts_registered_hosts_and_b23_tv |
| 2 | 锁定 yt-dlp 无 shell 子进程 + 回环过滤代理逐连接校验注册域 | downloader.py:517-547（sys.executable -m yt_dlp、shell=False、stdin=DEVNULL、--ignore-config/--no-cache-dir/--proxy 127.0.0.1:port）；downloader.py:44-47（注册域清单与 7.2.1 逐字一致）；downloader.py:69-373（CONNECT/明文双路径校验、resolve-then-connect、连接后对端复核、IP 字面量与保留段拒绝、内存计数表）；downloader.py:434-437（清空 4 个代理环境变量）；requirements.lock（yt-dlp==2026.7.4、psutil==7.2.2，venv 物理导入 2026.07.04/7.2.2） | test_proxy_rejects_unregistered_connect_without_outbound_bytes（真实 socket 发 CONNECT evil.example：无 200 隧道应答、denied_hosts 记录、connected_hosts 为空）；test_proxy_rejects_reserved_resolutions_in_production_mode（7 组保留段拒绝、3 组公网放行）；test_proxy_relays_registered_connect_via_validated_peer（真实 CONNECT 中继）；test_synthetic_redirect_to_unregistered_domain_fails_closed（真实 yt-dlp 遇 302→evil.example：DownloadInputInvalid + denied_hosts 含 evil.example + 出站 ⊆ {localhost}）；test_synthetic_download_full_chain（环境代理指向死端口仍成功 → 证明显式 --proxy + 清空环境变量生效） |
| 3 | per-job staging + 总超时/内存/磁盘/无进展断路器 + 协作取消 + probe 校验 + 高度 ≤1080 + ≤2GB + 容量预检 + 流式 artifact | downloader.py:551-600（监控循环：deadline、_workspace_size 磁盘、_process_memory_bytes、连续两窗口无增长且无输出 → no_progress）；downloader.py:440-472（psutil 进程树终止）；jobs.py:431-553（staging 创建/清理、probe 复用、高度后置断言、check_capacity、store_stream 流式写入） | test_downloader_circuit_breakers（4 模式：timeout/workspace_limit/memory_limit/no_progress——真实假 yt_dlp 子进程 + 真实监控循环，断言异常原因精确匹配）；test_downloader_cancel_terminates_process_tree（假 yt_dlp 拉起子进程 sleep(300)，取消后轮询断言 child_pid 确实不存在——真进程树终止）；test_product_validation_rejects_bad_products_without_artifact（probe 失败/height=1081 → failed、无 source、artifacts 目录空）；test_height_1080_boundary_is_accepted（1080 通过） |
| 4 | rights 必填 | models.py:222-241（RightsCategory 必填）；jobs.py:454（防御性二次校验） | test_download_link_requires_rights_and_valid_categories（缺失/非法/非法分类 → 422 request_validation） |
| 5 | provenance 表（进 EXPORT/BACKUP_TABLES）+ 脱敏变换 + 审计最小化 + Cookie 绝不入 DB/日志/备份/导出 | models.py:209-219（sanitize_download_url：去 userinfo/query/fragment、截断 4096）；sqlite.py:726-741（与 source/version/job 同事务 INSERT，source_id UNIQUE）；sqlite.py:141-158（EXPORT/BACKUP_TABLES + 列登记）；main.py:408-424（payload 只存脱敏链接）；transfers.py（schema 5→6、exclusions 增 state/download）；audit_events 无内容列 | test_download_success_creates_source_provenance_and_analyze_job（provenance 7 字段齐全 + url_sanitized 无 ?/# + payload 无 query 原文 + audit_events 恰 5 列 + 仅 event_type/entity_id/result）；test_backup_snapshot_and_export_carry_sanitized_provenance（备份快照 payload 无 ?p=9、导出 records.json 含 provenance 行）；test_sanitize_download_url_strips_userinfo_query_and_fragment |
| 6 | video_download 作业 kind + 成功同事务建 source/version/artifact + 自动入队 video_analyze + 失败无残留 | jobs.py:162-163（分支）、jobs.py:431-553（全流程）；imports.py:127-199（create_ingest 同事务 + artifact 补偿删除） | test_download_success_creates_source_provenance_and_analyze_job（source_type=video_link、video_analyze 自动入队且 queued）；test_download_job_failure_semantics_are_generic + test_download_cancel_leaves_no_staging_or_source（blocked/failed/cancelled 三态：list_sources()==[]、staging 目录空、workspace 已删）；test_synthetic_download_full_chain（video_analyze 随后成功） |
| 7 | 仅单视频；多P/合集/直播/需登录按失败、消息脱敏 | downloader.py:521（--no-playlist）；jobs.py:244-249（DownloadInputInvalid → failed 固定脱敏消息） | test_platform_rejections_are_failed_with_generic_message（消息 == 固定脱敏文案、不含 "DRM"/"会员"/URL）；test_download_job_failure_semantics_are_generic 同断言 |
| 8 | 工具缺失 blocked / 外部原因 failed 可重试、绝不静默切换 | main.py:397-402（503）；jobs.py:457-458（capability 检查→blocked）、jobs.py:238-249；sqlite.py:1438-1453（retry_job 支持 failed/blocked/cancelled） | test_download_job_blocked_when_tools_missing（503+downloader_unavailable、capabilities.enabled=false、作业 blocked）；test_provenance_failure_rolls_back_and_retry_creates_single_source（重试从头执行：恰 1 source、1 version、1 provenance 行） |
| 9 | 会员/付费/DRM 拒绝 + 公开免费档位 ≤1080p | downloader.py:529（-S res:1080）；jobs.py:520-522（probe 高度 ≤1080 后置断言双保险） | test_product_validation_rejects_bad_products_without_artifact（height_too_high 场景）；test_platform_rejections_are_failed_with_generic_message（DRM/会员 → 通用脱敏） |

### 4.2 REQ-047a（5 条）

| # | 需求要点 | 实现证据 | 测试断言 |
|---|---|---|---|
| 1 | cookies.txt 单通道、1MB、覆盖、幂等删除 | main.py:285-313（读 1MB+1 判定 413、.part 原子替换、DELETE unlink(missing_ok=True)）；downloader.py:40（MAX_COOKIE_BYTES） | test_cookie_upload_size_limit_overwrite_and_idempotent_delete（413+cookie_file_too_large+文件未创建；两次上传覆盖；两次 DELETE 均 204；capabilities 状态翻转） |
| 2 | use_cookie 显式选择、未导入 → 422、绝不静默回退 | main.py:403-407；jobs.py:479-487（作业内二次防御） | test_use_cookie_without_imported_file_rejected_without_fallback（422+cookie_file_unavailable+未创建任何 video_download 作业） |
| 3 | Cookie 绝不进 DB/日志/API/备份/导出/reimport | provenance 表无 Cookie 列（sqlite.py 建表语句）；transfers.py:270（manifest exclusions 含 state/download）；备份快照只读白名单表；操作日志只记路由模板+状态码（main.py:248-255） | test_backup_snapshot_and_export_carry_sanitized_provenance（快照 payload 断言）；test_gitignore.py（data/state/download/cookies.txt 被 data/ 根忽略的回归锚点）。缺口：无"备份 ZIP 成员不含 cookies.txt"的直接文件级断言（依赖既有白名单写入机制，见 F-3 注） |
| 4 | 拷贝注入 staging、作业结束即删、原文件不修改 | jobs.py:478-489（shutil.copyfile 入 staging）、jobs.py:550-553（finally 统一清理） | test_cookie_copy_is_staging_scoped_and_original_untouched（成功路径：拷贝内容一致、作业后 workspace 不存在、原文件字节未变）；test_download_cancel_leaves_no_staging_or_source（取消路径清理，但未带 Cookie，见 F-3） |
| 5 | cookie_file_available + UI 禁用引导 | downloader.py:409-425（存在且 ≤1MB、OSError→False）；frontend/src/App.tsx（cookie 开关 disabled+cookieAvailable 提示；提交按钮 !downloadEnabled 时显示"链接下载工具不可用（需 yt-dlp 与 FFmpeg/ffprobe）"） | test_cookie_upload_size_limit...（capabilities 翻转断言）；test_download_link_endpoints_in_openapi_and_capabilities（downloader 节 6 键齐全） |

### 4.3 T-VID-003 11 组负面用例"真断言"审查结论

全部 11 组均为真断言，无空转，具体如下（防空转要点单独说明）：

1. URL 白名单：真断言（11 组参数化，每组校验 422+code+消息无 URL 内容）。
2. Cookie 治理：真断言（413/覆盖/幂等删除/无静默回退均验证状态与副作用；**use_cookie=false 时断言 downloader 未收到 cookie_path**——真验证"不读取"）。见 F-3 的路径覆盖缺口。
3. 断路器：真断言——**真实假 yt-dlp 子进程 + 真实监控循环**，4 种断路各自断言异常原因字符串；取消用例断言**孙进程 pid 确实消亡**（非仅返回值）。
4. 取消清理：真断言（staging 目录空 + workspace 不存在 + 无 source）。
5. 产物校验回滚：真断言（artifacts 目录递归为空、无 source）。
6. 工具缺失：真断言（503+作业 blocked 双路径）。
7. 出处与脱敏：真断言（provenance 行 7 字段、audit_events 恰 5 列、快照 payload 无 query、导出 records.json 行、**DROP 表制造事务失败后断言回滚/补偿/重试后恰 1 source 1 version 1 provenance**——真验证幂等）。
8. 权利声明：真断言（3 个 422 场景）。
9. 多P/DRM：真断言（消息等于固定脱敏文案且不含敏感词/URL）。
10. 外联控制：真断言——**真实 TCP 连接代理**（未登记域无 200 隧道、denied_hosts 计数、connected_hosts 空）；**真实 yt-dlp 遇 302→evil.example 断言 DownloadInputInvalid 且 evil.example 在 denied_hosts**（真验证拒绝行为而非仅测返回值）；环境代理覆盖用"死端口代理 + 下载仍成功"反证。
11. settings 边界：真断言（6 组越界 422 + 3 组合法保存往返）。

### 4.4 错误码表（规范 6.4）逐条可达性

| code | 实测可达 | 证据 |
|---|---|---|
| request_validation (422) | 是 | rights 缺失/非法、非法分类、settings 越界（目标文件 55 通过内实跑） |
| invalid_url (422) | 是 | 11 组参数化 + 平台不匹配 |
| unsupported_platform (422) | 是 | platform=youtube |
| cookie_file_unavailable (422) | 是 | use_cookie 无导入 |
| cookie_file_too_large (413) | 是 | 1MB+1 字节上传 |
| downloader_unavailable (503) | 是 | 本机无 ffmpeg（真实现环境） |
| 404（框架） | 是 | 既有回归（全套通过） |
| internal_error (500) | 是 | 既有通用处理器，回归覆盖 |

### 4.5 capabilities 真实性

- 本机无 ffmpeg/ffprobe（PATH 与 YUANZHIKU_FFMPEG_BIN/FFPROBE_BIN 均未设置）→ `downloader.enabled=false` 实测成立，未伪装可用。
- UI 引导正确：提交按钮禁用并显示"链接下载工具不可用（需 yt-dlp 与 FFmpeg/ffprobe）"；Cookie 开关禁用并显示"（尚未导入，请在设置页导入后使用）"；联网告知文案"提交即向所选平台服务器发起下载请求"存在。
- `version` 报 2026.07.04（venv 物理导入一致）；yt-dlp 缺失时返回 "unavailable"（代码路径），非伪造。

## 5. v1.1 回归风险核查

- 全套既有回归实跑通过：test_video_media(6)、test_job_leases(7)、test_v1_archive(30)、test_defect_fixes(20)、test_evidence_bundles(16)、test_database_url_selection(7)、test_postgres_repository(15 通过+2 跳过)。
- 静态审查：
  - jobs.py：仅追加 video_download 分支，parse/video_analyze/backup/integrity_sample 分支与异常映射未动。
  - models.py：SettingsUpdate 追加 3 字段（additive）；SourceType 追加 VIDEO_LINK（无 CHECK 约束，7.7 结论核实无误）。
  - sqlite.py：create_ingest 新增可选参数；initialize() v7 块幂等（版本集合判断）；purge 追加 provenance 级联删除。
  - transfers.py：schema 5→6，SUPPORTED 含 1-6；reimport 对 schema 1-4（legacy_expected）与 schema 5（pre_provenance_expected）的兼容分支保留——v1.0/v1.1 归档可还原性由 test_v1_archive（30 用例）实跑确认。
  - main.py：CORS allow_methods 仅追加 DELETE；前端 request() 对 204 空体的处理不影响既有 JSON 端点。
  - config.py：DataPaths.create() 追加 download 目录（additive）。
- 结论：无 v1.1 行为劣化证据。

## 6. Finding 列表

| # | 严重度 | 摘要 | 证据 | 修复建议 |
|---|---|---|---|---|
| F-1 | 次要 | **平台标题未实现**：规范 6.2/api-contract 承诺"title 缺省时下载成功后使用平台标题，退化为'未命名视频'"，实现直接退化，从未提取平台标题 | ports/media.py:75-81 DownloadedVideo 无 title 字段；downloader.py:517-535 命令骨架无 --print title/-J 元数据；jobs.py:537 传空标题；imports.py:158-159 直接退化 | 二选一：(a) 端口/适配器返回标题（--print title 或 --dump-single-json 后取 title 字段）并写入 source；(b) 修改规范删除"使用平台标题"承诺（需走规范修订流程） |
| F-2 | 次要 | **测试环境依赖硬编码**：test_api.py 断言 `downloader["enabled"] is False`，在装有 ffmpeg 的机器上必挂；本机通过仅因无 ffmpeg | tests/unit/test_api.py（test_download_link_endpoints_in_openapi_and_capabilities） | 改为断言与 `shutil.which(ffmpeg/ffprobe)` 的一致性（enabled == 两者可用），或经 monkeypatch 固定 YUANZHIKU_FFMPEG_BIN/FFPROBE_BIN 指向不存在路径 |
| F-3 | 次要 | **Cookie 拷贝"三条路径即删"覆盖缺口**：成功+Cookie（:287-303）与取消无 Cookie（:461-471）有断言，失败/取消且 use_cookie=true 无直接断言；:301 `call["workspace"].name != "cookies.txt"` 对"拷贝位于 staging 内"是弱断言。实现侧清理在共享 finally（jobs.py:550-553）路径无关，风险低 | tests/unit/test_video_download.py | 追加一个 use_cookie=true + 取消/失败场景，断言 staging 内 cookies.txt 拷贝不存在且原文件字节未变；另可补"备份 ZIP 成员不含 cookies.txt"文件级断言（REQ-047a.3 强化） |

观察项（非缺陷，不阻塞）：

- O-1 冻结门禁未满足项：本机未安装 ffmpeg/ffprobe（门禁 2）；T-VID-005 真实平台手工验收未执行（门禁 3，属 acceptance 角色）；注册域清单未经锁定版本真实链接出站实测比对（假设 8）。均属 v1.2 冻结/验收前待办，与实施质量无关；release 保持 blocked 符合项目政策。
- O-2 下载内存断路器复用 `video_memory_limit_mb`（jobs.py:464），规范 7.5 未定义独立下载内存设置——属合理实现选择，建议在文档补一句口径说明。
- O-3 提交计数口径：v1.2 相关实际 7 个提交（含先行依赖锁定 1a841f5），"6 个实施提交"不含它；无冲突。
- O-4 "失败时消息为'本地处理失败'"：StorageLimitError（容量预检）落入通用异常处理器而非下载专属脱敏消息（jobs.py:257-268）——消息仍通用无敏感内容，仅文案差异。

## 7. 开发角色自述假设逐条评估（规范 §15 的 8 条 + 测试计数声明）

| # | 假设 | 核实结论 |
|---|---|---|
| 1 | yt-dlp/psutil 锁定版本在步骤 1 确定 | **属实**：requirements.lock 现含 yt-dlp==2026.7.4、psutil==7.2.2（原 10 包零漂移）；venv 物理导入成功（2026.07.04/7.2.2）。可接受 |
| 2 | create_job（空 source/version）入队 → create_ingest 落库可行 | **属实**：main.py:409-425 以空 id 入队；jobs.py:431-553 成功后经 imports.downloaded_video → create_ingest 同事务落库并入队 video_analyze（sqlite.py:684-745）。可接受 |
| 3 | payload 只存脱敏链接；出处存 provenance（进 EXPORT/BACKUP_TABLES）；审计最小化 | **属实**：main.py:408-424、sqlite.py:726-741、audit_events 5 列断言实测。可接受 |
| 4 | probe 复用 + 高度 ≤1080 后置断言双保险 | **属实**：jobs.py:510-522；height=1081 拒绝/1080 接受实测。可接受 |
| 5 | T-VID-004 服务层直调 + 测试专用注册域清单注入 | **属实**：_LoopbackExemptProxy 仅测试子类（test_video_download.py:695-703），生产代码无豁免分支；出站计数断言不受豁免影响（实测 connected ⊆ {localhost}）。可接受 |
| 6 | cookie_file_available＝文件存在且 ≤1MB，探测失败按不可用 | **属实**：downloader.py:409-415（OSError→False）；capabilities 翻转实测。可接受 |
| 7 | file:line 引用以 2026-08-13 工作区为基准 | **基本属实**：核查的引用在实施时准确；实施后有少量行号漂移（如 CORS 现位于 main.py:218-224），不影响实质。可接受 |
| 8 | 注册域清单为初始登记集，需真实链接实测比对 | **部分成立（未决）**：清单与规范 7.2.1 逐字一致；但"以锁定 yt-dlp 实测真实链接出站域集合逐项比对"尚未执行——属 T-VID-005/审核门禁待办，须在冻结前完成。假设本身表述诚实 |
| 9 | 测试计数 177 passed / 2 skipped / 0 failed | **属实**：本子智能体独立全套复核为 177/2/0（867s），目标文件单独复核 55/0/0 |

## 8. 总体结论

**有条件通过（conditional pass）**。

- 实施质量：6 个提交范围与信息一致、工作树干净；REQ-047 9 条与 REQ-047a 5 条全部找到实现证据与真断言测试；T-VID-003 11 组负面用例无空转；错误码表逐条实测可达；capabilities 如实报告（本机无 ffmpeg → enabled=false，UI 引导正确）；v1.1 回归无劣化证据；开发角色宣称的 177/2/0 亲自复核属实；前端离线构建通过。
- 未发现阻断/主要缺陷；3 条次要发现（F-1 平台标题未实现、F-2 测试环境依赖硬编码、F-3 Cookie 三路径覆盖缺口）建议在冻结前修复或裁决。
- 冻结门禁待办（与实施质量无关）：FFmpeg/ffprobe 物理可用验证、T-VID-005 真实平台独立验收（acceptance 角色）、注册域清单真实链接实测比对与独立审核报告。在这些门禁满足前，v1.2 不应冻结；release_readiness 按项目政策保持 blocked。

---

## 9. 关闭确认（2026-08-13 追加）

实施角色随后提交 3 个修复提交（2df6c5a、9d0cc73、4db48af，线性接于 d660c26，HEAD=4db48af，工作树仍干净），本小节逐条核对修复落地并抽跑验证。

### 9.1 本报告 3 条 finding 关闭状态

| Finding | 关闭状态 | 修复证据（file:line 为修复后 HEAD 状态） | 复核测试 |
|---|---|---|---|
| F-1 平台标题未实现 | **已关闭** | 2df6c5a：downloader.py 增加 `--print "%(title)s"` + stdout 受限捕获（512KB 上限纪律，输出字节计入无进展断路器）；`_extract_title` 清洗控制字符/换行并截断 500；ports/media.py `DownloadedVideo.title` 字段；jobs.py:527-529 标题优先级贯通（用户显式 > 平台捕获 > 落库侧"未命名视频"）；前端占位文案同步为"可留空，默认使用平台标题" | 4db48af：`test_download_title_backfills_from_downloader_when_not_submitted`、`test_download_title_degenerates_to_unnamed_when_capture_empty`、`test_download_title_explicit_submission_wins_over_captured`、`test_extract_title_cleans_and_truncates`、`test_synthetic_download_captures_platform_title`（**真实 yt-dlp** 下载合成 og:title 页面，断言捕获"合成平台标题"；直链退化断言） |
| F-2 测试环境依赖硬编码 | **已关闭** | 4db48af：test_api.py 改为与 `shutil.which(YUANZHIKU_FFMPEG_BIN/FFPROBE_BIN)` 探测一致——`assert downloader["enabled"] is tools_available`；503 用例按工具可用性分支（可用时断言 201+kind，不可用时断言 503+code），语义不变 | 本机（无 ffmpeg）实测：enabled=False 一致性断言通过、503 分支通过 |
| F-3 Cookie 拷贝三路径清理覆盖缺口 | **已关闭** | 4db48af：`test_cookie_copy_removed_on_failure_and_cancel`（参数化 failed/cancelled，use_cookie=true）断言 `cookie_path.parent == workspace`（替换原弱断言）、作业后 workspace 不存在、原文件字节未变、无 source 残留；成功路径既有断言保留，三条路径齐备 | 参数化 2 例实测通过 |

### 9.2 审核报告（20260813T005512Z-v1-2-implementation-review.md）F-01/F-02/F-04/F-05 交叉核对

| 审核 Finding | 关闭状态 | 修复证据 |
|---|---|---|
| F-01 保留段拒绝未覆盖 100.64.0.0/10（CGNAT） | **已关闭** | 2df6c5a：downloader.py `_reject_resolved_ip` 与 models.py `_host_is_reserved` 统一改为 `not address.is_global`（覆盖 CGNAT/文档段/广播段等全部非公网单播）；测试追加 100.64.0.1、100.127.255.255、192.0.2.1、169.254.169.254（代理与 URL 层双断言） |
| F-02 probe 阶段取消消息误为"视频分析已取消" | **已关闭** | 2df6c5a：jobs.py 在 probe 调用处捕获 `MediaProcessingCancelled` → 转抛 `DownloadProcessingCancelled`，落"链接下载已取消"；新增 `test_probe_phase_cancel_uses_download_cancel_message` |
| F-04 cookies.txt 1MB 判定在请求体完整解析之后 | **已关闭** | 9d0cc73：新增 `cookie_upload_length_preflight` 中间件（POST /settings/download-cookie 按 Content-Length > 1MB+64KB 表单开销边界立即 413，不落临时盘）；端点内解析后 1MB 二次校验保留兜底；新增 `test_cookie_upload_content_length_preflight_rejects_before_parsing` |
| F-05 test_api 依赖"测试机无 FFmpeg" | **已关闭** | 4db48af：断言与环境探测一致化（同 F-2） |

另注：审核 F-03（平台标题）与本报告 F-1 同源，随 2df6c5a 一并关闭——"实现回填 vs 修订规范"二选一已按实现路径落地，规范 6.2 承诺与代码现已一致。审核观察项 O-01（下载内存断路器复用 video_memory_limit_mb 的耦合未文档化）与 O-02（query 剥离可能下载非预期分P）仍为开放观察项，不阻塞、不在本次修复范围内。

### 9.3 修复后抽跑计数（本子智能体亲自执行，未跑全套）

`tests/unit/test_video_download.py` + `tests/unit/test_api.py`：**65 passed / 0 failed / 0 skipped**（432s）。对比修复前同两文件 51 通过（55 目标中含 test_gitignore 1 条），新增约 14 条用例全部通过（含真实 yt-dlp 标题捕获、CGNAT 双断言、Cookie 失败/取消清理参数化、Content-Length 预检、probe 取消消息、环境一致化断言）。git status 无代码改动残留。

### 9.4 关闭确认结论

**通过（本报告 3 条 finding 全部关闭，最终结论由"有条件通过"收敛为"通过——实施质量维度"）**。

- 本报告 F-1/F-2/F-3 与审核报告 F-01/F-02/F-03/F-04/F-05 全部落地修复并经抽跑验证；修复无新引入回归迹象（既有用例全部保持通过）。
- 仍有效的先决条件（与实施质量无关，属冻结门禁层面）：FFmpeg/ffprobe 物理可用验证（门禁 2）、T-VID-005 真实平台独立验收（门禁 3）、注册域清单真实链接实测比对（门禁 3/6）、独立审核报告正式归档；在此之前 v1.2 不应冻结，release_readiness 按项目政策保持 blocked。
