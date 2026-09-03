# 测试计划

| 标识 | 范围 | 验证方式 | 需求 |
|---|---|---|---|
| T-API-001 | 健康与 OpenAPI | TestClient 调用 `/api/v1/health` 与 `/openapi.json`；覆盖 `/videos/link` 与 `/settings/download-cookies/{platform}` 端点、错误码表逐条命中、`downloader` 能力节（按平台 `cookies` 映射）；跨源 `DELETE /settings/download-cookies/{platform}` 预检（OPTIONS）断言 `allow_methods` 含 `DELETE` 且放行 | REQ-001, REQ-043 |
| T-ING-001 | 粘贴导入 | 合成中文文本、rights、SHA、无路径 | REQ-010, REQ-011 |
| T-ING-002 | 本地文本解析 | 流式 artifact、native representation、evidence locator | REQ-011, REQ-014, REQ-020, REQ-021 |
| T-ING-003 | 导入预填 | 合成带元数据 PDF/DOCX、MD/TXT 标题规则、zh/en 语言启发、EXIF 图片作者/拍摄日期、损坏文件全 null、后缀与大小上限拒绝、端点零持久化零网络 | REQ-049 |
| T-VID-001 | 本地视频导入与分析 | 合成 MP4/WebM、rights、受控假媒体适配器、元数据和内容寻址 JPEG 帧、Range `206`/无效范围 `416`、工具缺失阻止、AI 未配置阻止且不伪造输出 | REQ-015..017, REQ-033a |
| T-VID-002 | 视频可移植性与清理 | 视频分析/帧记录导出、备份、还原、再导入、篡改记录拒绝及 purge 无引用原件/帧清理 | REQ-016, REQ-034, REQ-040..042 |
| T-VID-003 | 链接下载负面用例 | URL 白名单拒绝与脱敏消息、按平台 Cookie 库治理（每平台 413/覆盖/幂等删除/该平台未导入无静默回退/作业只用本平台文件/遗留单文件分拣迁移/拷贝即删）、断路器（总超时/无进展/内存/磁盘）、取消清理与进程树终止、产物校验回滚（probe/高度>1080）、工具缺失 blocked/503、出处记录与脱敏双重断言、rights 必填、多P/DRM 通用脱敏失败、外联控制（代理拒绝未登记域/重定向逐跳/环境代理覆盖）、settings 边界 | REQ-047, REQ-047a |
| T-VID-004 | 链接下载合成集成 | 本地合成 HTTP 服务器提供无版权 MP4 fixture，真实 yt-dlp 指向 localhost（测试注入注册域与保留段豁免，决策 9），断言全部出站 ⊆ 测试注册表、无外联，下载→video_analyze→播放→导出/备份/再导入→清理全链路 | REQ-047, REQ-042 |
| T-VID-005 | 真实平台手工验收 | 真实 B站/抖音链接手工验收（脱敏摘要与成功率），会员/付费/DRM 按 REQ-047.9 拒绝；因平台反爬不稳定，不作为自动化门禁；由 acceptance 角色独立登记 | REQ-047 |
| T-VID-006 | 链接元数据探测 | fake yt-dlp 子进程解析 title/uploader/upload_date、白名单拒绝脱敏、Cookie 规则（未导入 422/无静默回退）、工具缺失 503、失败/超时 502 脱敏、代理随请求销毁、无 shell/stdin 关闭/环境代理清空 | REQ-047b |
| T-IMG-001 | 图片导入与分析 | Pillow 合成带 EXIF JPEG/无 EXIF PNG/WebP：导入→image_analyze→image_metadata evidence→元数据检索命中→original inline；损坏图片 failed 脱敏；后缀白名单与容量预检；标题 stem 回退 | REQ-048 |
| T-VID-007 | 场景感知采样与分析治理 | `plan_frame_times` 边界/锚点/短视频 ≥3 帧/场景吸附与去重、黑帧候选重试、帧真实宽高与 scene/even reason、v8 迁移补 reason 列、≤v7 归档 reason 默认、多分析列表与当前标记、analysis 按 completeness 门控 | REQ-016, REQ-053 |
| T-AI-001 | 媒体 AI 配置与作业 | fake AI 边界（litellm 转写/completion 与 httpx 探测全部替换为进程内假实现，零真实网络）：base_url 校验与稳定 422、设置往返掩码与凭据文件落盘、分组门控、连通性检查脱敏、错误永不回显密钥、音频分块偏移合并、无时间戳分段合成、完整性规则短路/LLM 阈值、画面文字强制、建议收敛分类、级联 tier 与 visual_gap、先转写后摘要、失败不降版本状态、凭据排除备份与导出 | REQ-017, REQ-051, REQ-052, REQ-033a |
| T-TAX-001 | 分类体系 | taxonomy 端点唯一下发、写入校验（领域多选/体裁 ≤1/未知值拒绝）、SQLite v9 迁移拆分映射、多体裁遗留行编辑强制单选、schema v7 归档再导入拆分规范化、领域（OR/`_none`）/体裁/`topic_id` 过滤、分类与标签 token 退出全文 | REQ-050, REQ-024, REQ-025 |
| T-TOPIC-001 | 主题与来源关系 | 主题重命名/重名 409/删除级联成员/成员移除 404、关系删除涉及性校验、`topic_id` 只过滤来源分支、same-work 候选（同 artifact 哈希/规范化标题/已声明排除） | REQ-025 |
| T-JOB-001 | 作业执行 | queued 到 succeeded/blocked，attempt 与 evidence/index 校验 | REQ-032, REQ-033 |
| T-KNOW-001 | 知识发布 | 无引用拒绝，有有效 evidence 允许发布 | REQ-022 |
| T-EXT-001 | 外部卡 | URL 原样保存、抖音白名单/非 HTTPS 拒绝、无 URL 获取路径或网络访问 | REQ-030, REQ-031 |
| T-LIFE-001 | 生命周期 | 软删、恢复、purge 与 artifact 引用计数 | REQ-034 |
| T-BACK-001 | 备份与导出 | ZIP manifest 和 SHA 验证、禁止原路径字段 | REQ-040, REQ-041, REQ-042 |
| T-UI-001 | UI 烟测 | 真实浏览器访问库、导入、作业、外部卡页面；统一导入页智能识别（文本/文档/图片/视频/链接/外部卡自动路由、链接下载表单的 Cookie 开关按识别平台的 cookies 状态禁用引导/联网告知/识别链接按钮/提交跳转作业页、仅保存外部卡切换）；外部卡页只读列表；跨源 DELETE 下载 Cookie 预检 | REQ-001, REQ-044 |
| T-COMP-001 | Compose | 仅 `tests/runtime/compose-<run-id>` 数据卷，loopback 发布；一次性 `migrate` 成功后 API/worker 才启动，web 不挂载宿主 `dist` | REQ-045 |
| T-INT-001 | 本地全链路集成 | `tests/integration/test_local_full_chain.py`：TestClient 全链路（导入→解析→证据→引用→知识发布→检索→备份→导出→再导入→软删/恢复/purge）；`compose_data_root` 守卫强制 `YUANZHIKU_COMPOSE_DATA_ROOT` 解析为 `tests/runtime/compose-<run-id>`，日常数据根直接拒绝 | REQ-045, REQ-023 |
| T-ARCH-001 | v2 过程档案报告 | 构建 schema v2 目录与 ZIP；核对 Markdown + JSON 同 stem、`report_id`、UTC/枚举、REQ/DEF、来源/证据/manifest 交叉引用、`legacy_inferred` 最小字段、冻结 legacy 路径/哈希全集、冻结 snapshot 有序链、版本汇总逐项一致及 release blocked 门禁；验证已发布目录 ACL 拒绝写入，篡改仅在隔离副本进行；重算 manifest 后仍拒绝 schema、登记、验收身份、运行输出或候选链篡改，v1 fixture 继续验证 | REQ-001, REQ-044, REQ-045, REQ-046 |

测试数据只能位于 `tests/fixtures` 与 `tests/runtime/<run-id>`。开发自测不构成独立测试或验收结论。
| T-STT-001 | 本地转写适配器 | 分段偏移映射与时间戳退化；断路器与取消；config_hash 随引擎/模型变化；模型不可用异常语义 | REQ-054 |
| T-STT-002 | 转写路径策略 | auto/local/api × 模型可用/失败/API 可用降级矩阵；降级事实写入 parser_name/config_hash 与作业消息；REQ-033a 失败不降完整性 | REQ-054, REQ-051 |
| T-MDL-001 | 本地转写模型管理 | 下载成功/校验/重试/删除后策略；stt-model 端点错误码逐条命中；审计事件不含内容 | REQ-054 |
| T-VDIR-001 | 视频直送单元 | 两级直送判定（直送成功/visual_gap）；min(设置, 供应商上限) 判定；重编码+分块组合（段偏移、段级兜底）；config_hash 含供应商/模型 | REQ-055 |
| T-VDIR-002 | 视频直送集成 | 全链路 fake 转写/直送器：完整→tier1、缺失→直送成功（多模态直接出摘要）/失败→visual_gap 支路 | REQ-055, REQ-051 |
| T-RLY-001 | 自备中转 | fake relay 服务器（上传/取 URL/TTL）；relay 优先与上传失败回退；未配置行为不变；URL 不落库不落日志 | REQ-055, 决策 22 |
| T-ANCH-001 | 转写引导锚点融合 | 锚点池（场景点 ∪ 转写段边界 ∪ 静音空档中点 ∪ 等间隔）三级吸附、去重、封顶、黑帧护栏；reason 四值；config_hash 随转写来源变化；无转写退化 | REQ-056, REQ-053 |
| T-REORDER-001 | 入库双入队与链序 | 入队矩阵（auto on/off × 转写器可用/不可用）；priority 保序（转写先执行）；分析→摘要/转写→摘要双链去重；分析成功 ready 写点；REQ-033a 回归 | REQ-056, REQ-051 |
| T-FRAME-001 | 帧理解分支 | 兜底/增强触发矩阵；联络表构建与瞬态帧不入 video_frames/artifact；逐条 video_time_range 证据与独立表示；visual_gap 收窄；越界格子丢弃 | REQ-057 |
| T-FRAME-002 | 帧理解集成 | 全链路 fake：导入→双入队→转写→引导抽帧→摘要三分支→证据链完整；转写晚到→手动重分析→新分析身份并存、detail 取最新 | REQ-056, REQ-057 |
