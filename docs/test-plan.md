# 测试计划

| 标识 | 范围 | 验证方式 | 需求 |
|---|---|---|---|
| T-API-001 | 健康与 OpenAPI | TestClient 调用 `/api/v1/health` 与 `/openapi.json` | REQ-001, REQ-043 |
| T-ING-001 | 粘贴导入 | 合成中文文本、rights、SHA、无路径 | REQ-010, REQ-011 |
| T-ING-002 | 本地文本解析 | 流式 artifact、native representation、evidence locator | REQ-011, REQ-014, REQ-020, REQ-021 |
| T-VID-001 | 本地视频导入与分析 | 合成 MP4/WebM、rights、受控假媒体适配器、元数据和内容寻址 JPEG 帧、Range `206`/无效范围 `416`、工具缺失阻止、AI 未配置阻止且不伪造输出 | REQ-015..017, REQ-033a |
| T-VID-002 | 视频可移植性与清理 | 视频分析/帧记录导出、备份、还原、再导入、篡改记录拒绝及 purge 无引用原件/帧清理 | REQ-016, REQ-034, REQ-040..042 |
| T-JOB-001 | 作业执行 | queued 到 succeeded/blocked，attempt 与 evidence/index 校验 | REQ-032, REQ-033 |
| T-KNOW-001 | 知识发布 | 无引用拒绝，有有效 evidence 允许发布 | REQ-022 |
| T-EXT-001 | 外部卡 | URL 原样保存、抖音白名单/非 HTTPS 拒绝、无 URL 获取路径或网络访问 | REQ-030, REQ-031 |
| T-LIFE-001 | 生命周期 | 软删、恢复、purge 与 artifact 引用计数 | REQ-034 |
| T-BACK-001 | 备份与导出 | ZIP manifest 和 SHA 验证、禁止原路径字段 | REQ-040, REQ-041, REQ-042 |
| T-UI-001 | UI 烟测 | 真实浏览器访问库、导入、作业、外部卡页面 | REQ-001, REQ-044 |
| T-COMP-001 | Compose | 仅 `tests/runtime/compose-<run-id>` 数据卷，loopback 发布；一次性 `migrate` 成功后 API/worker 才启动，web 不挂载宿主 `dist` | REQ-045 |
| T-ARCH-001 | v2 过程档案报告 | 构建 schema v2 目录与 ZIP；核对 Markdown + JSON 同 stem、`report_id`、UTC/枚举、REQ/DEF、来源/证据/manifest 交叉引用、`legacy_inferred` 最小字段、冻结 legacy 路径/哈希全集、冻结 snapshot 有序链、版本汇总逐项一致及 release blocked 门禁；验证已发布目录 ACL 拒绝写入，篡改仅在隔离副本进行；重算 manifest 后仍拒绝 schema、登记、验收身份、运行输出或候选链篡改，v1 fixture 继续验证 | REQ-001, REQ-044, REQ-045, REQ-046 |

测试数据只能位于 `tests/fixtures` 与 `tests/runtime/<run-id>`。开发自测不构成独立测试或验收结论。
