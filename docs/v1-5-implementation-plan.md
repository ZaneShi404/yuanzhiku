# 源知库 v1.5 实施计划（视频媒体流水线重构）

- 依据：`docs/v1-5-requirements.md`（草案，REQ-054/055 新增、REQ-017/051/052 修订、决策 14–22）
- 范围：转写双路径（FunASR 本地默认 + API 降级）、视频直送补充理解（Qwen+MiMo 双适配、显式重编码、分块直送、自备中转）、设置/能力/UI 扩展
- 范围外：视频下载与分析环节（`video_download`/`video_analyze`）、文档/粘贴分类、图片分析——均不动

## 0. 前置任务（阻塞项，尽早并行推进）

- `D-1` **域名与 HTTPS**（E4 阻塞项）：用户提供域名并解析至 203.0.113.10（腾讯云已备案要求注意）；中转部署依赖此项，中转代码可先行交付。
- `D-2` **E1 实测**：FunASR 部署变体 torch 完整版 vs onnxruntime 轻量版（中文转写质量/内存/镜像体积），结论决定 `requirements.lock` 与 `LocalFunasrTranscriber` 实现。
- `D-3` **E2 实测**：qwen-omni（原生音视频）vs qwen3-vl（纯视觉+附转写文本）的视频输入能力/时长限制/成本，结论决定 Qwen 适配器模型与默认值。
- `D-4` **凭据**：用户申请 MiMo API Key（mimo.mi.com 控制台）并确认 Qwen/DashScope 凭据；凭据仅入本地凭据文件。

## 1. 阶段划分

### Phase 1：端口与音轨提取重构（行为不变）

- `backend/app/ports/media.py`：新增 `MediaTranscriberPort`（`capability()` / `config_hash()` / `transcribe(audio_chunks, cancelled)`）。
- 音轨提取上移（决策 18）：`ConfiguredMediaAi._ffmpeg_audio_chunks` 拆为共享助手（`adapters/media.py` 或新 `services/audio.py`），作业层调用。
- 新适配器 `ApiTranscriber`：迁出 litellm 转写端点逻辑（行为不变，parser_name 保持 `ai-*`）。
- 验收：现有媒体 AI 测试改经 `ApiTranscriber` 全绿，无行为差异。

### Phase 2：FunASR 本地转写（REQ-054）

- `backend/app/adapters/local_stt.py`：`LocalFunasrTranscriber`（部署变体按 D-2；推理循环注入 cancelled；段偏移映射回视频时间轴；时间戳缺失退化整块，与远程路径语义一致）。
- 模型管理（决策 19）：`data/models/stt/` + 锁文件 `manifest.lock.json`（模型包/版本/ModelScope 来源/许可/SHA-256）；`POST /settings/ai/stt-model`（download/delete，异步+审计，错误脱敏）。
- 设置：`stt_timeout_seconds`/`stt_memory_limit_mb`/`stt_disk_limit_mb`、`ai_local_stt_model`（paraformer-zh / paraformer-zh-quant）。
- 依赖：funasr 家族入 `requirements.lock`（按 D-2）；`docs/dependency-installation.md` 同步安装说明。
- 验收：fake 引擎替身单元测试（分段偏移、时间戳退化、断路器/取消）；模型下载/校验失败/重试/删除流程；下载全程无网络回退。

### Phase 3：转写作业双路径（REQ-054.2，REQ-051 修订）

- `jobs.py _video_transcribe` 重构：音轨提取 → 按 `ai_transcriber_engine`（auto/local/api）选路 → auto 失败降级重转 → representation 落库（parser_name `local-funasr-*` / `ai-*` + 降级原因）。
- 自动串联条件修订：转写入队条件 = 本地模型可用 **或** transcribe 组已配置。
- `/capabilities` `media.ai.local_stt` 节。
- 验收：降级矩阵单元测试（auto/local/api × 模型可用/失败/API 可用）；config_hash 随引擎/模型变化；`REQ-033a` 失败不降完整性回归。

### Phase 4：视频直送补充理解（REQ-055，决策 16/17/20/21）

- `ports/media.py`：新增 `VideoUnderstandingPort`（`capability()` 含 video_input/max_bytes/audio_in_video/duration_limits；`understand_video(video_path, transcript_text, focus, cancelled)`）。
- 新 `adapters/video_ai.py`：
  - `QwenVideoAdapter`：relay 优先 → getPolicy/OSS 临时上传（upload_host 经出站校验）→ `video_url` + 转写文本；超时长/体积按分块直送。
  - `MiMoVideoAdapter`：relay 优先 → base64 → 显式重编码（`ai_video_reencode`）→ 分块直送（`ai_video_chunk_seconds`）→ 单段兜底。
- `jobs.py _video_summarize` 三级回退：视频直送 → 关键帧兜底（现状 `describe_frames`）→ visual_gap；证据 `video_time_range`（段偏移定位）；摘要标记注明降级原因。
- 设置/凭据：`ai_video_provider`/`ai_video_model`/`ai_video_max_bytes`（默认 300MB）/`ai_video_reencode`/`ai_video_chunk_seconds`（默认 600）；凭据文件新增 `video_qwen`/`video_mimo`。
- `/capabilities` `media.ai.video_input` 节；出站校验覆盖 upload_host（`REQ-052` 修订）。
- 先做最小真实调用冒烟（按 D-2/D-3 结论）再写完整适配器。
- 验收：fake 适配器三级回退矩阵；min(设置, 供应商上限) 判定；重编码+分块组合；音频能力分支（原生音频 vs 附转写文本）；上传失败回退。

### Phase 5：自备中转服务（决策 22，REQ-055.3）

- 交付物 `tools/video-relay/`：单文件 FastAPI + Dockerfile/compose + README（上传 Bearer 密钥、≥32 位 hex token、TTL 默认 30 分钟自动删除、无目录列举、路径穿越防护、300MB 上传上限）。
- 应用侧：`ai_video_relay_base_url`/`ai_video_relay_secret`（REQ-052 校验与凭据纪律）；两适配器 relay 优先；上传失败按直送失败处理。
- 部署（D-1 就绪后）：服务器已核查（Ubuntu 22.04 / Docker 27 / 1Panel / 80-443 已放行），按「1Panel OpenResty 反向代理 + Let's Encrypt」方案远程部署。
- 验收：fake relay 服务器测试；未配置时行为不变；relay URL 不落库不落日志。

### Phase 6：设置 UI 与展示

- 前端设置页：本地转写引擎选择 + 模型下载/删除按钮与状态；视频直送供应商/模型/上限/重编码/分块配置；中转地址与密钥（掩码回显）。
- 视频详情：转写来源与降级标记展示（按 parser_name 前缀）。
- 验收：API 层测试 + 前端手工冒烟。

### Phase 7：文档冻结与基线

- `docs/requirements.md`：并入 REQ-054/055 与 REQ-017/051/052 修订（冻结基线）。
- 新 ADR：`ADR-010-funasr-local-transcription.md`（决策 14/15/18/19）、`ADR-011-video-direct-multimodal.md`（决策 16/17/20/21/22）。
- 同步 `threat-model.md`、`api-contract.md`、`dependency-installation.md`、`acceptance-matrix.md`、`test-plan.md`。
- 归档 v1-5 需求草案为已实施状态。

### Phase 8：回归与发布

- 全量测试回归（`PYTHONPATH=backend`）；本地全链路冒烟：下载 → 分析 → 本地转写 → 摘要（完整/缺失两支路含直送与兜底）。
- v1.5.0 提交（按仓库周期惯例，master 无 remote）。

## 2. 依赖关系

```
Phase 1 ──► Phase 2 ──► Phase 3（转写链路）
    │
    └──────► Phase 4（直送链路）──► Phase 5（relay 集成，部署依赖 D-1）
                        │
Phase 6（依赖 3+4+5）◄──┘
Phase 7（依赖 3–6）► Phase 8
```

D-1/D-2/D-3/D-4 与 Phase 1 并行推进；Phase 4 的供应商真实调用冒烟依赖 D-3/D-4。

## 3. 风险与对策

| 风险 | 对策 |
| --- | --- |
| FunASR 依赖重（torch）拖慢安装/镜像 | D-2 尽早实测，倾向 funasr-onnx；模型文件显式下载、不入镜像 |
| MiMo/Qwen 视频接口行为与文档不符 | Phase 4 先最小真实调用冒烟，再写完整适配器；能力声明驱动降级 |
| 中转无域名无法 HTTPS | 代码先行交付；部署等 D-1；未配置中转时链路自动走原路径，无阻塞 |
| 转写/直送成本失控 | 300MB 与 min(供应商) 上限、仅缺失触发、分块上限、visual_gap 兜底 |
| 本机 fake-IP 代理干扰 ModelScope 模型下载 | 沿用决策 10 隧道段例外纪律；下载失败可重试、绝不静默回退云转写 |
| 2C/1.9GB 服务器资源紧张 | 中转服务占用 <100MB；OpenResty 容器轻量；部署后实测内存水位 |

## 4. 验收标准（对应需求）

- `REQ-054`：本地转写默认路径、auto 降级可审计、显式模型下载、零静默网络。
- `REQ-055`：三级回退、双供应商、重编码/分块、段偏移证据定位、绝不静默降质。
- `REQ-017/051/052` 修订全部落地；`REQ-033a`/`REQ-016` 纪律无回归。
- 既有全量测试不回归，新增测试覆盖 §3 风险矩阵。
