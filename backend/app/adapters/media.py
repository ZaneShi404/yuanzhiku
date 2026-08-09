"""Explicit local FFmpeg media analysis and disabled-by-default AI adapter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, MediaTranscript, VideoMetadata
from app.ports.media import (
    MediaAiUnavailable,
    MediaAnalyzerPort,
    MediaInputInvalid,
    MediaProcessingCancelled,
    MediaToolUnavailable,
)


class LocalFfmpegMediaAnalyzer(MediaAnalyzerPort):
    """Run only locally installed ffprobe/ffmpeg binaries without a shell."""

    def __init__(self, ffprobe: str | None = None, ffmpeg: str | None = None) -> None:
        self.ffprobe = ffprobe or os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")
        self.ffmpeg = ffmpeg or os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")

    def capability(self) -> dict[str, object]:
        return {
            "enabled": bool(shutil.which(self.ffprobe) and shutil.which(self.ffmpeg)),
            "adapter": "ffmpeg-local",
            "ffprobe_available": bool(shutil.which(self.ffprobe)),
            "ffmpeg_available": bool(shutil.which(self.ffmpeg)),
            "network": False,
            "supported_media_types": ["video/mp4", "video/webm"],
        }

    @staticmethod
    def config_hash(maximum_frames: int) -> str:
        value = f"ffmpeg-local:1:jpeg-sampling:{maximum_frames}".encode("ascii")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _process_memory_bytes(process_id: int) -> int | None:
        try:
            import psutil  # type: ignore[import-not-found]

            process = psutil.Process(process_id)
            return process.memory_info().rss + sum(
                child.memory_info().rss for child in process.children(recursive=True)
            )
        except (ImportError, OSError):
            return None

    @staticmethod
    def _workspace_size(workspace: Path | None) -> int:
        if workspace is None:
            return 0
        try:
            return sum(item.stat().st_size for item in workspace.rglob("*") if item.is_file())
        except OSError:
            return 0

    @classmethod
    def _run(
        cls,
        command: list[str],
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        *,
        workspace: Path | None = None,
        capture_stdout: bool = False,
    ) -> bytes:
        output_file = tempfile.TemporaryFile() if capture_stdout else None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output_file if output_file is not None else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except FileNotFoundError as exc:
            if output_file is not None:
                output_file.close()
            raise MediaToolUnavailable("not_found") from exc
        started = time.monotonic()
        deadline = limits.deadline_monotonic or (started + limits.timeout_seconds)
        output = b""
        try:
            while True:
                if cancelled():
                    raise MediaProcessingCancelled()
                if time.monotonic() >= deadline:
                    raise MediaInputInvalid("timeout")
                if cls._workspace_size(workspace) > limits.maximum_workspace_bytes:
                    raise MediaInputInvalid("workspace_limit")
                memory = cls._process_memory_bytes(process.pid)
                if memory is not None and memory > limits.maximum_memory_bytes:
                    raise MediaInputInvalid("memory_limit")
                try:
                    process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    heartbeat()
            if output_file is not None:
                output_file.seek(0, os.SEEK_END)
                if output_file.tell() > 512 * 1024:
                    raise MediaInputInvalid("output_limit")
                output_file.seek(0)
                output = output_file.read()
            if cls._workspace_size(workspace) > limits.maximum_workspace_bytes:
                raise MediaInputInvalid("workspace_limit")
            if len(output) > 512 * 1024 or process.returncode != 0:
                raise MediaInputInvalid("failed")
            return output
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if output_file is not None:
                output_file.close()

    def probe(
        self,
        artifact_path: Path,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> VideoMetadata:
        output = self._run(
            [
                self.ffprobe,
                "-v", "error",
                "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,width,height",
                "-of", "json",
                str(artifact_path),
            ],
            limits,
            cancelled,
            heartbeat,
            capture_stdout=True,
        )
        try:
            payload = json.loads(output.decode("utf-8"))
            fmt = payload["format"]
            streams = payload["streams"]
            container_name = str(fmt["format_name"])
            duration_seconds = float(fmt.get("duration") or 0)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MediaInputInvalid("invalid_metadata") from exc
        if duration_seconds <= 0 or duration_seconds > 24 * 60 * 60:
            raise MediaInputInvalid("invalid_duration")
        video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
        if not isinstance(video_stream, dict):
            raise MediaInputInvalid("missing_video")
        audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
        width = video_stream.get("width")
        height = video_stream.get("height")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise MediaInputInvalid("invalid_dimensions")
        return VideoMetadata(
            container_name=container_name,
            duration_ms=max(1, round(duration_seconds * 1000)),
            width=width,
            height=height,
            video_codec=str(video_stream.get("codec_name") or "") or None,
            audio_codec=str(audio_stream.get("codec_name") or "") if isinstance(audio_stream, dict) else None,
        )

    def extract_frames(
        self,
        artifact_path: Path,
        metadata: VideoMetadata,
        workspace: Path,
        maximum_frames: int,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> tuple[ExtractedVideoFrame, ...]:
        if not 1 <= maximum_frames <= 32:
            raise ValueError("maximum_frames")
        workspace.mkdir(parents=True, exist_ok=True)
        count = min(maximum_frames, max(1, (metadata.duration_ms + 119_999) // 120_000))
        frames: list[ExtractedVideoFrame] = []
        for ordinal in range(count):
            if cancelled():
                raise MediaProcessingCancelled()
            time_ms = round(metadata.duration_ms * (ordinal + 1) / (count + 1))
            destination = workspace / f"frame-{ordinal + 1:02d}.jpg"
            self._run(
                [
                    self.ffmpeg,
                    "-nostdin",
                    "-v", "error",
                    "-ss", f"{time_ms / 1000:.3f}",
                    "-i", str(artifact_path),
                    "-frames:v", "1",
                    "-vf", "scale=min(640,iw):-2",
                    "-q:v", "3",
                    "-y",
                    str(destination),
                ],
                limits,
                cancelled,
                heartbeat,
                workspace=workspace,
            )
            if not destination.is_file() or destination.stat().st_size == 0:
                raise MediaInputInvalid("frame_failed")
            frames.append(ExtractedVideoFrame(ordinal, time_ms, destination, None, None))
        return tuple(frames)


class UnconfiguredMediaAi:
    """Stable disabled state until a provider is explicitly wired."""

    @staticmethod
    def capability() -> dict[str, object]:
        return {"enabled": False, "provider": None, "network": False, "reason": "not_configured"}

    @staticmethod
    def config_hash(operation: str) -> str:
        return hashlib.sha256(f"media-ai-unconfigured:{operation}:1".encode("ascii")).hexdigest()

    @staticmethod
    def transcribe(_: Path, __: str | None, ___: Callable[[], bool]) -> MediaTranscript:
        raise MediaAiUnavailable("not_configured")

    @staticmethod
    def summarize(_: str, __: Callable[[], bool]) -> str:
        raise MediaAiUnavailable("not_configured")
