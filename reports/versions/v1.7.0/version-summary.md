# v1.7.0 版本归档汇总

## 版本结论

`v1.7.0`（转写引导的关键帧分析与帧级画面理解：管线重排、锚点融合、联络表兜底/增强、手动重分析）实现完成并入冻结需求基线，全量回归 `503 passed, 4 skipped, 0 failed`（新增 14 用例）。开发/测试报告均为 `non_independent`（同会话开发自测）——按归档政策的独立性口径，本版本无独立验收记录，**不得进入推荐快照位：推荐审计快照保持为上一独立验收记录 `20260815T082711Z-v1-3-final-record`**，v1.7.0 待独立 `archive_local` 验收后方可推荐。

这不是产品发布批准。供应商真实联络表调用冒烟（GATE-PROVIDER-FRAME-SMOKE）、E5/E6 实测归档、浏览器黑盒 UI 冒烟与独立审核报告均为未完成门禁，如实登记为 `blocked`/未执行，不伪装通过；Compose/PostgreSQL 物理拓扑门禁维持既有 `blocked`（本机无 Docker）。

## 版本内容

- **管线重排**（`REQ-056`，决策 23，ADR-012）：入库单事务按入队矩阵同队 `video_transcribe`（priority 110）+ `video_analyze`（100），转写先行；`auto_pipeline=off` 或转写器不可用仅入队分析；分析成功按「无转写表示且转写器可用 → 补链转写；有转写表示 → 链式摘要」重写链式逻辑，双链去重；下载完成文案更新。
- **锚点融合抽帧**（`REQ-053`/`REQ-016` 修订，决策 24）：锚点池 = 场景切变点 ∪ 转写段边界 ∪ 静音空档中点（≥2s）∪ 等间隔，吸附优先级 scene > transcript/silence > even；帧 reason 扩展 transcript/silence（零迁移）；分析身份 `config_hash` 纳入转写来源（无转写与 v1.6 身份一致、幂等复用）；无转写表示自动退化纯信号抽帧（与 v1.6 逐位一致），分析作业保持零网络。
- **帧级画面理解**（`REQ-057`/`REQ-055`/`REQ-017` 修订，决策 25/26/27，ADR-013）：摘要作业三级级联——整片直送（主路径）→ 联络表帧理解兜底（`ai_video_frames_fallback` 默认 on，用户审定 D-b）→ `visual_gap` 收窄为两者皆不可行；`ai_video_frames_enrich`（默认 off）转写完整增强（tier 1.5，用户审定）；联络表为 ≤24 格（用户审定 D-c）瞬态缩略图网格 + 单次多模态调用（越界格子丢弃、绝不伪造）；条目落独立 `visual_understanding` 表示（父链挂 transcription、逐条 `video_time_range` 证据、进入检索）；摘要标记新增 `frame_fallback`/`enriched`；实施为摘要作业内分支（用户审定 D-a），零新增作业 kind 与链式机制。
- **手动重分析与 API/UI**（`REQ-043`/`REQ-044` 修订）：`POST /videos/{id}/analyze` 手动重分析（转写晚到后获得引导帧，多分析并存、detail 取最新）；设置页帧理解三字段；详情页画面理解条目区块、徽标与「重新分析」按钮；关键帧标题标注采样来源。
- **决策与文档**：决策 23–27 归档（ADR-012/013，ADR-006/011 标记部分取代）；REQ-056/057 新增、REQ-016/017/043/044/051/052/053/055 修订进入冻结基线；威胁模型 4 行、api-contract/acceptance-matrix/test-plan/operations/user-guide 同步。

## 门禁状态

| 门禁 | 状态 | 说明 |
| --- | --- | --- |
| GATE-UNIT-INTEGRATION-REGRESSION | passed | 503 passed / 4 skipped / 0 failed（含 14 新用例） |
| GATE-PROVIDER-FRAME-SMOKE | blocked | 供应商真实联络表冒烟（Qwen/MiMo 各一次）未执行 |
| GATE-UI-BLACKBOX | blocked | 浏览器黑盒 UI 冒烟未执行 |
| GATE-INDEPENDENT-REVIEW | blocked | 独立审核报告未出具 |
| GATE-PG-PHYSICAL / GATE-COMPOSE-PHYSICAL | blocked | 沿用既有状态（本机无 Docker） |
| E5/E6 实测归档 | blocked | 供应商图像输入能力与联络表成本实测未执行 |

## 提交链

| 提交 | 内容 |
| --- | --- |
| `5dfd3bc` | docs(v1-7)：已审定需求与实施计划入库 |
| `292e592` | feat(video)：双入队与链序改造（REQ-056.1） |
| `7ca740f` | feat(video)：转写引导锚点融合（REQ-056.2/.3） |
| `ace83bd` | feat(video)：联络表帧理解兜底/增强（REQ-057） |
| `a978743` | feat(video)：手动重分析端点与前端（REQ-056.4） |
| `f234385` | docs(v1-7)：冻结基线并入与文档同步 |

## 报告链

| 报告 | 角色 | 独立性 |
| --- | --- | --- |
| `reports/development/20260904T044500Z-v1-7-frame-pipeline-implementation.md` | development | non_independent |
| `reports/testing/20260904T050000Z-v1-7-frame-pipeline-regression.md` | testing | non_independent |
| 本汇总 | release_management | not_applicable |
