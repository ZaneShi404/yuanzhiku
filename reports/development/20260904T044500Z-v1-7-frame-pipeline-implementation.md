# 开发报告：v1.7 转写引导的关键帧分析与帧级画面理解（实现）

- 日期：2026-09-04。
- 角色边界：本报告仅记录开发实现与开发自测，不包含或推断后续独立测试、验收的结论（独立性口径：`non_independent`，同会话开发自测）。
- 依据：`docs/v1-7-requirements.md`（已审定，REQ-056/057 新增、REQ-016/017/043/044/051/052/053/055 修订、决策 23–27）与 `docs/v1-7-implementation-plan.md`。

## 已实现（四阶段 + 文档冻结，提交 292e592 / 7ca740f / ace83bd / a978743 / f234385）

- **Phase 1 双入队与链序**（`REQ-056.1`，决策 23）：`create_ingest` 支持 `extra_job`（kind, priority）同事务第二作业；本地导入与链接下载按入队矩阵入队 `video_transcribe`（priority 110）+ `video_analyze`（100）；`ai_auto_pipeline=off` 或转写器不可用仅入队分析。移除分析成功无条件链式转写，改为「无转写表示且转写器可用 → 补链转写；有转写表示 → 链式摘要」，转写成功链式摘要保留，`_chained_child_if_due` 去重兜住双触发；下载完成文案更新。
- **Phase 2 锚点融合**（`REQ-056.2/.3`，决策 24）：`transcript_anchor_points`（段边界 transcript 锚点 + 相邻段 ≥2s 静音空档中点 silence 锚点，纯本地零网络）；`plan_frame_times` 槽位吸附三级优先 scene > transcript/silence > even（无转写时与 v1.6 行为逐位一致）；黑帧重试候选扩展至未使用锚点；帧 reason 扩展 transcript/silence（自由 TEXT 列，零迁移）；分析身份 `analysis_config_hash` 纳入转写表示 config_hash（无转写与 v1.6 身份一致、幂等复用；有转写构成新身份并存）；`_video_analyze` 读取最新 transcription 表示传入段级时间范围，无转写退化并注明。
- **Phase 3 联络表帧理解**（`REQ-057`，决策 25/26/27）：`VideoUnderstandingPort.understand_frames(sheet_image, cells, transcript_text, cancelled)` + capability `image_input`；`build_contact_sheet`（≤`ai_video_sheet_frames` 格瞬态缩略图、单格 ≤320px、PIL 网格 + 1..N 编号，全部位于作业 staging）；`_VideoChatAdapter._sheet_call` 单次多模态调用（格子号即时间定位，越界条目一律丢弃，有效条目为零按失败）；`_video_summarize` 三级级联——直送（主路径）→ 帧理解兜底（`ai_video_frames_fallback` 默认 on）→ visual_gap 收窄（两者皆不可行）；`ai_video_frames_enrich`（默认 off）转写完整增强（tier 1.5）；条目落独立 `visual_understanding` 表示（父链挂 transcription、逐条 `video_time_range` 证据、进入检索）；摘要标记新增 `frame_fallback`/`enriched`；设置键 `ai_video_frames_fallback`/`ai_video_frames_enrich`/`ai_video_sheet_frames` 进入 sqlite/postgres 默认种子（实施期发现：`update_settings` 为 UPDATE 语义，新键不入种子则 PUT 无法落库——T-FRAME-001 首轮暴露后修复）。
- **Phase 4 手动重分析与前端**（`REQ-056.4`/`REQ-043`/`REQ-044`）：`POST /videos/{id}/analyze`（无前置条件，幂等由分析身份去重）；前端设置页三字段、详情页画面理解条目区块、`frame_fallback`/`enriched` 徽标、「重新分析」按钮、关键帧标题标注采样来源（场景切换/转写语义/静音空档/等距）；TypeScript 构建通过。
- **Phase 5 文档冻结**：REQ-056/057 并入 `docs/requirements.md`、八项修订文本替换；威胁模型 4 行、api-contract v1.7 节、acceptance-matrix 2 行、test-plan 4 行、operations 与 user-guide 更新；ADR-012/013 归档。

## 开发自测证据

| 命令/标识 | 结果 | 覆盖 |
|---|---|---|
| 变更前基线：`PYTHONPATH=backend pytest tests/unit tests/integration -q` | `489 passed, 4 skipped in 2471.26s` | 变更前全量基线与 v1.6.0 记录一致 |
| `pytest tests/unit/test_media_ai.py tests/unit/test_video_download.py tests/unit/test_job_idempotency.py tests/unit/test_job_atomic_commits.py tests/unit/test_local_stt.py -q`（Phase 1 后） | `27 passed`（含新增 T-REORDER-001 两用例） | 双入队矩阵、priority 保序、晚配置补链、链序去重、`REQ-033a` 回归 |
| `pytest tests/unit/test_video_media.py -q`（Phase 2 后） | `31 passed`（含 7 个新锚点用例） | 锚点派生/三级吸附/优先级去重、reason 四值、抽帧融合、分析身份组合 |
| `pytest tests/unit/test_media_ai.py tests/unit/test_video_direct.py -q`（Phase 3 后） | `39 passed` | 三级级联改造后既有直送/摘要语义无回归 |
| `pytest tests/unit/test_frame_understanding.py -q`（Phase 3/4 新文件） | `5 passed` | T-FRAME-001（兜底/增强/关闭矩阵、瞬态帧不持久化、逐条证据、越界格子丢弃）+ T-FRAME-002（转写晚到→重分析→新分析身份并存、detail 取最新） |
| `cd frontend && npm run build` | 成功（Vite 产物 248.62 kB） | TypeScript 与生产 UI 构建 |
| 全量回归（实现完成后） | 见 `reports/testing/20260904T….md` | 最终门禁 |

## 已知限制（如实登记，不伪装通过）

- E5（供应商图像输入能力：qwen-vl 单请求图片数量/分辨率上限、mimo-v2.5 图像输入上限）仅完成官方文档口径声明，实测归档未完成；E6（联络表成本）未实测——两者均为 v1.7 需求 §10 门禁项。
- 供应商真实联络表调用冒烟（Qwen/MiMo 各一次）未执行：本会话无供应商凭据与出站授权，属独立验收事项。
- 独立审核报告未出具：本报告为 `non_independent` 开发自测，独立复核按治理流程另行安排。
- Compose/PostgreSQL 物理拓扑门禁维持既有 `blocked` 状态（本机无 Docker），与 v1.6.0 一致。
