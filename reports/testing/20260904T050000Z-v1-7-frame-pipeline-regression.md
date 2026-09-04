# 测试回归报告：v1.7 转写引导的关键帧分析与帧级画面理解

- 日期：2026-09-04。
- 角色边界：本报告仅记录开发侧全量回归与新增测试执行结果，不包含独立验收结论（独立性口径：`non_independent`）。
- 被测状态：master `f234385`（实现四提交 292e592 / 7ca740f / ace83bd / a978743 + 文档冻结 f234385）；第三行覆盖独立复核处置提交 `585dc1b`（P2-1/P2-2/P3 修复 + 2 新用例，见 `reports/review/20260904T015022Z-v1-7-independent-adversarial-code-review.md`）。

## 执行结果

| 命令 | 结果 |
|---|---|
| `PYTHONPATH=backend .venv/Scripts/python.exe -m pytest tests/unit tests/integration -p no:cacheprovider -q` | **`503 passed, 4 skipped in 1964.04s (0:32:44)`，0 failed** |
| 变更前基线（同命令，变更前代码） | `489 passed, 4 skipped in 2471.26s`，0 failed |
| **独立复核处置后全量回归**（同命令，树 = 处置提交 `585dc1b`） | **`505 passed, 4 skipped in 1964.37s (0:32:44)`，0 failed**（503 + 复核处置新增 2 用例） |

新增 14 个用例全部通过：T-REORDER-001 双用例（`test_ingest_enqueue_matrix_prioritizes_transcription`、`test_analyze_chains_transcribe_when_configured_late`）、T-ANCH-001 七用例（锚点派生/三级吸附/同距优先级/去重优先级/抽帧融合/身份组合）、T-FRAME-001 四用例（兜底救援/关闭回退/增强支路/越界格子丢弃）、T-FRAME-002 一用例（转写晚到→手动重分析→新分析身份并存、detail 取最新、旧帧保留）。

## 回归要点核对

- 顺序钉子改造后无回归：`test_media_ai.py`（自动流水线链序断言改写为双入队口径）、`test_local_full_chain.py`（auto off 手动逐作业驱动路径不受影响）、`test_video_download.py`（下载完成文案 + 下载后分析入队）、`test_local_stt.py`/`test_video_direct.py`（以分析为前置的用例适配新 `extract_frames` 签名）。
- `REQ-033a` 回归：转写/摘要/帧理解失败与取消不触碰版本完整性与来源处理状态的既有断言全部通过。
- 分析零网络：`test_video_media.py` 既有负向断言（`rg` 级别的媒体适配器无网络 + FakeAnalyzer 路径）通过；转写引导抽帧未引入任何分析路径出站。
- 前端：`npm run build` 通过（Vite 产物 248.62 kB），设置页三字段、详情页画面理解区块与「重新分析」按钮随构建交付。

## 未执行项（如实登记）

- 供应商真实联络表调用冒烟（Qwen/MiMo 各一次）：无凭据与出站授权，独立验收项（GATE-PROVIDER-FRAME-SMOKE `blocked`）。
- E5/E6 实测归档：官方文档口径已按能力声明实现，实测与成本校准未执行。
- Compose/PostgreSQL 物理拓扑：本机无 Docker，维持既有 `blocked`（与 v1.6.0 一致）。
- 浏览器黑盒 UI 冒烟：本轮未执行，留待独立验收轮。
