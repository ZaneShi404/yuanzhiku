"""Shared audio-track extraction for the transcription pipeline (决策 18).

Each transcription job extracts the 16kHz mono track exactly once into the
job staging workspace; the local (FunASR) and remote (API) transcriber
adapters consume the same chunks. The workspace is cleaned up with the job,
so the track is never stored as a durable artifact. Execution follows the
LocalFfmpegMediaAnalyzer discipline: bounded time, memory and workspace,
cooperative cancellation, shell-less subprocesses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.domain.media import MediaProcessingLimits
from app.ports.media import MediaInputInvalid, MediaToolUnavailable

AUDIO_CHUNK_MAX_BYTES = 24 * 1024 * 1024
AUDIO_SEGMENT_SECONDS = 1800
AUDIO_BITRATE = "48k"


def _probe_duration_ms(
    path: Path,
    limits: MediaProcessingLimits,
    cancelled: Callable[[], bool],
    ffprobe: str,
) -> int:
    output = LocalFfmpegMediaAnalyzer._run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
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


def extract_audio_chunks(
    artifact_path: Path,
    workspace: Path,
    limits: MediaProcessingLimits,
    cancelled: Callable[[], bool],
    *,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> list[tuple[Path, int, int]]:
    """提取 16kHz 单声道音轨并按需分段；返回 (块路径, 偏移毫秒, 时长毫秒)。

    与既有远程转写路径同一参数纪律（16kHz 单声道、48kbps、>24MB 按
    1800s 分段）；失败统一脱敏为 RuntimeError（不含路径与命令内容）。
    """
    ffmpeg = ffmpeg or os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
    ffprobe = ffprobe or os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")
    heartbeat = lambda: None
    audio = workspace / "audio.mp3"
    try:
        LocalFfmpegMediaAnalyzer._run(
            [
                ffmpeg, "-nostdin", "-v", "error",
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
            return [(audio, 0, _probe_duration_ms(audio, limits, cancelled, ffprobe))]
        pattern = workspace / "chunk-%03d.mp3"
        LocalFfmpegMediaAnalyzer._run(
            [
                ffmpeg, "-nostdin", "-v", "error",
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
        duration_ms = _probe_duration_ms(chunk, limits, cancelled, ffprobe)
        chunks.append((chunk, offset_ms, duration_ms))
        offset_ms += duration_ms
    if not chunks:
        raise RuntimeError("本地音频提取失败")
    return chunks
