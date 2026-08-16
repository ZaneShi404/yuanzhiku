# ADR-005：媒体 AI 双组配置与凭据隔离

状态：接受。

转写与内容理解在提供方间模型形态差异很大（whisper 类转写端点、chat/vision 类理解端点），单组配置无法表达，也不宜强制同钥同端点。决定设两个相互独立的显式配置分组：语音转写（provider/base_url/model/key）与理解摘要（provider/base_url/chat_model/vision_model（可选）/key），各自独立开关、独立连通性检查；任一分组未启用或无 key 时对应作业 blocked，两组全关时行为与未配置完全一致。API key 只存 `<data-root>/state/ai/credentials.json`（原子写入），绝不进入数据库、备份、导出、日志或任何 API 出参（仅回显 has_key 与掩码提示）。后果：用户可只开一组（如仅转写），密钥泄露面限于单个本地文件，并被备份/导出/再导入显式排除。（`REQ-017`、`REQ-051`、`REQ-052`）
