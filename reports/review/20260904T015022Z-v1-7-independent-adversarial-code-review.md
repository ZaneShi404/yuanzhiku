# v1-7-independent-adversarial-code-review：复核报告

- 报告 ID：`RPT-V1-7-INDEPENDENT-ADVERSARIAL-CODE-REVIEW-20260904T015022Z-001`
- 记录时间（UTC）：`2026-09-04T01:50:22Z`
- 报告类型：`review`
- 作者角色：`review`
- 独立性：`non_independent`（复核由全新上下文的独立代理执行，与实现会话隔离；按政策口径如实标注，登记有效但不进推荐快照位）
- 产品版本：`v1.7.0`
- 裁定范围：`archive_local`
- 裁定：`accepted`（叙事裁决：**APPROVE-WITH-CONDITIONS**，两项 P2 条件已于归档前处置完毕，见「发现与处置」）

## 范围

对 v1.7.0 全部实现提交（`5dfd3bc..7bad9bd`，7 个提交，35 文件 +1468/−105）做逐提交对抗性代码复核，关联需求：`REQ-053`、`REQ-055`、`REQ-056`、`REQ-057`（并核对 `REQ-016`/`REQ-017`/`REQ-043`/`REQ-044`/`REQ-051`/`REQ-052` 修订与 `REQ-033a`/`REQ-015` 纪律）。复核对象：双入队事务性与保序、分析成功补链条件、锚点融合边界、分析身份组合哈希、联络表构建/调用/瞬态纪律、兜底与增强触发矩阵、`visual_understanding` 证据链、REQ-052 出站边界、错误脱敏、测试质量、API 路由完整性。

## 验证

| 验证 | 命令/方式 | 结果 |
| --- | --- | --- |
| 逐提交审查 | `git show` 全部 7 个提交 | 完成（35 文件，+1468/−105） |
| 全量回归（复核代理亲测，处置前树 `7bad9bd`） | `PYTHONPATH=backend .venv/Scripts/python.exe -m pytest tests/unit tests/integration -p no:cacheprovider -q` | `503 passed, 4 skipped, 0 failed`（2019.28s） |
| 前端构建 | `cd frontend && npm run build` | 通过（1579 modules） |
| 路由探针 | v1.6（`5dfd3bc`）与受审树的 `main.py` 路由对拍 | 74 → 75 条，仅新增 `/videos/{source_id}/analyze`，零删改 |
| 无转写对拍 | v1.6 vs v1.7 `plan_frame_times`（无转写锚点，时间序输入）fuzz | 6000 组 0 差异（乱序输入 12 组并列距离 tie-break 顺序伪影，已按 P3-3 处置消除） |
| 处置后验证 | P2/P3 修复提交 `585dc1b`：针对性测试（帧理解 7 用例、视频媒体 32 用例、媒体 AI 39 用例全绿）+ 处置后全量回归由 `reports/testing/20260904T050000Z-v1-7-frame-pipeline-regression.md` 第二行登记 | 通过 |

## 发现与处置

- **P2-1 分析身份未覆盖「同引擎、不同转写内容」**（`services/videos.py` `analysis_config_hash`、`services/jobs.py`）：转写 A → 分析（身份 H1）→ 同引擎重转产生新段边界 → 手动重分析仍命中 H1 → 新帧集与既有行不一致抛错 → 重试耗尽后 `failed` 且来源被降级（`video_analyze ∉ AI_JOB_KINDS`）。**处置（提交 `585dc1b`）**：分析身份改纳入转写表示的唯一身份（representation id，唯一对应一次转写作业产出的具体转写内容），同引擎重转产生新内容即构成新身份、并存不冲突；`REQ-016`/`REQ-056.3` 冻结文本同步修订；`test_analysis_config_hash_identity_combines_transcript_source` 更新钉住「同 config_hash 不同 rep id → 不同身份」。
- **P2-2 三处冻结需求明文的「作业消息注明（不静默）」未实现**：① `REQ-056.2` 无转写退化时分析成功消息恒为「本地视频分析完成」；② `REQ-057.6` 增强开启但供应商不具备图像输入时静默跳过；③ 威胁模型行 3 联络表超限截断静默执行。**处置（提交 `585dc1b`）**：① 退化路径终态消息改为「转写不可用，已按场景感知策略抽帧」；② 增强跳过时作业消息注明「画面增强已开启但供应商不具备图像输入，已跳过」；③ 截断时作业消息注明「联络表候选 N 点超出上限 M 格，已按上限截断」，并顺带在兜底尝试失败时于 degraded 原因注明「（关键帧画面理解兜底亦未成功）」。
- **P3-1 `docs/api-contract.md` 链序失同步**：已按 v1.7 链序改写（处置于 `585dc1b`）。
- **P3-2 联络表格子号解析过宽（bool→1、浮点截断）**：已改为仅接受真整数，bool/浮点一律丢弃；测试新增 bool/2.9/2.0 三例断言（处置于 `585dc1b`）。
- **P3-3 锚点池并列裁决依赖调用方传入顺序**：`_anchor_pool` 已显式排序场景点（处置于 `585dc1b`），对拍伪影消除。
- **P3-4 测试缺口（登记留后续轮次，不阻断）**：联络表构建失败路径与 image_input 不可行矩阵格本轮已补两用例；仍缺「真实 builder 的 staging 清理断言」「`transcript_segments` 接线显式断言」「转写先成功而来源尚未 ready 的中间态断言」「分析路径零网络负向断言的自动化载体」（该性质由构造保证——分析路径无任何 HTTP 客户端，复核已逐一核实）。
- **P3-5 观察项（不计缺陷）**：兜底尝试失败的区分度已随 P2-2 改善（degraded 原因注明）；联络表候选实现为「全部按时间点瞬态重抽」而非「复用持久帧文件 + 补抽」，结果等价、瞬态纪律满足，属需求文本的宽松实现，予以接受。

## 逐项对抗核查结论

双入队事务性与保序通过（同事务插入、重放不补队、priority 差不依赖 created_at 精度、单 worker 无并发领取）；分析成功补链条件通过（摘要永不在无 transcription 时被链入，转写失败不循环）；锚点融合边界通过（零长/越界/重叠/去重全防护，无转写行为逐位不变）；联络表通过（staging 隔离 + finally 清理、无任何持久化写点、越界/空结果不伪造）；设置链路通过（代码默认 + schema 种子 + PUT 校验 + GET 回显 + 前端往返，种子缺失时读取处兜底）；`visual_understanding` 证据链通过（作业级确定性 id、父链挂转写、逐条 `video_time_range`、检索口径与 transcription/summary 一致）；REQ-052 出站边界通过（帧字节唯一出站经既有 litellm 通道至用户显式配置端点，无新增 httpx/中转/上传逻辑）；错误脱敏通过（新增消息均为固定中文短消息）；测试质量整体良好（14+3 新用例，缺口已登记）。

## 结论

**APPROVE-WITH-CONDITIONS**——核心架构（双入队同事务、priority 保序、锚点融合退化、三级级联、瞬态联络表、visual_understanding 证据链）实现正确，无 P0/P1；两项 P2 条件（分析身份语义、作业消息诚实性）与可处置 P3 已在归档前全部修复（`585dc1b`）并经针对性测试验证，处置后全量回归由测试报告登记。代码可进入两段式归档流程；推荐快照位按政策保持 `20260815T082711Z-v1-3-final-record`（本复核独立性口径为 `non_independent`）。
