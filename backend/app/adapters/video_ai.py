"""Video direct-send adapters (REQ-055, decisions 16/17/20/21/22).

三级补充理解的①层：把视频文件直送多模态模型做补充转写/画面理解。
- 配置自备中转（决策 22）时两适配器优先经 relay URL 直送；
- 否则 Qwen 走 DashScope getPolicy→OSS 临时上传→video_url 流程，
  MiMo 走 base64（编码后 ≤50MB）传入，超限时显式重编码（决策 20）、
  仍超限按 ai_video_chunk_seconds 分块直送（决策 21）、段级失败兜底。
所有出站仅发往用户显式配置的端点；错误一律脱敏；字节流不落库不落日志。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.domain.media import MediaProcessingLimits
from app.ports.media import MediaAiUnavailable, MediaProcessingCancelled
from app.ports.media import VideoUnderstandingPort

VIDEO_PROMPT_VERSION = "1"
# MiMo base64 传入：编码后 ≤50MB；37MB 原始文件编码后约 49.3MB。
MIMO_BASE64_SOURCE_LIMIT = 37 * 1024 * 1024
MIMO_DEFAULT_MODEL = "mimo-v2.5"
MIMO_ENDPOINT = "https://api.xiaomimimo.com/v1"
# DashScope 临时上传（URL 传入）保守上限（qwen-vl-plus URL 档位为 1GB）。
QWEN_URL_SOURCE_LIMIT = 1024 * 1024 * 1024
QWEN_DEFAULT_MODEL = "qwen-vl-plus"
QWEN_DEFAULT_DURATION_SECONDS = 600

_SYSTEM_PROMPT = (
    "你是视频内容理解助手。视频的语音转写已单独提供；你的任务是补充仅以画面呈现、"
    "语音中缺失的关键信息（产品名、演示步骤、图表、画面文字、操作界面等）。"
    '只输出 JSON：{"segments": [{"time_offset_seconds": 数字或null, "content": "画面要点"}]}。'
    "time_offset_seconds 为信息在视频中出现的大致秒数，无法判断时输出 null。"
)


def _parse_segments(content: str) -> list[dict[str, Any]]:
    """解析模型 JSON 输出为画面理解条目（与关键帧 describe_frames 同契约）。"""
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("视频直送理解结果无法解析")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("视频直送理解结果无法解析") from exc
    raw = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("视频直送理解结果无法解析")
    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        description = str(item.get("content") or "").strip()[:500]
        if not description:
            continue
        offset = item.get("time_offset_seconds")
        try:
            time_ms = max(0, round(float(offset) * 1000)) if offset is not None else 0
        except (TypeError, ValueError):
            time_ms = 0
        entries.append({"time_ms": time_ms, "description": description, "visible_text": ""})
    if not entries:
        raise RuntimeError("视频直送理解结果无法解析")
    return entries


def _message_content(response: Any) -> str:
    payload = response
    if not isinstance(payload, dict):
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            try:
                payload = model_dump()
            except Exception:
                payload = None
    if isinstance(payload, dict):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
    raise RuntimeError("视频直送理解结果无法解析")


class RelayClient:
    """自备视频中转客户端（决策 22）：上传拿临时 URL，仅发往用户配置端点。"""

    def __init__(
        self,
        settings_getter: Callable[[], dict[str, str]],
        credentials_reader: Callable[[], dict[str, str]],
        *,
        uploader: Callable[[Path, str, str], str] | None = None,
    ) -> None:
        self._settings_getter = settings_getter
        self._credentials_reader = credentials_reader
        self._uploader = uploader

    def configured(self) -> bool:
        base = (self._settings_getter().get("ai_video_relay_base_url") or "").strip()
        secret = self._credentials_reader().get("video_relay") or ""
        return bool(base and secret)

    def upload(self, path: Path) -> str:
        base = (self._settings_getter().get("ai_video_relay_base_url") or "").strip()
        secret = self._credentials_reader().get("video_relay") or ""
        if not base or not secret:
            raise MediaAiUnavailable("relay_not_configured")
        try:
            url = (self._uploader or self._default_uploader)(path, base.rstrip("/"), secret)
        except MediaAiUnavailable:
            raise
        except Exception as exc:
            raise RuntimeError("视频中转上传失败") from exc
        if not isinstance(url, str) or not url.strip():
            raise RuntimeError("视频中转上传失败")
        return url

    @staticmethod
    def _default_uploader(path: Path, base: str, secret: str) -> str:
        import httpx

        with path.open("rb") as stream:
            with httpx.Client(timeout=600.0) as client:
                response = client.post(
                    f"{base}/upload",
                    headers={"Authorization": f"Bearer {secret}"},
                    files={"file": (path.name, stream, "application/octet-stream")},
                )
        if response.status_code >= 400:
            raise RuntimeError("视频中转上传失败")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("视频中转上传失败") from exc
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url:
            raise RuntimeError("视频中转上传失败")
        return url


def _limits_from(settings: dict[str, str], timeout_seconds: float) -> MediaProcessingLimits:
    try:
        memory = max(64, min(32_768, int(settings.get("video_memory_limit_mb", "2048"))))
    except (TypeError, ValueError):
        memory = 2048
    try:
        disk = max(64, min(32_768, int(settings.get("video_disk_limit_mb", "1024"))))
    except (TypeError, ValueError):
        disk = 1024
    return MediaProcessingLimits(timeout_seconds, memory * 1024 * 1024, disk * 1024 * 1024)


def reencode_video(
    source: Path,
    destination: Path,
    limits: MediaProcessingLimits,
    cancelled: Callable[[], bool],
    *,
    ffmpeg: str,
) -> Path:
    """显式重编码（决策 20）：低码率视频 + ≥48kbps 单声道音轨，保留时长与可懂度。"""
    LocalFfmpegMediaAnalyzer._run(
        [
            ffmpeg, "-nostdin", "-v", "error",
            "-i", str(source),
            "-vf", "scale='min(640,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-ac", "1", "-b:a", "48k",
            "-movflags", "+faststart", "-y", str(destination),
        ],
        limits,
        cancelled,
        lambda: None,
        workspace=destination.parent,
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("视频直送前重编码失败")
    return destination


def split_video_segments(
    source: Path,
    workspace: Path,
    chunk_seconds: int,
    limits: MediaProcessingLimits,
    cancelled: Callable[[], bool],
    *,
    ffmpeg: str,
) -> list[tuple[Path, int, int]]:
    """分块直送（决策 21）：按时间切段（重编码输出），返回 (段路径, 偏移毫秒, 时长毫秒)。"""
    pattern = workspace / "segment-%03d.mp4"
    LocalFfmpegMediaAnalyzer._run(
        [
            ffmpeg, "-nostdin", "-v", "error",
            "-i", str(source),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-ac", "1", "-b:a", "48k",
            "-force_key_frames", f"expr:gte(t,n_forced*{chunk_seconds})",
            "-f", "segment", "-segment_time", str(chunk_seconds),
            "-reset_timestamps", "1", "-y", str(pattern),
        ],
        limits,
        cancelled,
        lambda: None,
        workspace=workspace,
    )
    chunks: list[tuple[Path, int, int]] = []
    offset_ms = 0
    ffprobe = os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")
    for segment in sorted(workspace.glob("segment-*.mp4")):
        if cancelled():
            raise MediaProcessingCancelled()
        probe = LocalFfmpegMediaAnalyzer._run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(segment)],
            limits, cancelled, lambda: None, capture_stdout=True,
        )
        try:
            duration = float(json.loads(probe.decode("utf-8"))["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        duration_ms = max(1, round(duration * 1000))
        chunks.append((segment, offset_ms, duration_ms))
        offset_ms += duration_ms
    if not chunks:
        raise RuntimeError("视频分块失败")
    return chunks


class _VideoChatAdapter(VideoUnderstandingPort):
    """供应商无关的直送框架：视频定位（url/data url）+ 提示词 → 条目。"""

    provider_name = "generic"

    def __init__(
        self,
        settings_getter: Callable[[], dict[str, str]],
        credentials_reader: Callable[[], dict[str, str]],
        relay: RelayClient,
        *,
        completion_caller: Callable[..., Any] | None = None,
    ) -> None:
        self._settings_getter = settings_getter
        self._credentials_reader = credentials_reader
        self._relay = relay
        self._completion_caller = completion_caller

    def _default_completion(self) -> Callable[..., Any]:
        import litellm

        return litellm.completion

    def _chat(self, messages: list[dict[str, Any]], model: str, base_url: str | None, api_key: str, timeout: float) -> Any:
        caller = self._completion_caller or self._default_completion()
        try:
            return caller(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                api_key=api_key,
                api_base=base_url,
                timeout=timeout,
            )
        except Exception as exc:
            from app.adapters.media_ai import sanitize_ai_error

            raise RuntimeError(sanitize_ai_error(exc)) from None

    def config_hash(self) -> str:
        settings = self._settings_getter()
        value = (
            f"video-direct:{self.provider_name}:{settings.get('ai_video_provider', 'off')}"
            f":{settings.get('ai_video_model', '')}:{VIDEO_PROMPT_VERSION}"
        ).encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def _timeout_seconds(self) -> float:
        try:
            return max(60.0, min(86_400.0, float(self._settings_getter().get("ai_timeout_seconds", "300"))))
        except (TypeError, ValueError):
            return 300.0

    def _user_text(self, transcript_text: str, focus: str, offset_hint: int = 0) -> str:
        hint = f"\n本段起点约为视频第 {offset_hint // 1000} 秒。" if offset_hint else ""
        return (
            f"视频主题：{focus[:200] or '未知'}\n"
            f"已有语音转写（节选）：{transcript_text[:6000] or '（无）'}\n"
            "请补充仅以画面呈现、语音缺失的关键信息。"
            f"{hint}"
        )


class QwenVideoAdapter(_VideoChatAdapter):
    """通义千问直送（决策 17）：relay 优先 → DashScope 临时上传 → video_url + 转写文本。"""

    provider_name = "qwen"

    def __init__(self, settings_getter, credentials_reader, relay, *, completion_caller=None, policy_fetcher=None, uploader=None) -> None:
        super().__init__(settings_getter, credentials_reader, relay, completion_caller=completion_caller)
        self._policy_fetcher = policy_fetcher
        self._oss_uploader = uploader

    def capability(self) -> dict[str, object]:
        settings = self._settings_getter()
        credentials = self._credentials_reader()
        enabled = settings.get("ai_video_provider", "off") == "qwen" and bool(credentials.get("video_qwen"))
        return {
            "video_input": enabled,
            "max_bytes": QWEN_URL_SOURCE_LIMIT,
            "audio_in_video": False,
            "duration_limits": f"2s-{QWEN_DEFAULT_DURATION_SECONDS}s（qwen-vl-plus）",
            "reencode": False,
            "relay_configured": self._relay.configured(),
        }

    def _config(self) -> tuple[str, str, str, str | None]:
        settings = self._settings_getter()
        api_key = self._credentials_reader().get("video_qwen") or ""
        if not api_key:
            raise MediaAiUnavailable("video_input")
        model = settings.get("ai_video_model", "").strip() or QWEN_DEFAULT_MODEL
        base_url = settings.get("ai_understand_base_url", "").strip()
        if settings.get("ai_understand_provider", "off") != "openai_compatible":
            base_url = ""
        return model, api_key, base_url or None, self._timeout_seconds()

    def _dashscope_temp_url(self, path: Path) -> str:
        fetch = self._policy_fetcher
        uploader = self._oss_uploader
        if fetch is None or uploader is None:
            import urllib.parse

            import httpx

            def fetch(model: str) -> dict:
                with httpx.Client(timeout=60.0) as client:
                    response = client.get(
                        "https://dashscope.aliyuncs.com/api/v1/uploads",
                        params={"action": "getPolicy", "model": model},
                    )
                if response.status_code >= 400:
                    raise RuntimeError("视频临时上传失败")
                data = response.json().get("data")
                if not isinstance(data, dict):
                    raise RuntimeError("视频临时上传失败")
                return data

            def uploader(policy: dict, path: Path) -> str:
                with path.open("rb") as stream:
                    with httpx.Client(timeout=600.0) as client:
                        response = client.post(
                            str(policy.get("upload_host") or policy.get("uploadHost")),
                            data=policy,
                            files={"file": (path.name, stream, "application/octet-stream")},
                        )
                if response.status_code not in {200, 201, 204}:
                    raise RuntimeError("视频临时上传失败")
                key = policy.get("key") or policy.get("object")
                upload_dir = policy.get("upload_dir") or policy.get("dir") or ""
                parsed = urllib.parse.urlparse(str(policy.get("upload_host")))
                return f"https://{parsed.netloc}/{upload_dir.rstrip('/')}/{key}"

            self._policy_fetcher, self._oss_uploader = fetch, uploader
        model, _, _, _ = self._config()
        try:
            return uploader(fetch(model), path)
        except Exception as exc:
            raise RuntimeError("视频临时上传失败") from exc

    def _call(self, video_ref: dict[str, Any], transcript_text: str, focus: str, offset_hint: int, model: str, api_key: str, base_url: str | None, timeout: float) -> list[dict[str, Any]]:
        response = self._chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": [video_ref, {"type": "text", "text": self._user_text(transcript_text, focus, offset_hint)}]},
            ],
            model, base_url, api_key, timeout,
        )
        return _parse_segments(_message_content(response))

    def understand_video(self, video_path: Path, transcript_text: str, focus: str, cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
        model, api_key, base_url, timeout = self._config()
        settings = self._settings_getter()
        if self._relay.configured():
            url = self._relay.upload(video_path)
            return self._call(
                {"type": "video_url", "video_url": {"url": url}},
                transcript_text, focus, 0, model, api_key, base_url, timeout,
            )
        if video_path.stat().st_size > QWEN_URL_SOURCE_LIMIT:
            raise MediaAiUnavailable("video_input")
        try:
            chunk_seconds = max(60, min(3600, int(settings.get("ai_video_chunk_seconds", "600"))))
        except (TypeError, ValueError):
            chunk_seconds = 600
        chunk_seconds = min(chunk_seconds, QWEN_DEFAULT_DURATION_SECONDS)
        entries: list[dict[str, Any]] = []
        workspace = video_path.parent
        limits = _limits_from(settings, timeout)
        segments: list[tuple[Path, int, int]] = [(video_path, 0, 0)]
        if self._probe_duration_ms(video_path, limits, cancelled) > chunk_seconds * 1000:
            try:
                segments = split_video_segments(
                    video_path, workspace, chunk_seconds, limits, cancelled,
                    ffmpeg=os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg"),
                )
            except Exception:
                # 分块失败不静默降质：交由作业层关键帧兜底。
                raise MediaAiUnavailable("video_input") from None
        for segment, offset_ms, _ in segments:
            if cancelled():
                raise MediaProcessingCancelled()
            url = self._dashscope_temp_url(segment)
            for entry in self._call(
                {"type": "video_url", "video_url": {"url": url}},
                transcript_text, focus, offset_ms, model, api_key, base_url, timeout,
            ):
                entry["time_ms"] = offset_ms + int(entry["time_ms"])
                entries.append(entry)
        return entries

    @staticmethod
    def _probe_duration_ms(path: Path, limits: MediaProcessingLimits, cancelled: Callable[[], bool]) -> int:
        try:
            probe = LocalFfmpegMediaAnalyzer._run(
                [os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                limits, cancelled, lambda: None, capture_stdout=True,
            )
        except Exception:
            # 探测为尽力而为：不可探测按短视频处理（单段直送），发送失败
            # 由供应商调用路径如实失败并转关键帧兜底。
            return 0
        try:
            duration = float(json.loads(probe.decode("utf-8"))["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return 0
        return max(1, round(duration * 1000))


class MiMoVideoAdapter(_VideoChatAdapter):
    """小米 MiMo 直送（决策 17/20/21）：relay 优先 → base64（重编码 → 分块）。"""

    provider_name = "mimo"

    def capability(self) -> dict[str, object]:
        settings = self._settings_getter()
        credentials = self._credentials_reader()
        enabled = settings.get("ai_video_provider", "off") == "mimo" and bool(credentials.get("video_mimo"))
        return {
            "video_input": enabled,
            "max_bytes": MIMO_BASE64_SOURCE_LIMIT,
            "audio_in_video": True,
            "duration_limits": "1M 上下文（mimo-v2.5）",
            "reencode": settings.get("ai_video_reencode", "on") == "on",
            "relay_configured": self._relay.configured(),
        }

    def _config(self) -> tuple[str, str, str, str | None]:
        settings = self._settings_getter()
        api_key = self._credentials_reader().get("video_mimo") or ""
        if not api_key:
            raise MediaAiUnavailable("video_input")
        model = settings.get("ai_video_model", "").strip() or MIMO_DEFAULT_MODEL
        return model, api_key, MIMO_ENDPOINT, self._timeout_seconds()

    def _call(self, video_ref: dict[str, Any], transcript_text: str, focus: str, offset_hint: int, model: str, api_key: str, base_url: str, timeout: float) -> list[dict[str, Any]]:
        response = self._chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": [video_ref, {"type": "text", "text": self._user_text(transcript_text, focus, offset_hint)}]},
            ],
            model, base_url, api_key, timeout,
        )
        return _parse_segments(_message_content(response))

    def understand_video(self, video_path: Path, transcript_text: str, focus: str, cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
        model, api_key, base_url, timeout = self._config()
        settings = self._settings_getter()
        if self._relay.configured():
            url = self._relay.upload(video_path)
            return self._call(
                {"type": "video_url", "video_url": {"url": url}},
                transcript_text, focus, 0, model, api_key, base_url, timeout,
            )
        limits = _limits_from(settings, timeout)
        ffmpeg = os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
        candidates: list[tuple[Path, int]] = [(video_path, 0)]
        reencode = settings.get("ai_video_reencode", "on") == "on"
        if video_path.stat().st_size > MIMO_BASE64_SOURCE_LIMIT:
            if not reencode:
                raise MediaAiUnavailable("video_input")
            reencoded = video_path.parent / "video-direct.mp4"
            reencode_video(video_path, reencoded, limits, cancelled, ffmpeg=ffmpeg)
            candidates = [(reencoded, 0)]
            if reencoded.stat().st_size > MIMO_BASE64_SOURCE_LIMIT:
                try:
                    chunk_seconds = max(60, min(3600, int(settings.get("ai_video_chunk_seconds", "600"))))
                except (TypeError, ValueError):
                    chunk_seconds = 600
                try:
                    segments = split_video_segments(reencoded, video_path.parent, chunk_seconds, limits, cancelled, ffmpeg=ffmpeg)
                except Exception:
                    raise MediaAiUnavailable("video_input") from None
                candidates = [(segment, offset_ms) for segment, offset_ms, _ in segments]
        entries: list[dict[str, Any]] = []
        for segment, offset_ms in candidates:
            if cancelled():
                raise MediaProcessingCancelled()
            encoded = base64.b64encode(segment.read_bytes()).decode("ascii")
            if len(encoded) > 50 * 1024 * 1024:
                # 某段仍超限：该段转关键帧兜底（作业层按整体失败处理）。
                raise MediaAiUnavailable("video_input")
            data_url = f"data:video/mp4;base64,{encoded}"
            for entry in self._call(
                {"type": "video_url", "video_url": {"url": data_url}},
                transcript_text, focus, offset_ms, model, api_key, base_url, timeout,
            ):
                entry["time_ms"] = offset_ms + int(entry["time_ms"])
                entries.append(entry)
        return entries
