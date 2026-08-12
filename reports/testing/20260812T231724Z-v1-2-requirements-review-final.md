# v1.2 链接获取需求文档独立终审报告

- 报告 ID：`20260812T231724Z-v1-2-requirements-review-final`
- 角色：独立审核子智能体（review）。本报告为单件 Markdown 审查记录，对被审文档给出终审裁定；不构成测试或验收结论。
- 被审对象：`docs/v1-2-requirements.md`（2026-08-13 修订版，状态 DRAFT）。
- 对照基准：`reports/testing/20260812T230103Z-v1-2-requirements-review.md`（首轮审核，17 条 finding）；同时核对了文档引用的 `reports/testing/20260812T225424Z-v1-2-requirements-verification.md`（11 条，文件存在）。
- 独立性：未阅读开发子智能体聊天记录；未修改任何文件；未执行 git；未联网。全部结论基于文档文本与代码静态核对。
- 终审日期：2026-08-13（UTC 20260812T231724Z）。

## 1. 逐条 finding 关闭状态（对照首轮 17 条）

| ID | 首轮严重度 | 状态 | 核验说明 |
|---|---|---|---|
| F-01 | 阻断 | **已关闭** | REQ-047.2 + 7.2.1 + 决策 7：出站注册域清单（bilibili 组 4 域、douyin 组 4 域）+ 作业内回环过滤代理逐连接 CONNECT 校验、保留段/回环解析拒绝、重定向链逐跳强制、显式 `--proxy` 覆盖并清空代理环境变量、内存出站计数供测试断言、清单变更须实测+人工安全评估+审核门禁。支撑：T-VID-003 用例 10、冻结门禁 5/6、T-VID-004 出站断言。硬强制机制从无到有，且为标准库可实现设计。残余加固见 N-01/N-02 |
| F-02 | 主要 | **已关闭** | REQ-047.1 与 6.2 显式放行 `b23.tv`（归属 bilibili），重定向终点由回环代理限制在 bilibili 组注册域；T-VID-003 用例 1 覆盖 |
| F-03 | 主要 | **已关闭** | 7.2 断路器归因更正：不再声称复用 `_run` 的无进展能力（核对 `media.py:68-131` 属实无此能力）；无进展断路器按 REQ-033 语义新实现（staging 目录总量滚动窗口）；psutil 锁定进 `requirements.lock`（核对 lock 现 10 包均无 yt-dlp/psutil，`media.py:48-57` 确为可选导入）；门禁 1 物理验证扩至 psutil；T-VID-003 用例 3 覆盖 |
| F-04 | 主要 | **已关闭** | 骨架显式 `--proxy http://127.0.0.1:<作业端口>` 并清空 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`no_proxy` 双保险；T-VID-003 用例 10 断言环境代理被覆盖 |
| F-05 | 主要 | **已关闭（消解）** | 浏览器直读通道整体删除（决策 1 追加二次决策 + 决策 8），REQ-047a 缩为单通道 5 条，适配器去除 `--cookies-from-browser`，端口签名改 `use_cookie: bool`，capabilities 去除 `cookie_browser_available` |
| F-06 | 主要 | **已关闭（消解）** | 随单通道决策消解；威胁模型第 2 行更新为"浏览器 Cookie 库直读已按二次决策整体删除" |
| F-07 | 主要 | **已关闭** | 7.4 新增 `video_download_provenance` 表；脱敏变换定义明确（`scheme://host/path`，去 userinfo/query/fragment，4096 截断）；payload_json 只存脱敏链接；依赖 query 才能解析的链接按不支持处理（已披露取舍）。核实代码：`rows_for_backup`/`rows_for_export` 为 `SELECT *` 遍历 BACKUP_TABLES/EXPORT_TABLES（sqlite.py:1534/1553），`insert_backup_rows`/`insert_export_rows` 通用按表插入，reimport 经 `_logical_records` 与 `insert_export_rows` 表驱动（transfers.py:759/806-808/921）——新表入两清单即自动携带与恢复，文档声明属实。T-VID-003 用例 7 改"存在性 + 脱敏"双重断言（含备份快照与导出 records.json 断言） |
| F-08 | 主要 | **已关闭** | 新增 REQ-047 第 9 条（会员/付费墙/DRM 一律拒绝、不利用 Cookie 获取超出自身权益内容、档位无法区分会员画质时按失败处理）；T-VID-003 用例 9 + T-VID-005 观察项 |
| F-09 | 主要 | **已关闭** | §15 如实披露平台条款风险（自动化下载可能违反抖音/B站用户协议，法律风险用户自担）；威胁模型"平台条款与版权"行同步 |
| F-10 | 主要 | **已关闭** | §10 改为四角色（development/testing/acceptance/review）；给出 report-schema `author_role` 扩展或非声明式辅助产物二选一并留档；冻结门禁第 8 项要求独立审核报告出具且阻断项已解决、主要项已裁决 |
| F-11 | 主要 | **已关闭** | 门禁第 6 项"外联域控制负向验证"（重定向至非白名单域断言拒绝且无出站）；门禁第 4 项细化静态证据覆盖"URL 原文不落入备份/导出/日志/审计正文" |
| F-12 | 次要 | **已关闭** | §2 新增分发边界说明（产品不提供分发能力；导出遵循 REQ-041 确认纪律，后续使用用户自担）；决策 6 追加澄清 |
| F-13 | 次要 | **已关闭（消解）** | 随单通道决策消解；`cookie_file_available`＝文件存在且 ≤1MB，探测失败一律按不可用处理，无精确可访问性承诺 |
| F-14 | 次要 | **已关闭** | 7.2 明确 2GB 检查对象为 staging 目录总量（yt-dlp 合并/remux 多中间文件场景）；T-VID-003 用例 3 |
| F-15 | 次要 | **已关闭** | 7.2 明确取消时进程树终止（含 yt-dlp 拉起的 ffmpeg），实施时确认锁定版本自清理行为；T-VID-003 用例 4 |
| F-16 | 次要 | **已关闭** | REQ-031 例外条款改为点名"REQ-047 与 REQ-047a"，并复禁"密码/登录凭据不在例外之列，任何情况下不使用、不保存" |
| F-17 | 次要 | **已关闭** | REQ-044 修订与 7.8 增加联网告知文案（提交即向所选平台服务器发起下载请求） |

关闭统计：17/17 全部关闭——13 条文本修复 + 4 条随"浏览器直读通道删除"消解（F-05/F-06/F-13，另 F-02 部分机制依赖决策 7 落地）。修订记录章节逐条映射首轮 17 条与验证报告 11 条，追溯性完整。

## 2. 新增 file:line 引用抽查（全部命中）

- `main.py:211-217` CORS `allow_methods=["GET","POST","PUT"]` 属实——新增 DELETE 端点的预检修复（6.1、步骤 5）是真实存在的缺口，修复方向正确。
- `main.py:138-143` backup/integrity_sample 空 source/version 入队先例属实。
- `sqlite.py:530-548` 版本 6 迁移块（video_analyses/video_frames + schema_migrations 版本号）属实；版本 7 建表先例成立；`sqlite.py:37` schema_migrations 表存在。
- `sqlite.py:139-149` EXPORT_TABLES/BACKUP_TABLES 位置属实；provenance 表加入两清单的"自动携带/自动恢复"结论经 `rows_for_backup/export` 与 `insert_backup_rows/insert_export_rows` 表驱动实现核实成立（见 F-07 行）。
- `media.py:48-57` psutil 可选导入路径属实；`jobs.py:360-370` 设置读取模式引用属实。
- `App.tsx:1116-1126` `jobLabel` 映射表属实，且 `labels[kind] || kind` 回退英文原文的问题真实存在——新增 `video_download: '链接下载'` 条目为必要修复。
- `backend/alembic/versions/` 现存 001-007，新迁移命名 `008_video_download_provenance.py` 与序列一致。
- 引用外部材料：`reports/testing/20260812T225424Z-v1-2-requirements-verification.md` 存在，被引 11 条与修订记录映射一致。

## 3. 新发现

### N-01（次要，安全加固）回环代理 DNS 重绑定防御存在 TOCTOU 表述缺口

- 证据：7.2.1 规定"目标主机 DNS 解析结果必须非回环/非内网/非保留段……命中保留段 → 拒绝（防 DNS 重绑定 SSRF）"，但未规定代理连接必须使用已校验的解析结果（resolve-then-connect-to-validated-IP），也未禁止校验后二次解析。
- 建议：补一句——"代理解析目标主机并校验后，连接必须使用该次已校验的 IP，不得再次解析主机名"；一步即可闭合经典重绑定 TOCTOU。

### N-02（次要，fail-closed 语义与代理绕过面收敛）

- 证据：7.3 流程"启动回环过滤代理"未写启动失败语义；且未声明 yt-dlp 不得把 ffmpeg 用作网络下载器（HLS 等场景若指定 ffmpeg 下载器会绕过 `--proxy`）。
- 建议：明确两点——(1) 回环代理启动失败 → 作业 blocked/failed（fail-closed），任何情况下绝不直连；(2) ffmpeg 仅用于本地合并/remux，不得作为 yt-dlp 的网络下载器。另注明 CONNECT 解析应拒绝 IP 字面量（含 IPv6 括号形式），因其天然不命中注册域（实现说明，一行即可）。

### N-03（主要，测试设计矛盾）T-VID-004 的 localhost fixture 与保留段拒绝规则冲突

- 证据：7.2.1 规定代理拒绝"解析到回环/内网/保留段"的目标；T-VID-004（冻结门禁 5）却要求真实 yt-dlp 经回环代理下载 localhost 合成 fixture——fixture 域解析为 127.0.0.1，按规则会被代理自身拒绝，测试按当前文本无法通过。文档只写了注入"测试专用注册域清单"（解决主机名校验），未解除保留段拒绝，也未允许 fixture 绑定非保留地址。
- 建议：规定测试注入模式（仅测试代码路径、不进生产注册表）可显式豁免保留段拒绝并保留出站计数断言；或 fixture 绑定非保留本地地址。两选一写入 T-VID-004 夹具纪律，否则门禁 5 不可执行。

## 4. 通过项（维持首轮结论并确认增强）

- 出站控制从"无机制的声明"升级为"链路层硬强制 + 注册域清单 + 维护门禁 + 负向测试 + 冻结门禁"，可执行、可测试、可审计。
- Cookie 治理收敛为单通道后全链路自洽：REQ-047a（5 条）、6.2 `use_cookie`、7.1 签名、7.2 参数、capabilities、威胁模型、T-VID-003 用例 2、决策 1 追加与决策 8 无一处残留浏览器直读引用；决策 1 原文保留+追加的写法符合仓库"历史不改写"文化。
- provenance 承载设计闭环：表结构、脱敏变换、payload/审计/导出/备份四层去向全部明确，且"进 EXPORT/BACKUP_TABLES 即自动携带"经代码核实成立。
- REQ-031 例外条款：点名 REQ-047/047a + 密码复禁 + 外部卡/解析器/检索/导入/AI 端口"一无所知"，无放宽面。
- 断路器设计不再有归因错误；`-S "res:1080"` 格式选择 + probe 高度 ≤1080 后置断言双保险，且已删除原 `-f .../b` 无高度过滤的兜底。
- 合规披露完整：DRM/会员/付费墙拒绝条款、平台条款风险如实披露、分发边界澄清、权利声明与 REQ-011 衔接不变。
- 流程门禁 8 项覆盖首轮要求的全部四类（yt-dlp/psutil/FFmpeg 物理验证、合成集成、真实平台独立验收、Cookie 治理审计）外加外联负向验证与独立审核报告项；release 保持 blocked、archive-local 口径、DRAFT 冻结纪律全部保留。
- 报告卫生：修订版全文无凭据、绝对路径、原始运行输出、Cookie 值或请求体。

## 5. 最终裁定

**accepted_with_remediation**

理由：首轮 17 条 finding 全部关闭——原阻断项 F-01 已具备可实现的硬强制设计（作业内回环过滤代理 + 注册域清单 + 逐跳重定向校验 + 维护门禁），并有 T-VID-003 用例 10、门禁 5/6 与 T-VID-004 出站断言三重支撑；浏览器直读相关三条 finding 随单通道人工拍板整体消解；其余每条修复均有对应条款与测试/门禁支撑，修订记录逐条可追溯。新增 file:line 引用抽查全部命中，provenance 表"进两清单即自动携带/恢复"的关键声明经代码核实成立。终审未发现新的阻断项。

剩余条件（冻结前必须处理）：
1. **N-03（主要）**：T-VID-004 的 localhost fixture 与代理保留段拒绝规则冲突必须先裁决并写入测试夹具纪律，否则门禁 5 不可执行。
2. **N-01/N-02（次要）**：回环代理的 resolve-then-connect 表述、fail-closed 启动语义与"ffmpeg 不得作网络下载器"两行加固句建议随修订一并补入。

本裁定仅针对需求文档冻结层面的可接受性；不改变 release_readiness=blocked 的立场，不构成对实现的批准——实现完成后仍须按 §10 走独立测试、独立验收与四角色双件报告归档。

## 6. 关闭确认（2026-08-13，UTC 20260812T232755Z）

开发角色完成收尾修订（决策 9、7.2.1 DNS 重绑定加固、fail-closed 与 FFmpeg 角色限定、7.4 幂等性、修订记录）。逐条关闭确认：

- **N-01（DNS 重绑定加固）已关闭**：7.2.1 已写入 resolve-then-connect（"先解析目标主机并校验，连接必须使用该次已校验的 IP，不得再次解析主机名"）、连接后对端地址复核、IP 字面量（含 IPv6 括号形式）拒绝，并对 TOCTOU 理论残留如实说明（缓解措施 + REQ-002 loopback 威胁假设下可接受）。表述到位，无夸大。
- **N-02（fail-closed 与 FFmpeg 角色限定）已关闭**：7.2.1 与 7.3 明确"代理启动失败 → 作业 blocked，任何情况下绝不直连回退"；7.2 明确 FFmpeg 仅作本地合并/remux、绝不作为网络下载器，且不得向 yt-dlp 指定任何以 ffmpeg 为下载器的选项（闭合 HLS 绕过代理路径）。加固句到位。
- **N-03（T-VID-004 夹具矛盾）已关闭**：按"测试注入模式"裁决落地（决策 9 + 7.2.1 + §8 夹具纪律）——仅测试代码路径注入"保留段拒绝豁免"标志，不进生产注册表、生产代码无该分支（生产 fail-closed 语义不变）；豁免只影响回环/保留段解析拒绝，**不影响注册域主机名校验与出站计数断言**。测试可行性矛盾消除，且生产安全边界未弱化。
- **无新引入风险**：随修订新增的 7.4 幂等性设计（provenance 行与 source/content version/artifact 同一事务写入、`source_id UNIQUE`、失败整体回滚 + artifact 补偿沿用 imports.py:41-66 模式、audit 移至事务提交后）与 7.5 `download_no_progress_seconds`（ge=10、默认 10、阈值＝连续两个观察窗口无增长且无输出）语义清晰、与 REQ-032 幂等纪律一致，未发现新安全或合规风险。
- **非阻断备注（编辑层，供第 13 章文档同步时随手补全）**：7.8 设置页改动清单（§7.8）与 T-VID-003 用例 11（settings 边界）只枚举了 `download_timeout_seconds`、`download_disk_limit_mb` 两个字段，未列入 7.5 新增的 `download_no_progress_seconds`。该字段在 7.5 已完整定义、步骤 7 门禁"新 settings 键 GET/PUT 往返"为通用覆盖，故不构成阻断；建议同步清单更新时把第三个字段并入前端设置控件与边界用例枚举。

### 最终裁定

**accepted**（就 v1.2 需求文档修订的审核关闭而言）：首轮 17 条与终审 3 条 finding 全部关闭，修订均以"条款 + 测试/门禁 + 修订记录映射"形式落地，生产 fail-closed 语义未弱化，无新引入风险；仅余一条非阻断编辑备注。本裁定不改变 release_readiness=blocked，不构成对实现的批准——实现、独立测试与独立验收仍按 §10 八项冻结门禁与四角色双件报告执行。
