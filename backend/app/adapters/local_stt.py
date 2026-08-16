"""Local FunASR transcription adapter (REQ-054, decisions 14/19).

Shell-less and network-free. Models are explicitly downloaded through
``SttModelManager`` and verified before use; each audio chunk is converted
to 16 kHz WAV locally, inferred with the funasr-onnx engine, and mapped
back to the video timeline via chunk offsets. Timestamp-less output
degrades to one segment per chunk (same semantics as the remote path).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.domain.media import MediaProcessingLimits, MediaTranscript, MediaTranscriptSegment
from app.ports.media import (
    MediaAiUnavailable,
    MediaInputInvalid,
    MediaProcessingCancelled,
    MediaTranscriberPort,
)

PROMPT_VERSION = "1"


def _default_engine_loader(paraformer_dir: Path, vad_dir: Path, punc_dir: Path) -> Any:
    """FunASR 引擎加载（无网络）：torch 完整版（funasr.AutoModel）优先，
    onnx 变体兜底（E1 实测：funasr-onnx 的 numpy<=1.26.4 约束与 Python 3.13
    不兼容，实际只有 torch 版可跑；兜底分支供其它 Python 版本使用）。"""
    try:
        from funasr import AutoModel

        try:
            model = AutoModel(
                model=str(paraformer_dir),
                vad_model=str(vad_dir),
                punc_model=str(punc_dir),
                disable_update=True,
            )
        except TypeError:
            model = AutoModel(model=str(paraformer_dir), disable_update=True)
        return _TorchFunasrEngine(model)
    except ImportError:
        pass
    try:
        from funasr_onnx import Paraformer
    except ImportError as exc:
        raise MediaAiUnavailable("engine_missing") from exc
    kwargs: dict[str, Any] = {"batch_size": 1, "quantize": True}
    try:
        model = Paraformer(
            str(paraformer_dir),
            vad_model_dir=str(vad_dir),
            punc_model_dir=str(punc_dir),
            **kwargs,
        )
    except TypeError:
        # 引擎版本不支持 VAD/标点目录参数时降级为基础模型（时间戳随之退化）。
        model = Paraformer(str(paraformer_dir), **kwargs)
    return _OnnxFunasrEngine(model)


class _TorchFunasrEngine:
    """funasr.AutoModel 推理包装：统一 infer(wav) -> list[dict]。"""

    flavor = "funasr"

    def __init__(self, model: Any) -> None:
        self._model = model

    def infer(self, wav_path: Path) -> list[dict[str, Any]]:
        result = self._model.generate(input=str(wav_path))
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            return [result]
        return []


class _OnnxFunasrEngine:
    """funasr-onnx Paraformer 推理包装：统一 infer(wav) -> list[dict]。"""

    flavor = "funasr-onnx"

    def __init__(self, model: Any) -> None:
        self._model = model

    def infer(self, wav_path: Path) -> list[dict[str, Any]]:
        result = self._model(str(wav_path))
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            return [result]
        return []


class LocalFunasrTranscriber(MediaTranscriberPort):
    """本地 FunASR/Paraformer 转写（REQ-054 路径 1，默认）。"""

    def __init__(
        self,
        manager: Any,
        settings_getter: Callable[[], dict[str, str]],
        *,
        engine_loader: Callable[[Path, Path, Path], Any] | None = None,
        wav_converter: Callable[[Path, MediaProcessingLimits, Callable[[], bool]], Path] | None = None,
        ffmpeg: str | None = None,
    ) -> None:
        self._manager = manager
        self._settings_getter = settings_getter
        self._engine_loader = engine_loader or _default_engine_loader
        self._wav_converter = wav_converter or self._ffmpeg_to_wav
        self._ffmpeg = ffmpeg or os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
        self._model: Any = None
        self._flavor: str | None = None

    def capability(self) -> dict[str, object]:
        status = self._manager.status()
        return {
            "enabled": bool(status.get("model_available")),
            "engine": self._flavor or "funasr",
            "model": status.get("model_name") or "paraformer-zh",
            "model_available": bool(status.get("model_available")),
            "network": False,
        }

    def config_hash(self) -> str:
        settings = self._settings_getter()
        model = settings.get("ai_local_stt_model", "paraformer-zh")
        value = f"local-funasr:{self._flavor or 'funasr'}:{model}:{PROMPT_VERSION}".encode("ascii")
        return hashlib.sha256(value).hexdigest()

    def _limits(self) -> MediaProcessingLimits:
        settings = self._settings_getter()
        try:
            timeout = max(60.0, min(86_400.0, float(settings.get("stt_timeout_seconds", "3600"))))
        except (TypeError, ValueError):
            timeout = 3600.0
        try:
            memory = max(64, min(32_768, int(settings.get("stt_memory_limit_mb", "2048"))))
        except (TypeError, ValueError):
            memory = 2048
        try:
            disk = max(64, min(32_768, int(settings.get("stt_disk_limit_mb", "1024"))))
        except (TypeError, ValueError):
            disk = 1024
        return MediaProcessingLimits(timeout, memory * 1024 * 1024, disk * 1024 * 1024)

    def _ensure_model(self) -> Any:
        if self._model is None:
            status = self._manager.status()
            if not status.get("model_available"):
                raise MediaAiUnavailable("model_missing")
            dirs = self._manager.model_dirs()
            self._model = self._engine_loader(
                dirs["paraformer"],
                dirs.get("vad", dirs["paraformer"]),
                dirs.get("punc", dirs["paraformer"]),
            )
            self._flavor = getattr(self._model, "flavor", "funasr")
        return self._model

    def _ffmpeg_to_wav(self, chunk_path: Path, limits: MediaProcessingLimits, cancelled: Callable[[], bool]) -> Path:
        wav = chunk_path.with_name(chunk_path.stem + ".wav")
        LocalFfmpegMediaAnalyzer._run(
            [
                self._ffmpeg, "-nostdin", "-v", "error",
                "-i", str(chunk_path),
                "-ac", "1", "-ar", "16000", "-y", str(wav),
            ],
            limits,
            cancelled,
            lambda: None,
        )
        if not wav.is_file() or wav.stat().st_size == 0:
            raise MediaInputInvalid("audio_missing")
        return wav

    @staticmethod
    def _parse(raw: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
        """解析带时间戳的分段；无有效时间戳的条目交由调用方整块退化。"""
        parsed: list[tuple[int, int, str]] = []
        for item in raw:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            stamps = item.get("timestamp")
            if isinstance(stamps, list) and stamps and isinstance(stamps[0], (list, tuple)) and len(stamps[0]) == 2:
                try:
                    start_ms = max(0, round(float(stamps[0][0])))
                    end_ms = max(start_ms + 1, round(float(stamps[0][1])))
                except (TypeError, ValueError):
                    continue
                parsed.append((start_ms, end_ms, text))
        return parsed

    def transcribe(
        self,
        audio_chunks: list[tuple[Path, int, int]],
        cancelled: Callable[[], bool],
    ) -> MediaTranscript:
        model = self._ensure_model()
        limits = self._limits()
        segments: list[MediaTranscriptSegment] = []
        texts: list[str] = []
        for chunk_path, offset_ms, duration_ms in audio_chunks:
            if cancelled():
                raise MediaProcessingCancelled()
            wav = self._wav_converter(chunk_path, limits, cancelled)
            try:
                raw = model.infer(wav)
            except Exception as exc:
                raise RuntimeError("本地语音转写失败") from exc
            finally:
                wav.unlink(missing_ok=True)
            parsed = self._parse(raw)
            if parsed:
                for start_ms, end_ms, text in parsed:
                    segments.append(MediaTranscriptSegment(
                        text,
                        offset_ms + start_ms,
                        max(offset_ms + start_ms + 1, offset_ms + end_ms),
                    ))
                    texts.append(text)
            else:
                # 无有效时间戳：整块为一条（与远程路径退化语义一致）。
                fallback = "".join(str(item.get("text") or "") for item in raw).strip()
                if fallback:
                    segments.append(MediaTranscriptSegment(
                        fallback, offset_ms, max(offset_ms + 1, offset_ms + duration_ms),
                    ))
                    texts.append(fallback)
        if not segments:
            raise RuntimeError("本地语音转写未返回可用文本")
        return MediaTranscript("\n".join(texts), tuple(segments))
