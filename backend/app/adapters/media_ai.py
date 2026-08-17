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

from app.domain.media import MediaProcessingLimits, MediaTranscript, MediaTranscriptSegment
from app.domain.models import TAXONOMY_DOMAIN_VALUES, TAXONOMY_GENRE_VALUES
from app.ports.media import (
    MediaAiPort,
    MediaAiUnavailable,
    MediaProcessingCancelled,
    MediaTranscriberPort,
)
from app.services.audio import extract_audio_chunks

# 提示词与判定阈值版本：任何变更都会改变 config_hash，使新旧输出成为不同身份。
AI_PROMPT_VERSION = "1"
VISION_BATCH_SIZE = 4
MAX_TRANSCRIPT_PROMPT_CHARS = 60_000
MAX_CLASSIFY_PROMPT_CHARS = 8_000
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


def _passthrough_model(model: str) -> str:
    """OpenAI 兼容分组统一走 litellm 的 openai/ 透传：任何模型名都可用，
    不依赖 litellm 版本对模型名的注册表（新模型名如 deepseek-v4-pro 在
    旧版 litellm 上会路由失败报 400）。用户已带前缀的模型名原样保留。"""
    return model if "/" in model else f"openai/{model}"


def _litellm_completion(**kwargs: Any) -> Any:
    import litellm

    kwargs["model"] = _passthrough_model(str(kwargs.get("model") or ""))
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


def transcribe_config_hash(settings: dict[str, str]) -> str:
    """远程转写路径的配置身份（与 ConfiguredMediaAi.config_hash("transcribe") 一致）。"""
    value = (
        f"{settings.get('ai_transcribe_provider', 'off')}:{settings.get('ai_transcribe_base_url', '')}"
        f":{settings.get('ai_transcribe_model', '')}:{AI_PROMPT_VERSION}:transcribe"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _AiGroupConfig:
    base_url: str
    model: str
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
        self._audio_extractor = audio_extractor or self._default_audio_extractor
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
        enabled = transcribe_enabled or understand_enabled
        return {
            "enabled": enabled,
            "transcribe_enabled": transcribe_enabled,
            "understand_enabled": understand_enabled,
            "network": True,
            "provider": "openai_compatible" if enabled else None,
        }

    def config_hash(self, operation: str) -> str:
        settings = self._settings_getter()
        if operation == "transcribe":
            return transcribe_config_hash(settings)
        group = "transcribe" if operation == "transcribe" else "understand"
        models = {
            "transcribe": settings.get("ai_transcribe_model", ""),
            "assess": settings.get("ai_chat_model", ""),
            "summarize": settings.get("ai_chat_model", ""),
            "classify": settings.get("ai_chat_model", ""),
        }.get(operation, "")
        value = (
            f"{settings.get(f'ai_{group}_provider', 'off')}:{settings.get(f'ai_{group}_base_url', '')}"
            f":{models}:{AI_PROMPT_VERSION}:{operation}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _require_group(self, group: str) -> _AiGroupConfig:
        settings = self._settings_getter()
        if settings.get(f"ai_{group}_provider", "off") != "openai_compatible":
            raise MediaAiUnavailable("not_configured")
        api_key = self._credentials_reader().get(group, "")
        if not api_key:
            raise MediaAiUnavailable("not_configured")
        model = (
            settings.get("ai_transcribe_model", "").strip() or "whisper-1"
            if group == "transcribe"
            else settings.get("ai_chat_model", "").strip() or "qwen-plus"
        )
        try:
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("ai_timeout_seconds", "300"))))
        except (TypeError, ValueError):
            timeout_seconds = 300.0
        return _AiGroupConfig(
            base_url=settings.get(f"ai_{group}_base_url", "").strip(),
            model=model,
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

    def _default_audio_extractor(
        self,
        artifact_path: Path,
        workspace: Path,
        timeout_seconds: float,
        cancelled: Callable[[], bool],
    ) -> list[tuple[Path, int, int]]:
        """默认音轨提取（决策 18）：共享助手 + 视频组资源上限。"""
        return extract_audio_chunks(
            artifact_path,
            workspace,
            self._limits(timeout_seconds),
            cancelled,
            ffmpeg=self._ffmpeg,
            ffprobe=self._ffprobe,
        )

    def transcribe(self, artifact_path: Path, media_type: str | None, cancelled: Callable[[], bool]) -> MediaTranscript:
        """远程转写（REQ-017 现状语义；音轨提取上移后委托 ApiTranscriber，决策 18）。"""
        config = self._require_group("transcribe")
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="ai-audio-", dir=self._staging_dir))
        try:
            chunks = self._audio_extractor(artifact_path, workspace, config.timeout_seconds, cancelled)
            return ApiTranscriber(
                self._settings_getter, self._credentials_reader,
                transcription_caller=self._transcription_caller,
            ).transcribe(chunks, cancelled)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _chat_json(self, config: _AiGroupConfig, system_prompt: str, user_content: str, *, model: str | None = None) -> dict[str, Any]:
        try:
            response = self._completion_caller(
                model=_passthrough_model(model or config.model),
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

    def classify(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        """文档/粘贴正文分类：约束 JSON 输出领域/体裁/标签，值域强制收敛到分类体系。"""
        config = self._require_group("understand")
        title = str(context.get("title") or "未命名")[:200]
        raw_domains = context.get("taxonomy_domains")
        raw_genres = context.get("taxonomy_genres")
        domains = [str(value) for value in raw_domains if isinstance(value, str)] if isinstance(raw_domains, list) else list(TAXONOMY_DOMAIN_VALUES)
        genres = [str(value) for value in raw_genres if isinstance(value, str)] if isinstance(raw_genres, list) else list(TAXONOMY_GENRE_VALUES)
        system = (
            "你是中文知识库分类助手。基于来源正文选择最合适的分类。"
            '只输出 JSON：{"domains": ["领域"], "genres": ["体裁"], "tags": ["标签"]}。'
            "domains 只能从给定领域清单取值（0到3个）；genres 只能从给定体裁清单取值（0到1个）；"
            "tags 为自由短标签（0到8个，每个不超过20字）。"
        )
        user = (
            f"标题：{title}\n"
            f"领域清单：{'、'.join(domains)}\n"
            f"体裁清单：{'、'.join(genres)}\n"
            f"正文：\n{text[:MAX_CLASSIFY_PROMPT_CHARS]}"
        )
        payload = self._chat_json(config, system, user)
        # 建议值域强制收敛到分类体系：清单外的一律丢弃，体裁最多保留一项。
        raw_suggested_domains = payload.get("domains")
        suggested_domains = sorted({
            item for item in raw_suggested_domains if isinstance(item, str)
        }.intersection(domains)) if isinstance(raw_suggested_domains, list) else []
        raw_suggested_genres = payload.get("genres")
        suggested_genres = (
            [item for item in raw_suggested_genres if isinstance(item, str) and item in genres][:1]
            if isinstance(raw_suggested_genres, list) else []
        )
        raw_suggested_tags = payload.get("tags")
        suggested_tags = (
            list(dict.fromkeys(
                str(item).strip()[:20] for item in raw_suggested_tags if isinstance(item, str) and item.strip()
            ))[:8]
            if isinstance(raw_suggested_tags, list) else []
        )
        return {"domains": suggested_domains, "genres": suggested_genres, "tags": suggested_tags}

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
                    model=_passthrough_model(config.model),
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


class ApiTranscriber(MediaTranscriberPort):
    """远程转写端点适配器（REQ-054 路径 2）：OpenAI 兼容转写端点，经 litellm。

    输入为作业统一提取的音轨分块（决策 18）；逐块调用并把分段映射回
    视频时间轴（块偏移 + 段内偏移）；无分段时间戳时整块退化为一条。
    与 ConfiguredMediaAi 共用同一转写配置求解（转写组设置 + 凭据）。
    """

    def __init__(
        self,
        settings_getter: Callable[[], dict[str, str]],
        credentials_reader: Callable[[], dict[str, str]],
        *,
        transcription_caller: Callable[..., Any] | None = None,
    ) -> None:
        self._settings_getter = settings_getter
        self._credentials_reader = credentials_reader
        self._transcription_caller = transcription_caller or _litellm_transcription

    def capability(self) -> dict[str, object]:
        settings = self._settings_getter()
        credentials = self._credentials_reader()
        enabled = (
            settings.get("ai_transcribe_provider", "off") == "openai_compatible"
            and bool(credentials.get("transcribe"))
        )
        return {"enabled": enabled, "engine": "api", "network": True}

    def config_hash(self) -> str:
        return transcribe_config_hash(self._settings_getter())

    def _config(self) -> _AiGroupConfig:
        settings = self._settings_getter()
        if settings.get("ai_transcribe_provider", "off") != "openai_compatible":
            raise MediaAiUnavailable("not_configured")
        api_key = self._credentials_reader().get("transcribe", "")
        if not api_key:
            raise MediaAiUnavailable("not_configured")
        model = settings.get("ai_transcribe_model", "").strip() or "whisper-1"
        try:
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("ai_timeout_seconds", "300"))))
        except (TypeError, ValueError):
            timeout_seconds = 300.0
        return _AiGroupConfig(
            base_url=settings.get("ai_transcribe_base_url", "").strip(),
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )

    def transcribe(
        self,
        audio_chunks: list[tuple[Path, int, int]],
        cancelled: Callable[[], bool],
    ) -> MediaTranscript:
        config = self._config()
        segments: list[MediaTranscriptSegment] = []
        texts: list[str] = []
        for chunk_path, offset_ms, duration_ms in audio_chunks:
            if cancelled():
                raise MediaProcessingCancelled()
            try:
                with chunk_path.open("rb") as audio_file:
                    response = self._transcription_caller(
                        model=_passthrough_model(config.model),
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
