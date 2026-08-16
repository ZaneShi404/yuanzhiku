"""Explicit local FFmpeg media analysis and disabled-by-default AI adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageStat

from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, MediaTranscript, VideoMetadata
from app.ports.media import (
    MediaAiUnavailable,
    MediaAnalyzerPort,
    MediaInputInvalid,
    MediaProcessingCancelled,
    MediaToolUnavailable,
)

# 关键帧抽样参数：任何变更都会改变 config_hash，使新旧分析成为不同身份。
SAMPLING_STRATEGY = "scene-hybrid"
SAMPLING_DENSITY_SECONDS = 120
SAMPLING_PLACEMENT = "5-95-anchors"
SAMPLING_SCALE_MAX_WIDTH = 640
SAMPLING_JPEG_QUALITY = 3
SAMPLING_SCENE_THRESHOLD = 0.3

_SCENE_TIME_PATTERN = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def plan_frame_times(
    duration_ms: int,
    maximum_frames: int,
    scene_times_ms: list[int] | tuple[int, ...],
) -> list[tuple[int, str]]:
    """规划关键帧时间点：场景切换优先，等间隔槽位兜底。

    规则：短视频（<120s）至少抽 3 帧；长视频目标帧数为 ceil(时长/120s)，
    并以 maximum_frames 封顶。首槽锚定约 5%、末槽约 95%，中间槽均匀分布；
    每个槽位在半个槽距容差内吸附最近的未使用场景点，吸不到就保留等间隔
    位置；相距不足 1 秒的时间点去重（同距优先保留场景帧）。
    """
    if duration_ms <= 0:
        return []
    maximum_frames = max(1, maximum_frames)
    if duration_ms < SAMPLING_DENSITY_SECONDS * 1000:
        count = min(maximum_frames, 3)
    else:
        count = min(maximum_frames, max(1, math.ceil(duration_ms / (SAMPLING_DENSITY_SECONDS * 1000))))
    first = duration_ms * 0.05
    last = duration_ms * 0.95
    if count == 1:
        ideals = [round(duration_ms * 0.5)]
    else:
        ideals = [round(first + (last - first) * index / (count - 1)) for index in range(count)]
    scenes = sorted({int(time_ms) for time_ms in scene_times_ms if 0 < time_ms < duration_ms})
    tolerance = round((last - first) / (count - 1) / 2) if count > 1 else round(duration_ms * 0.05)
    used_scenes: set[int] = set()
    planned: list[tuple[int, str]] = []
    for ideal in ideals:
        candidates = [time_ms for time_ms in scenes if time_ms not in used_scenes and abs(time_ms - ideal) <= tolerance]
        scene = min(candidates, key=lambda time_ms: abs(time_ms - ideal), default=None)
        if scene is None:
            planned.append((ideal, "even"))
        else:
            planned.append((scene, "scene"))
            used_scenes.add(scene)
    planned.sort(key=lambda item: (item[0], item[1] != "scene"))
    result: list[tuple[int, str]] = []
    for time_ms, reason in planned:
        if result and time_ms - result[-1][0] < 1000:
            if reason == "scene" and result[-1][1] == "even":
                result[-1] = (time_ms, reason)
            continue
        result.append((time_ms, reason))
    return result


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
        value = (
            f"ffmpeg-local:2:{SAMPLING_STRATEGY}:{SAMPLING_DENSITY_SECONDS}:{SAMPLING_PLACEMENT}"
            f":{SAMPLING_SCALE_MAX_WIDTH}:{SAMPLING_JPEG_QUALITY}:{maximum_frames}"
        ).encode("ascii")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _process_memory_bytes(process_id: int) -> int | None:
        try:
            import psutil  # type: ignore[import-not-found]

            process = psutil.Process(process_id)
            return process.memory_info().rss + sum(
                child.memory_info().rss for child in process.children(recursive=True)
            )
        except Exception:
            # 尽力而为监测：子进程在 poll 与 psutil.Process() 之间恰好退出时抛
            # psutil.NoSuchProcess——它继承 Exception 而非 OSError（0.1s 轮询
            # 极易命中该 TOCTOU 窗口），任何失败都不应炸作业。
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
        capture_stderr: bool = False,
    ) -> bytes:
        output_file = tempfile.TemporaryFile() if capture_stdout else None
        error_file = tempfile.TemporaryFile() if capture_stderr else None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output_file if output_file is not None else subprocess.DEVNULL,
                stderr=error_file if error_file is not None else subprocess.DEVNULL,
                shell=False,
            )
        except FileNotFoundError as exc:
            if output_file is not None:
                output_file.close()
            if error_file is not None:
                error_file.close()
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
            for captured in (output_file, error_file):
                if captured is None:
                    continue
                captured.seek(0, os.SEEK_END)
                if captured.tell() > 512 * 1024:
                    raise MediaInputInvalid("output_limit")
                captured.seek(0)
            if output_file is not None:
                output = output_file.read()
            elif error_file is not None:
                output = error_file.read()
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
            if error_file is not None:
                error_file.close()

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

    def _scene_times(
        self,
        artifact_path: Path,
        metadata: VideoMetadata,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> list[int]:
        """Detect scene-cut times via ffmpeg showinfo（输出在 stderr）。"""
        output = self._run(
            [
                self.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-nostats",
                "-v", "info",
                "-i", str(artifact_path),
                "-vf", f"select='gt(scene,{SAMPLING_SCENE_THRESHOLD})',showinfo",
                "-an",
                "-f", "null",
                "-",
            ],
            limits,
            cancelled,
            heartbeat,
            capture_stderr=True,
        )
        times: list[int] = []
        for match in _SCENE_TIME_PATTERN.finditer(output.decode("utf-8", errors="replace")):
            time_ms = round(float(match.group(1)) * 1000)
            if 0 < time_ms < metadata.duration_ms:
                times.append(time_ms)
        return times

    def _extract_frame_at(
        self,
        artifact_path: Path,
        time_ms: int,
        destination: Path,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        workspace: Path,
    ) -> None:
        self._run(
            [
                self.ffmpeg,
                "-nostdin",
                "-v", "error",
                "-ss", f"{time_ms / 1000:.3f}",
                "-i", str(artifact_path),
                "-frames:v", "1",
                # filtergraph 中逗号是链分隔符：min(640,iw) 的逗号必须转义。
                "-vf", f"scale=min({SAMPLING_SCALE_MAX_WIDTH}\\,iw):-2",
                "-q:v", str(SAMPLING_JPEG_QUALITY),
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

    @staticmethod
    def _is_black_frame(path: Path) -> bool:
        """黑帧护栏：灰度均值低于阈值视为无信息帧。"""
        with Image.open(path) as image:
            gray = image.convert("L")
            return ImageStat.Stat(gray).mean[0] < 16

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
        scene_times = self._scene_times(artifact_path, metadata, limits, cancelled, heartbeat)
        planned = plan_frame_times(metadata.duration_ms, maximum_frames, scene_times)
        shift = max(1_000, round(metadata.duration_ms * 0.05))
        frames: list[ExtractedVideoFrame] = []
        for ordinal, (time_ms, reason) in enumerate(planned):
            if cancelled():
                raise MediaProcessingCancelled()
            destination = workspace / f"frame-{ordinal + 1:02d}.jpg"
            # 黑帧时依次尝试：未使用的最近场景点、±5% 平移的等距点；仍黑则接受当前结果。
            unused_scenes = sorted(
                (scene for scene in scene_times if scene not in {time for time, _ in planned}),
                key=lambda scene: abs(scene - time_ms),
            )
            candidates = [time_ms, *unused_scenes[:2], time_ms - shift, time_ms + shift]
            candidates = [
                candidate for index, candidate in enumerate(candidates)
                if 0 < candidate < metadata.duration_ms and candidate not in candidates[:index]
            ][:4]
            chosen_ms = candidates[-1]
            for candidate in candidates:
                self._extract_frame_at(
                    artifact_path, candidate, destination, limits, cancelled, heartbeat, workspace
                )
                chosen_ms = candidate
                if not self._is_black_frame(destination):
                    break
            with Image.open(destination) as image:
                width, height = image.size
            frames.append(ExtractedVideoFrame(ordinal, chosen_ms, destination, width, height, reason))
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
    def assess_completeness(_: str, __: dict) -> dict:
        raise MediaAiUnavailable("not_configured")

    @staticmethod
    def summarize(_: dict, __: Callable[[], bool]) -> dict:
        raise MediaAiUnavailable("not_configured")

    @staticmethod
    def classify(_: str, __: dict) -> dict:
        raise MediaAiUnavailable("not_configured")
