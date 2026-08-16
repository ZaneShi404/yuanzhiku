# ADR-010：本地 FunASR 转写引擎与双路径策略

状态：接受（v1.5）。

视频转写从单一远程端点改为双路径（决策 14/15/18/19）：本地 FunASR/Paraformer
为默认路径，远程 OpenAI 兼容转写端点（transcribe 组）为降级/指定路径。本地模型
按 REQ-013 纪律以锁文件（`backend/stt-models.lock.json`，ModelScope 公开源）管
理、设置页显式下载、哈希校验后启用，绝不静默联网；路径策略 `ai_transcriber_engine`
= auto（本地优先，失败自动降级 API，降级事实写入表示的 parser_name/config_hash
与作业消息）/local/api。音轨提取上移为转写作业内共享子步骤（`services/audio.py`），
两条路径共用同一 16kHz 单声道分块音轨，作业结束清理、不落 artifact。两适配器
（`LocalFunasrTranscriber` / `ApiTranscriber`）实现同一 `MediaTranscriberPort`，
config_hash 各自独立。本地转写使用独立断路器设置组（`stt_*`）并全程无网络、无
shell。E1 实测结论：funasr-onnx 锁死 numpy<=1.26.4、与 Python 3.13 不兼容，故
锁定 funasr（torch）完整版。后果：转写默认零出站（本地）；torch 依赖显著增大安装
体积；模型未下载时作业按路径策略降级或 blocked；模型下载/删除写审计事件且不入
备份/导出。（`REQ-054`、`REQ-017`/`REQ-051` 修订、`REQ-033a`）
