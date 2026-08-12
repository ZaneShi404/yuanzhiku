# v1.2 需求文档独立验证报告（第二轮复核关闭）

- 验证对象：`docs/v1-2-requirements.md`（修订版，开发子智能体按首轮测试验证 11 条 + 独立审核 17 条修订）
- 验证角色：testing（独立测试子智能体，第二轮复核）
- 验证时间（UTC）：20260812T231526Z
- 对照基线：本角色首轮报告 `reports/testing/20260812T225424Z-v1-2-requirements-verification.md`（F-01..F-11）；独立审核报告 `reports/testing/20260812T230103Z-v1-2-requirements-review.md`（F-01..F-17）
- 冻结基线/代码核实源：同首轮；代码侧 `git status` 确认工作区仍无代码改动（仅新增 4 个未跟踪文档/报告文件），首轮全部 45 处 `file:line` 引用继续有效。
- 纪律：未修改任何被验证文件；未 git add/commit；未联网；未运行 pytest 全套。

## 1. 复核范围与方法

1. 重读修订后全文（重点：REQ-047/047a 重写、7.2.1 回环过滤代理、7.4 provenance 表、6.1 CORS DELETE、断路器机制改写、决策记录 1 追加二次决策 + 新增 7/8、文末修订记录）。
2. 对照首轮 11 条 finding 逐条核验关闭状态与修复真实性。
3. 交叉核验独立审核报告 17 条（F-01..F-17）在文档中的可观察修复，核对修订记录引用的审核 finding ID 均真实存在。
4. 重新抽查新增引用：`sqlite.py:530-548`（版本 6 迁移先例）、`backend/alembic/versions/007_video_media.py`、`postgres.py migrate_to_head` → `alembic command.upgrade("head")`、`media.py:48-57`、`main.py:211-217`、`App.tsx:1116-1126`、`requirements.lock` 无 psutil/yt-dlp。
5. 全文档 grep 双通道残留（`双通道/cookie_source/cookie_browser/cookies-from-browser/浏览器直读`）与单通道一致性（`use_cookie/cookie_file_available`）。

## 2. 首轮 finding 逐条关闭状态

| ID | 首轮严重度 | 状态 | 证据（修订版位置） |
|---|---|---|---|
| F-01 | 主要 | **关闭** | §7.4 新增 `video_download_provenance` 表（8 列，含脱敏 `url_sanitized=scheme://host/path` 去 userinfo/query/fragment 且 ≤4096 字符）；进 `EXPORT_TABLES`/`BACKUP_TABLES`（sqlite.py:139-149，泛型 `rows_for_export`/`insert_export_rows` 已核实 sqlite.py:1534+ 按清单遍历，新表自动进导出/备份/reimport 且受既有 hash 校验）；审计仅 `event_type/entity_id/result`；`payload_json` 只存脱敏链接；T-VID-003 用例 7 改"存在性 + 脱敏"双重断言；迁移计划（SQLite 版本 7 块沿 `sqlite.py:530-548` 版本 6 先例——已核实；PostgreSQL Alembic `008` 沿 `007_video_media.py`——文件存在、链 006→007 正确；`migrate_to_head`→`command.upgrade(..., "head")`——postgres.py:210-225 已核实）。自述假设 3 同步重写。 |
| F-02 | 主要 | **关闭** | §6.1 新增 CORS 段（"allow_methods 现为 ["GET","POST","PUT"] 需改为含 DELETE"，引用 `main.py:211-217` 准确）；步骤 5 加 CORS 变更与回滚；T-API-001/T-UI-001 扩展增加跨源 DELETE 预检断言（§8）。 |
| F-03 | 主要 | **关闭** | §7.2.1 注册域清单（bilibili 组含 `bilivideo.com`/`hdslb.com`/`b23.tv`，douyin 组含 `iesdouyin.com`/`snssdk.com`/`douyinvod.com`）+ 回环过滤代理逐连接校验 + 拒绝回环/内网/保留段解析（防 DNS 重绑定）；显式 `--proxy` + 清空子进程代理环境变量；重定向链逐跳强制校验；决策 7 与威胁模型第 1 行同步；用例 10 与冻结门禁 6 覆盖负向验证。F-03 三个子问题（代理、重定向、CDN 域）全部落实。 |
| F-04 | 次要 | **关闭** | §8 T-VID-004 明确"适配器/服务层直调（绕过 API 层 URL 校验——两层控制独立）+ 测试专用注册域清单注入（仅测试代码注入、不进生产注册表）+ 回环代理记录断言全部出站 ⊆ 测试注册表"；自述假设 5 一致。 |
| F-05 | 次要 | **关闭** | §7.8 增加 `jobLabel`（`App.tsx:1116-1126`，已核实）`video_download: '链接下载'` 条目；步骤 6 同步；作业页表述改为"其余渲染通用"。 |
| F-06 | 次要 | **关闭** | REQ-047.3 引用更正为 `REQ-016`（总超时/内存/磁盘）与 `REQ-033`（无进展语义）；§7.2 断路器重写——明确承认 `_run`（media.py:68-131）**没有**无进展检测，不再声称复用；无进展断路器新实现（staging 目录总量滚动窗口无增长判定）；`psutil` 锁定进 `requirements.lock`（核实当前 lock 10 包不含 psutil，media.py:48-57 可选导入属实），内存断路器双路径生效。 |
| F-07 | 次要 | **关闭** | 骨架参数改 `-S "res:1080"`（取代无高度过滤的 `-f .../b` 兜底）+ probe 后置高度 ≤1080 断言（超出 → `DownloadInputInvalid`）双保险；用例 5 覆盖高度 >1080；自述假设 4 一致。 |
| F-08 | 次要 | **关闭** | T-VID-003 新增用例 8（rights 缺失/非法 → 422，对应 6.4 `request_validation` 加注"含 rights 缺失/非法"）与用例 9（多P/合集/直播/需登录/会员/付费/DRM → failed 通用脱敏）；REQ-047 增至 9 条（新增第 9 条会员/付费墙/DRM 拒绝条款，头部计数"9 条"一致）。 |
| F-09 | 次要 | **关闭** | 验收矩阵行"自测标识"列仅 `T-VID-003, T-VID-004`；表下注明 T-VID-005 为独立验收、由 acceptance 角色登记（`independence=independent`）、不写入自测列；§10 三角色→四角色分离表述一致。 |
| F-10 | 次要 | **关闭** | §13 同步清单 threat-model.md 条目明确"既有'外部平台合规'行加注'REQ-031 例外仅限 REQ-047/047a 通道，外部卡/解析器/检索/导入/AI 端口不受影响'"。 |
| F-11 | 次要 | **关闭** | §13 同步清单 dependency-installation.md 条目增加职责句限定："ffmpeg/ffprobe 自身不承担联网下载职责；链接下载由 yt-dlp 受限通道单独承担"，并补 psutil 锁定说明。 |

**关闭统计：11/11 全部关闭**（首轮 3 条主要 + 8 条次要均已真实落地，非表面修补）。

## 3. 独立审核报告（17 条）交叉核验

审核 finding ID 引用全部真实存在，且修订版可观察对应修复：

- 审核 F-01（阻断，出站白名单无机制）→ 拍板 1 + 决策 7 + §7.2.1 完整机制 + 冻结门禁 6。修订记录中该条未按 ID 标注（以"拍板 1 落地"表述），内容覆盖完整，仅属标注风格问题。
- 审核 F-02（b23.tv 矛盾）→ §6.2 url 规则显式放行 b23.tv（归属 bilibili）+ §7.2.1 b23.tv 注册域行（重定向终点限 bilibili 组）。
- 审核 F-03（断路器归因 + psutil）→ §7.2 重写（同 F-06 证据）。
- 审核 F-04（环境代理）→ 决策 7 `--proxy` 指向回环代理 + 清空 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/no_proxy` + 用例 10。
- 审核 F-05/F-06/F-13（浏览器直读依赖/暴露面/探测语义）→ 决策 8 单通道收敛整体删除，REQ-047a 重写为 5 条单通道，§2 非目标列"浏览器 Cookie 库直读"。
- 审核 F-07（元数据无承载 + 脱敏未定义）→ §7.4（同 F-01 证据），"脱敏变换定义"显式给出。
- 审核 F-08（会员/付费/DRM 无条款）→ REQ-047.9 + 用例 9 + T-VID-005 观察项。
- 审核 F-09（条款风险）→ §15 新增"平台条款风险"披露（自动化下载可能违反用户协议、法律风险用户自担、个人本地使用不豁免）。
- 审核 F-10（审核角色定位）→ §10 四角色分离 + `author_role` 扩展 `review` 的二选一留档方案 + 冻结门禁 8。
- 审核 F-11（门禁缺外联负向验证）→ 门禁 6 + Cookie 治理审计细化（门禁 4）。
- 审核 F-12（分发边界）→ §2"分发边界说明" + 决策 6 追加澄清。
- 审核 F-14（2GB 检查对象）→ §7.2"2GB 检查对象：staging 目录总量"。
- 审核 F-15（进程树清理）→ §7.2 取消清理明确进程树终止 + 用例 4。
- 审核 F-16（例外未点名 REQ-047a + 密码复禁）→ REQ-031 修订点名 `REQ-047` 与 `REQ-047a` 并追加"密码/登录凭据不在例外之列，任何情况下不使用、不保存"。
- 审核 F-17（联网告知）→ REQ-044 修订 + §7.8 表单"提交即向所选平台服务器发起下载请求"文案。

## 4. 引用真实性复抽

- 代码无改动（git status 仅 4 个未跟踪文档），首轮全部 45 处引用继续有效。
- 新增引用全部核实属实：`sqlite.py:530-548`（`if 6 not in migration_versions` 建 video_analyses/video_frames + 版本 6 登记，恰为版本块先例）；`backend/alembic/versions/007_video_media.py`（存在，revision="007_video_media"，down_revision="006_evidence_bundles"）；`postgres.py` `migrate_to_head`（210 行起，`command.upgrade(self._alembic_config(), "head")` 于 225 行）；`media.py:48-57`（`_process_memory_bytes`）；`main.py:211-217`（CORS allow_methods=["GET","POST","PUT"]）；`App.tsx:1116-1126`（jobLabel）；`requirements.lock:1-10`（10 包，无 psutil/yt-dlp）。
- 双通道残留扫描：`双通道/cookie_source/cookie_browser/cookies-from-browser/浏览器直读` 仅出现于 §2（单通道声明与删除说明）、§6.3 括号说明、决策 1 保留原文 + 二次决策追加、决策 8、修订记录——规范性章节（REQ/6.x/7.x/8/9/10/12/13）零残留。
- 内部一致性：REQ-047"9 条"与实际 1-9 条款一致；REQ-047a"5 条单通道"一致；错误码 `cookie_file_unavailable` 在 6.2/6.4/047a.2/用例 2 一致；capabilities JSON 与 §7.1 注释均仅 `cookie_file_available`；`-S "res:1080"`、高度 ≤1080 后置、2GB=staging 目录总量（=download_disk_limit_mb 默认 2048 口径）跨章节一致；门禁"8 项"与步骤 9"冻结门禁 8 项全绿"计数一致；四处修订 REQ"原文"与冻结基线逐字一致未变。

## 5. 新发现问题（本轮）

| ID | 严重度 | 位置 | 证据与修复建议 |
|---|---|---|---|
| N-1 | 次要 | 14 章决策 1 | 保留的决策 1 原文含"两条通道的治理规则已在 4.4 节分别写明"，但 4.4 已重写为单通道条款；二次决策追加说明"自本稿删除"未修正该过时交叉引用。建议：追加说明补一句"原文'两条通道的治理规则已在 4.4 节分别写明'随单通道修订失效"。 |
| N-2 | 次要 | 7.2 无进展断路器；7.5 设置 | "父进程监控循环按固定间隔（默认 10s，**随设置**）……连续无增长达阈值"：7.5 `SettingsUpdate` 仅新增 `download_timeout_seconds`/`download_disk_limit_mb` 两字段，无 no_progress 间隔设置项，"随设置"悬空；且"达阈值"的阈值（连续次数/时长）未定义，用例 3 无可断言参数。建议：7.5 补 `download_no_progress_seconds`（ge/le 与默认值），或改为固定间隔；同时定义阈值语义。 |
| N-3 | 次要 | 7.3 作业流 | 流程顺序为 `create_ingest`（落 source/version/artifact + 入队 video_analyze）→ 写 provenance → audit，provenance 写入在 create_ingest 事务之外、同一作业内；若 provenance 写入抛异常，作业落通用重试路径（source_id 为空不误写状态），而 source 已完整落库——手动重试将再次下载并创建第二个 source（无幂等去重），与"失败无残留"精神及 REQ-032 幂等纪律存在张力。建议：将 provenance 行写入并入 create_ingest 同一事务（扩展 create_ingest 或同 `with self.connection()` 块），或明确该失败窗口的幂等/去重处理。 |

新发现：3 条，均为次要；无新的主要/阻断。

## 6. 总体结论

**有条件通过**

- 首轮 11 条 finding 全部关闭（11/11），修复真实落地；独立审核 17 条可观察修复全部在文档中对应；全部新增引用属实；双通道残留仅在保留的历史决策文本与说明中，规范性章节零残留。
- 条件：N-1/N-2/N-3 三条次要项在冻结版文本中一并澄清（不要求再走一轮独立复核；可在实施步骤 4/7 前完成，其中 N-3 建议在实施前定稿以避免重复 source 缺陷）。

---

- 报告作者角色：testing（独立测试，第二轮）
- 被验证文档：`docs/v1-2-requirements.md`（未做任何修改）
- 本报告仅代表本地可归档测试结论（archive-local），不构成发布门禁变更；release_readiness 维持 blocked。

## 7. 关闭确认（2026-08-13，UTC 20260812T232807Z）

对开发角色按本报告 N-1/N-2/N-3 完成的收尾修订做最终关闭确认（只读核验 + grep 抽查；代码仍无改动，git status 仅未跟踪文档/报告文件）：

| ID | 状态 | 证据 |
|---|---|---|
| N-1 | **关闭** | 决策 1 追加说明补注"原文'两条通道的治理规则已在 4.4 节分别写明'随单通道修订失效，现行治理规则见单通道 REQ-047a（4.4 节）"（第 414 行）；原决策文本保留为历史记录，现行规则指向明确。 |
| N-2 | **关闭（含 1 项轻微残留提示）** | §7.5 新增 `download_no_progress_seconds`（ge=10、le=86_400、默认 10，默认种子登记引用 `sqlite.py:549-562` 属实；下界协调说明引 `models.py:148/150` 的 `video_max_frames ge=1`/`max_retry_attempts ge=0` 先例属实）；§7.2 阈值语义定义（连续两个观察窗口内 staging 目录总字节数无增长且子进程无输出）与 §7.5、T-VID-003 用例 3、修订记录四处表述一致。**残留提示（不阻断）**：步骤 7 表仍写"`SettingsUpdate` 两字段"（现为三字段）；§7.8 设置页与用例 11 的字段枚举仍只列 `download_timeout_seconds`、`download_disk_limit_mb`，未含 `download_no_progress_seconds`——建议冻结版同步为"三字段"并补全枚举。 |
| N-3 | **关闭** | §7.3 流程改为 provenance 行与 source/content version/artifact **在同一数据库事务内**写入（事务提交后 audit）；§7.4 增"幂等性与重试不变量"（同事务 + `source_id UNIQUE` 去重 + artifact 补偿删除沿用 `imports.py:41-66`——该行段补偿模式核实属实；重试不变量与 `REQ-032` 对齐）；T-VID-003 用例 7 增"重试幂等断言"。"同事务"表述在 7.3/7.4 一致，全文无矛盾。 |

grep 抽查：`download_no_progress_seconds` 出现在 §7.2/§7.5/用例 3/修订记录四处且语义一致；"同事务"出现在 §7.3/§7.4 两处且一致。未发现由本轮修订新引入的规范性矛盾（另核：决策 9 的 T-VID-004 测试注入豁免仅豁免保留段解析拒绝、不影响注册域校验，与已关闭的 F-04 不冲突）。

**最终结论：有条件通过。** 条件仅为 N-2 残留的文字同步（步骤 7"两字段"→"三字段"；§7.8 与用例 11 枚举补 `download_no_progress_seconds`），属冻结前纯文本同步，不要求再走独立复核；release_readiness 维持 blocked。
