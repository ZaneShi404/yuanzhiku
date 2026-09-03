# 源知库 v1.7 实施计划（转写引导的关键帧分析与帧级画面理解）

- 依据：`docs/v1-7-requirements.md`（已审定 2026-09-04：REQ-056/057 新增、REQ-016/017/043/044/051/052/053/055 修订、决策 23–27）
- 范围：管线重排（入库双入队保序）、转写引导锚点融合抽帧、联络表帧理解（兜底 + 可选增强）、手动重分析端点、设置/能力/UI 扩展
- 范围外：视频下载通道（`REQ-047` 系列）、本地转写引擎（`REQ-054`）、文档/粘贴分类、图片分析——均不动

## 0. 前置任务（阻塞项，尽早并行推进）

- `D-5` **E5 实测（供应商图像输入能力）**：qwen-vl 系列单请求图片数量/分辨率/大小上限；mimo-v2.5 图像输入支持与上限——官方文档核实，结论决定适配器 `image_input` 能力声明与联络表参数，归档进需求文档 §3。
- `D-6` **E6 实测（联络表成本）**：16/24/32 格 × 单格分辨率的体积/token 成本/时间定位粒度平衡，验证默认 `ai_video_sheet_frames=24`；结论归档。
- 无其他阻塞项：零新依赖（复用 FFmpeg/yt-dlp/既有媒体 AI 端点）、零新凭据（复用 `ai_video_provider` 凭据）、零新基础设施（帧理解走摘要作业，无新部署物）。

## 1. 阶段划分

### Phase 1：入库双入队与链序改造（REQ-056.1/.5，决策 23）

- `adapters/sqlite.py create_ingest`：`job_kind: str` 参数扩展为支持第二作业（如 `extra_job: tuple[str, int] | None`），同事务插入两行作业。
- `services/imports.py`（`video` 与 `downloaded_video`）：入队矩阵——`ai_auto_pipeline=on` 且转写器可用（检查经组装根注入，本地模型已下载或转写 API 已配置）→ 入队 `video_transcribe`(priority 110) + `video_analyze`(priority 100)；否则仅入队分析(100)。
- `services/jobs.py`：移除分析成功链式转写（现 `jobs.py:580-584`）；分析成功链式摘要（门控 auto_pipeline + understand_enabled）；转写成功链式摘要保留——`_chained_child_if_due` 按 version+kind 去重兜住双触发；下载成功文案更新。
- 验收：`T-REORDER-001`（双入队矩阵、priority 保序断言、双链去重、分析成功 ready 写点、`REQ-033a` 回归）；顺序钉子测试改造（`test_media_ai.py:551-576`、`test_local_full_chain.py:193-240`、`test_job_idempotency.py:153-174`、`test_job_atomic_commits.py`、以分析为前置的用例）。

### Phase 2：锚点融合抽帧（REQ-053 修订、REQ-056.2/.3/.6，决策 24）

- `services/videos.py analyze`：新增可选入参读取同版本 transcription 表示（段级时间范围列表 + 其 `config_hash`）；无则按现行路径。
- `adapters/media.py plan_frame_times`：接受 `transcript_anchors`，派生转写段边界与静音空档中点（静音口径与摘要作业 `jobs.py:869-885` 共用同一算法，提取共享助手避免双实现）；槽位吸附三级优先 scene > transcript/silence > even；`reason` 写入扩展值（scene/even/transcript/silence，零迁移）。
- 分析身份：`config_hash` 输入追加转写来源（transcription 表示的 `config_hash`，无转写以 `none` 参与）。
- 验收：`T-ANCH-001`（融合/三级吸附/去重/max_frames 封顶/黑帧护栏、reason 四值、无→有→换引擎三种分析身份并存、退化路径作业消息、分析零网络负向断言）。

### Phase 3：联络表与摘要三分支（REQ-057，决策 25/26/27）

- `ports/media.py`：`VideoUnderstandingPort` 扩展 `understand_frames(sheet_image, transcript_text, sheet_times, cancelled)`；`capability()` 增加 `image_input`（按 D-5 结论写入）。
- `adapters/video_ai.py`：Qwen/MiMo 图像输入分支（先最小真实调用冒烟再写完整适配器）；联络表构建助手——候选 = 持久帧 + 锚点补抽瞬态缩略图（摘要作业 staging 内 ffmpeg 提取、单格 ≤320px 宽、≤`ai_video_sheet_frames` 截断并在作业消息注明、作业结束清理）。
- `services/jobs.py _video_summarize` 三分支：直送失败/不可行 → 读 `ai_video_frames_fallback` 与 `image_input` → 联络表兜底；`!want_direct` → 读 `ai_video_frames_enrich` → 增强；`visual_gap = want_direct and not video_direct and not frame_fallback`（现 `jobs.py:932` 收窄）。
- 落库：`persist_representation_bundle(kind="visual_understanding", parent=transcription 表示 ID)`，逐条 `video_time_range` 证据（模型未给时间定位以联络表窗格范围定位）；摘要正文标记 `frame_fallback`/`enriched`（`jobs.py:161-210` 口径）。
- 设置/能力：`ai_video_frames_fallback`(on)/`ai_video_frames_enrich`(off)/`ai_video_sheet_frames`(24) 入 `PUT /settings/ai`；`/capabilities` 与 `GET /settings/ai` 扩展。
- 验收：`T-FRAME-001`（兜底/增强触发矩阵、联络表构建与瞬态帧不入 video_frames/artifact 断言、staging 清理、独立表示与逐条证据、visual_gap 收窄、错误脱敏）。

### Phase 4：手动重分析与前端（REQ-043/044 修订）

- `main.py`：新增 `POST /videos/{id}/analyze`（校验 detail 存在 → 创建 `video_analyze` 作业 priority 100 → 202；无前置条件，幂等由分析身份去重）。
- 前端 `frontend/src/App.tsx`：设置页视频直送区三字段；详情页画面理解条目区块（`[mm:ss]` 定位）与 `frame_fallback`/`enriched` 标记；「重新分析」按钮。
- 验收：`T-FRAME-002`（集成全链路：导入/下载 → 双入队 → 转写 → 引导抽帧 → 摘要三分支 → 证据链完整；转写晚到 → 手动重分析 → 新分析身份并存、detail 取最新）+ 前端冒烟。

### Phase 5：文档冻结与基线

- `docs/requirements.md`：并入 REQ-056/057 与八项修订（需求文档第 4 章文本）。
- 新 ADR：`ADR-012-transcript-guided-frame-sampling.md`（决策 23/24）、`ADR-013-frame-understanding-fallback.md`（决策 25/26/27，ADR-006/ADR-011 标记部分取代）。
- 同步：`threat-model.md`（4 行）、`api-contract.md`、`acceptance-matrix.md`（2 行）、`test-plan.md`、`operations-and-recovery.md`、`user-guide/index.html`、`dependency-installation.md`（无新增依赖则仅核对）。
- 归档 v1-7 需求文档为已实施状态。

### Phase 6：回归与发布

- 全量回归（`PYTHONPATH=backend`，489+ 项基线不劣化）；本地全链路冒烟（双入队 → 转写 → 引导抽帧 → 摘要三分支含兜底/增强）。
- 供应商真实联络表调用冒烟（Qwen/MiMo 各一次，独立验收登记，脱敏摘要）。
- v1.7.0 提交（按仓库周期惯例，master 无 remote）。

## 2. 依赖关系

```
D-5/D-6（并行实测）─┐
                    ▼
Phase 1 ──► Phase 2 ──► Phase 3（适配器参数依赖 D-5）──► Phase 4 ──► Phase 5 ──► Phase 6
```

线性主干：双入队是后续一切的地基（转写先行 → 锚点融合 → 帧理解均在摘要作业内）。真实调用冒烟依赖 D-5/D-6 与用户凭据就绪。

## 3. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 顺序钉子测试改造面大（多文件以分析成功为前置） | Phase 1 内一并改造并跑全量回归；暴露的隐藏顺序假设按发现即修，不积压 |
| 供应商图像接口与文档不符 | Phase 3 先最小真实调用冒烟（D-5）再写完整适配器；`image_input` 能力声明驱动降级，失败走 visual_gap |
| 联络表 token 成本失控 | `ai_video_sheet_frames` 上限 8–48、超限截断并注明不静默；E6 实测校准默认 24 |
| 帧理解条目时间戳漂移/缺失 | 模型不给定位时以联络表窗格范围定位；UI 展示定位精度来源，不伪装精确 |
| `config_hash` 输入扩展致分析身份漂移 | 仅影响新分析（既有行不迁移）；`T-ANCH-001` 断言三种身份并存、detail 取最新 |
| 转写晚到用户不知可重分析 | 详情页「重新分析」入口 + 多分析并存取最新；重分析消息注明「已按转写引导」 |
| 双入队 created_at 同刻并列 | 保序依赖 priority 差（110/100）而非时间戳精度（需求文档自述假设 3） |

## 4. 验收标准（对应需求）

- `REQ-056`：双入队保序与退化、分析全程零网络（负向断言）、分析身份含转写来源、多分析并存与手动重分析、ready 写点与顺序无关。
- `REQ-057`：三级级联不伪造、兜底/增强触发矩阵、逐条 `video_time_range` 证据、瞬态帧不持久化、出站纪律（仅已配置端点）、`visual_gap` 收窄。
- `REQ-016/017/043/044/051/052/053/055` 修订全部落地；`REQ-033a`/`REQ-016` 纪律无回归。
- 既有全量测试不回归，新增测试覆盖 §3 风险矩阵；需求文档 §10 门禁清单 8 项逐项满足后才允许冻结为 v1.7。
