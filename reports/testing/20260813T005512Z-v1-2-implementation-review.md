# v1.2 链接获取功能实现——独立代码安全与流程审核报告

- 报告 ID：20260813T005512Z-v1-2-implementation-review
- 角色：独立审核（review）
- 审核对象：master 上 v1.2 链接获取功能 6 个实施提交（fa7a7f3、397e2db、247c829、1bc7530、b71e83e、d660c26，另核对依赖锁定提交 1a841f5）
- 审核依据：docs/v1-2-requirements.md（2026-08-13 拍板修订版，权威规范）
- 审核方式：git diff 逐提交比对 + 重点文件静态审读 + 定向轻量验证（未运行全套 pytest，与测试角色并行互不干扰）；无代码修改、无 commit
- 范围外说明：真实平台手工验收（T-VID-005）、FFmpeg 物理可用性、前端 npm 构建与真实浏览器 UI 属其他角色门禁，不在本次代码审核范围内。

## 审核范围

| 领域 | 覆盖文件 | 结论 |
|---|---|---|
| 回环过滤代理与出站控制 | backend/app/adapters/downloader.py | 核心机制正确（详见通过项与 F-01） |
| 下载适配器（参数/断路器/进程树/代理启停） | backend/app/adapters/downloader.py、backend/app/adapters/media.py | 通过 |
| 作业流与失败语义 | backend/app/services/jobs.py、backend/app/services/imports.py | 通过（F-02 文案） |
| Cookie 治理 | backend/app/main.py、backend/app/adapters/downloader.py、jobs.py | 通过（F-04 上传体预处理） |
| 脱敏与泄露面 | backend/app/domain/models.py、main.py、sqlite.py | 通过 |
| provenance 承载与迁移 | backend/app/adapters/sqlite.py、backend/app/adapters/postgres.py、backend/alembic/versions/008_*.py、backend/app/core/config.py | 通过 |
| 归档 schema 5→6 与旧档兼容（假设 6 重点） | backend/app/services/transfers.py、scripts/archive_v1.py、scripts/verify_v1_archive.py | 通过 |
| API/中间件 | backend/app/main.py、backend/app/domain/models.py | 通过 |
| 前端 | frontend/src/App.tsx、frontend/src/styles.css | 通过 |
| 测试资产质量 | tests/unit/test_video_download.py、tests/unit/test_api.py、tests/unit/test_gitignore.py | 通过（F-05 环境敏感断言） |
| 文档同步（第 13 章清单 7 文件） | docs/ 下 7 个文件 | 通过 |

## Finding 列表

> 无阻断项。5 条次要项 + 2 条观察项。

### F-01（次要）保留段拒绝未覆盖 100.64.0.0/10（运营商级 NAT 共享段）

- 位置：`backend/app/adapters/downloader.py` `_reject_resolved_ip`（约 214-233 行）；同型代码 `backend/app/domain/models.py` `_host_is_reserved`（约 157-166 行）
- 证据：两处均以 `is_loopback or is_private or is_link_local or is_reserved or is_multicast or is_unspecified` 判定。在项目 venv（Python 3.13.0）实测，`100.64.0.1` 与 `100.127.255.255` 上述属性全为 False 且 `is_global=False`，即 CGNAT 共享段（100.64.0.0/10）不落任何拒绝分支，可被放行连接；该版本无 `is_shared` 属性可用。
- 影响：注册域主机名校验在先，且需白名单域解析到 100.64/10 才会触发（需恶意 DNS + 运营商 CGN 环境），在规范声明的"单用户本地、无上游恶意 DNS"威胁假设下属可接受残余，但属规范 7.2.1"回环/内网/保留段（……等）"的覆盖缺口。
- 建议：统一改用 `if not address.is_global: 拒绝`（`is_global=False` 覆盖 100.64/10、文档段、广播段等全部非公网单播地址），或显式追加 `is_shared`/100.64/10 网段判断；两处同步修改，并补一条单元断言（`test_proxy_rejects_reserved_resolutions_in_production_mode` 已参数化，直接追加用例即可）。

### F-02（次要）probe 阶段取消时作业消息误为"视频分析已取消"

- 位置：`backend/app/services/jobs.py` 约 207-213 行（run_once 的 `except MediaProcessingCancelled` 分支）
- 证据：下载作业在产物校验（`LocalFfmpegMediaAnalyzer.probe`）期间被取消时，probe 抛 `MediaProcessingCancelled`，被该分支捕获并落 `cancelled`，但消息为"视频分析已取消"；随后对 `content_version_id=None`、`source_id=None` 执行空 UPDATE（无副作用）。
- 影响：状态语义正确（cancelled）、staging/代理清理不受影响（`_video_download` 的 finally 与 downloader 的 finally 均执行），仅作业页消息文案错位，违反"极简中文、语义准确"的体验要求。
- 建议：probe 取消路径在 `_video_download` 内转换为 `DownloadProcessingCancelled`（或在 probe 前后以 `job_cancel_requested` 预检），使消息落"链接下载已取消"；测试补一条"校验阶段取消 → cancelled + 正确消息"用例。

### F-03（次要）title 缺省时的"平台标题"回填未实现

- 位置：`backend/app/services/imports.py` 约 158-159 行（`downloaded_video` 内 `if not title.strip(): title = "未命名视频"`）；`backend/app/adapters/downloader.py` 子进程 标准输出=DEVNULL、无 `--print` 参数；`frontend/src/App.tsx` 标题占位文案"可留空，默认未命名视频"
- 证据：规范 6.2 字段表写明"title 缺省时下载成功后使用平台标题，退化为'未命名视频'"。实现从未捕获平台标题，恒退化为"未命名视频"，规范中"使用平台标题"分支为空转；docs/api-contract.md 亦按规范原文保留了该承诺，形成文档与代码不一致。
- 影响：功能完整性缺口（用户体验降级），无安全影响。
- 建议：二选一并裁决——(a) 实现平台标题回填（yt-dlp 增加 `--print`/info-json 只提取标题，仍不落盘、不落日志）；(b) 若按"最小面"维持现状，则需修订规范 6.2 与 api-contract 对应行文案为"缺省时使用'未命名视频'"，避免规范承诺与实现不符。本条需人工拍板。

### F-04（次要）cookies.txt 1MB 上限在请求体完整解析之后才判定

- 位置：`backend/app/main.py` 约 285-307 行（`upload_download_cookie`）
- 证据：`await file.read(1024*1024+1)` 发生在 Starlette 已完成整个 multipart 请求体解析（超 1MB 部分 spool 到临时文件）之后；413 语义与文件大小判定正确，但超大请求体先完整落临时盘。上传容量预检中间件按规范 6.3 有意不覆盖该端点。
- 影响：仅 loopback、单用户场景下的资源消耗硬化缺口（超大上传可先占用临时盘），无 Cookie 泄露面。
- 建议：可为该端点增加 Content-Length 预检（>1MB+边界 直接 413），或在未来统一上传预检中间件时纳入；作为次要硬化项裁决是否本版修复。

### F-05（次要）test_api 的能力断言依赖"测试机未安装 FFmpeg"

- 位置：`tests/unit/test_api.py` `test_download_link_endpoints_in_openapi_and_capabilities`（约 216-220 行，`assert downloader["enabled"] is False`）
- 证据：该断言依赖本机 PATH 无 ffmpeg/ffprobe（测试内注释亦写明"本机未安装"）；在装有 FFmpeg 的机器上 `enabled=True`，断言反转失败，测试不具备环境可移植性。
- 影响：测试套件在部分开发者/CI 机器上红，非功能缺陷。
- 建议：改为断言结构完整性（字段集合、adapter、supported_platforms）并容忍 `enabled` 两态；"如实报告 disabled"的语义验证改由工具缺失场景的作业级用例（已存在）承担。

## 观察项（非 finding，供裁决参考）

### O-01 下载作业内存断路器复用 `video_memory_limit_mb`

- 位置：`backend/app/services/jobs.py` 约 464 行。规范 7.5 只定义三个新设置（timeout/no_progress/disk），内存上限复用视频分析设置是合理选型，但修改"视频内存上限"会静默改变下载断路器，耦合未在文档注明。建议后续版本设独立 `download_memory_limit_mb` 或在 operations 文档注明耦合关系。

### O-02 查询串剥离可能静默下载非预期内容

- 位置：`backend/app/domain/models.py` `sanitize_download_url` + 规范 7.4"下载执行即使用脱敏链接"。实现与规范字面一致；但携带语义型 query 的链接（如 bilibili `?p=2` 分P选择）会被剥离后静默下载默认分P，而非按 REQ-047.7 的多P条款 failed。建议（可选强化）：对携带分P/合集选择类 query 参数的 URL 直接 `invalid_url` 拒绝，或下载后检测"下载产物标题/分P与用户预期不符"并提示。

## 通过项（重点核实结论）

### 1. 回环过滤代理（decision 7 硬强制）
- 逐连接 CONNECT/明文请求均先过注册域校验（`_validate_host`）再出站，未登记域立即断连、无任何字节出站（`_handle_connection` 返回前不建立远端连接）；`denied_hosts`/`connected_hosts` 内存计数表供断言，作业结束丢弃。
- resolve-then-connect 落地：`_open_validated_connection` 仅 `getaddrinfo` 一次，连接使用该次已校验的 sockaddr（无二次解析），连接后 `getpeername` 复核属于本次校验集；规范声明的 TOCTOU 残余即已收敛到该实现所及的最小面。
- IP 字面量拒绝：IPv4、方括号 IPv6、大写、带端口形式均不命中注册域（实测 `host_matches_registered_domain` 对 `[::1]`、`::1`、`127.0.0.1` 均 False）；hostname 规范化：小写、去尾点、`douyin.com.evil.com`/`evil-bilibili.com` 冒充均拒绝（实测确认）；IDN 形式因不匹配 ASCII 注册表而 fail-closed。
- 回环/内网/保留段拒绝（127/8、10/8、172.16/12、192.168/16、169.254/16、::1、fe80::/10、multicast、unspecified、reserved）实测覆盖正确，唯一缺口见 F-01（100.64/10）。
- 启动失败 fail-closed：`proxy.start()` OSError → `DownloadUnavailable` → 作业 blocked，无任何直连回退分支；`download()` 的 finally 保证作业结束（含全部异常路径）必然 `proxy.close()`（监听端口 + 全部存活连接）。
- 子进程环境：`HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY` 按 key.upper() 全清（Windows 大小写不敏感环境亦覆盖），显式 `--proxy` 指向回环代理双保险；`--ignore-config --no-cache-dir` 防用户级配置注入代理/下载器；测试用死端口环境代理（127.0.0.1:1）验证流量仍只经回环代理。
- 保留段拒绝豁免仅存在于测试子类 `_LoopbackExemptProxy`（测试注入模式，decision 9），生产代码无该分支；豁免不触及注册域主机名校验与计数断言。

### 2. Cookie 治理（REQ-047a 单通道）
- 仅 `data/state/download/cookies.txt`：1MB 上限（读 1MB+1 判定 413）、重复导入覆盖（`.part` 临时文件 + `os.replace`，finally 清理）、DELETE 幂等（`unlink(missing_ok=True)` 恒 204）。
- 逐作业拷贝：`jobs.py` 拷贝进 per-job staging，作业结束统一 `shutil.rmtree`（成功/失败/取消/blocked/代理启动失败全覆盖），原文件全程只读；`use_cookie=false` 全程不触碰文件；`use_cookie=true` 且未导入 → API 422，作业层二次校验 → failed，绝不静默回退。
- 零持久化：Cookie 内容不进 DB（provenance 表列不含 Cookie）、不进日志（yt-dlp 错误输出 入匿名临时文件即删、操作日志只记路由模板+状态码）、不进审计（audit_events 仅 event_type/entity_id/result）、不进备份/导出/reimport（manifest `exclusions` 增 `state/download`，且归档构建只白名单写入、从不遍历数据根）；`.gitignore` 第 16 行 `data/` 覆盖并有新增回归锚点断言。

### 3. 脱敏与泄露面
- `sanitize_download_url`：`scheme://host/path`，去 userinfo（`netloc.rsplit("@",1)[-1]`）、query、fragment，4096 截断；实测用例通过。
- `POST /videos/link` 的 payload_json 只存脱敏链接（`main.py` 调用处明确注释），下载执行即用脱敏链接（与规范 7.4 字面一致）；备份快照 payload、导出 records.json、provenance 行均实测不含 query/userinfo。
- 拒绝消息（invalid_url/cookie_file_unavailable/downloader_unavailable/失败作业消息）全部通用中文模板，实测不含 URL 内容；Pydantic 校验错误处理器返回通用消息不回显输入值。
- provenance 表 8 列（id/source_id/platform/url_sanitized/yt_dlp_version/format_profile/cookie_used/config_hash/created_at）逐一核验不承载 Cookie/请求头/响应体。

### 4. 作业流（REQ-047.6 + 幂等不变量 N-3）
- provenance 行与 source/content version/artifact/入队 video_analyze 在同一 `create_ingest` 事务内写入（`sqlite.py` 同 `with connection()` 块追加 INSERT）；`source_id UNIQUE` 保证每来源至多一条。
- 事务失败整体回滚 + 补偿删除刚写入的 artifact（`imports.py` 沿用 `_persist_ingest` 补偿模式，`stored.was_new` 判断）；重试从头执行不重复创建 source/version——专项测试 `test_provenance_failure_rolls_back_and_retry_creates_single_source` 覆盖（DROP TABLE 注入失败后重试，断言单 source/单 version/单 provenance 行），实测通过。
- blocked/failed/cancelled 语义与消息：`DownloadUnavailable`→blocked（消息含工具引导）、`DownloadInputInvalid`→failed（通用脱敏模板）、`DownloadProcessingCancelled`→cancelled；失败路径无半成品 source（source 仅在成功后一次事务创建）。
- 进程树终止：`_terminate_process_tree` 用 psutil 递归 terminate→wait→kill，ImportError 时退化为 terminate/kill；专项测试以子进程拉起孙进程验证取消后孙进程消失，实测通过。
- staging 清理：per-job 唯一目录（`staging_path().with_suffix("")` 去 .part 后缀 + exist_ok=False），finally rmtree；取消用例断言 staging 根目录清空，实测通过。

### 5. 下载参数
- 骨架参数与规范 7.2 一致：`-S "res:1080"`（选择不超 1080p 最佳组合）+ probe 后置高度 ≤1080 断言双保险（高度 1080 边界放行、1081 拒绝，均有测试）；`--no-playlist --no-simulate --ignore-config --no-cache-dir --retries 1 --socket-timeout 30 --merge-output-format mp4 --remux-video mp4`。
- 2GB/磁盘断路器：`download_disk_limit_mb`（默认 2048）作为 staging 目录总量上限（`_workspace_size` 全目录统计，覆盖合并/remux 多中间文件场景），`check_capacity` 覆盖单文件 2GB 与容量预检——与规范 7.2 的"staging 目录总量"口径一致。
- FFmpeg 仅本地合并/remux：无任何以 ffmpeg 为下载器的选项（无 `--external-downloader`/`--downloader ffmpeg`）；产物输出限 mp4/webm（输出文件名后缀白名单，非 mp4/webm 且唯一性不满足 → failed）。
- 断路器四项（总超时/无进展/内存/磁盘）均由专项测试覆盖并通过；psutil 已锁定（requirements.lock 12 包 = 原 10 包零漂移 + `yt-dlp==2026.7.4` + `psutil==7.2.2`），venv 实测导入成功（yt_dlp 2026.07.04 / psutil 7.2.2）。

### 6. API/中间件
- CORS `allow_methods` 已含 `DELETE`；OPTIONS 预检测试断言放行。
- 容量预检中间件路径白名单未变（仅 imports/file、videos/local），符合规范 6.3；下载大小由作业断路器与 2GB 检查约束。
- 最小审计中间件只记路由模板 + 状态码，天然脱敏，未改动。
- 错误码逐条核对实现与 6.4 表一致：422 request_validation/invalid_url/unsupported_platform/cookie_file_unavailable、413 cookie_file_too_large、503 downloader_unavailable、404/500 沿用框架；204 语义正确（前端 `request()` 已容忍空响应体）。
- platform 白名单：`DOWNLOAD_PLATFORMS`（=注册表键集）校验；URL 层校验 `DOWNLOAD_URL_HOSTS` 中 b23.tv 显式归属 bilibili（douyin 提交 b23.tv → 422），两层控制独立且均生效。

### 7. UI（REQ-044）
- 联网告知文案、平台选择、URL 输入、权利声明必选、Cookie 开关按 `cookie_file_available` 禁用并附导入引导、提交后跳转作业页、保留"不预览/嗅探"提示——全部落地；下载器不可用时提交按钮禁用并显示引导文案。
- `video_link` 徽标"链接视频"、过滤下拉选项、`jobLabel` `video_download: '链接下载'` 落地；设置页新增 Cookie 导入/删除与三项断路器设置（边界与后端一致），政策列表文案同步更新。

### 8. 归档 schema 5→6 与旧档兼容（重点核实项，开发自述假设 6）
- `transfers.py` `ARCHIVE_SCHEMA_VERSION` 5→6，`SUPPORTED_ARCHIVE_SCHEMA_VERSIONS` 保留 1-5。
- 读取路径 `_logical_records`：schema 1-4（无视频表、无 provenance）与 schema 5（无 provenance）分别按 `legacy_expected`/`pre_provenance_expected` 分发并补空表；v1.0/v1.1 备份与导出均可还原/再导入。
- 快照一致性校验 `_sqlite_snapshot_records`：旧快照（schema ≤5）缺 `video_download_provenance` 表时按空表处理，与逻辑记录一致，verify_archive 不误报。
- 写入路径：`EXPORT_TABLES`/`BACKUP_TABLES` 已追加该表且列清单登记（`BACKUP_TABLE_COLUMNS`），备份快照（含 sqlite 状态快照）与导出 records.json 随 manifest 哈希校验携带；reimport 冲突检测新增 `("source_id",)` 唯一键。
- SQLite schema v7 块幂等补表（重查 migration_versions 后按 7 判定）并注册默认设置三键；PostgreSQL Alembic 008（down_revision=007）与 defaults 同步，`initialize()` 保持"未到 head 拒绝启动"的既有门禁。
- `scripts/archive_v1.py` / `scripts/verify_v1_archive.py`（过程档案工具，schema v2）未受本批提交修改，builder/verifier 版本一致（`ARCHIVE_SCHEMA_VERSION=2`、`SUPPORTED={1,2}`），与本功能无冲突；"归档 schema 5→6"仅指后端备份/导出归档 schema，二者已分清，均正确。

### 9. 文档同步（第 13 章清单，7 文件）
逐文件核对 d660c26：requirements.md（REQ-015/031/043/044 修订文本、REQ-047 9 条、REQ-047a 5 条、REQ-030 注明不变）、threat-model.md（5 新行 + 既有合规行 REQ-031 例外注）、api-contract.md（两端点、6.2 字段表、6.4 错误码、downloader 节）、acceptance-matrix.md（新行、T-VID-005 不入"自测标识"列）、test-plan.md（T-VID-003/004/005 + T-API-001/T-UI-001 扩展）、dependency-installation.md（锁版本与手动评估纪律、FFmpeg 职责限定句）、operations-and-recovery.md（blocked/failed 运维语义、Cookie 文件、代理生命周期、state/download 排除）——文本与规范第 4/5/6/9/13 章一致（除 F-03 所述"平台标题"承诺随规范原文一并保留的既有一致性问题）。

### 10. 开发角色 9 条自述假设逐条裁定
> 自述假设未以单独文档入库（repo 内 docs/v1-2-requirements.md 第 15 章载明 8 条，归档兼容假设经协调方转达），按"spec 8 条 + 归档兼容 1 条"共 9 条核对：

1. 锁定版本在实施步骤确定（yt-dlp==2026.7.4、psutil==7.2.2）——已核实，lock 与 venv 导入均通过。
2. create_job（空 source/version）→ create_ingest 可行路径——已核实（`create_job` 允许空 source/version，jobs 表列可空），且不变量（失败无半成品 source、成功自动入队）成立。
3. payload_json 只存脱敏链接 / provenance 进 EXPORT/BACKUP_TABLES / 审计仅三字段——已核实（含备份快照 payload 断言测试）。
4. probe 复用 + 高度 ≤1080 后置断言——已核实。
5. T-VID-004 测试注入模式（测试专用注册域 + 保留段豁免、绕过 API 校验）——已核实，注入仅经 `proxy_factory`/测试子类，生产代码无豁免分支，fail-closed 语义不变。
6. 归档 schema 5→6 与 v1.0/v1.1 旧档兼容、archive 脚本 schema 分发——已核实（见通过项 8）。
7. `cookie_file_available`＝存在且 ≤1MB，探测失败按不可用——已核实（`is_file()`+`stat()` 均包裹 OSError 返回 False）。
8. file:line 引用以工作区为基准——实现侧引用的关键锚点（CORS 列表、EXPORT/BACKUP_TABLES、create_ingest、exclusions）与代码一致；规范中的行号引用随代码演进自然偏移，不构成缺陷。
9. 注册域清单为初始登记集、实测比对属 T-VID-005 验收职责——清单与规范 7.2.1 完全一致（bilibili 组 4 域 / douyin 组 4 域），未登记域一律拒（fail-closed）；真实链接出站域实测按规范属 acceptance 角色（冻结门禁 3），不在本审核内。

"零规范偏差"声称：硬性需求（白名单、代理硬强制、Cookie 单通道、脱敏、幂等、错误码、CORS、文档清单）逐项核实无偏差；存在两处可讨论的软性偏差——F-03（平台标题回填未实现，规范 6.2 与代码不一致）与 F-01（保留段覆盖缺口，规范"等"字外延）——均不构成阻断。

## 总体裁定

**accepted_with_remediation（有条件通过）**

理由：
- 安全边界与流程纪律的核心承诺全部落地且经静态核实 + 定向测试实测：回环过滤代理逐连接硬强制、resolve-then-connect + 对端复核、fail-closed 无直连回退、Cookie 单通道零持久化、脱敏链路（payload/provenance/审计/日志/备份/导出/reimport）闭环、同事务幂等落库与重试不变量、进程树终止与 staging 清理、两层独立控制（URL 校验 + 出站注册域）均与规范一致。
- 无阻断级 finding；5 条次要 finding 均为可修复/可裁决项，其中 F-03（平台标题）需人工拍板（实现回填 vs 修订规范文案），其余 4 条建议修复后纳入冻结门禁"主要项已裁决"要求。
- 归档兼容性（v1.0/v1.1 旧档可还原/再导入）经代码路径与测试双重核实成立。

冻结门禁联动提示（供上层角色）：
- 门禁 1（venv 物理验证 yt-dlp + psutil）——本审核已实测通过。
- 门禁 2（FFmpeg/ffprobe 物理可用）——本机 PATH 未见（test_api 断言 enabled=false 通过），属部署环境项，需安装后复核。
- 门禁 3/5/6/7（T-VID-005 手工验收、T-VID-004 外联负向验证、T-VID-003 全量、既有回归）——由独立测试与验收角色出具，本审核只对代码与测试资产做静态复核；定向抽测的 9 条用例全部通过。

## 最需人工决策的事项

1. **F-03 平台标题**：实现"使用平台标题"回填，还是修订规范 6.2/api-contract 文案为"缺省即未命名视频"？（二选一，需拍板）
2. **F-01/F-04 是否本轮修复**：CGNAT 100.64/10 拒绝补齐（建议用 `not address.is_global` 一行改动 + 测试断言）与 cookies.txt 上传 Content-Length 预检，作为次要硬化项裁决是否阻塞冻结。
3. 测试角色的 T-VID-003 全量/既有回归结果与本报告交叉：F-02（probe 期取消消息）与 F-05（环境敏感断言）是否需要本版修复或登记为已知次要项。

---

## 关闭确认（2026-08-13，第二轮：修复核验）

实施角色提交 3 个修复（2df6c5a、9d0cc73、4db48af），逐条核验如下（静态复核 + 定向抽测，未运行全套 pytest）：

### F-01 已关闭 —— 保留段拒绝改 `not address.is_global`
- `backend/app/adapters/downloader.py` `_reject_resolved_ip` 与 `backend/app/domain/models.py` `_host_is_reserved` 均改为 `not address.is_global`，一次性覆盖回环/私网/链路本地/保留/多播/未指定及 100.64.0.0/10（CGNAT）、文档段、广播段等全部非公网单播。
- 测试断言扩展（100.64.0.1、100.127.255.255、192.0.2.1、169.254.169.254 拒绝；1.1.1.1/8.8.8.8/2606:4700:4700::1111 放行），URL 层与代理层双断言；实测通过。

### F-02 已关闭 —— probe 阶段取消文案统一
- `backend/app/services/jobs.py` 在 probe 调用处捕获 `MediaProcessingCancelled` 并转换 `raise DownloadProcessingCancelled() from None`，作业落 `cancelled` 且消息为"链接下载已取消"；专项用例 `test_probe_phase_cancel_uses_download_cancel_message` 实测通过（无 source 残留）。

### F-03 已关闭 —— 平台标题回填实现（用户已裁决"实现回填"选项）
- `backend/app/adapters/downloader.py` 追加 `--print "%(title)s"`：标准输出 仅捕获标题到 `tempfile.TemporaryFile`（即删，不落 data/、不进日志/消息/审计），512KB 上限纪律（超限 → `DownloadInputInvalid("output_limit")` → failed，fail-closed）；`_extract_title` 清洗控制字符/换行并截断 500（与 title 字段上限一致）。
- `DownloadedVideo` 增 `title` 字段（默认空），`jobs.py` 优先级：用户显式提交 > 平台标题 > "未命名视频"（落库侧回退）；前端占位文案同步为"可留空，默认使用平台标题"。
- 新增 5 条用例（回填/空捕获退化/显式标题优先/清洗截断/合成服务器 og:title 真实 yt-dlp 捕获）覆盖；FakeDownloader 相关断言已适配。实测通过。
- 残留观察（非阻断）：yt-dlp 对缺失字段的默认输出为 "NA" 字面值，极端情况下标题可能显示 "NA" 而非"未命名视频"；如在意可在 `_extract_title` 追加 `{"NA", "null"}` 黑名单归一。

### F-04 已关闭 —— cookies.txt 上传 Content-Length 预检前移
- `backend/app/main.py` 新增 `cookie_upload_length_preflight` 中间件：仅路径限定 `POST /api/v1/settings/download-cookie`，Content-Length > 1MB+64KB（表单开销边界）时在 multipart 解析前立即 413 `cookie_file_too_large`，不落临时盘；端点内解析后的 1MB 二次校验保留兜底；缺失/非法 Content-Length 放行至端点兜底。
- 影响面复核：`/imports/file`、`/videos/local` 的既有容量预检及其他端点不受影响（路径集合互斥）；新用例 `test_cookie_upload_content_length_preflight_rejects_before_parsing` 实测通过。
- 残留观察（非阻断）：该 413 短路响应不经 CORSMiddleware（中间件栈最内层），跨源开发模式下前端可能显示通用失败而非 413 文案；与既有 `upload_capacity_preflight` 的 413 路径同型行为，无 Cookie/URL 泄露面。

### F-05 已关闭 —— 能力断言与探测一致
- `tests/unit/test_api.py` 两处断言改为以 `shutil.which(ffmpeg/ffprobe)`（含 `YUANZHIKU_FFMPEG_BIN`/`YUANZHIKU_FFPROBE_BIN` 环境变量覆盖）计算 `tools_available` 后断言 `enabled` 与 503/201 两态，不再硬编码测试机无 FFmpeg；503 语义改由作业级工具缺失用例覆盖。

### 新引入安全面复核（静态）
- 标准输出 捕获：标题字节流仅存在于匿名临时文件 → `DownloadedVideo.title` → `sources.title`（与本地导入文件名/标题同信任级），任何作业消息、进度回调、审计事件、操作日志均不承载标题字节；512KB 上限 fail-closed。
- 预检中间件：无新出站路径、无新增路由暴露、无请求体读取（仅头部判定），审计中间件仍只记路由模板+状态码。
- `is_global` 收窄：对公网 CDN IPv4/IPv6 无回归（测试断言保留），仅扩大拒绝面，符合 fail-closed 方向。
- 取消/异常路径：probe 期取消转换不改变清理链路（staging/代理/Cookie 拷贝仍由既有 finally 保证）；新增 `test_cookie_copy_removed_on_failure_and_cancel` 补齐 use_cookie=true 的失败/取消两条路径的拷贝清理断言，实测通过。

### 定向验证
轻量抽测 20 个相关用例（清洗截断、CGNAT/文档段双断言、probe 期取消、标题三级优先级、预检 413、URL 白名单全参数化、Cookie 拷贝失败/取消清理）全部通过（20 passed）。

### 最终裁定：**accepted**

理由：首轮 5 条次要 finding 全部关闭且修复方向与建议一致；三条残留观察（"NA" 字面值、413 响应缺 CORS 头、O-01/O-02）均为非阻断的边界/体验项，登记留档即可，不构成冻结条件。冻结门禁中属本审核的"阻断项已解决、主要项已裁决"要求满足；其余门禁项（T-VID-005 手工验收、T-VID-003/004 全量与既有回归、FFmpeg 物理可用）由测试与验收角色独立出具，本审核不做代结论。

---

## 隧道段例外安全门禁（决策 10，2026-08-13 二次门禁）

审核对象：5fff506（实现）、abdd3d6（规范修订）。审核方式：静态审读 + venv ipaddress 语义实测 + 调用关系核对（未运行 pytest）。

### 结论：**放行**（附 1 条次要 finding + 2 条观察项，均不阻塞）

### 逐项核验

**1. 例外只在注册域主机名校验通过后生效（调用关系）——成立**
- `_reject_resolved_ip` 在现文件中仅被 `_open_validated_connection`（downloader.py:254）调用；后者仅有两处调用方：`_relay_connect`（CONNECT 路径，其前置 `_validate_host` 于 :293）与 `_forward_plain_request`（明文路径，其前置 `_validate_host` 于 :342）。生产代码不存在其他入口。
- IP 字面量（`198.18.0.55` 等）与未登记主机名在 `_validate_host` 层即拒绝（literal 判定 + label 边界匹配），永远不会到达隧道段放行分支；测试以 `_validate_host` 断言 + `denied_hosts` 计数双重覆盖。
- URL 层（`models.py` `_host_is_reserved`）未引入隧道逻辑且无需引入：API 层不解析 DNS，IP 字面量 URL 一律不匹配白名单域而拒绝（含 28.x 等 is_global=True 的字面量）。

**2. 多播段修复（224.0.0.1 等）——正确且无副作用**
- venv 实测（Python 3.13.0）：`224.0.0.1`、`239.255.255.255`、`ff02::1` 的 `is_global` 均为 **True**——确认 2df6c5a 的 `not is_global` 简化确实引入多播漏放行，5fff506 的 `return not address.is_global or address.is_multicast` 为**承重修复**而非冗余。
- 副作用核对：公网单播（8.8.8.8、2606:4700:4700::1111）is_global=True 且 is_multicast=False → 仍放行；测试保留 public 断言。回环/私网/链路本地/保留段（127/8、10/8、172.16/12、192.168/16、169.254/16、100.64/10、文档段、240/4、255.255.255.255、::1、fe80::/10）仍拒绝。无副作用。

**3. 隧道段常量与判定——正确**
- `TUNNEL_RANGES = (ip_network("198.18.0.0/15"), ip_network("28.0.0.0/8"))`；`any(address in network ...)` 版本不匹配（IPv6 地址入 IPv4 网络）返回 False 不抛异常。
- venv 实测：`198.18.0.5`/`198.19.255.255` is_global=False（修正前确被阻断，属验收环境实测到的 fake-IP 段）→ 现放行，修复有效；边界外 `198.17.255.255`、`198.20.0.1` is_global=True（本就放行，无变化）。
- 对端复核（getpeername ∈ validated_ips）、resolve-then-connect、connected_hosts 按主机名计数均不受影响；"全部出站 ⊆ 注册表"断言语义不变。

**4. 测试（T-VID-003 用例 10 修订）——真断言**
- `test_proxy_tunnel_range_exemption_requires_registered_hostname`：隧道段边界值（198.18.0.55/198.18.255.254/28.0.0.1/28.255.255.255）放行；14 个其余保留段（含 224.0.0.1、255.255.255.255、100.64/10、文档段、回环、私网）拒绝；未登记主机名与 IP 字面量代理层拒绝并计数；决策 9 测试子类豁免行为不受影响。均为一等断言。
- 观察：例外"仅在校验后生效"目前靠静态调用关系 + 分离断言保证，无 getaddrinfo mock 的端到端链测试；建议后续补一条（monkeypatch getaddrinfo → 未登记主机名解析到隧道段仍拒绝）作为防重构回归锚点。

**5. 规范修订（abdd3d6）——结构完整、论证主体成立**
- REQ-047.2、7.2.1 新增 bullet、决策 10、T-VID-003 用例 10、修订记录条目五处同步，且明确"属安全边界变更，须经独立审核门禁"，与本门禁形成闭环。
- 论证主体（fake-IP 环境隧道段由本地 TUN 独占、无法引向受害主机内部服务；无 fake-IP 工具时不可达快速失败）对 198.18/15 成立。

**6. 残余风险（fail-fast 场景）——确认无攻击面**
- 无 fake-IP 工具时：198.18/15 无路由 → 连接快速失败（不可达或黑洞丢弃），受 `--socket-timeout 30` 与无进展断路器兜底，作业落 failed（通用脱敏消息），无数据可达任何内部服务。
- 28/8：公网可路由前缀（见 F-10），连接行为与"平台 DNS 指向任一公网地址"等价——攻击者本就控制其所辖注册域 DNS，未新增能力。
- 理论边缘（企业内部误配路由把 198.18/15 指向真实内网主机 + 注册域 DNS 被篡改）需"上游恶意 DNS + 异常网络配置"双重前提，落在规范既有"无上游恶意 DNS"威胁假设之外且概率极低，建议在 7.2.1 论证句补一句如实披露（见 O-03）。

### Finding（1 条次要）

**F-10（次要）规范与代码注释声称 28.0.0.0/8 "公网不可路由"与事实不符（论证文字错误，零安全影响）**
- 位置：`docs/v1-2-requirements.md` 7.2.1 隧道段 bullet 与决策 10；`backend/app/adapters/downloader.py:48-56` TUNNEL_RANGES 注释。
- 证据：venv 实测 `28.1.2.3`、`28.255.255.255` 的 `is_global=True`（28/8 为 DoD 分配的全局可路由前缀，不在任何私网/保留清单）；因此 28/8 在 `not is_global` 规则下**从未被拒绝**，TUNNEL_RANGES 包含 28/8 在生产判定中是**纯文档性 no-op**（行为零变化，无安全影响）。
- 建议（二选一）：(a) 保留代码现状，修正两处文字——改为"28.0.0.0/8 为全局可路由段、本就不受保留段拒绝影响，列入仅为与使用该段的 fake-IP 工具对齐的文档声明"；(b) 从 TUNNEL_RANGES 移除 28/8（行为同样无变化），只保留实测到的 198.18/15。不阻塞冻结，建议随下轮文档修订处理。

### 观察项

- O-03：7.2.1 "无可用攻击面"为绝对表述，建议追加一句如实披露（"若企业网络将 198.18/15 内部路由到真实主机且存在上游恶意 DNS 的极端场景属既有威胁假设之外"）。
- O-04：隧道段例外仅覆盖 IPv4（198.18/15、28/8）；若未来出现 IPv6 fake-IP 工具环境，注册域解析落 IPv6 隧道段仍会被拒（fail-closed、功能受限而非安全问题），建议在 7.2.1 注明"IPv6 隧道段不在例外之列，遇到时按保留段拒绝"。

### 门禁裁定：放行（accepted）

实现与测试正确、例外不可达主机名校验前置路径之外的结论经调用关系核实成立、多播修复承重且无副作用、残余风险可控；唯一 finding 为论证文字事实性错误（零代码影响）。决策 10 生效。
