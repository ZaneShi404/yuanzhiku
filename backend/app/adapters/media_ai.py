"""Configured media AI adapter：OpenAI 兼容端点，全部 AI I/O 经 litellm。

双分组（语音转写 / 理解与摘要）+ 本地凭据文件；设置与凭据每次调用时
惰性读取，运行期改配置无需重启。所有 SDK/HTTP 异常一律经
``sanitize_ai_error`` 脱敏为中文短消息——SDK 错误可能内嵌 URL 或密钥，
绝不原样传播；API 密钥与原始 AI 响应绝不落库或日志。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.domain.media import MediaProcessingLimits, MediaTranscript, MediaTranscriptSegment
from app.domain.models import TAXONOMY_DOMAIN_VALUES, TAXONOMY_GENRE_VALUES
from app.ports.media import (
    MediaAiPort,
    MediaAiUnavailable,
    MediaInputInvalid,
    MediaProcessingCancelled,
    MediaToolUnavailable,
)

# 提示词与判定阈值版本：任何变更都会改变 config_hash，使新旧输出成为不同身份。
AI_PROMPT_VERSION = "1"
# 转写分块：48kbps 下 1800 秒约 10.8MB，远低于 24MB 块上限。
AUDIO_CHUNK_MAX_BYTES = 24 * 1024 * 1024
AUDIO_SEGMENT_SECONDS = 1800
AUDIO_BITRATE = "48k"
VISION_BATCH_SIZE = 4
MAX_TRANSCRIPT_PROMPT_CHARS = 60_000
COMPLETENESS_MIN_COVERAGE_CHARS_PER_SEC = 0.5
COMPLETENESS_MAX_SILENCE_RATIO = 0.3
COMPLETENESS_CONFIDENCE_THRESHOLD = 0.6

_AI_ERROR_MESSAGES = (
    "鉴权失败：请检查 API Key",
    "网络不可达或连接超时",
    "端点无效或模型不可用",
    "模型不可用或请求无效",
    "媒体 AI 服务调用失败",
)


def sanitize_ai_error(exc: BaseException) -> str:
    """把 SDK/HTTP 异常映射为不含 URL、密钥或响应正文的中文短消息。"""
    text = str(exc)
    if text in _AI_ERROR_MESSAGES:
        return text
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "鉴权失败：请检查 API Key"
    if status == 404:
        return "端点无效或模型不可用"
    name = type(exc).__name__.lower().replace("_", "")
    if "authentication" in name or "permission" in name or "unauthorized" in name:
        return "鉴权失败：请检查 API Key"
    if "timeout" in name or "connection" in name or "connect" in name or "network" in name or "dns" in name:
        return "网络不可达或连接超时"
    if "notfound" in name:
        return "端点无效或模型不可用"
    if "badrequest" in name or "invalid" in name:
        return "模型不可用或请求无效"
    return "媒体 AI 服务调用失败"


def _litellm_transcription(**kwargs: Any) -> Any:
    import litellm

    return litellm.transcription(**kwargs)


def _litellm_completion(**kwargs: Any) -> Any:
    import litellm

    return litellm.completion(**kwargs)


def _httpx_models_probe(base_url: str, api_key: str, timeout: float) -> None:
    """转写端点连通性探测：GET {base}/models；状态码与网络错误一律脱敏。"""
    import httpx

    base = (base_url.strip() or "https://api.openai.com/v1").rstrip("/")
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/models", headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise RuntimeError("网络不可达或连接超时") from exc
    if response.status_code in {401, 403}:
        raise RuntimeError("鉴权失败：请检查 API Key")
    if response.status_code == 404:
        raise RuntimeError("端点无效或模型不可用")
    if response.status_code >= 400:
        raise RuntimeError("媒体 AI 服务调用失败")


def _coerce_json(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            payload = model_dump()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("媒体 AI 服务返回结果无法解析")


def _message_content(response: Any) -> str:
    payload = _coerce_json(response)
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    raise RuntimeError("媒体 AI 服务返回结果无法解析")


def _parse_json_dict(content: str) -> dict[str, Any]:
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("媒体 AI 服务返回结果无法解析")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("媒体 AI 服务返回结果无法解析") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("媒体 AI 服务返回结果无法解析")
    return payload


def _transcription_segments(payload: dict[str, Any]) -> list[tuple[float, float, str]]:
    raw = payload.get("segments")
    if not isinstance(raw, list):
        return []
    segments: list[tuple[float, float, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        segments.append((start, end, str(item.get("text") or "")))
    return segments


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class _AiGroupConfig:
    base_url: str
    model: str
    vision_model: str
    api_key: str
    timeout_seconds: float


class ConfiguredMediaAi(MediaAiPort):
    """按当前设置与凭据惰性求解的媒体 AI 适配器（单例即可，改配置即时生效）。"""

    def __init__(
        self,
        settings_getter: Callable[[], dict[str, str]],
        credentials_reader: Callable[[], dict[str, str]],
        staging_dir: Path,
        *,
        transcription_caller: Callable[..., Any] = _litellm_transcription,
        completion_caller: Callable[..., Any] = _litellm_completion,
        models_prober: Callable[[str, str, float], None] = _httpx_models_probe,
        audio_extractor: Callable[[Path, Path, float, Callable[[], bool]], list[tuple[Path, int, int]]] | None = None,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ) -> None:
        self._settings_getter = settings_getter
        self._credentials_reader = credentials_reader
        self._staging_dir = staging_dir
        self._transcription_caller = transcription_caller
        self._completion_caller = completion_caller
        self._models_prober = models_prober
        self._audio_extractor = audio_extractor or self._ffmpeg_audio_chunks
        self._ffmpeg = ffmpeg or os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
        self._ffprobe = ffprobe or os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")

    def capability(self) -> dict[str, object]:
        settings = self._settings_getter()
        credentials = self._credentials_reader()
        transcribe_enabled = (
            settings.get("ai_transcribe_provider", "off") == "openai_compatible" and bool(credentials.get("transcribe"))
        )
        understand_enabled = (
            settings.get("ai_understand_provider", "off") == "openai_compatible" and bool(credentials.get("understand"))
        )
        tier2_enabled = understand_enabled and bool(settings.get("ai_vision_model", "").strip())
        enabled = transcribe_enabled or understand_enabled
        return {
            "enabled": enabled,
            "transcribe_enabled": transcribe_enabled,
            "understand_enabled": understand_enabled,
            "tier2_enabled": tier2_enabled,
            "network": True,
            "provider": "openai_compatible" if enabled else None,
        }

    def config_hash(self, operation: str) -> str:
        settings = self._settings_getter()
        group = "transcribe" if operation == "transcribe" else "understand"
        models = {
            "transcribe": settings.get("ai_transcribe_model", ""),
            "assess": settings.get("ai_chat_model", ""),
            "describe_frames": settings.get("ai_vision_model", ""),
            "summarize": f"{settings.get('ai_chat_model', '')}|{settings.get('ai_vision_model', '')}",
        }.get(operation, "")
        value = (
            f"{settings.get(f'ai_{group}_provider', 'off')}:{settings.get(f'ai_{group}_base_url', '')}"
            f":{models}:{AI_PROMPT_VERSION}:{operation}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _require_group(self, group: str, *, vision: bool = False) -> _AiGroupConfig:
        settings = self._settings_getter()
        if settings.get(f"ai_{group}_provider", "off") != "openai_compatible":
            raise MediaAiUnavailable("not_configured")
        api_key = self._credentials_reader().get(group, "")
        if not api_key:
            raise MediaAiUnavailable("not_configured")
        if group == "transcribe":
            model = settings.get("ai_transcribe_model", "").strip() or "whisper-1"
            vision_model = ""
        else:
            model = settings.get("ai_chat_model", "").strip() or "qwen-plus"
            vision_model = settings.get("ai_vision_model", "").strip()
        if vision and not vision_model:
            raise MediaAiUnavailable("vision_not_configured")
        try:
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("ai_timeout_seconds", "300"))))
        except (TypeError, ValueError):
            timeout_seconds = 300.0
        return _AiGroupConfig(
            base_url=settings.get(f"ai_{group}_base_url", "").strip(),
            model=model,
            vision_model=vision_model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def _limits(self, timeout_seconds: float) -> MediaProcessingLimits:
        settings = self._settings_getter()
        try:
            memory_limit_mb = max(64, min(32_768, int(settings.get("video_memory_limit_mb", "2048"))))
            disk_limit_mb = max(64, min(32_768, int(settings.get("video_disk_limit_mb", "1024"))))
        except (TypeError, ValueError):
            memory_limit_mb, disk_limit_mb = 2048, 1024
        return MediaProcessingLimits(
            timeout_seconds=timeout_seconds,
            maximum_memory_bytes=memory_limit_mb * 1024 * 1024,
            maximum_workspace_bytes=disk_limit_mb * 1024 * 1024,
            deadline_monotonic=time.monotonic() + timeout_seconds,
        )

    def _probe_duration_ms(
        self,
        path: Path,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
    ) -> int:
        output = LocalFfmpegMediaAnalyzer._run(
            [self._ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            limits,
            cancelled,
            lambda: None,
            capture_stdout=True,
        )
        try:
            duration = float(json.loads(output.decode("utf-8"))["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MediaInputInvalid("invalid_duration") from exc
        return max(1, round(duration * 1000))

    def _ffmpeg_audio_chunks(
        self,
        artifact_path: Path,
        workspace: Path,
        timeout_seconds: float,
        cancelled: Callable[[], bool],
    ) -> list[tuple[Path, int, int]]:
        """提取 16kHz 单声道音频并按需分段；返回 (块路径, 偏移毫秒, 时长毫秒)。"""
        limits = self._limits(timeout_seconds)
        heartbeat = lambda: None
        audio = workspace / "audio.mp3"
        try:
            LocalFfmpegMediaAnalyzer._run(
                [
                    self._ffmpeg, "-nostdin", "-v", "error",
                    "-i", str(artifact_path),
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", AUDIO_BITRATE,
                    "-codec:a", "libmp3lame", "-f", "mp3", "-y", str(audio),
                ],
                limits,
                cancelled,
                heartbeat,
                workspace=workspace,
            )
            if not audio.is_file() or audio.stat().st_size == 0:
                raise MediaInputInvalid("audio_missing")
            if audio.stat().st_size <= AUDIO_CHUNK_MAX_BYTES:
                return [(audio, 0, self._probe_duration_ms(audio, limits, cancelled))]
            pattern = workspace / "chunk-%03d.mp3"
            LocalFfmpegMediaAnalyzer._run(
                [
                    self._ffmpeg, "-nostdin", "-v", "error",
                    "-i", str(audio), "-c", "copy",
                    "-f", "segment", "-segment_time", str(AUDIO_SEGMENT_SECONDS),
                    "-reset_timestamps", "1", "-y", str(pattern),
                ],
                limits,
                cancelled,
                heartbeat,
                workspace=workspace,
            )
        except (MediaInputInvalid, MediaToolUnavailable) as exc:
            raise RuntimeError("本地音频提取失败") from exc
        audio.unlink(missing_ok=True)
        chunks: list[tuple[Path, int, int]] = []
        offset_ms = 0
        for chunk in sorted(workspace.glob("chunk-*.mp3")):
            if cancelled():
                raise MediaProcessingCancelled()
            duration_ms = self._probe_duration_ms(chunk, limits, cancelled)
            chunks.append((chunk, offset_ms, duration_ms))
            offset_ms += duration_ms
        if not chunks:
            raise RuntimeError("本地音频提取失败")
        return chunks

    def transcribe(self, artifact_path: Path, media_type: str | None, cancelled: Callable[[], bool]) -> MediaTranscript:
        config = self._require_group("transcribe")
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="ai-audio-", dir=self._staging_dir))
        try:
            chunks = self._audio_extractor(artifact_path, workspace, config.timeout_seconds, cancelled)
            segments: list[MediaTranscriptSegment] = []
            texts: list[str] = []
            for chunk_path, offset_ms, duration_ms in chunks:
                if cancelled():
                    raise MediaProcessingCancelled()
                try:
                    with chunk_path.open("rb") as audio_file:
                        response = self._transcription_caller(
                            model=config.model,
                            file=audio_file,
                            response_format="verbose_json",
                            timestamp_granularities=["segment"],
                            api_key=config.api_key,
                            api_base=config.base_url or None,
                            timeout=config.timeout_seconds,
                        )
                except Exception as exc:
                    raise RuntimeError(sanitize_ai_error(exc)) from None
                payload = _coerce_json(response)
                chunk_segments = _transcription_segments(payload)
                if not chunk_segments:
                    # 无分段时间戳时以整块为一条，保证证据总能定位到时间范围。
                    fallback = str(payload.get("text") or "").strip()
                    if fallback:
                        segments.append(MediaTranscriptSegment(
                            fallback, offset_ms, max(offset_ms + 1, offset_ms + duration_ms),
                        ))
                        texts.append(fallback)
                    continue
                for start_seconds, end_seconds, text in chunk_segments:
                    cleaned = text.strip()
                    if not cleaned:
                        continue
                    start_ms = offset_ms + max(0, round(start_seconds * 1000))
                    end_ms = offset_ms + max(0, round(end_seconds * 1000))
                    if end_ms <= start_ms:
                        end_ms = start_ms + 1
                    segments.append(MediaTranscriptSegment(cleaned, start_ms, end_ms))
                    texts.append(cleaned)
            if not segments:
                raise RuntimeError("语音转写未返回可用文本")
            return MediaTranscript("\n".join(texts), tuple(segments))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _chat_json(self, config: _AiGroupConfig, system_prompt: str, user_content: str, *, model: str | None = None) -> dict[str, Any]:
        try:
            response = self._completion_caller(
                model=model or config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                api_key=config.api_key,
                api_base=config.base_url or None,
                timeout=config.timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(sanitize_ai_error(exc)) from None
        return _parse_json_dict(_message_content(response))

    def assess_completeness(self, transcript_text: str, context: dict[str, Any]) -> dict[str, Any]:
        duration_ms = _as_int(context.get("duration_ms"))
        coverage = _as_float(context.get("coverage_chars_per_sec"))
        max_silence_ms = _as_int(context.get("max_silence_ms"))
        # 确定性规则层：覆盖过低或长静音直接判不完整，不再消耗 LLM 调用。
        if duration_ms > 0 and (
            coverage < COMPLETENESS_MIN_COVERAGE_CHARS_PER_SEC
            or max_silence_ms > COMPLETENESS_MAX_SILENCE_RATIO * duration_ms
        ):
            return {
                "verdict": "likely_incomplete",
                "confidence": 0.9,
                "missing_aspects": [],
                "reason": "转写覆盖率过低或存在长时间静音，疑似存在仅以画面呈现的信息",
                "rule_triggered": True,
            }
        config = self._require_group("understand")
        system = (
            "你是内容完整性判断助手。判断视频转写是否可能遗漏了仅以画面呈现的信息"
            "（如产品名、演示步骤、图表、画面文字），并对照标题与备注检查转写内容的对齐度。"
            '只输出 JSON：{"verdict": "complete" 或 "likely_incomplete", "confidence": 0到1的小数, '
            '"missing_aspects": ["缺失方面"], "reason": "一句话依据"}。'
        )
        user = (
            f"标题：{str(context.get('title') or '未命名')[:200]}\n"
            f"备注：{str(context.get('notes') or '无')[:1000]}\n"
            f"时长（毫秒）：{duration_ms}\n"
            f"转写覆盖率（字符/秒）：{coverage:.2f}\n"
            f"最长静音（毫秒）：{max_silence_ms}\n"
            f"转写内容：\n{transcript_text[:MAX_TRANSCRIPT_PROMPT_CHARS]}"
        )
        payload = self._chat_json(config, system, user)
        confidence = _clamp_confidence(payload.get("confidence"))
        llm_incomplete = (
            payload.get("verdict") == "likely_incomplete" and confidence >= COMPLETENESS_CONFIDENCE_THRESHOLD
        )
        raw_missing = payload.get("missing_aspects")
        missing_aspects = (
            [str(item).strip()[:100] for item in raw_missing if isinstance(item, str) and item.strip()][:8]
            if isinstance(raw_missing, list) else []
        )
        reason = str(payload.get("reason") or "").strip()[:300]
        return {
            "verdict": "likely_incomplete" if llm_incomplete else "complete",
            "confidence": confidence,
            "missing_aspects": missing_aspects if llm_incomplete else [],
            "reason": reason or ("AI 判断转写可能遗漏画面信息" if llm_incomplete else "AI 判断转写内容完整"),
            "rule_triggered": False,
        }

    def describe_frames(
        self,
        frame_inputs: list[dict[str, Any]],
        focus: str,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        config = self._require_group("understand", vision=True)
        system = (
            "你是视频画面理解助手。逐帧输出 JSON："
            '{"frames": [{"index": 序号, "description": "画面要点", "visible_text": "画面文字"}]}。'
            "description 概括画面呈现的关键信息（产品名、演示、图表、人物动作）；"
            "visible_text 逐字提取画面中可见的文字（标题、产品名、标注），没有可见文字时输出空字符串。"
            "只输出 JSON。"
        )
        described: dict[int, dict[str, Any]] = {}
        for batch_start in range(0, len(frame_inputs), VISION_BATCH_SIZE):
            if cancelled is not None and cancelled():
                raise MediaProcessingCancelled()
            batch = frame_inputs[batch_start:batch_start + VISION_BATCH_SIZE]
            content: list[dict[str, Any]] = [{
                "type": "text",
                "text": (
                    f"视频主题：{focus[:200] or '未知'}\n"
                    f"以下 {len(batch)} 张图片为按时间采样的关键帧，index 从 0 到 {len(batch) - 1}。"
                    "请逐帧给出画面要点与画面文字。"
                ),
            }]
            for item in batch:
                try:
                    encoded = base64.b64encode(Path(str(item["path"])).read_bytes()).decode("ascii")
                except OSError as exc:
                    raise RuntimeError("本地关键帧读取失败") from exc
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
            try:
                response = self._completion_caller(
                    model=config.vision_model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
                    response_format={"type": "json_object"},
                    api_key=config.api_key,
                    api_base=config.base_url or None,
                    timeout=config.timeout_seconds,
                )
            except Exception as exc:
                raise RuntimeError(sanitize_ai_error(exc)) from None
            frames = _parse_json_dict(_message_content(response)).get("frames")
            if not isinstance(frames, list):
                raise RuntimeError("媒体 AI 服务返回结果无法解析")
            for entry in frames:
                if not isinstance(entry, dict):
                    continue
                index = entry.get("index")
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(batch):
                    continue
                described[batch_start + index] = {
                    "time_ms": _as_int(batch[index].get("time_ms")),
                    "description": str(entry.get("description") or "").strip()[:500],
                    "visible_text": str(entry.get("visible_text") or "").strip()[:500],
                }
        # 未被模型覆盖的帧补空描述，保证输出与输入一一对应。
        return [
            described.get(ordinal, {"time_ms": _as_int(item.get("time_ms")), "description": "", "visible_text": ""})
            for ordinal, item in enumerate(frame_inputs)
        ]

    def summarize(self, inputs: dict[str, Any], cancelled: Callable[[], bool]) -> dict[str, Any]:
        config = self._require_group("understand")
        if cancelled():
            raise MediaProcessingCancelled()
        transcript = str(inputs.get("transcript_text") or "")
        title = str(inputs.get("title") or "未命名")[:200]
        raw_domains = inputs.get("taxonomy_domains")
        raw_genres = inputs.get("taxonomy_genres")
        domains = [str(value) for value in raw_domains if isinstance(value, str)] if isinstance(raw_domains, list) else list(TAXONOMY_DOMAIN_VALUES)
        genres = [str(value) for value in raw_genres if isinstance(value, str)] if isinstance(raw_genres, list) else list(TAXONOMY_GENRE_VALUES)
        frame_lines: list[str] = []
        raw_frames = inputs.get("frame_descriptions")
        if isinstance(raw_frames, list):
            for item in raw_frames:
                if not isinstance(item, dict):
                    continue
                description = str(item.get("description") or "").strip()
                visible_text = str(item.get("visible_text") or "").strip()
                if description or visible_text:
                    line = f"- [{_as_int(item.get('time_ms')) // 1000}s] {description}"
                    if visible_text:
                        line += f"（画面文字：{visible_text}）"
                    frame_lines.append(line)
        system = (
            "你是中文知识库摘要助手。基于视频转写（可选画面理解）输出结构化摘要。"
            '只输出 JSON：{"summary": "200到600字中文摘要", "suggested_domains": ["领域"], '
            '"suggested_genres": ["体裁"], "suggested_tags": ["标签"]}。'
            "suggested_domains 只能从给定领域清单取值（0到3个）；suggested_genres 只能从给定体裁清单取值（0到1个）；"
            "suggested_tags 为自由短标签（0到8个，每个不超过20字）。"
        )
        user_lines = [
            f"标题：{title}",
            f"领域清单：{'、'.join(domains)}",
            f"体裁清单：{'、'.join(genres)}",
        ]
        if frame_lines:
            user_lines.append("画面理解：")
            user_lines.extend(frame_lines)
        user_lines.append(f"转写内容：\n{transcript[:MAX_TRANSCRIPT_PROMPT_CHARS]}")
        payload = self._chat_json(config, system, "\n".join(user_lines))
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("媒体 AI 服务返回结果无法解析")
        # 建议值域强制收敛到分类体系：清单外的一律丢弃，体裁最多保留一项。
        raw_suggested_domains = payload.get("suggested_domains")
        suggested_domains = sorted({
            item for item in raw_suggested_domains if isinstance(item, str)
        }.intersection(domains)) if isinstance(raw_suggested_domains, list) else []
        raw_suggested_genres = payload.get("suggested_genres")
        suggested_genres = (
            [item for item in raw_suggested_genres if isinstance(item, str) and item in genres][:1]
            if isinstance(raw_suggested_genres, list) else []
        )
        raw_suggested_tags = payload.get("suggested_tags")
        suggested_tags = (
            list(dict.fromkeys(
                str(item).strip()[:20] for item in raw_suggested_tags if isinstance(item, str) and item.strip()
            ))[:8]
            if isinstance(raw_suggested_tags, list) else []
        )
        return {
            "summary": summary[:10_000],
            "suggested_domains": suggested_domains,
            "suggested_genres": suggested_genres,
            "suggested_tags": suggested_tags,
        }

    def test_connection(self, part: str) -> tuple[bool, str]:
        """轻量连通性检查：理解分组一次最小 completion，转写分组 GET /models。"""
        if part not in {"transcribe", "understand"}:
            return False, "不支持的测试分组"
        try:
            config = self._require_group(part)
        except MediaAiUnavailable:
            return False, "该分组未启用或未配置 API Key"
        try:
            if part == "understand":
                self._completion_caller(
                    model=config.model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    api_key=config.api_key,
                    api_base=config.base_url or None,
                    timeout=10,
                )
            else:
                self._models_prober(config.base_url, config.api_key, 10.0)
        except Exception as exc:
            return False, sanitize_ai_error(exc)
        return True, ""
