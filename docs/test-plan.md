# 测试计划

| 标识 | 范围 | 验证方式 | 需求 |
|---|---|---|---|
| T-API-001 | 健康与 OpenAPI | TestClient 调用 `/api/v1/health` 与 `/openapi.json` | REQ-001, REQ-043 |
| T-ING-001 | 粘贴导入 | 合成中文文本、rights、SHA、无路径 | REQ-010, REQ-011 |
| T-ING-002 | 本地文本解析 | 流式 artifact、native representation、evidence locator | REQ-011, REQ-014, REQ-020, REQ-021 |
| T-JOB-001 | 作业执行 | queued 到 succeeded/blocked，attempt 与 evidence/index 校验 | REQ-032, REQ-033 |
| T-KNOW-001 | 知识发布 | 无引用拒绝，有有效 evidence 允许发布 | REQ-022 |
| T-EXT-001 | 外部卡 | URL 原样保存、抖音白名单/非 HTTPS 拒绝、无网络 | REQ-030, REQ-031 |
| T-LIFE-001 | 生命周期 | 软删、恢复、purge 与 artifact 引用计数 | REQ-034 |
| T-BACK-001 | 备份与导出 | ZIP manifest 和 SHA 验证、禁止原路径字段 | REQ-040, REQ-041, REQ-042 |
| T-UI-001 | UI 烟测 | 真实浏览器访问库、导入、作业、外部卡页面 | REQ-001, REQ-044 |
| T-COMP-001 | Compose | 仅 `tests/runtime/compose-<run-id>` 数据卷，loopback 发布 | REQ-045 |

测试数据只能位于 `tests/fixtures` 与 `tests/runtime/<run-id>`。开发自测不构成独立测试或验收结论。
