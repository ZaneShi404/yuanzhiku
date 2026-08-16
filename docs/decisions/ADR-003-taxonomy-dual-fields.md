# ADR-003：分类双字段（领域×体裁）重构与清单规则

状态：接受。

旧固定分类清单混合领域与体裁两个维度，且前后端各自硬编码、无法扩展也无兜底值。决定将分类重构为两个独立字段：领域多选、可空，体裁写入最多一项、可空；清单以后端为唯一来源，经 `GET /taxonomy` 下发中文标签。旧值按固定映射迁移（technical/business/education/news→领域，interview/podcast/document→体裁，未知值忽略），数据库 schema v9 与可移植归档 schema v8 规范化共用同一映射；多体裁遗留行全部保留，下次编辑时强制单选。后果：导入、元数据与检索接口以 `domains`/`genres` 取代 `categories`，构成破坏性 API 变更；分类 token 同时退出全文语料（ADR-008）。（`REQ-050`、`REQ-024`、`REQ-025`）
